import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
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

    # 현재 실행 환경에서 시작 시 데이터베이스 연결 검사가
    # 활성화된 경우에만 Local RAG MySQL 연결을 확인한다.
    #
    # 기본값은 False이므로 외부 DB가 아직 준비되지 않은
    # 로컬 개발 및 단위 테스트 환경에서도 서버를 실행할 수 있다.
    #
    # 실제 연결 검사는 특정 테이블을 조회하지 않고
    # SELECT 1만 실행하므로 데이터 변경이 발생하지 않는다.
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
        # 실제 데이터베이스 연결이 한 번도 생성되지 않은 경우에도
        # close_database()가 안전하게 실행될 수 있도록
        # infrastructure.database.session 계층에서 처리한다.
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
    # 추후 환경별 로그 레벨 설정이 필요하면 Settings와 연결한다.
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

    # 요청 추적 미들웨어는 모든 HTTP 요청에 Request ID를 부여한다.
    #
    # 애플리케이션 서버가 유효한 UUID 형식의 X-Request-ID를 전달하면
    # 동일한 값을 사용하여 서버 간 로그를 연결한다.
    #
    # 헤더가 없거나 유효하지 않으면 RAG 서버가 새로운 UUID를 생성한다.
    #
    # 생성하거나 전달받은 Request ID는 다음 위치에서 동일하게 사용한다.
    # - 요청 시작 로그
    # - 요청 완료 또는 실패 로그
    # - 전역 예외 처리 로그
    # - X-Request-ID 응답 헤더
    application.add_middleware(RequestLoggingMiddleware)

    # 애플리케이션 정의 예외, 요청 검증 오류, HTTP 오류 및
    # 처리되지 않은 일반 예외를 공통 오류 응답으로 변환한다.
    #
    # 데이터베이스 비밀번호, 내부 예외 메시지 및 Stack Trace와 같은
    # 내부 정보는 외부 API 응답에 직접 노출하지 않는다.
    register_exception_handlers(application)

    # 모든 v1 API에 공통 API prefix를 적용한다.
    #
    # 기본값은 /api/v1이며 실제 값은 환경 설정에서 변경할 수 있다.
    application.include_router(
        api_v1_router,
        prefix=settings.api_v1_prefix,
    )

    return application


def main() -> None:
    """환경 설정을 적용하여 Uvicorn 기반 RAG 서버를 실행한다."""

    settings = get_settings()

    # 애플리케이션 객체 대신 import 문자열을 전달한다.
    #
    # Uvicorn의 reload 기능은 애플리케이션 객체를 직접 전달하는 방식보다
    # `module:attribute` 형식의 import 문자열을 사용하는 것이 안전하다.
    #
    # settings.debug가 True이면 소스 파일 변경을 감지하여
    # 개발 서버를 자동으로 다시 시작한다.
    uvicorn.run(
        "jipsa_rag.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        # Uvicorn이 자체 기본 로깅 설정으로 루트 로거를 덮어쓰지 않도록 한다.
        #
        # 애플리케이션 접근 로그와 예외 로그는
        # configure_logging()과 RequestLoggingMiddleware가 관리한다.
        log_config=None,
    )


# Uvicorn이 `jipsa_rag.main:app` 경로로 가져올
# FastAPI 애플리케이션 객체이다.
#
# 다음 두 실행 방식에서 모두 이 객체를 사용한다.
# - uv run jipsa-rag
# - uv run uvicorn jipsa_rag.main:app
app = create_app()
