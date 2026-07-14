from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.schemas.common import ApiResponse
from jipsa_rag.schemas.health import HealthResponse

router = APIRouter(
    prefix="/health",
    tags=["Health"],
)

SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


@router.get(
    "/live",
    response_model=ApiResponse[HealthResponse],
    summary="RAG 서비스 생존 상태 확인",
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
