"""Jipsa RAG FastAPI 애플리케이션의 진입점을 정의한다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from jipsa_rag.api.v1.router import router as api_v1_router
from jipsa_rag.core.config import get_settings
from jipsa_rag.infrastructure.database.session import (
    check_database_connection,
    close_database,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """애플리케이션 전역 자원의 시작 및 종료 생명주기를 관리한다."""

    settings = get_settings()

    try:
        # 현재 환경에서 시작 시 DB 연결 검사가 활성화된 경우에만
        # 애플리케이션이 사용하는 데이터베이스의 연결 상태를 확인한다.
        if settings.database_check_on_startup:
            await check_database_connection()

        yield
    finally:
        # 정상 종료 또는 시작 단계의 예외 발생 시
        # SQLAlchemy 엔진이 관리하는 연결 풀을 정리한다.
        await close_database()


def create_app() -> FastAPI:
    """현재 환경 설정을 적용하여 FastAPI 애플리케이션을 생성한다."""

    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # v1에 포함된 모든 API 라우터에 공통 API prefix를 적용한다.
    application.include_router(
        api_v1_router,
        prefix=settings.api_v1_prefix,
    )

    return application


# Uvicorn이 `jipsa_rag.main:app` 경로를 통해 불러올
# FastAPI 애플리케이션 객체를 생성한다.
app = create_app()
