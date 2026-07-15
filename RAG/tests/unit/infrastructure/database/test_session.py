"""Local RAG 데이터베이스 세션 관리 기능의 단위 테스트를 정의한다."""

from types import SimpleNamespace, TracebackType
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from jipsa_rag.infrastructure.database import session as database_session


class FakeAsyncContextManager[ContextValueT]:
    """비동기 with 문에서 사용할 최소 비동기 컨텍스트 매니저 대역이다.

    SQLAlchemy의 AsyncEngine.connect()와 async_sessionmaker()는
    실제 실행 시 비동기 컨텍스트 매니저를 반환한다.

    단위 테스트에서는 실제 MySQL 연결이나 SQLAlchemy 세션을 생성하지 않고,
    지정한 테스트 대역 객체를 async with 문에 전달하기 위해 이 클래스를 사용한다.
    """

    def __init__(self, value: ContextValueT) -> None:
        """비동기 컨텍스트 진입 시 반환할 객체를 저장한다."""

        self._value = value

    async def __aenter__(self) -> ContextValueT:
        """async with 문에 진입할 때 저장된 테스트 대역을 반환한다."""

        return self._value

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """async with 문 종료 시 발생한 예외를 억제하지 않는다."""

        # False를 반환하면 컨텍스트 내부에서 발생한 예외가
        # 호출자에게 그대로 전달된다.
        return False


@pytest.mark.asyncio
async def test_check_database_connection_executes_select_one() -> None:
    """데이터베이스 연결 검사 시 SELECT 1 쿼리를 실행하는지 검증한다."""

    # AsyncConnection.execute() 호출 여부와 전달된 SQL 문장을
    # 실제 데이터베이스 연결 없이 확인하기 위한 비동기 Mock이다.
    execute = AsyncMock()

    # check_database_connection()은 engine.connect()로 얻은 객체의
    # execute() 메서드만 사용하므로 테스트에 필요한 최소 속성만 정의한다.
    fake_connection = SimpleNamespace(
        execute=execute,
    )

    # 실제 AsyncEngine.connect()의 반환값처럼 async with 문에서
    # 사용할 수 있는 테스트용 컨텍스트 매니저를 구성한다.
    connect = Mock(
        return_value=FakeAsyncContextManager(fake_connection),
    )

    # SQLAlchemy AsyncEngine 인스턴스의 connect 메서드는 읽기 전용이므로
    # 메서드를 직접 patch하지 않고 모듈의 engine 참조 전체를 교체한다.
    #
    # cast는 런타임 객체를 변경하지 않으며 테스트 대역을 AsyncEngine으로
    # 취급한다는 정적 타입 정보만 mypy에 제공한다.
    fake_engine = cast(
        AsyncEngine,
        SimpleNamespace(
            connect=connect,
        ),
    )

    # 전역 엔진 참조를 테스트 대역으로 교체하여
    # 실제 MySQL 서버 연결이 발생하지 않도록 한다.
    with patch.object(
        database_session,
        "engine",
        fake_engine,
    ):
        await database_session.check_database_connection()

    # 연결 컨텍스트가 정확히 한 번 생성되었는지 확인한다.
    connect.assert_called_once_with()

    # 연결 검사 쿼리가 정확히 한 번 실행되었는지 확인한다.
    execute.assert_awaited_once()

    # AsyncMock.await_args의 정적 타입은 _Call | None이다.
    #
    # assert_awaited_once()가 성공했더라도 mypy는 await_args가
    # None이 아니라고 자동으로 추론하지 못하므로 명시적으로 검증한다.
    await_args = execute.await_args
    assert await_args is not None

    # execute()에 전달된 첫 번째 위치 인자가 SQLAlchemy TextClause이다.
    executed_statement = await_args.args[0]

    # 특정 테이블의 존재 여부와 무관하게 DB 연결만 검사하기 위해
    # SELECT 1 쿼리가 사용되는지 확인한다.
    assert str(executed_statement) == "SELECT 1"


@pytest.mark.asyncio
async def test_get_db_session_yields_session_without_rollback() -> None:
    """정상적인 요청에서는 세션을 반환하고 rollback하지 않는지 검증한다."""

    # get_db_session()이 반환해야 하는 AsyncSession 테스트 대역이다.
    #
    # 실제 객체는 SimpleNamespace이지만 get_db_session()의 반환 타입과
    # 테스트 대역의 정적 타입을 일치시키기 위해 AsyncSession으로 cast한다.
    #
    # cast는 런타임 객체를 변경하지 않고 mypy에 타입 정보만 제공한다.
    fake_session = cast(
        AsyncSession,
        SimpleNamespace(
            rollback=AsyncMock(),
        ),
    )

    # async_session_factory()가 실제 세션 대신 테스트 대역을 포함한
    # 비동기 컨텍스트 매니저를 반환하도록 구성한다.
    session_factory = Mock(
        return_value=FakeAsyncContextManager(fake_session),
    )

    # 실제 세션 팩토리를 테스트용 팩토리로 교체하여
    # 데이터베이스 세션이 생성되지 않도록 한다.
    with patch.object(
        database_session,
        "async_session_factory",
        session_factory,
    ):
        # get_db_session()은 AsyncGenerator이므로 anext()를 호출하여
        # yield된 세션 객체를 가져온다.
        session_generator = database_session.get_db_session()
        yielded_session = await anext(session_generator)

        # 의존성 함수가 세션 팩토리에서 생성한 동일한 객체를
        # 요청 처리 계층에 전달하는지 확인한다.
        assert yielded_session is fake_session

        # 정상적인 요청 종료 상황을 재현하기 위해
        # 비동기 제너레이터를 명시적으로 종료한다.
        await session_generator.aclose()

    # 요청당 세션 컨텍스트가 정확히 한 번 생성되었는지 확인한다.
    session_factory.assert_called_once_with()

    # 정상 처리에서는 트랜잭션을 되돌릴 이유가 없으므로
    # rollback()이 호출되지 않아야 한다.
    rollback = cast(AsyncMock, fake_session.rollback)
    rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_db_session_rolls_back_and_reraises_exception() -> None:
    """요청 처리 중 예외가 발생하면 rollback 후 예외를 다시 전달하는지 검증한다."""

    # rollback 호출 여부를 독립적으로 검증하기 위한 비동기 Mock이다.
    rollback = AsyncMock()

    # get_db_session()이 반환할 AsyncSession 테스트 대역이다.
    #
    # SimpleNamespace와 AsyncSession 사이의 타입 불일치를 방지하기 위해
    # 테스트 목적에 한해서 AsyncSession으로 명시적으로 cast한다.
    fake_session = cast(
        AsyncSession,
        SimpleNamespace(
            rollback=rollback,
        ),
    )

    # 실제 데이터베이스 세션 대신 테스트 대역을 반환하도록
    # 세션 팩토리를 구성한다.
    session_factory = Mock(
        return_value=FakeAsyncContextManager(fake_session),
    )

    # 실제 세션 팩토리를 테스트 대역으로 교체한다.
    with patch.object(
        database_session,
        "async_session_factory",
        session_factory,
    ):
        session_generator = database_session.get_db_session()
        yielded_session = await anext(session_generator)

        # 예외가 발생하기 전까지는 정상적인 세션 객체가
        # 호출자에게 전달되는지 먼저 확인한다.
        assert yielded_session is fake_session

        # FastAPI 요청 처리 계층에서 예외가 발생한 상황을 재현한다.
        #
        # athrow()는 비동기 제너레이터가 yield로 중단된 위치에
        # 지정한 예외를 전달한다.
        with pytest.raises(
            RuntimeError,
            match="요청 처리 실패",
        ):
            await session_generator.athrow(
                RuntimeError("요청 처리 실패"),
            )

    # 예외가 발생하면 진행 중인 트랜잭션이 정확히 한 번
    # rollback되어야 한다.
    rollback.assert_awaited_once_with()

    # get_db_session() 내부에서 예외를 삼키지 않고 다시 전달했는지는
    # pytest.raises 검증을 통해 확인한다.
    session_factory.assert_called_once_with()


@pytest.mark.asyncio
async def test_close_database_disposes_engine() -> None:
    """애플리케이션 종료 시 SQLAlchemy 엔진을 정리하는지 검증한다."""

    # AsyncEngine.dispose()의 비동기 호출 여부를 확인하기 위한 Mock이다.
    dispose = AsyncMock()

    # SQLAlchemy AsyncEngine 인스턴스의 dispose 메서드는 읽기 전용이므로
    # 메서드를 직접 patch하지 않고 dispose 대역을 가진 엔진 전체를 구성한다.
    #
    # 테스트 대역을 AsyncEngine으로 cast하여 close_database()에서 사용하는
    # 전역 engine 객체의 정적 타입과 일치시킨다.
    fake_engine = cast(
        AsyncEngine,
        SimpleNamespace(
            dispose=dispose,
        ),
    )

    # 모듈의 전역 엔진 참조를 테스트 대역으로 교체하여
    # 실제 SQLAlchemy 연결 풀이 종료되지 않도록 한다.
    with patch.object(
        database_session,
        "engine",
        fake_engine,
    ):
        await database_session.close_database()

    # 애플리케이션 종료 시 연결 풀이 정확히 한 번
    # 정리되는지 확인한다.
    dispose.assert_awaited_once_with()
