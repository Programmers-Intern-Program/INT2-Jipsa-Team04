"""인증된 내부 호출자에게 RAG 네트워크 진단 정보를 제공한다."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.network_config import (
    NetworkDiagnosticsSettings,
    get_network_diagnostics_settings,
)
from jipsa_rag.infrastructure.network.diagnostics import (
    OutboundPublicIpResolver,
)
from jipsa_rag.schemas.common import ApiResponse
from jipsa_rag.schemas.network_diagnostics import (
    NetworkDiagnosticsResponse,
    TunnelDiagnostics,
)

router = APIRouter(
    prefix="/diagnostics",
    tags=["Diagnostics"],
)

# 실제 Uvicorn bind host와 port를 응답에 포함하기 위한
# 애플리케이션 공통 Settings 의존성이다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]

# RAG 외부 주소, 터널 제공자 및 공인 IP 조회 설정을 제공한다.
NetworkDiagnosticsSettingsDependency = Annotated[
    NetworkDiagnosticsSettings,
    Depends(get_network_diagnostics_settings),
]


def get_outbound_public_ip_resolver(
    network_settings: NetworkDiagnosticsSettingsDependency,
) -> OutboundPublicIpResolver:
    """현재 네트워크 진단 설정이 적용된 공인 IP 조회기를 생성한다."""

    return OutboundPublicIpResolver(network_settings)


# 테스트에서는 이 의존성을 Stub으로 교체하여 실제 외부 공인 IP 조회
# 서비스에 요청하지 않고 API 응답 구조를 검증한다.
OutboundPublicIpResolverDependency = Annotated[
    OutboundPublicIpResolver,
    Depends(get_outbound_public_ip_resolver),
]


@router.get(
    "/network",
    response_model=ApiResponse[NetworkDiagnosticsResponse],
    summary="RAG 외부 주소와 outbound 네트워크 상태 확인",
    description=(
        "RAG 서버의 bind 주소, 설정된 외부 주소, outbound 공인 IP 및 "
        "설정 기준 터널 사용 여부를 확인한다. "
        "외부 주소가 인터넷에서 실제 접근 가능한지는 호출 위치와 "
        "방화벽 정책에 따라 달라지므로 이 API에서 자동 보장하지 않는다."
    ),
)
async def check_network_diagnostics(
    settings: SettingsDependency,
    network_settings: NetworkDiagnosticsSettingsDependency,
    outbound_ip_resolver: OutboundPublicIpResolverDependency,
) -> ApiResponse[NetworkDiagnosticsResponse]:
    """현재 RAG 프로세스의 네트워크 노출 및 egress 정보를 반환한다."""

    outbound_ip_result = await outbound_ip_resolver.resolve()

    diagnostics_data = NetworkDiagnosticsResponse(
        checked_at=datetime.now(UTC),
        bind_host=settings.host,
        bind_port=settings.port,
        external_base_url=network_settings.external_base_url,
        external_address_configured=(network_settings.external_base_url is not None),
        outbound_public_ip=outbound_ip_result.public_ip,
        outbound_ip_lookup_status=outbound_ip_result.lookup_status,
        tunnel=TunnelDiagnostics(
            enabled=network_settings.tunnel_enabled,
            provider=network_settings.tunnel_provider,
        ),
    )

    return ApiResponse[NetworkDiagnosticsResponse](
        success=True,
        code="SUCCESS",
        message="RAG network diagnostics completed.",
        data=diagnostics_data,
    )
