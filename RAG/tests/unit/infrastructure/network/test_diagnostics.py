"""outbound 공인 IP 조회기를 테스트한다."""

import httpx2
import pytest

from jipsa_rag.core.network_config import (
    NetworkDiagnosticsSettings,
)
from jipsa_rag.infrastructure.network.diagnostics import (
    OutboundPublicIpResolver,
)


def _create_settings() -> NetworkDiagnosticsSettings:
    """실제 외부 네트워크에 의존하지 않는 진단 설정을 생성한다."""

    return NetworkDiagnosticsSettings(
        external_base_url="https://rag.example.com",
        tunnel_provider="cloudflare",
        outbound_ip_lookup_url=("https://public-ip.example.test?format=json"),
        network_diagnostics_timeout_seconds=1.0,
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_resolver_returns_global_outbound_public_ip() -> None:
    """조회 서비스가 반환한 전역 IP를 정상 결과로 반환한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        """공인 IP 조회 요청의 메서드와 헤더를 검증한다."""

        assert request.method == "GET"
        assert request.url.host == "public-ip.example.test"
        assert request.url.params["format"] == "json"
        assert request.headers["Accept"] == "application/json"

        return httpx2.Response(
            status_code=200,
            json={
                "ip": "8.8.8.8",
            },
        )

    resolver = OutboundPublicIpResolver(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    result = await resolver.resolve()

    assert result.lookup_status == "AVAILABLE"
    assert result.public_ip == "8.8.8.8"


@pytest.mark.asyncio
async def test_resolver_rejects_private_ip_response() -> None:
    """사설 IP를 outbound 공인 IP 성공 결과로 반환하지 않는다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        """사설 IP 응답을 반환한다."""

        del request

        return httpx2.Response(
            status_code=200,
            json={
                "ip": "192.168.0.10",
            },
        )

    resolver = OutboundPublicIpResolver(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    result = await resolver.resolve()

    assert result.lookup_status == "UNAVAILABLE"
    assert result.public_ip is None


@pytest.mark.asyncio
async def test_resolver_handles_invalid_json_response() -> None:
    """공인 IP 조회 서비스의 잘못된 JSON 응답을 안전하게 처리한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        """JSON이 아닌 응답을 반환한다."""

        del request

        return httpx2.Response(
            status_code=200,
            content=b"not-json",
            headers={
                "Content-Type": "text/plain",
            },
        )

    resolver = OutboundPublicIpResolver(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    result = await resolver.resolve()

    assert result.lookup_status == "UNAVAILABLE"
    assert result.public_ip is None


@pytest.mark.asyncio
async def test_resolver_handles_upstream_server_error() -> None:
    """공인 IP 조회 서비스의 서버 오류를 실패 결과로 변환한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        """일시적인 upstream 오류를 반환한다."""

        del request

        return httpx2.Response(
            status_code=503,
        )

    resolver = OutboundPublicIpResolver(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    result = await resolver.resolve()

    assert result.lookup_status == "UNAVAILABLE"
    assert result.public_ip is None
