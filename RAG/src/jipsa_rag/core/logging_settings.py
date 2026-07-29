"""RAG 로그 출력 레벨과 형식을 환경 변수로 관리한다."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jipsa_rag.core.config import resolve_env_file, resolve_environment

# Python 표준 logging 모듈에서 운영 중 실제로 사용할 로그 레벨만 허용한다.
# NOTSET은 전체 로그를 통과시켜 의도치 않은 출력량 증가를 만들 수 있으므로 제외한다.
LogLevel = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]

# console은 로컬 PowerShell에서 사람이 직접 읽는 용도이고,
# json은 로그 수집기와 자동 분석 도구에서 사용하는 구조화 출력 형식이다.
LogFormat = Literal[
    "console",
    "json",
]

_SUPPORTED_LOG_LEVELS = frozenset(
    {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }
)

_LOG_LEVEL_ALIASES = {
    "WARN": "WARNING",
    "FATAL": "CRITICAL",
}


class LoggingSettings(BaseSettings):
    """RAG 로그 출력에 필요한 최소 설정을 제공한다.

    애플리케이션 전체 설정인 ``Settings``와 분리하여 로그 초기화 시점에
    데이터베이스, Qdrant, TEI 등 다른 필수 환경 변수를 먼저 검증하지 않는다.

    이 구조를 사용하면 설정 오류를 포함한 애플리케이션 시작 문제도 로깅 시스템이
    가능한 한 먼저 준비된 상태에서 확인할 수 있다.
    """

    # JIPSA_RAG_LOG_LEVEL 환경 변수로 설정한다.
    # 빈 값은 운영 중 로그가 완전히 사라지는 상황을 방지하기 위해 INFO로 정규화한다.
    log_level: LogLevel = "INFO"

    # JIPSA_RAG_LOG_FORMAT 환경 변수로 설정한다.
    # 명시하지 않으면 local/development는 console, test는 json을 기본값으로 사용한다.
    log_format: LogFormat | None = None

    model_config = SettingsConfigDict(
        env_prefix="JIPSA_RAG_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
    )

    @field_validator(
        "log_level",
        mode="before",
    )
    @classmethod
    def normalize_log_level(
        cls,
        value: object,
    ) -> object:
        """로그 레벨 문자열을 표준 대문자 이름으로 정규화한다."""

        if not isinstance(value, str):
            return value

        normalized_value = value.strip().upper()

        if not normalized_value:
            return "INFO"

        normalized_value = _LOG_LEVEL_ALIASES.get(
            normalized_value,
            normalized_value,
        )

        if normalized_value not in _SUPPORTED_LOG_LEVELS:
            supported_values = ", ".join(
                sorted(
                    _SUPPORTED_LOG_LEVELS,
                )
            )
            raise ValueError(
                f"지원하지 않는 로그 레벨입니다: {normalized_value}. 지원 값: {supported_values}"
            )

        return normalized_value

    @field_validator(
        "log_format",
        mode="before",
    )
    @classmethod
    def normalize_log_format(
        cls,
        value: object,
    ) -> object:
        """로그 형식을 소문자로 정규화하고 빈 문자열은 미설정으로 처리한다."""

        if value is None:
            return None

        if not isinstance(value, str):
            return value

        normalized_value = value.strip().lower()

        if not normalized_value:
            return None

        return normalized_value

    def resolve_log_format(
        self,
        *,
        environment: str,
    ) -> LogFormat:
        """명시 설정 또는 실행 환경에 따라 최종 로그 형식을 결정한다.

        로컬과 개발 환경은 사람이 직접 콘솔을 확인하는 경우가 많으므로 console을
        기본값으로 사용한다. 테스트 환경은 기존 구조화 로그 회귀 테스트와 자동 분석을
        안정적으로 유지하기 위해 json을 기본값으로 사용한다.
        """

        if self.log_format is not None:
            return self.log_format

        normalized_environment = environment.strip().lower()

        if normalized_environment in {
            "local",
            "development",
        }:
            return "console"

        return "json"


@lru_cache(maxsize=1)
def get_logging_settings() -> LoggingSettings:
    """현재 실행 환경의 로그 설정 객체를 생성하고 재사용한다."""

    environment = resolve_environment()
    env_file = resolve_env_file(environment)

    return LoggingSettings(
        _env_file=env_file,
    )
