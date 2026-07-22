"""File_IDX별 MySQL advisory lock 동작을 테스트한다."""

from collections.abc import Sequence
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from jipsa_rag.infrastructure.indexing.exceptions import LocalRagStorageError
from jipsa_rag.infrastructure.indexing.file_lock import (
    MySqlAdvisoryFileIndexLock,
)


class FakeScalarResult:
    """GET_LOCK 및 RELEASE_LOCK의 단일 스칼라 결과 대역."""

    def __init__(self, value: object) -> None:
        """반환할 값을 저장한다."""

        self._value = value

    def scalar_one(self) -> object:
        """설정된 값을 반환한다."""

        return self._value


class FakeAsyncConnection:
    """named lock SQL 호출과 연결 정리 여부를 기록한다."""

    def __init__(self, results: Sequence[object]) -> None:
        """연결에서 순서대로 반환할 SQL 결과를 저장한다."""

        self._results = list(results)
        self.execute_calls: list[tuple[str, object | None]] = []
        self.invalidated = False
        self.closed = False

    async def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> FakeScalarResult:
        """SQL 호출을 기록하고 다음 스칼라 결과를 반환한다."""

        if not self._results:
            raise AssertionError("No fake lock result remains.")

        self.execute_calls.append((str(statement), parameters))
        return FakeScalarResult(self._results.pop(0))

    async def invalidate(self) -> None:
        """연결 폐기 호출을 기록한다."""

        self.invalidated = True

    async def close(self) -> None:
        """연결 종료 호출을 기록한다."""

        self.closed = True


class FakeAsyncEngine:
    """대기 재시도마다 미리 준비된 연결을 반환한다."""

    def __init__(self, connections: Sequence[FakeAsyncConnection]) -> None:
        """반환할 연결 목록을 저장한다."""

        self._connections = list(connections)
        self.connect_count = 0

    async def connect(self) -> AsyncConnection:
        """다음 연결을 반환한다."""

        if not self._connections:
            raise AssertionError("No fake connection remains.")

        self.connect_count += 1
        return cast(AsyncConnection, self._connections.pop(0))


@pytest.mark.asyncio
async def test_file_lock_retries_then_releases_owned_lock() -> None:
    """첫 대기 실패 후 재시도하여 lock을 얻고 같은 연결에서 해제한다."""

    timed_out_connection = FakeAsyncConnection([0])
    owner_connection = FakeAsyncConnection([1, 1])
    fake_engine = FakeAsyncEngine(
        [
            timed_out_connection,
            owner_connection,
        ]
    )
    lock = MySqlAdvisoryFileIndexLock(
        cast(AsyncEngine, fake_engine),
        wait_slice_seconds=1,
    )

    async with lock.hold(file_idx=123):
        assert owner_connection.closed is False

    assert fake_engine.connect_count == 2
    assert timed_out_connection.closed is True
    assert timed_out_connection.invalidated is False
    assert owner_connection.closed is True
    assert owner_connection.invalidated is False

    acquire_sql, acquire_parameters = owner_connection.execute_calls[0]
    release_sql, release_parameters = owner_connection.execute_calls[1]

    assert "GET_LOCK" in acquire_sql
    assert "RELEASE_LOCK" in release_sql
    assert acquire_parameters == {
        "lock_name": "jipsa:rag:file:123",
        "timeout_seconds": 1,
    }
    assert release_parameters == {
        "lock_name": "jipsa:rag:file:123",
    }


@pytest.mark.asyncio
async def test_file_lock_releases_when_protected_body_fails() -> None:
    """색인 본문 예외가 발생해도 보유한 named lock을 해제한다."""

    owner_connection = FakeAsyncConnection([1, 1])
    fake_engine = FakeAsyncEngine([owner_connection])
    lock = MySqlAdvisoryFileIndexLock(
        cast(AsyncEngine, fake_engine),
        wait_slice_seconds=1,
    )

    with pytest.raises(RuntimeError, match="index failed"):
        async with lock.hold(file_idx=10):
            raise RuntimeError("index failed")

    assert owner_connection.closed is True
    assert owner_connection.invalidated is False
    assert len(owner_connection.execute_calls) == 2


@pytest.mark.asyncio
async def test_file_lock_invalid_release_result_discards_connection() -> None:
    """현재 연결이 lock 소유자가 아니면 연결을 풀에서 폐기한다."""

    owner_connection = FakeAsyncConnection([1, 0])
    fake_engine = FakeAsyncEngine([owner_connection])
    lock = MySqlAdvisoryFileIndexLock(
        cast(AsyncEngine, fake_engine),
        wait_slice_seconds=1,
    )

    with pytest.raises(LocalRagStorageError) as exception_info:
        async with lock.hold(file_idx=10):
            pass

    assert exception_info.value.operation == "file_index_lock_release_result"
    assert owner_connection.invalidated is True
    assert owner_connection.closed is True
