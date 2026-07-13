from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])

SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get(
    "/live",
    response_model=HealthResponse,
    summary="RAG 서비스 생존 상태 확인",
)
async def check_liveness(settings: SettingsDependency) -> HealthResponse:
    """RAG 서버 프로세스가 정상적으로 실행 중인지 확인한다."""

    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )
