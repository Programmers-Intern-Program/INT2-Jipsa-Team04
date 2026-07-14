"""RAG 서비스의 생존 상태와 요청 처리 준비 상태를 확인한다."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.database.session import check_database_connection
from jipsa_rag.schemas.common import ApiResponse
from jipsa_rag.schemas.health import (
    DependencyHealth,
    HealthResponse,
    ReadinessResponse,
)

router = APIRouter(
    prefix="/health",
    tags=["Health"],
)

# Health Check 엔드포인트에서 공통으로 사용할 Settings 의존성이다.
#
# get_settings()는 현재 실행 환경에 대응하는 설정 객체를 캐싱하여 반환하므로
# 요청마다 dotenv 파일을 반복해서 읽지 않는다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


@router.get(
    "/live",
    response_model=ApiResponse[HealthResponse],
    summary="RAG 서비스 생존 상태 확인",
    description=(
        "FastAPI 프로세스가 정상적으로 실행 중인지 확인한다. "
        "데이터베이스와 같은 외부 의존성 상태는 검사하지 않는다."
    ),
)
async def check_liveness(
    settings: SettingsDependency,
) -> ApiResponse[HealthResponse]:
    """RAG 서버 프로세스가 정상적으로 실행 중인지 확인한다."""

    health_data = HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )

    return ApiResponse[HealthResponse](
        success=True,
        code="SUCCESS",
        message="RAG service is running.",
        data=health_data,
    )


@router.get(
    "/ready",
    response_model=ApiResponse[ReadinessResponse],
    summary="RAG 서비스 요청 처리 준비 상태 확인",
    description=(
        "RAG 서버가 요청을 처리하기 위해 필요한 Local RAG 데이터베이스의 연결 상태를 확인한다."
    ),
    responses={
        503: {
            "model": ApiResponse[None],
            "description": "필수 외부 의존성을 사용할 수 없어 요청 처리 준비가 완료되지 않은 상태",
        },
    },
)
async def check_readiness(
    settings: SettingsDependency,
) -> ApiResponse[ReadinessResponse]:
    """RAG 서버와 필수 외부 의존성이 요청 처리 가능한 상태인지 확인한다."""

    try:
        # 특정 테이블의 존재 여부와 관계없이 SELECT 1을 실행하여
        # Local RAG 데이터베이스 연결 가능 여부만 확인한다.
        await check_database_connection()
    except SQLAlchemyError as error:
        # DB 주소, 계정, 비밀번호 또는 내부 예외 메시지는 외부 응답에 노출하지 않는다.
        #
        # 실제 예외는 AppException의 예외 체인과 내부 로그를 통해 추적하고,
        # 클라이언트에는 서비스 준비가 완료되지 않았다는 정보만 전달한다.
        raise AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            public_message="RAG service is not ready.",
            log_context={
                "dependency": "local_database",
                "environment": settings.app_env,
            },
        ) from error

    readiness_data = ReadinessResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        database=DependencyHealth(),
    )

    return ApiResponse[ReadinessResponse](
        success=True,
        code="SUCCESS",
        message="RAG service is ready.",
        data=readiness_data,
    )
