"""현재 RAG 프로세스의 outbound 공인 IP를 확인한다."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Final, Literal

import httpx2

from jipsa_rag.core.network_config import NetworkDiagnosticsSettings

logger = logging.getLogger(__name__)

# 공인 IP 조회 서비스가 반환해야 하는 JSON 필드 이름이다.
_PUBLIC_IP_FIELD: Final[str] = "ip"

OutboundIpLookupStatus = Literal[
    "AVAILABLE",
    "UNAVAILABLE",
]


@dataclass(frozen=True, slots=True)
class OutboundIpLookupResult:
    """outbound 공인 IP 조회 결과를 표현한다."""

    public_ip: str | None
    lookup_status: OutboundIpLookupStatus

    def __post_init__(self) -> None:
        """조회 상태와 공인 IP 존재 여부의 일관성을 검증한다."""

        if self.lookup_status == "AVAILABLE" and self.public_ip is None:
            raise ValueError("AVAILABLE lookup result must contain public_ip.")

        if self.lookup_status == "UNAVAILABLE" and self.public_ip is not None:
            raise ValueError("UNAVAILABLE lookup result must not contain public_ip.")


class OutboundPublicIpResolver:
    """외부 HTTPS 서비스를 통해 현재 프로세스의 outbound 공인 IP를 조회한다."""

    def __init__(
        self,
        settings: NetworkDiagnosticsSettings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """네트워크 진단 설정과 테스트용 HTTP transport를 저장한다."""

        self._settings = settings
        self._transport = transport

    async def resolve(self) -> OutboundIpLookupResult:
        """현재 RAG 프로세스가 외부 요청에 사용하는 공인 IP를 반환한다."""

        timeout_seconds = self._settings.network_diagnostics_timeout_seconds

        timeout = httpx2.Timeout(
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )

        try:
            async with httpx2.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                # RAG의 다른 내부 HTTP 클라이언트와 동일하게 시스템 Proxy
                # 환경 변수가 의도하지 않게 egress 경로를 변경하지 않도록 한다.
                trust_env=False,
                transport=self._transport,
                headers={
                    "Accept": "application/json",
                },
            ) as client:
                response = await client.get(
                    self._settings.outbound_ip_lookup_url,
                )

        except httpx2.RequestError as error:
            return _create_unavailable_result(
                reason=type(error).__name__,
            )

        if response.status_code != 200:
            return _create_unavailable_result(
                reason="unexpected_status",
                upstream_status_code=response.status_code,
            )

        try:
            payload: object = response.json()
        except ValueError:
            return _create_unavailable_result(
                reason="invalid_json",
            )

        if not isinstance(payload, Mapping):
            return _create_unavailable_result(
                reason="invalid_payload_type",
            )

        raw_public_ip = payload.get(_PUBLIC_IP_FIELD)

        if not isinstance(raw_public_ip, str):
            return _create_unavailable_result(
                reason="missing_ip_field",
            )

        normalized_public_ip = raw_public_ip.strip()

        try:
            parsed_public_ip = ip_address(normalized_public_ip)
        except ValueError:
            return _create_unavailable_result(
                reason="invalid_ip_address",
            )

        # 사설 IP, Loopback, Link-local, 문서 예시 대역 등은 실제 인터넷
        # outbound 공인 IP로 사용할 수 없으므로 성공 결과로 반환하지 않는다.
        if not parsed_public_ip.is_global:
            return _create_unavailable_result(
                reason="non_global_ip_address",
            )

        return OutboundIpLookupResult(
            public_ip=parsed_public_ip.compressed,
            lookup_status="AVAILABLE",
        )


def _create_unavailable_result(
    *,
    reason: str,
    upstream_status_code: int | None = None,
) -> OutboundIpLookupResult:
    """공인 IP 조회 실패를 민감정보 없이 기록하고 실패 결과를 반환한다."""

    log_context: dict[str, object] = {
        "event": "outbound_public_ip_lookup_unavailable",
        "reason": reason,
    }

    if upstream_status_code is not None:
        log_context["upstream_status_code"] = upstream_status_code

    # 응답 본문과 조회 URL은 외부 설정값 또는 예상하지 못한 데이터를
    # 포함할 수 있으므로 로그 컨텍스트에 기록하지 않는다.
    logger.warning(
        "Outbound public IP lookup is unavailable.",
        extra=log_context,
    )

    return OutboundIpLookupResult(
        public_ip=None,
        lookup_status="UNAVAILABLE",
    )
