"""서비스 상태 확인 API에서 사용하는 응답 스키마를 정의한다."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """RAG 서비스의 기본 상태 정보를 표현하는 응답 스키마."""

    # 상태 확인 응답에 정의되지 않은 필드가 포함되는 것을 차단한다.
    #
    # Health Check 응답은 모니터링 시스템이나 로드 밸런서가 사용할 수 있으므로
    # 응답 구조가 의도하지 않게 변경되지 않도록 엄격하게 관리한다.
    model_config = ConfigDict(extra="forbid")

    status: Literal["UP"] = Field(
        default="UP",
        description="RAG 서비스 상태",
        examples=["UP"],
    )

    service: str = Field(
        min_length=1,
        description="현재 실행 중인 RAG 서비스 이름",
        examples=["Jipsa RAG Service"],
    )

    version: str = Field(
        min_length=1,
        description="현재 실행 중인 애플리케이션 버전",
        examples=["0.1.0"],
    )

    environment: str = Field(
        min_length=1,
        description="현재 애플리케이션 실행 환경",
        examples=["local"],
    )


class DependencyHealth(BaseModel):
    """Readiness 검사 대상 외부 의존성의 상태를 표현한다."""

    # 의존성 상태 응답에도 정의되지 않은 필드가 추가되지 않도록 제한한다.
    model_config = ConfigDict(extra="forbid")

    status: Literal["UP"] = Field(
        default="UP",
        description="외부 의존성 연결 상태",
        examples=["UP"],
    )


class ReadinessResponse(HealthResponse):
    """RAG 서비스와 필수 외부 의존성의 준비 상태를 표현한다."""

    database: DependencyHealth = Field(
        description="Local RAG 데이터베이스 연결 상태",
    )
