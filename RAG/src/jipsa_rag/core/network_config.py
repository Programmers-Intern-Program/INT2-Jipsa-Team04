"""RAG 외부 노출 주소와 네트워크 진단 설정을 관리한다."""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import SplitResult, urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jipsa_rag.core.config import resolve_env_file, resolve_environment

# RAG 서버를 외부 애플리케이션 서버에 노출할 때 사용할 수 있는
# 터널 또는 중계 방식을 제한된 문자열로 관리한다.
#
# none:
# - DDNS와 공유기 포트 포워딩만 사용하는 경우
# - 터널 없이 직접 Reverse Proxy를 사용하는 경우
#
# custom:
# - SSH Tunnel, 자체 Reverse Proxy 또는 팀에서 직접 관리하는
#   터널 구현을 사용하는 경우
TunnelProvider = Literal[
    "none",
    "cloudflare",
    "ngrok",
    "tailscale",
    "custom",
]


def _parse_url(
    value: str,
    *,
    setting_name: str,
) -> SplitResult:
    """네트워크 진단 URL의 공통 구성 요소를 검증한다."""

    parsed = urlsplit(value)

    if parsed.hostname is None or not parsed.netloc:
        raise ValueError(f"{setting_name}에는 호스트가 필요합니다.")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{setting_name}에 인증 정보를 포함할 수 없습니다.")

    try:
        parsed_port = parsed.port
    except ValueError as error:
        raise ValueError(f"{setting_name}의 포트가 올바르지 않습니다.") from error

    if parsed_port is not None and not 1 <= parsed_port <= 65535:
        raise ValueError(f"{setting_name} 포트는 1부터 65535 사이여야 합니다.")

    return parsed


class NetworkDiagnosticsSettings(BaseSettings):
    """RAG 외부 주소와 네트워크 진단에 필요한 설정."""

    # 애플리케이션 서버가 RAG 서버를 호출할 때 사용하는 외부 기본 주소다.
    #
    # 예:
    # - http://INT2-jipsa.iptime.org:9802
    # - https://rag.example.com
    #
    # 현재 개발 구성은 DDNS와 공유기 포트 포워딩을 사용하므로
    # HTTP 주소도 허용한다.
    #
    # HTTP에서는 X-Internal-Token이 전송 구간에서 암호화되지 않으므로
    # 운영 또는 장기 개발 환경에서는 HTTPS 전환이 필요하다.
    external_base_url: str | None = None

    # RAG 서버 외부 노출에 사용하는 터널 제공자다.
    #
    # 이 값은 실행 중인 프로세스나 URL 형태를 추측하여 자동 판별하지 않는다.
    # 운영자가 실제 네트워크 구성과 동일한 값을 환경 변수에 명시해야 한다.
    tunnel_provider: TunnelProvider = "none"

    # 현재 RAG 프로세스의 outbound 공인 IP를 확인할 외부 서비스 URL이다.
    #
    # 응답은 다음 JSON 구조를 반환해야 한다.
    #
    # {
    #   "ip": "203.0.113.10"
    # }
    outbound_ip_lookup_url: str = "https://api.ipify.org?format=json"

    # 공인 IP 확인 요청의 연결, 읽기, 쓰기 및 연결 풀 제한 시간이다.
    #
    # 네트워크 진단은 핵심 파일 처리 경로가 아니므로 장시간 대기하지 않도록
    # 최대 30초로 제한한다.
    network_diagnostics_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=30,
    )

    model_config = SettingsConfigDict(
        # external_base_url
        # -> JIPSA_RAG_EXTERNAL_BASE_URL
        #
        # tunnel_provider
        # -> JIPSA_RAG_TUNNEL_PROVIDER
        #
        # outbound_ip_lookup_url
        # -> JIPSA_RAG_OUTBOUND_IP_LOOKUP_URL
        #
        # network_diagnostics_timeout_seconds
        # -> JIPSA_RAG_NETWORK_DIAGNOSTICS_TIMEOUT_SECONDS
        env_prefix="JIPSA_RAG_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    @field_validator(
        "external_base_url",
        mode="before",
    )
    @classmethod
    def normalize_optional_external_base_url(
        cls,
        value: object,
    ) -> object:
        """공백 외부 주소를 미설정 상태로 변환한다."""

        if isinstance(value, str):
            normalized_value = value.strip()

            if not normalized_value:
                return None

            return normalized_value

        return value

    @field_validator(
        "tunnel_provider",
        mode="before",
    )
    @classmethod
    def normalize_tunnel_provider(
        cls,
        value: object,
    ) -> object:
        """터널 제공자 문자열의 공백과 대소문자를 정규화한다."""

        if isinstance(value, str):
            return value.strip().lower()

        return value

    @field_validator(
        "outbound_ip_lookup_url",
        mode="before",
    )
    @classmethod
    def normalize_outbound_ip_lookup_url(
        cls,
        value: object,
    ) -> object:
        """공인 IP 조회 URL의 앞뒤 공백을 제거한다."""

        if isinstance(value, str):
            return value.strip()

        return value

    @field_validator("external_base_url")
    @classmethod
    def validate_external_base_url(
        cls,
        value: str | None,
    ) -> str | None:
        """RAG 외부 기본 주소가 HTTP 또는 HTTPS 기본 URL인지 검증한다."""

        if value is None:
            return None

        if value.endswith("/"):
            raise ValueError("RAG 외부 기본 주소는 '/'로 끝날 수 없습니다.")

        parsed = _parse_url(
            value,
            setting_name="RAG 외부 기본 주소",
        )

        # 현재 개발 구성은 DDNS와 공유기 포트 포워딩을 사용하므로
        # HTTP와 HTTPS를 모두 허용한다.
        #
        # FTP, WebSocket 등 애플리케이션 서버가 POST /ingest를
        # 호출할 수 없는 스킴은 설정 단계에서 거부한다.
        if parsed.scheme not in {
            "http",
            "https",
        }:
            raise ValueError("RAG 외부 기본 주소는 http 또는 https 스킴을 사용해야 합니다.")

        if parsed.path:
            raise ValueError("RAG 외부 기본 주소에 경로를 포함할 수 없습니다.")

        if parsed.query or parsed.fragment:
            raise ValueError("RAG 외부 기본 주소에 query 또는 fragment를 포함할 수 없습니다.")

        return value

    @field_validator("outbound_ip_lookup_url")
    @classmethod
    def validate_outbound_ip_lookup_url(
        cls,
        value: str,
    ) -> str:
        """공인 IP 조회 URL이 HTTPS를 사용하는지 검증한다."""

        parsed = _parse_url(
            value,
            setting_name="outbound 공인 IP 조회 URL",
        )

        # 공인 IP 조회 결과가 네트워크 중간자에 의해 변조되지 않도록
        # 조회 서비스에는 HTTPS만 허용한다.
        if parsed.scheme != "https":
            raise ValueError("outbound 공인 IP 조회 URL은 https 스킴을 사용해야 합니다.")

        if parsed.fragment:
            raise ValueError("outbound 공인 IP 조회 URL에 fragment를 포함할 수 없습니다.")

        return value

    @model_validator(mode="after")
    def validate_tunnel_configuration(self) -> Self:
        """터널 사용 시 외부 기본 주소가 함께 설정되었는지 검증한다."""

        if self.tunnel_provider != "none" and self.external_base_url is None:
            raise ValueError("터널을 사용하는 경우 RAG 외부 기본 주소를 함께 설정해야 합니다.")

        return self

    @property
    def tunnel_enabled(self) -> bool:
        """현재 설정에서 터널을 사용하도록 지정했는지 반환한다."""

        return self.tunnel_provider != "none"


@lru_cache(maxsize=1)
def get_network_diagnostics_settings() -> NetworkDiagnosticsSettings:
    """현재 실행 환경의 네트워크 진단 설정을 생성하고 재사용한다."""

    environment = resolve_environment()
    env_file = resolve_env_file(environment)

    return NetworkDiagnosticsSettings(
        # 기존 Settings와 동일한 .env.local, .env.development,
        # .env.test 파일에서 네트워크 진단 설정을 읽는다.
        _env_file=env_file,
    )
