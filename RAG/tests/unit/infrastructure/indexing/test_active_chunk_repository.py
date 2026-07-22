"""최신 활성 청크 저장소 조회 정책을 테스트한다."""

import hashlib
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.indexing.active_chunk_repository import (
    LocalRagActiveChunkRepository,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
)

_OLD_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
_LATEST_FIRST_CHUNK_ID = "22222222-2222-2222-2222-222222222222"
_LATEST_SECOND_CHUNK_ID = "33333333-3333-3333-3333-333333333333"


class FakeTransactionContext:
    """AsyncSession.begin()이 반환하는 비동기 트랜잭션 대역."""

    async def __aenter__(
        self,
    ) -> "FakeTransactionContext":
        """비동기 트랜잭션 context에 진입한다."""

        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """전달된 예외 정보 참조를 정리한다."""

        del exception_type
        del exception
        del traceback


class FakeMappingRows:
    """Result.mappings().all() 호출을 지원하는 대역."""

    def __init__(
        self,
        rows: Sequence[Mapping[str, object]],
    ) -> None:
        """반환할 조회 행을 불변 튜플로 저장한다."""

        self._rows = tuple(rows)

    def all(
        self,
    ) -> Sequence[Mapping[str, object]]:
        """설정된 조회 행 전체를 반환한다."""

        return self._rows


class FakeExecuteResult:
    """최신 활성 청크 SELECT 결과를 표현하는 대역."""

    def __init__(
        self,
        rows: Sequence[Mapping[str, object]],
    ) -> None:
        """반환할 조회 행을 저장한다."""

        self._rows = tuple(rows)

    def mappings(
        self,
    ) -> FakeMappingRows:
        """매핑 기반 조회 결과 대역을 반환한다."""

        return FakeMappingRows(
            self._rows,
        )


class FakeAsyncSession:
    """실행 SQL과 바인딩 파라미터를 기록하는 AsyncSession 대역."""

    def __init__(
        self,
        *,
        rows: Sequence[Mapping[str, object]],
    ) -> None:
        """조회 결과와 SQL 기록 필드를 초기화한다."""

        self._rows = tuple(rows)
        self.executed_statement = ""
        self.executed_parameters: Mapping[str, object] | None = None

    def begin(
        self,
    ) -> FakeTransactionContext:
        """비동기 트랜잭션 대역을 반환한다."""

        return FakeTransactionContext()

    async def execute(
        self,
        statement: object,
        parameters: Mapping[str, object],
    ) -> FakeExecuteResult:
        """실행 SQL과 파라미터를 기록하고 준비된 조회 결과를 반환한다."""

        self.executed_statement = str(statement)
        self.executed_parameters = dict(parameters)

        return FakeExecuteResult(
            self._rows,
        )


def _create_chunk_row(
    *,
    rag_document_idx: int,
    chunk_id: str,
    chunk_index: int,
    content: str,
    page: int,
) -> dict[str, object]:
    """최신 활성 문서에 속한 단일 청크 조회 행을 생성한다."""

    return {
        "rag_document_idx": rag_document_idx,
        "users_idx": 45,
        "file_idx": 123,
        "index_version": 2,
        "chunk_count": 2,
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "content": content,
        "content_hash": hashlib.sha256(
            content.encode(
                "utf-8",
            )
        ).hexdigest(),
        "token_count": None,
        "page": page,
        "slide_no": None,
        "sheet_name": None,
        "section_title": None,
    }


@pytest.mark.asyncio
async def test_fetches_all_chunks_from_latest_successful_active_document() -> None:
    """특정 이전 문서가 아니라 최신 SUCCESS 실행의 전체 청크를 반환해야 한다."""

    latest_document_idx = 200
    rows = (
        _create_chunk_row(
            rag_document_idx=latest_document_idx,
            chunk_id=_LATEST_FIRST_CHUNK_ID,
            chunk_index=0,
            content="최신 재색인의 첫 번째 청크",
            page=1,
        ),
        _create_chunk_row(
            rag_document_idx=latest_document_idx,
            chunk_id=_LATEST_SECOND_CHUNK_ID,
            chunk_index=1,
            content="최신 재색인의 두 번째 청크",
            page=2,
        ),
    )
    fake_session = FakeAsyncSession(
        rows=rows,
    )
    repository = LocalRagActiveChunkRepository(
        cast(
            AsyncSession,
            fake_session,
        )
    )

    snapshot = await repository.fetch_latest_active_chunk_snapshot(
        users_idx=45,
        file_idx=123,
    )

    assert snapshot.rag_document_idx == latest_document_idx
    assert snapshot.chunk_count == 2
    assert tuple(chunk.chunk_id for chunk in snapshot.chunks) == (
        _LATEST_FIRST_CHUNK_ID,
        _LATEST_SECOND_CHUNK_ID,
    )
    assert _OLD_CHUNK_ID not in {chunk.chunk_id for chunk in snapshot.chunks}

    # 호출자가 과거 처리 결과의 RAG_Document_IDX를 바인딩하지 않는다.
    # 저장소 쿼리가 최신 SUCCESS 실행을 MAX(PK)로 직접 선택해야 한다.
    assert fake_session.executed_parameters == {
        "users_idx": 45,
        "file_idx": 123,
    }
    assert "MAX(candidate_run.`RAG_Index_Run_IDX`)" in fake_session.executed_statement
    assert ":rag_document_idx" not in fake_session.executed_statement


@pytest.mark.asyncio
async def test_rejects_missing_latest_active_chunk_snapshot() -> None:
    """최신 성공 문서 또는 청크가 없으면 빈 성공 payload를 만들지 않아야 한다."""

    fake_session = FakeAsyncSession(
        rows=(),
    )
    repository = LocalRagActiveChunkRepository(
        cast(
            AsyncSession,
            fake_session,
        )
    )

    with pytest.raises(
        LocalRagStorageError,
    ) as exception_info:
        await repository.fetch_latest_active_chunk_snapshot(
            users_idx=45,
            file_idx=123,
        )

    assert exception_info.value.operation == "latest_active_chunk_snapshot_not_found"
