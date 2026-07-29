"""RAG 로그 레벨, 출력 형식 및 로컬 콘솔 표시 정책을 관리한다."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jipsa_rag.core.config import resolve_env_file, resolve_environment

# Python 표준 logging 모듈에서 운영 중 실제로 사용할 로그 레벨만 허용한다.
# NOTSET은 모든 로그를 통과시켜 의도치 않은 출력량 증가를 만들 수 있으므로 제외한다.
LogLevel = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]

# console은 사람이 직접 읽는 로컬 개발 로그이고,
# json은 로그 수집기와 자동 분석 도구에서 사용하는 구조화 형식이다.
LogFormat = Literal[
    "console",
    "json",
]

# local은 운영체제 시간대를 사용하고 utc는 모든 환경에서 UTC를 사용한다.
ConsoleTimezone = Literal[
    "local",
    "utc",
]

# auto는 TTY에서만 색상을 사용하며, always와 never는 명시적으로 동작을 고정한다.
ConsoleColorMode = Literal[
    "auto",
    "always",
    "never",
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
    """RAG 로그 초기화에 필요한 독립 설정을 제공한다.

    애플리케이션 전체 설정과 분리하여 로그 초기화 시점에 데이터베이스,
    Qdrant 또는 TEI 설정을 먼저 검증하지 않는다. 이 구조를 사용하면
    애플리케이션 시작 실패 자체도 일관된 형식으로 확인할 수 있다.
    """

    # JIPSA_RAG_LOG_LEVEL 환경 변수로 설정한다.
    # 빈 값은 로그가 완전히 사라지는 상황을 막기 위해 INFO로 정규화한다.
    log_level: LogLevel = "INFO"

    # JIPSA_RAG_LOG_FORMAT 환경 변수로 설정한다.
    # 미설정 시 local/development는 console, 나머지 환경은 json을 사용한다.
    log_format: LogFormat | None = None

    # JIPSA_RAG_LOG_CONSOLE_TIMEZONE 환경 변수로 설정한다.
    # 로컬 개발자는 실제 벽시계 시간과 즉시 대조할 수 있도록 local을 기본으로 사용한다.
    # 운영 JSON 로그는 이 설정과 무관하게 항상 UTC RFC 3339 형식을 유지한다.
    log_console_timezone: ConsoleTimezone = "local"

    # JIPSA_RAG_LOG_COLOR 환경 변수로 설정한다.
    # auto는 PowerShell 같은 대화형 TTY에서만 제한된 ANSI 색상을 적용한다.
    log_color: ConsoleColorMode = "auto"

    # JIPSA_RAG_LOG_REQUEST_ID_LENGTH 환경 변수로 설정한다.
    # Console에서는 UUID 앞부분만 표시하여 한 줄 폭을 줄이고 JSON에는 전체 값을 유지한다.
    # 8자는 로컬 추적에 충분한 가독성을 제공하며 필요 시 36자로 전체 UUID를 표시할 수 있다.
    log_request_id_length: int = Field(
        default=8,
        ge=8,
        le=36,
    )

    # JIPSA_RAG_LOG_THIRD_PARTY_LEVEL 환경 변수로 설정한다.
    # httpx, Qdrant SDK 등 외부 라이브러리의 정상 요청 로그가 파일 처리 로그를 묻지 않도록
    # 기본값을 WARNING으로 둔다. 문제 분석이 필요할 때만 INFO 또는 DEBUG로 낮춘다.
    log_third_party_level: LogLevel = "WARNING"

    # JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS 환경 변수로 설정한다.
    # 다운로드, 파싱/OCR, 청킹, 임베딩, 색인 및 백엔드 통신 단계가 이 값을 넘으면
    # 정상 완료 로그라도 WARNING으로 승격하여 장시간 처리 구간을 빠르게 식별한다.
    slow_stage_threshold_ms: float = Field(
        default=5000.0,
        gt=0,
        le=3_600_000,
    )

    model_config = SettingsConfigDict(
        env_prefix="JIPSA_RAG_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
    )

    @field_validator(
        "log_level",
        "log_third_party_level",
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
            supported_values = ", ".join(sorted(_SUPPORTED_LOG_LEVELS))
            raise ValueError(
                f"지원하지 않는 로그 레벨입니다: {normalized_value}. 지원 값: {supported_values}"
            )

        return normalized_value

    @field_validator(
        "log_format",
        "log_console_timezone",
        "log_color",
        mode="before",
    )
    @classmethod
    def normalize_lowercase_option(
        cls,
        value: object,
    ) -> object:
        """선택형 로그 설정을 소문자로 정규화한다."""

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
        """명시 설정 또는 실행 환경에 따라 최종 로그 형식을 결정한다."""

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
