"""Local RAG 데이터베이스의 비동기 엔진과 세션을 관리한다."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jipsa_rag.core.config import get_settings


# 현재 애플리케이션 환경에 해당하는 데이터베이스 연결 설정을 불러온다.
_settings = get_settings()


# SQLAlchemy 비동기 엔진을 생성한다.
#
# create_async_engine() 호출 시점에는 실제 데이터베이스 연결을 생성하지 않는다.
# 세션이나 connection이 최초로 연결을 요청할 때 연결 풀을 통해 연결한다.
#
# pool_pre_ping=True:
# 연결 풀에서 가져온 기존 연결이 MySQL 서버에 의해 종료되었는지 확인한다.
# 연결을 사용할 수 없으면 해당 연결을 폐기하고 새로운 연결을 생성한다.
engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=_settings.database_echo,
    pool_pre_ping=True,
)


# FastAPI 요청마다 독립적인 AsyncSession을 생성하기 위한 세션 팩토리이다.
#
# expire_on_commit=False:
# commit 이후에도 이미 조회하거나 저장한 ORM 객체의 속성값을 유지한다.
# 비동기 환경에서 commit 이후 불필요한 속성 재조회가 발생하는 것을 방지한다.
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 요청 하나에서 사용할 독립적인 비동기 DB 세션을 제공한다."""

    async with async_session_factory() as session:
        try:
            # 이 함수는 FastAPI의 Depends()를 통해 요청 단위로 사용한다.
            #
            # commit은 이 세션을 사용하는 서비스 계층 또는 명시적인
            # 트랜잭션 경계에서 수행한다.
            yield session
        except Exception:
            # 요청 처리 중 예외가 발생하면 진행 중인 트랜잭션을 되돌린다.
            # rollback 이후 원래 예외를 다시 전달하여 상위 예외 처리기가
            # 적절한 HTTP 응답을 생성할 수 있도록 한다.
            await session.rollback()
            raise


async def check_database_connection() -> None:
    """간단한 조회 쿼리로 Local RAG DB 연결 가능 여부를 확인한다."""

    async with engine.connect() as connection:
        # 특정 테이블을 조회하지 않으므로 ORM 모델이나 테이블 생성 여부와
        # 관계없이 데이터베이스 서버 연결 상태만 확인할 수 있다.
        await connection.execute(text("SELECT 1"))


async def close_database() -> None:
    """애플리케이션 종료 시 SQLAlchemy 엔진의 연결 풀을 정리한다."""

    await engine.dispose()