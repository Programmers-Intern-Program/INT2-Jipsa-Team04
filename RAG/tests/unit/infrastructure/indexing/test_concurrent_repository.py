"""최신 색인 실행만 Local RAG 최종 상태를 변경하는지 테스트한다."""

from collections.abc import Sequence
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.indexing.concurrent_repository import (
    ConcurrentSafeLocalRagIndexRepository,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    IndexRunOwnershipLostError,
)


class FakeTransactionContext:
    """AsyncSession.begin() 비동기 context 대역."""

    async def __aenter__(self) -> "FakeTransactionContext":
        """트랜잭션 context에 진입한다."""

        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """실제 commit 또는 rollback 없이 context를 종료한다."""

        del exception_type
        del exception
        del traceback


class FakeUpdateResult:
    """조건부 UPDATE rowcount를 반환하는 결과 대역."""

    def __init__(self, rowcount: int) -> None:
        """UPDATE 영향 행 수를 저장한다."""

        self.rowcount = rowcount


class FakeAsyncSession:
    """조건부 상태 SQL과 파라미터를 기록하는 세션 대역."""

    def __init__(self, rowcounts: Sequence[int]) -> None:
        """execute 호출 순서에 맞는 rowcount 목록을 저장한다."""

        self._rowcounts = list(rowcounts)
        self.execute_calls: list[tuple[str, object | None]] = []

    def begin(self) -> FakeTransactionContext:
        """비동기 트랜잭션 context를 반환한다."""

        return FakeTransactionContext()

    async def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> FakeUpdateResult:
        """SQL 호출을 기록하고 다음 rowcount를 반환한다."""

        if not self._rowcounts:
            raise AssertionError("No fake rowcount remains.")

        self.execute_calls.append((str(statement), parameters))
        return FakeUpdateResult(self._rowcounts.pop(0))


@pytest.mark.asyncio
async def test_mark_indexed_rejects_run_when_newer_run_exists() -> None:
    """최신 실행이 아니어서 SUCCESS 갱신이 0행이면 소유권 상실로 중단한다."""

    fake_session = FakeAsyncSession([0])
    repository = ConcurrentSafeLocalRagIndexRepository(cast(AsyncSession, fake_session))

    with pytest.raises(IndexRunOwnershipLostError) as exception_info:
        await repository.mark_indexed(
            rag_document_idx=100,
            rag_index_run_idx=200,
            superseded_rag_document_idxs=(90,),
        )

    assert exception_info.value.operation == "mark_indexed_run_ownership"
    assert len(fake_session.execute_calls) == 1

    statement, parameters = fake_session.execute_calls[0]
    normalized_statement = " ".join(statement.split()).lower()

    assert "left join `rag_index_run` as `newer_run`" in normalized_statement
    assert "newer_run`.`rag_index_run_idx` is null" in normalized_statement
    assert parameters is not None


@pytest.mark.asyncio
async def test_mark_indexed_updates_only_current_success_and_superseded_documents() -> None:
    """최신 실행은 실행, 문서 및 이전 문서 상태를 한 트랜잭션에서 갱신한다."""

    fake_session = FakeAsyncSession([1, 1, 2])
    repository = ConcurrentSafeLocalRagIndexRepository(cast(AsyncSession, fake_session))

    await repository.mark_indexed(
        rag_document_idx=100,
        rag_index_run_idx=200,
        superseded_rag_document_idxs=(90, 91),
    )

    assert len(fake_session.execute_calls) == 3

    run_sql = " ".join(fake_session.execute_calls[0][0].split()).lower()
    document_sql = " ".join(fake_session.execute_calls[1][0].split()).lower()
    superseded_sql = " ".join(fake_session.execute_calls[2][0].split()).lower()

    assert "status` = 'success'" in run_sql
    assert "index_status` = 'indexed'" in document_sql
    assert "superseded_document" in superseded_sql
    assert "newer_run`.`rag_index_run_idx` is null" in superseded_sql


@pytest.mark.asyncio
async def test_mark_failed_preserves_newer_run_for_same_document() -> None:
    """실패 SQL은 실행 자신만 종료하고 같은 문서의 최신 실행을 보존한다."""

    fake_session = FakeAsyncSession([1, 0])
    repository = ConcurrentSafeLocalRagIndexRepository(cast(AsyncSession, fake_session))

    await repository.mark_failed(
        rag_document_idx=100,
        rag_index_run_idx=200,
        error_message="vector failure",
    )

    assert len(fake_session.execute_calls) == 2

    run_sql = " ".join(fake_session.execute_calls[0][0].split()).lower()
    document_sql = " ".join(fake_session.execute_calls[1][0].split()).lower()

    assert "status` = 'failed'" in run_sql
    assert "status` = 'running'" in run_sql
    assert "index_status` = 'indexing'" in document_sql
    assert "newer_document_run`.`rag_index_run_idx` is null" in document_sql
