"""RAG 네트워크 진단 API를 테스트한다."""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.network_diagnostics import (
    get_outbound_public_ip_resolver,
)
from jipsa_rag.core.config import get_settings
from jipsa_rag.core.network_config import (
    NetworkDiagnosticsSettings,
    get_network_diagnostics_settings,
)
from jipsa_rag.infrastructure.network.diagnostics import (
    OutboundIpLookupResult,
)
from jipsa_rag.main import app


class StubOutboundPublicIpResolver:
    """네트워크 진단 API 테스트용 공인 IP 조회기 대역."""

    def __init__(
        self,
        result: OutboundIpLookupResult,
    ) -> None:
        """API에 반환할 고정 조회 결과를 저장한다."""

        self._result = result
        self.call_count = 0

    async def resolve(self) -> OutboundIpLookupResult:
        """고정된 공인 IP 조회 결과를 반환한다."""

        self.call_count += 1
        return self._result


@pytest.fixture
def network_diagnostics_client(
    client: TestClient,
) -> Iterator[TestClient]:
    """외부 네트워크 요청이 제거된 네트워크 진단 클라이언트를 제공한다."""

    network_settings = NetworkDiagnosticsSettings(
        external_base_url="https://rag.example.com",
        tunnel_provider="cloudflare",
        outbound_ip_lookup_url=("https://public-ip.example.test?format=json"),
        network_diagnostics_timeout_seconds=1.0,
        _env_file=None,
    )

    resolver = StubOutboundPublicIpResolver(
        OutboundIpLookupResult(
            public_ip="8.8.8.8",
            lookup_status="AVAILABLE",
        )
    )

    app.dependency_overrides[get_network_diagnostics_settings] = lambda: network_settings
    app.dependency_overrides[get_outbound_public_ip_resolver] = lambda: resolver

    try:
        yield client
    finally:
        app.dependency_overrides.pop(
            get_network_diagnostics_settings,
            None,
        )
        app.dependency_overrides.pop(
            get_outbound_public_ip_resolver,
            None,
        )


def test_network_diagnostics_returns_external_and_outbound_information(
    network_diagnostics_client: TestClient,
) -> None:
    """외부 주소, 공인 IP 및 터널 설정을 공통 성공 응답으로 반환한다."""

    response = network_diagnostics_client.get("/api/v1/diagnostics/network")

    assert response.status_code == 200

    body = response.json()
    data = body["data"]
    application_settings = get_settings()

    assert body["success"] is True
    assert body["code"] == "SUCCESS"
    assert body["message"] == "RAG network diagnostics completed."

    assert data["bind_host"] == application_settings.host
    assert data["bind_port"] == application_settings.port

    assert data["external_base_url"] == "https://rag.example.com"
    assert data["external_address_configured"] is True

    assert data["outbound_public_ip"] == "8.8.8.8"
    assert data["outbound_ip_lookup_status"] == "AVAILABLE"

    assert data["tunnel"] == {
        "enabled": True,
        "provider": "cloudflare",
    }

    # ISO 8601 문자열을 datetime으로 변환할 수 있으면
    # 응답 시각이 올바른 형식으로 직렬화되었다는 의미다.
    checked_at = datetime.fromisoformat(data["checked_at"])
    assert checked_at.tzinfo is not None


def test_network_diagnostics_requires_internal_token(
    client: TestClient,
) -> None:
    """네트워크 구성 정보는 내부 토큰이 없는 호출자에게 노출하지 않는다."""

    response = client.get(
        "/api/v1/diagnostics/network",
        headers={
            "X-Internal-Token": "",
        },
    )

    assert response.status_code == 401

    body = response.json()

    assert body["success"] is False
    assert body["code"] == "UNAUTHORIZED"
    assert body["data"] is None
