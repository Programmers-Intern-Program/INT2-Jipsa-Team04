"""비동기 데이터베이스 엔진과 세션 관리 기능을 단위 테스트한다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import jipsa_rag.infrastructure.database.session as database_session


class _FakeAsyncContextManager:
    """비동기 context manager 동작을 대체하는 테스트 객체."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> object:
        self.entered = True
        return self.value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool:
        self.exited = True

        # 예외를 억제하지 않고 호출한 코드로 다시 전달한다.
        return False


def test_check_database_connection_executes_select_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 연결 검사가 SELECT 1 쿼리를 실행해야 한다."""

    execute = AsyncMock()
    connection = SimpleNamespace(execute=execute)
    connection_context = _FakeAsyncContextManager(connection)

    fake_engine = SimpleNamespace(
        connect=lambda: connection_context,
    )

    monkeypatch.setattr(
        database_session,
        "engine",
        fake_engine,
    )

    asyncio.run(database_session.check_database_connection())

    execute.assert_awaited_once()

    executed_statement = execute.await_args.args[0]

    assert str(executed_statement) == "SELECT 1"
    assert connection_context.entered is True
    assert connection_context.exited is True


def test_get_db_session_yields_session_without_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 요청에서는 세션을 제공하고 rollback하지 않아야 한다."""

    rollback = AsyncMock()
    fake_session = SimpleNamespace(rollback=rollback)
    session_context = _FakeAsyncContextManager(fake_session)

    monkeypatch.setattr(
        database_session,
        "async_session_factory",
        lambda: session_context,
    )

    async def run_test() -> None:
        session_generator = database_session.get_db_session()

        yielded_session = await anext(session_generator)

        assert yielded_session is fake_session

        # 의존성 사용이 정상 종료된 상황을 재현한다.
        await session_generator.aclose()

    asyncio.run(run_test())

    rollback.assert_not_awaited()
    assert session_context.entered is True
    assert session_context.exited is True


def test_get_db_session_rolls_back_when_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """요청 처리 중 예외가 발생하면 트랜잭션을 rollback해야 한다."""

    rollback = AsyncMock()
    fake_session = SimpleNamespace(rollback=rollback)
    session_context = _FakeAsyncContextManager(fake_session)

    monkeypatch.setattr(
        database_session,
        "async_session_factory",
        lambda: session_context,
    )

    async def run_test() -> None:
        session_generator = database_session.get_db_session()

        yielded_session = await anext(session_generator)

        assert yielded_session is fake_session

        # FastAPI 의존성에서 yield 이후 요청 처리 예외가 발생한
        # 상황을 async generator의 athrow()로 재현한다.
        with pytest.raises(
            RuntimeError,
            match="request failed",
        ):
            await session_generator.athrow(RuntimeError("request failed"))

    asyncio.run(run_test())

    rollback.assert_awaited_once_with()
    assert session_context.entered is True
    assert session_context.exited is True


def test_close_database_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 종료 함수가 SQLAlchemy 엔진의 연결 풀을 정리해야 한다."""

    dispose = AsyncMock()
    fake_engine = SimpleNamespace(dispose=dispose)

    monkeypatch.setattr(
        database_session,
        "engine",
        fake_engine,
    )

    asyncio.run(database_session.close_database())

    dispose.assert_awaited_once_with()
