"""Local RAG DB 문서·청크·실행 이력 저장소를 테스트한다."""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.chunking.models import TextChunk
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.indexing.exceptions import LocalRagStorageError
from jipsa_rag.infrastructure.indexing.local_repository import (
    LocalRagIndexRepository,
)
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata

TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
OTHER_CHUNK_ID = "22222222-2222-2222-2222-222222222222"
TEST_CONTENT = "Local RAG repository test content."
TEST_CONTENT_HASH = hashlib.sha256(TEST_CONTENT.encode("utf-8")).hexdigest()
TEST_FILE_HASH = "a" * 64
TEST_EMBEDDING_MODEL = "test/embedding-model"
TEST_INDEX_VERSION = 2


class FakeTransactionContext:
    """AsyncSession.begin()이 반환하는 비동기 트랜잭션 대역."""

    def __init__(
        self,
        session: "FakeAsyncSession",
    ) -> None:
        """트랜잭션 진입과 종료를 기록할 세션 대역을 저장한다."""

        self._session = session

    async def __aenter__(
        self,
    ) -> "FakeTransactionContext":
        """트랜잭션 진입 횟수를 기록한다."""

        self._session.transaction_enter_count += 1
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """트랜잭션 종료 횟수를 기록한다."""

        del exception_type
        del exception
        del traceback

        self._session.transaction_exit_count += 1


class FakeScalarOneResult:
    """SELECT LAST_INSERT_ID()의 단일 값을 반환하는 Result 대역."""

    def __init__(
        self,
        value: object,
    ) -> None:
        """반환할 단일 스칼라 값을 저장한다."""

        self._value = value

    def scalar_one(
        self,
    ) -> object:
        """설정된 단일 스칼라 값을 반환한다."""

        return self._value


class FakeMappingRows:
    """Result.mappings().one_or_none() 호출을 지원하는 대역."""

    def __init__(
        self,
        row: Mapping[str, object] | None,
    ) -> None:
        """반환할 문서 행을 저장한다."""

        self._row = row

    def one_or_none(
        self,
    ) -> Mapping[str, object] | None:
        """정확히 일치하는 문서 행 또는 None을 반환한다."""

        return self._row


class FakeScalarRows:
    """Result.scalars().all() 호출을 지원하는 대역."""

    def __init__(
        self,
        values: Sequence[object],
    ) -> None:
        """반환할 스칼라 값을 불변 튜플로 저장한다."""

        self._values = tuple(values)

    def all(
        self,
    ) -> Sequence[object]:
        """설정된 스칼라 결과 목록을 반환한다."""

        return self._values


class FakeExecuteResult:
    """일반 SELECT와 INSERT·UPDATE·DELETE 결과를 표현하는 대역."""

    def __init__(
        self,
        *,
        mapping_row: Mapping[str, object] | None = None,
        scalar_values: Sequence[object] = (),
    ) -> None:
        """매핑 행과 스칼라 목록을 저장한다."""

        self._mapping_row = mapping_row
        self._scalar_values = tuple(scalar_values)

    def mappings(
        self,
    ) -> FakeMappingRows:
        """매핑 기반 행 조회 대역을 반환한다."""

        return FakeMappingRows(
            self._mapping_row,
        )

    def scalars(
        self,
    ) -> FakeScalarRows:
        """스칼라 목록 조회 대역을 반환한다."""

        return FakeScalarRows(
            self._scalar_values,
        )

    def scalar_one(
        self,
    ) -> object:
        """LAST_INSERT_ID 외 SQL에서 잘못 호출되면 테스트를 실패시킨다."""

        raise AssertionError("scalar_one() must only be called for LAST_INSERT_ID queries.")


class FakeAsyncSession:
    """실제 DB 없이 SQL 호출 순서와 파라미터를 기록하는 세션 대역."""

    def __init__(
        self,
    ) -> None:
        """SQL 응답과 호출 기록의 초기 상태를 생성한다."""

        self.execute_calls: list[tuple[str, object | None]] = []

        self.last_insert_ids: list[object] = [
            100,
            200,
        ]

        self.transaction_enter_count = 0
        self.transaction_exit_count = 0

        self.execute_error: SQLAlchemyError | None = None

        # 현재 색인 정체성과 정확히 일치하는 활성 문서다.
        #
        # None이면 새 RAG_Document를 준비하는 경로를 수행한다.
        self.exact_document_row: (
            Mapping[
                str,
                object,
            ]
            | None
        ) = None

        # 동일한 정상 색인을 재사용할 때 비교할 기존 Chunk ID 목록이다.
        self.persisted_chunk_ids: tuple[str, ...] = ()

        # 새 색인이 성공한 뒤 대체될 이전 INDEXED 문서 식별자다.
        self.previous_rag_document_idxs: tuple[int, ...] = ()

    def begin(
        self,
    ) -> FakeTransactionContext:
        """비동기 트랜잭션 context 대역을 반환한다."""

        return FakeTransactionContext(
            self,
        )

    async def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> FakeScalarOneResult | FakeExecuteResult:
        """SQL과 파라미터를 기록하고 SQL 종류에 맞는 결과를 반환한다."""

        if self.execute_error is not None:
            raise self.execute_error

        statement_text = str(statement)
        normalized_statement = " ".join(statement_text.split()).lower()

        self.execute_calls.append(
            (
                statement_text,
                parameters,
            )
        )

        if "select last_insert_id()" in normalized_statement:
            if not self.last_insert_ids:
                raise AssertionError("No fake LAST_INSERT_ID value remains.")

            return FakeScalarOneResult(
                self.last_insert_ids.pop(0),
            )

        # 기존 정상 색인의 청크 정체성 검증 SELECT는
        # Result.scalars().all()을 통해 Chunk ID 목록을 읽는다.
        if "select" in normalized_statement and "chunk_id" in normalized_statement:
            return FakeExecuteResult(
                mapping_row=self.exact_document_row,
                scalar_values=self.persisted_chunk_ids,
            )

        # 이전 INDEXED 문서 조회는
        # RAG_Document_IDX 스칼라 목록을 반환한다.
        if (
            "select" in normalized_statement
            and "rag_document_idx" in normalized_statement
            and "index_status" in normalized_statement
            and "indexed" in normalized_statement
        ):
            return FakeExecuteResult(
                mapping_row=self.exact_document_row,
                scalar_values=self.previous_rag_document_idxs,
            )

        # 정확한 문서 정체성 조회는
        # mappings().one_or_none()으로 문서 행을 읽는다.
        #
        # 일반 쓰기 SQL에서는 mappings()나 scalars()가 호출되지 않으므로
        # 같은 Result 대역을 반환해도 테스트 결과에 영향을 주지 않는다.
        return FakeExecuteResult(
            mapping_row=self.exact_document_row,
        )


def _create_metadata() -> DocumentIndexMetadata:
    """저장소 테스트에서 공통으로 사용할 문서 메타데이터를 생성한다."""

    return DocumentIndexMetadata(
        users_idx=1,
        file_idx=10,
        folder_idx=3,
        file_name="project-guide.pdf",
        file_type=DocumentType.PDF,
        file_hash=TEST_FILE_HASH,
        index_version=TEST_INDEX_VERSION,
        parser_type="PDF_TEXT",
        parser_version="1.0.0",
    )


def _create_embedded_document() -> EmbeddedDocument:
    """페이지 메타데이터를 가진 단일 임베딩 청크를 생성한다."""

    chunk = TextChunk(
        chunk_id=TEST_CHUNK_ID,
        chunk_index=0,
        content=TEST_CONTENT,
        content_hash=TEST_CONTENT_HASH,
        start_offset=0,
        end_offset=len(TEST_CONTENT),
        source_metadata={
            "page_number": 2,
            "source_unit_index": 1,
            "unit_start_offset": 0,
            "unit_end_offset": len(TEST_CONTENT),
        },
    )

    return EmbeddedDocument(
        embedding_model=TEST_EMBEDDING_MODEL,
        embedding_dim=3,
        chunks=(
            EmbeddedChunk(
                chunk=chunk,
                embedding=(
                    0.1,
                    0.2,
                    0.3,
                ),
            ),
        ),
    )


def _find_execute_call(
    fake_session: FakeAsyncSession,
    *required_fragments: str,
) -> tuple[str, object | None]:
    """모든 문자열 조각을 포함하는 첫 SQL 호출을 반환한다."""

    normalized_fragments = tuple(fragment.lower() for fragment in required_fragments)

    for statement_text, parameters in fake_session.execute_calls:
        normalized_statement = " ".join(statement_text.split()).lower()

        if all(fragment in normalized_statement for fragment in normalized_fragments):
            return (
                statement_text,
                parameters,
            )

    raise AssertionError("Expected SQL call was not found: " + ", ".join(required_fragments))


def _find_execute_call_by_parameter_key(
    fake_session: FakeAsyncSession,
    parameter_key: str,
) -> tuple[str, Mapping[str, object]]:
    """지정한 파라미터 키를 포함하는 첫 SQL 호출을 반환한다."""

    for statement_text, parameters in fake_session.execute_calls:
        if isinstance(parameters, Mapping) and parameter_key in parameters:
            return (
                statement_text,
                parameters,
            )

    raise AssertionError(f"Expected SQL call with parameter key was not found: {parameter_key}")


def _has_execute_call(
    fake_session: FakeAsyncSession,
    *required_fragments: str,
) -> bool:
    """모든 문자열 조각을 포함하는 SQL 호출이 존재하는지 반환한다."""

    normalized_fragments = tuple(fragment.lower() for fragment in required_fragments)

    return any(
        all(
            fragment in " ".join(statement_text.split()).lower()
            for fragment in normalized_fragments
        )
        for statement_text, _ in fake_session.execute_calls
    )


def _has_parameter_key(
    fake_session: FakeAsyncSession,
    parameter_key: str,
) -> bool:
    """지정한 파라미터 키를 포함하는 SQL 호출이 존재하는지 반환한다."""

    return any(
        isinstance(parameters, Mapping) and parameter_key in parameters
        for _, parameters in fake_session.execute_calls
    )


@pytest.mark.asyncio
async def test_prepare_indexing_saves_new_document_chunks_and_run() -> None:
    """새 색인의 문서·청크·실행을 저장하고 이전 문서 목록을 반환한다."""

    fake_session = FakeAsyncSession()
    fake_session.previous_rag_document_idxs = (
        90,
        91,
    )

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    prepared_index = await repository.prepare_indexing(
        metadata=_create_metadata(),
        embedded_document=_create_embedded_document(),
    )

    assert prepared_index.rag_document_idx == 100
    assert prepared_index.rag_index_run_idx == 200
    assert prepared_index.chunk_ids == (TEST_CHUNK_ID,)
    assert prepared_index.previous_rag_document_idxs == (
        90,
        91,
    )
    assert prepared_index.reuses_existing_index is False

    assert fake_session.transaction_enter_count == 1
    assert fake_session.transaction_exit_count == 1

    # 문서 UPSERT에는 AWS 서버 DB가 관리하는 S3_Key를 포함하지 않는다.
    _, document_parameters = _find_execute_call(
        fake_session,
        "insert into `rag_document`",
    )

    assert isinstance(
        document_parameters,
        Mapping,
    )
    assert "s3_key" not in document_parameters
    assert document_parameters["file_idx"] == 10
    assert document_parameters["file_hash"] == TEST_FILE_HASH
    assert document_parameters["parser_type"] == "PDF_TEXT"
    assert document_parameters["parser_version"] == "1.0.0"
    assert document_parameters["embedding_model"] == TEST_EMBEDDING_MODEL
    assert document_parameters["index_version"] == TEST_INDEX_VERSION

    # 현재 준비 중인 문서에 남아 있을 수 있는 실패 청크만 제거한다.
    #
    # 이전 INDEXED 문서의 청크는 새 색인이 성공할 때까지 삭제하지 않는다.
    _, delete_parameters = _find_execute_call(
        fake_session,
        "delete from `rag_chunk`",
    )

    assert delete_parameters == {
        "rag_document_idx": 100,
        "index_version": TEST_INDEX_VERSION,
    }

    # 새 Chunk ID와 메타데이터는 executemany 방식으로 저장한다.
    _, chunk_parameter_rows = _find_execute_call(
        fake_session,
        "insert into `rag_chunk`",
    )

    assert isinstance(
        chunk_parameter_rows,
        list,
    )
    assert len(chunk_parameter_rows) == 1

    chunk_parameters = chunk_parameter_rows[0]

    assert chunk_parameters["chunk_id"] == TEST_CHUNK_ID
    assert chunk_parameters["rag_document_idx"] == 100
    assert chunk_parameters["page"] == 2
    assert chunk_parameters["content"] == TEST_CONTENT
    assert chunk_parameters["content_hash"] == TEST_CONTENT_HASH
    assert chunk_parameters["embedding_model"] == TEST_EMBEDDING_MODEL
    assert chunk_parameters["index_version"] == TEST_INDEX_VERSION

    # 실행 이력은 Qdrant 반영 전 상태인 RUNNING으로 생성한다.
    _, run_parameters = _find_execute_call(
        fake_session,
        "insert into `rag_index_run`",
    )

    assert isinstance(
        run_parameters,
        Mapping,
    )
    assert run_parameters["rag_document_idx"] == 100
    assert run_parameters["chunk_count"] == 1


@pytest.mark.asyncio
async def test_prepare_indexing_reuses_exact_healthy_index() -> None:
    """동일 정체성의 정상 색인은 청크를 삭제하지 않고 재사용한다."""

    fake_session = FakeAsyncSession()

    fake_session.exact_document_row = {
        "rag_document_idx": 100,
        "index_status": "INDEXED",
    }
    fake_session.persisted_chunk_ids = (TEST_CHUNK_ID,)

    # 정상 문서를 재사용하는 경로에서는 새 RAG_Document를 만들지 않는다.
    #
    # 따라서 새로 생성되는 PK는 RAG_Index_Run의 200 하나뿐이다.
    fake_session.last_insert_ids = [
        200,
    ]

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    prepared_index = await repository.prepare_indexing(
        metadata=_create_metadata(),
        embedded_document=_create_embedded_document(),
    )

    assert prepared_index.rag_document_idx == 100
    assert prepared_index.rag_index_run_idx == 200
    assert prepared_index.chunk_ids == (TEST_CHUNK_ID,)
    assert prepared_index.previous_rag_document_idxs == ()
    assert prepared_index.reuses_existing_index is True

    # 정상 색인을 재사용할 때 기존 RAG_Chunk를 삭제하거나 재삽입하면
    # 새 처리 실패가 기존 정상 검색 결과까지 손상시킬 수 있다.
    assert not _has_execute_call(
        fake_session,
        "delete from `rag_chunk`",
    )
    assert not _has_execute_call(
        fake_session,
        "insert into `rag_chunk`",
    )
    assert not _has_execute_call(
        fake_session,
        "insert into `rag_document`",
    )

    # 동일 정상 문서를 재사용하더라도 재시도 실행 이력은 별도로 생성한다.
    _, run_parameters = _find_execute_call(
        fake_session,
        "insert into `rag_index_run`",
    )

    assert isinstance(
        run_parameters,
        Mapping,
    )
    assert run_parameters["rag_document_idx"] == 100
    assert run_parameters["chunk_count"] == 1


@pytest.mark.asyncio
async def test_prepare_indexing_rejects_reused_index_with_different_chunk_ids() -> None:
    """정상 색인의 저장 Chunk ID가 현재 계산 결과와 다르면 재사용하지 않는다."""

    fake_session = FakeAsyncSession()

    fake_session.exact_document_row = {
        "rag_document_idx": 100,
        "index_status": "INDEXED",
    }
    fake_session.persisted_chunk_ids = (OTHER_CHUNK_ID,)

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    with pytest.raises(
        LocalRagStorageError,
    ) as exception_info:
        await repository.prepare_indexing(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert exception_info.value.operation == "indexed_chunk_identity_mismatch"

    # 저장된 정상 청크와 현재 계산 결과가 다르면
    # 기존 정상 색인과 새 색인 준비 상태를 모두 수정하지 않는다.
    assert not _has_execute_call(
        fake_session,
        "delete from `rag_chunk`",
    )
    assert not _has_execute_call(
        fake_session,
        "insert into `rag_chunk`",
    )
    assert not _has_execute_call(
        fake_session,
        "insert into `rag_index_run`",
    )


@pytest.mark.asyncio
async def test_prepare_indexing_rejects_invalid_last_insert_id() -> None:
    """DB가 양수 PK를 반환하지 않으면 저장소 예외로 변환한다."""

    fake_session = FakeAsyncSession()
    fake_session.last_insert_ids = [
        0,
    ]

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    with pytest.raises(
        LocalRagStorageError,
    ) as exception_info:
        await repository.prepare_indexing(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert exception_info.value.operation == "document_upsert"


@pytest.mark.asyncio
async def test_prepare_indexing_converts_sqlalchemy_error() -> None:
    """DB 드라이버 오류를 안전한 Local RAG 저장소 예외로 변환한다."""

    fake_session = FakeAsyncSession()
    fake_session.execute_error = SQLAlchemyError(
        "database failure",
    )

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    with pytest.raises(
        LocalRagStorageError,
    ) as exception_info:
        await repository.prepare_indexing(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert exception_info.value.operation == "prepare_indexing"


@pytest.mark.asyncio
async def test_mark_indexed_updates_current_run_and_superseded_documents() -> None:
    """성공한 새 색인을 확정하고 대체된 이전 문서를 소프트 삭제한다."""

    fake_session = FakeAsyncSession()

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    await repository.mark_indexed(
        rag_document_idx=100,
        rag_index_run_idx=200,
        superseded_rag_document_idxs=(
            90,
            91,
        ),
    )

    assert fake_session.transaction_enter_count == 1
    assert fake_session.transaction_exit_count == 1
    assert len(fake_session.execute_calls) == 3

    _, indexed_parameters = _find_execute_call(
        fake_session,
        "index_status` = 'indexed'",
    )
    _, success_parameters = _find_execute_call(
        fake_session,
        "status` = 'success'",
    )

    # 이전 문서 소프트 삭제 SQL은
    # 복수 문서 식별자인 rag_document_idxs 파라미터로 정확히 찾는다.
    #
    # 현재 문서를 INDEXED로 변경하는 SQL에도 Deleted_At 표현이 있을 수
    # 있으므로 단순 문자열 검색으로 이전 문서 SQL을 찾으면 안 된다.
    _, superseded_parameters = _find_execute_call_by_parameter_key(
        fake_session,
        "rag_document_idxs",
    )

    assert indexed_parameters == {
        "rag_document_idx": 100,
    }

    # 성공 시각은 저장소가 실행 시점에 생성하므로
    # 고정 딕셔너리 전체 비교가 아니라 필드별로 검증한다.
    assert isinstance(
        success_parameters,
        Mapping,
    )
    assert success_parameters["rag_index_run_idx"] == 200
    assert isinstance(
        success_parameters["finished_at"],
        datetime,
    )

    superseded_ids = cast(
        Sequence[int],
        superseded_parameters["rag_document_idxs"],
    )

    assert tuple(superseded_ids) == (
        90,
        91,
    )


@pytest.mark.asyncio
async def test_mark_indexed_skips_superseded_update_for_empty_ids() -> None:
    """대체할 이전 문서가 없으면 현재 문서와 실행 상태만 확정한다."""

    fake_session = FakeAsyncSession()

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    await repository.mark_indexed(
        rag_document_idx=100,
        rag_index_run_idx=200,
        superseded_rag_document_idxs=(),
    )

    assert len(fake_session.execute_calls) == 2

    assert _has_execute_call(
        fake_session,
        "index_status` = 'indexed'",
    )
    assert _has_execute_call(
        fake_session,
        "status` = 'success'",
    )

    # 현재 문서를 INDEXED로 변경하는 SQL의 Deleted_At 표현과 구분하기 위해
    # 이전 문서 목록 전용 파라미터가 전달되지 않았는지 검증한다.
    assert not _has_parameter_key(
        fake_session,
        "rag_document_idxs",
    )


@pytest.mark.asyncio
async def test_mark_failed_limits_error_message_and_updates_both_states() -> None:
    """새 문서 실패 시 제한 길이 메시지로 문서와 실행을 FAILED 처리한다."""

    fake_session = FakeAsyncSession()

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    long_error_message = "x" * 1200

    await repository.mark_failed(
        rag_document_idx=100,
        rag_index_run_idx=200,
        error_message=long_error_message,
    )

    assert len(fake_session.execute_calls) == 2

    assert _has_execute_call(
        fake_session,
        "index_status` = 'failed'",
    )
    assert _has_execute_call(
        fake_session,
        "status` = 'failed'",
    )

    _, failed_run_parameters = _find_execute_call(
        fake_session,
        "status` = 'failed'",
        "error_message",
    )

    assert isinstance(
        failed_run_parameters,
        Mapping,
    )
    assert (
        len(
            cast(
                str,
                failed_run_parameters["error_message"],
            )
        )
        == 1000
    )


@pytest.mark.asyncio
async def test_mark_run_failed_preserves_reused_healthy_document() -> None:
    """정상 색인 재사용 실패 시 실행만 FAILED 처리하고 문서는 보존한다."""

    fake_session = FakeAsyncSession()

    repository = LocalRagIndexRepository(
        cast(
            AsyncSession,
            fake_session,
        ),
    )

    await repository.mark_run_failed(
        rag_index_run_idx=200,
        error_message="qdrant activation failed",
    )

    assert len(fake_session.execute_calls) == 1

    assert _has_execute_call(
        fake_session,
        "status` = 'failed'",
    )
    assert not _has_execute_call(
        fake_session,
        "index_status` = 'failed'",
    )
