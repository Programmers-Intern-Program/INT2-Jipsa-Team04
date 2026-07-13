from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """서비스 생존 상태 응답."""

    status: Literal["UP"] = "UP"
    service: str
    version: str
    environment: str
