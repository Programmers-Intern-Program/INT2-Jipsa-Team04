import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from jipsa_rag.api.v1.router import router as api_v1_router
from jipsa_rag.core.config import get_settings
from jipsa_rag.core.exception_handlers import register_exception_handlers
from jipsa_rag.core.logging import configure_logging
from jipsa_rag.core.middleware import RequestLoggingMiddleware
from jipsa_rag.infrastructure.database.session import (
    check_database_connection,
    close_database,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """애플리케이션 전역 자원의 시작 및 종료 생명주기를 관리한다."""

    settings = get_settings()

    logger.info(
        "Application startup initiated.",
        extra={
            "event": "application_startup_initiated",
            "database_check_on_startup": settings.database_check_on_startup,
        },
    )

    # 해당 환경에서 시작 시 DB 검사가 활성화된 경우에만 연결을 확인한다.
    #
    # 기본값은 False이므로 로컬 개발 및 테스트 과정에서
    # 외부 DB가 준비되지 않았다는 이유만으로 서버가 시작되지 않는 상황을 방지한다.
    if settings.database_check_on_startup:
        await check_database_connection()

    logger.info(
        "Application startup completed.",
        extra={
            "event": "application_startup_completed",
        },
    )

    try:
        yield
    finally:
        logger.info(
            "Application shutdown initiated.",
            extra={
                "event": "application_shutdown_initiated",
            },
        )

        # SQLAlchemy AsyncEngine이 관리하는 연결 풀을 종료한다.
        #
        # 실제 DB 연결이 생성되지 않은 경우에도 close_database()가
        # 안전하게 실행될 수 있도록 database.session 계층에서 책임진다.
        await close_database()

        logger.info(
            "Application shutdown completed.",
            extra={
                "event": "application_shutdown_completed",
            },
        )


def create_app() -> FastAPI:
    """환경 설정과 공통 인프라를 적용하여 FastAPI 애플리케이션을 생성한다."""

    settings = get_settings()

    # FastAPI 애플리케이션과 lifespan 로그가 생성되기 전에
    # 루트 로거와 JSON Formatter를 먼저 구성한다.
    #
    # 현재 configure_logging()의 기본 로그 레벨은 INFO이다.
    # 환경별 로그 레벨 설정은 최종 정리 단계에서 Settings와 연결한다.
    configure_logging(
        service_name=settings.app_name,
        environment=settings.app_env,
    )

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # 요청 추적 미들웨어는 모든 HTTP 요청에 request_id를 부여하고,
    # 요청 시작·완료·실패 로그와 X-Request-ID 응답 헤더를 생성한다.
    application.add_middleware(RequestLoggingMiddleware)

    # 애플리케이션 정의 예외, 요청 검증 오류, HTTP 오류,
    # 처리되지 않은 일반 예외를 공통 오류 응답으로 변환한다.
    register_exception_handlers(application)

    # 모든 v1 API에 공통 API prefix를 적용한다.
    application.include_router(
        api_v1_router,
        prefix=settings.api_v1_prefix,
    )

    return application


# Uvicorn이 `jipsa_rag.main:app` 경로로 실행할 FastAPI 애플리케이션 객체이다.
app = create_app()
