"""RAG 서비스의 상태 확인 API 엔드포인트를 정의한다."""

from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.schemas.health import HealthResponse

# Health API에 공통으로 적용할 URL prefix와 OpenAPI 태그를 설정한다.
router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


# FastAPI 엔드포인트에서 현재 실행 환경의 Settings 객체를
# 의존성으로 주입받기 위한 타입 별칭이다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


@router.get(
    "/live",
    response_model=HealthResponse,
    summary="RAG 서비스 생존 상태 확인",
)
async def check_liveness(
    settings: SettingsDependency,
) -> HealthResponse:
    """RAG 서버 프로세스가 정상적으로 실행 중인지 확인한다."""

    # Liveness 검사는 데이터베이스나 외부 시스템에 접근하지 않는다.
    # 현재 FastAPI 프로세스가 요청을 처리할 수 있는지만 확인한다.
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )
