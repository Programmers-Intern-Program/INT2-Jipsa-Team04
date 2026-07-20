"""Local RAG DB 문서·청크·실행 이력 저장소를 테스트한다."""

import hashlib
from collections.abc import Mapping
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
TEST_CONTENT = "Local RAG repository test content."
TEST_CONTENT_HASH = hashlib.sha256(TEST_CONTENT.encode("utf-8")).hexdigest()
TEST_FILE_HASH = "a" * 64


class FakeTransactionContext:
    """AsyncSession.begin()이 반환하는 비동기 트랜잭션 대역."""

    def __init__(self, session: "FakeAsyncSession") -> None:
        self._session = session

    async def __aenter__(self) -> "FakeTransactionContext":
        self._session.transaction_enter_count += 1
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self._session.transaction_exit_count += 1


class FakeScalarResult:
    """SELECT LAST_INSERT_ID() 결과를 반환하는 Result 대역."""

    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class FakeExecuteResult:
    """scalar 조회가 아닌 SQL 실행 결과 대역."""

    def scalar_one(self) -> object:
        raise AssertionError("scalar_one() must only be called for LAST_INSERT_ID queries.")


class FakeAsyncSession:
    """실제 DB 없이 SQL 호출 순서와 파라미터를 기록하는 세션 대역."""

    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, object | None]] = []
        self.last_insert_ids: list[object] = [100, 200]
        self.transaction_enter_count = 0
        self.transaction_exit_count = 0
        self.execute_error: SQLAlchemyError | None = None

    def begin(self) -> FakeTransactionContext:
        return FakeTransactionContext(self)

    async def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> FakeScalarResult | FakeExecuteResult:
        if self.execute_error is not None:
            raise self.execute_error

        statement_text = str(statement)
        self.execute_calls.append((statement_text, parameters))

        if "SELECT LAST_INSERT_ID()" in statement_text:
            if not self.last_insert_ids:
                raise AssertionError("No fake LAST_INSERT_ID value remains.")

            return FakeScalarResult(self.last_insert_ids.pop(0))

        return FakeExecuteResult()


def _create_metadata() -> DocumentIndexMetadata:
    """저장소 테스트에서 공통으로 사용할 문서 메타데이터를 생성한다."""

    return DocumentIndexMetadata(
        users_idx=1,
        file_idx=10,
        folder_idx=3,
        file_name="project-guide.pdf",
        file_type=DocumentType.PDF,
        file_hash=TEST_FILE_HASH,
        index_version=1,
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
        embedding_model="test/embedding-model",
        embedding_dim=3,
        chunks=(
            EmbeddedChunk(
                chunk=chunk,
                embedding=(0.1, 0.2, 0.3),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_prepare_indexing_saves_document_chunks_and_run_without_s3_key() -> None:
    """Local RAG 저장 파라미터에 S3_Key 없이 문서·청크·실행을 저장한다."""

    fake_session = FakeAsyncSession()
    repository = LocalRagIndexRepository(
        cast(AsyncSession, fake_session),
    )

    prepared_index = await repository.prepare_indexing(
        metadata=_create_metadata(),
        embedded_document=_create_embedded_document(),
    )

    assert prepared_index.rag_document_idx == 100
    assert prepared_index.rag_index_run_idx == 200
    assert prepared_index.chunk_ids == (TEST_CHUNK_ID,)
    assert fake_session.transaction_enter_count == 1
    assert fake_session.transaction_exit_count == 1

    # 문서 UPSERT가 첫 번째 SQL이며 S3_Key 컬럼이나 파라미터를 포함하지 않아야 한다.
    document_sql, document_parameters = fake_session.execute_calls[0]

    assert "INSERT INTO `RAG_Document`" in document_sql
    assert "S3_Key" not in document_sql
    assert isinstance(document_parameters, Mapping)
    assert "s3_key" not in document_parameters
    assert document_parameters["file_idx"] == 10
    assert document_parameters["file_hash"] == TEST_FILE_HASH
    assert document_parameters["parser_type"] == "PDF_TEXT"
    assert document_parameters["parser_version"] == "1.0.0"

    # 동일 색인 버전의 이전 청크를 삭제한 뒤 새 청크를 executemany로 저장한다.
    delete_sql, delete_parameters = fake_session.execute_calls[2]
    chunk_sql, chunk_parameter_rows = fake_session.execute_calls[3]

    assert "DELETE FROM `RAG_Chunk`" in delete_sql
    assert delete_parameters == {
        "rag_document_idx": 100,
        "index_version": 1,
    }

    assert "INSERT INTO `RAG_Chunk`" in chunk_sql
    assert isinstance(chunk_parameter_rows, list)
    assert len(chunk_parameter_rows) == 1

    chunk_parameters = chunk_parameter_rows[0]

    assert chunk_parameters["chunk_id"] == TEST_CHUNK_ID
    assert chunk_parameters["rag_document_idx"] == 100
    assert chunk_parameters["page"] == 2
    assert chunk_parameters["content"] == TEST_CONTENT
    assert chunk_parameters["content_hash"] == TEST_CONTENT_HASH
    assert chunk_parameters["embedding_model"] == "test/embedding-model"

    # 실행 이력은 Qdrant 업서트 전 상태인 RUNNING으로 생성된다.
    run_sql, run_parameters = fake_session.execute_calls[4]

    assert "INSERT INTO `RAG_Index_Run`" in run_sql
    assert isinstance(run_parameters, Mapping)
    assert run_parameters["rag_document_idx"] == 100
    assert run_parameters["chunk_count"] == 1


@pytest.mark.asyncio
async def test_prepare_indexing_rejects_invalid_last_insert_id() -> None:
    """DB가 양수 PK를 반환하지 않으면 저장소 예외로 변환한다."""

    fake_session = FakeAsyncSession()
    fake_session.last_insert_ids = [0]
    repository = LocalRagIndexRepository(
        cast(AsyncSession, fake_session),
    )

    with pytest.raises(LocalRagStorageError) as exception_info:
        await repository.prepare_indexing(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert exception_info.value.operation == "document_upsert"


@pytest.mark.asyncio
async def test_prepare_indexing_converts_sqlalchemy_error() -> None:
    """DB 드라이버 오류를 안전한 Local RAG 저장소 예외로 변환한다."""

    fake_session = FakeAsyncSession()
    fake_session.execute_error = SQLAlchemyError("database failure")
    repository = LocalRagIndexRepository(
        cast(AsyncSession, fake_session),
    )

    with pytest.raises(LocalRagStorageError) as exception_info:
        await repository.prepare_indexing(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert exception_info.value.operation == "prepare_indexing"


@pytest.mark.asyncio
async def test_mark_indexed_updates_document_and_run_in_one_transaction() -> None:
    """Qdrant 성공 후 문서 INDEXED와 실행 SUCCESS를 함께 기록한다."""

    fake_session = FakeAsyncSession()
    repository = LocalRagIndexRepository(
        cast(AsyncSession, fake_session),
    )

    await repository.mark_indexed(
        rag_document_idx=100,
        rag_index_run_idx=200,
    )

    assert fake_session.transaction_enter_count == 1
    assert fake_session.transaction_exit_count == 1
    assert len(fake_session.execute_calls) == 2
    assert "`Index_Status` = 'INDEXED'" in fake_session.execute_calls[0][0]
    assert "`Status` = 'SUCCESS'" in fake_session.execute_calls[1][0]


@pytest.mark.asyncio
async def test_mark_failed_limits_error_message_and_updates_both_states() -> None:
    """안전한 제한 길이 메시지로 문서와 실행을 FAILED 처리한다."""

    fake_session = FakeAsyncSession()
    repository = LocalRagIndexRepository(
        cast(AsyncSession, fake_session),
    )
    long_error_message = "x" * 1200

    await repository.mark_failed(
        rag_document_idx=100,
        rag_index_run_idx=200,
        error_message=long_error_message,
    )

    assert len(fake_session.execute_calls) == 2
    assert "`Index_Status` = 'FAILED'" in fake_session.execute_calls[0][0]
    assert "`Status` = 'FAILED'" in fake_session.execute_calls[1][0]

    failed_run_parameters = fake_session.execute_calls[1][1]

    assert isinstance(failed_run_parameters, Mapping)
    assert len(cast(str, failed_run_parameters["error_message"])) == 1000
