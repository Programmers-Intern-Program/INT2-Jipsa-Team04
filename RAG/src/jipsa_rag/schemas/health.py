"""서비스 상태 확인 API에서 사용하는 응답 스키마를 정의한다."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """서비스 상태 확인 API의 정상 응답 스키마."""

    status: Literal["UP"] = Field(
        default="UP",
        description="서비스 상태",
        examples=["UP"],
    )
    service: str = Field(
        min_length=1,
        description="현재 실행 중인 서비스 이름",
        examples=["Jipsa RAG Service"],
    )
    version: str = Field(
        min_length=1,
        description="현재 실행 중인 애플리케이션 버전",
        examples=["0.1.0"],
    )
    environment: str = Field(
        min_length=1,
        description="애플리케이션 실행 환경",
        examples=["local"],
    )
