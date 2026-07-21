"""RAG 외부 주소와 네트워크 진단 설정을 테스트한다."""

from typing import Any

import pytest
from pydantic import ValidationError

from jipsa_rag.core.network_config import (
    NetworkDiagnosticsSettings,
)


def _create_settings(
    **overrides: Any,
) -> NetworkDiagnosticsSettings:
    """OS 환경 변수에 의존하지 않는 네트워크 진단 설정을 생성한다."""

    values: dict[str, Any] = {
        "external_base_url": None,
        "tunnel_provider": "none",
        "outbound_ip_lookup_url": ("https://api.ipify.org?format=json"),
        "network_diagnostics_timeout_seconds": 5.0,
        "_env_file": None,
    }

    values.update(overrides)

    return NetworkDiagnosticsSettings(**values)


def test_network_settings_allow_local_only_configuration() -> None:
    """외부 주소와 터널이 없는 로컬 구성을 허용한다."""

    settings = _create_settings()

    assert settings.external_base_url is None
    assert settings.tunnel_provider == "none"
    assert settings.tunnel_enabled is False


def test_network_settings_normalize_external_url_and_tunnel_provider() -> None:
    """외부 주소와 터널 제공자 문자열의 공백과 대소문자를 정규화한다."""

    settings = _create_settings(
        external_base_url="  https://rag.example.com  ",
        tunnel_provider="  CLOUDFLARE  ",
    )

    assert settings.external_base_url == "https://rag.example.com"
    assert settings.tunnel_provider == "cloudflare"
    assert settings.tunnel_enabled is True


def test_network_settings_require_external_url_when_tunnel_is_enabled() -> None:
    """터널을 사용하는 경우 외부 기본 주소를 필수로 요구한다."""

    with pytest.raises(
        ValidationError,
        match="외부 기본 주소",
    ):
        _create_settings(
            tunnel_provider="ngrok",
            external_base_url=None,
        )


def test_network_settings_allow_http_external_url_for_port_forwarding() -> None:
    """DDNS와 공유기 포트 포워딩에 사용하는 HTTP 주소를 허용한다."""

    settings = _create_settings(
        external_base_url="http://INT2-jipsa.iptime.org:9802",
        tunnel_provider="none",
    )

    assert settings.external_base_url == "http://INT2-jipsa.iptime.org:9802"
    assert settings.tunnel_enabled is False


def test_network_settings_allow_https_external_url() -> None:
    """TLS가 적용된 외부 RAG 기본 주소를 허용한다."""

    settings = _create_settings(
        external_base_url="https://rag.example.com",
    )

    assert settings.external_base_url == "https://rag.example.com"


def test_network_settings_reject_unsupported_external_url_scheme() -> None:
    """HTTP와 HTTPS 이외의 외부 주소 스킴을 거부한다."""

    with pytest.raises(
        ValidationError,
        match="http 또는 https",
    ):
        _create_settings(
            external_base_url="ftp://rag.example.com",
        )


def test_network_settings_reject_external_url_with_path() -> None:
    """외부 기본 주소에 API 경로가 포함되는 것을 거부한다."""

    with pytest.raises(
        ValidationError,
        match="경로",
    ):
        _create_settings(
            external_base_url="https://rag.example.com/api/v1",
        )


def test_network_settings_reject_insecure_outbound_lookup_url() -> None:
    """공인 IP 조회 URL에 HTTP 사용을 허용하지 않는다."""

    with pytest.raises(
        ValidationError,
        match="https",
    ):
        _create_settings(
            outbound_ip_lookup_url=("http://api.ipify.org?format=json"),
        )
