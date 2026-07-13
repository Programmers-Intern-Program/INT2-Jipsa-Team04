from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from jipsa_rag.api.v1.router import router as api_v1_router
from jipsa_rag.core.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """애플리케이션 시작 및 종료 시 자원을 관리한다."""

    # 후속 단계에서 DB Engine, S3 Client 등의 초기화 지점으로 사용한다.
    yield
    # 후속 단계에서 연결 및 자원 정리 지점으로 사용한다.


def create_app() -> FastAPI:
    """설정에 따라 FastAPI 애플리케이션을 생성한다."""

    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    application.include_router(
        api_v1_router,
        prefix=settings.api_v1_prefix,
    )

    return application


app = create_app()
