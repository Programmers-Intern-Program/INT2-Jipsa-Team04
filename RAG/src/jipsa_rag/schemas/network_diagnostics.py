"""RAG 외부 주소와 네트워크 진단 API 응답 스키마를 정의한다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jipsa_rag.core.network_config import TunnelProvider


class TunnelDiagnostics(BaseModel):
    """운영자가 설정한 RAG 터널 사용 정보를 표현한다."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        description="설정 기준 터널 사용 여부",
        examples=[True],
    )

    provider: TunnelProvider = Field(
        description="설정된 터널 제공자",
        examples=["cloudflare"],
    )


class NetworkDiagnosticsResponse(BaseModel):
    """RAG 프로세스의 네트워크 노출 및 egress 정보를 표현한다."""

    model_config = ConfigDict(extra="forbid")

    checked_at: datetime = Field(
        description="네트워크 진단을 수행한 UTC 시각",
    )

    bind_host: str = Field(
        min_length=1,
        description="Uvicorn이 실제로 바인딩하는 호스트",
        examples=["127.0.0.1"],
    )

    bind_port: int = Field(
        ge=1,
        le=65535,
        description="Uvicorn이 실제로 바인딩하는 포트",
        examples=[8000],
    )

    external_base_url: str | None = Field(
        default=None,
        description=("애플리케이션 서버가 RAG를 호출할 때 사용하도록 설정한 외부 기본 주소"),
        examples=["https://rag.example.com"],
    )

    external_address_configured: bool = Field(
        description="RAG 외부 기본 주소 설정 여부",
        examples=[True],
    )

    outbound_public_ip: str | None = Field(
        default=None,
        description="현재 RAG 프로세스의 외부 요청에 관측된 공인 IP",
        examples=["8.8.8.8"],
    )

    outbound_ip_lookup_status: Literal[
        "AVAILABLE",
        "UNAVAILABLE",
    ] = Field(
        description="outbound 공인 IP 조회 성공 여부",
        examples=["AVAILABLE"],
    )

    tunnel: TunnelDiagnostics = Field(
        description="환경 설정을 기준으로 한 터널 사용 정보",
    )
