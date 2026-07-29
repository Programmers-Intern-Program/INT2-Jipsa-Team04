"""RAG 애플리케이션의 구조화 로그, 콘솔 로그 및 민감 정보 보호를 구성한다."""

import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, TextIO, cast

from pythonjsonlogger.json import JsonFormatter

from jipsa_rag.core.logging_settings import (
    ConsoleColorMode,
    ConsoleTimezone,
    LogFormat,
    get_logging_settings,
)
from jipsa_rag.core.request_context import get_request_id

DEFAULT_LOG_LEVEL = "INFO"

_JSON_LOG_FIELDS = [
    "asctime",
    "levelname",
    "name",
    "message",
    "request_id",
    "exc_info",
]

_REDACTED_VALUE: Final[str] = "[REDACTED]"
_REDACTED_PRESIGNED_URL: Final[str] = "[REDACTED_PRESIGNED_URL]"
_REDACTED_DATABASE_DSN: Final[str] = "[REDACTED_DATABASE_DSN]"
_DROP_LOG_FIELD: Final[object] = object()

# Console 출력은 사람이 빠르게 훑는 용도이므로 메시지와 개별 값을 합리적인 길이로
# 제한한다. JSON 로그는 수집기에서 후처리하므로 마스킹 외의 임의 절단을 수행하지 않는다.
_CONSOLE_MESSAGE_MAX_LENGTH: Final[int] = 1024
_CONSOLE_VALUE_MAX_LENGTH: Final[int] = 256
_CONSOLE_COMPONENT_MAX_LENGTH: Final[int] = 24
_CONSOLE_SERVICE_MAX_LENGTH: Final[int] = 24

# ANSI SGR, OSC 및 일반 CSI 제어 시퀀스를 제거한다. Uvicorn의 color_message나
# 외부 라이브러리 메시지가 리다이렉션된 로그 파일에 Escape 문자열을 남기지 않게 한다.
_ANSI_ESCAPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\x1B\][^\x07]*(?:\x07|\x1B\\))|"
    r"(?:\x1B\[[0-?]*[ -/]*[@-~])"
)

# Console 한 줄 형식을 깨뜨릴 수 있는 제어 문자는 사람이 확인 가능한 이스케이프
# 문자열로 변환한다. 예외 Traceback은 별도 줄로 출력하므로 이 변환에서 제외한다.
_CONTROL_CHARACTER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

# Console Formatter가 LogRecord 기본 속성을 구조화 extra로 중복 출력하지 않도록
# Python logging이 기본적으로 생성하는 필드 집합을 한 번만 계산한다.
_STANDARD_LOG_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        *logging.makeLogRecord({}).__dict__.keys(),
        "asctime",
        "message",
        "service",
        "environment",
        "log_schema_version",
    }
)

# 사람이 가장 자주 확인하는 진단 필드를 의미 순서대로 배치한다. 나머지 사용자 정의
# 필드는 이름순으로 이어 붙여 동일 입력이 항상 동일한 출력 순서를 갖게 한다.
_CONSOLE_PREFERRED_EXTRA_FIELDS: Final[tuple[str, ...]] = (
    "method",
    "path",
    "status_code",
    "success",
    "users_idx",
    "user_idx",
    "file_idx",
    "folder_idx",
    "file_type",
    "size_bytes",
    "structure_unit_count",
    "text_unit_count",
    "chunk_count",
    "batch_count",
    "embedding_dim",
    "rag_document_idx",
    "rag_index_run_idx",
    "index_version",
    "parser_type",
    "parser_version",
    "ocr_enabled",
    "ocr_max_concurrency",
    "database_check_on_startup",
    "callback_type",
    "duration_ms",
    "total_duration_ms",
    "slow_stage_threshold_ms",
    "response_started",
    "error_code",
)

# Console에서는 이벤트명으로 이미 식별 가능한 내부 필드와 서드파티 Formatter 전용
# 필드를 제외한다. JSON 로그에는 stage가 유지되지만 color_message는 완전히 제거한다.
_CONSOLE_IGNORED_EXTRA_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "color_message",
        "taskName",
        "stage",
        "is_slow_stage",
    }
)

# Console에서 길이가 긴 내부 필드명을 짧고 명확한 진단 키로 바꾼다. JSON 출력은
# 외부 수집 계약을 보존하기 위해 원래 필드명을 그대로 유지한다.
_CONSOLE_FIELD_ALIASES: Final[Mapping[str, str]] = {
    "status_code": "status",
    "users_idx": "user",
    "user_idx": "user",
    "file_idx": "file",
    "folder_idx": "folder",
    "file_type": "type",
    "size_bytes": "size",
    "structure_unit_count": "units",
    "text_unit_count": "text_units",
    "chunk_count": "chunks",
    "batch_count": "batches",
    "embedding_dim": "dim",
    "rag_document_idx": "document",
    "rag_index_run_idx": "run",
    "index_version": "index_ver",
    "parser_type": "parser",
    "parser_version": "parser_ver",
    "ocr_enabled": "ocr",
    "ocr_max_concurrency": "ocr_workers",
    "database_check_on_startup": "db_check",
    "callback_type": "callback",
    "duration_ms": "duration",
    "total_duration_ms": "total",
    "slow_stage_threshold_ms": "slow_threshold",
    "response_started": "response_started",
    "error_code": "error",
}

# 긴 Python logger 경로를 로컬 운영자가 즉시 이해할 수 있는 컴포넌트로 축약한다.
# 가장 구체적인 prefix를 먼저 배치하여 하위 모듈이 상위 규칙에 먼저 매칭되지 않게 한다.
_COMPONENT_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("jipsa_rag.api.v1.endpoints.file_processing", "file-processing"),
    ("jipsa_rag.api.ingest", "ingest"),
    ("jipsa_rag.core.middleware", "http"),
    ("jipsa_rag.infrastructure.ocr", "ocr"),
    ("jipsa_rag.infrastructure.embedding", "embedding"),
    ("jipsa_rag.infrastructure.indexing", "indexing"),
    ("jipsa_rag.infrastructure.database", "database"),
    ("jipsa_rag.services", "services"),
    ("jipsa_rag.main", "app"),
    ("uvicorn", "uvicorn"),
    ("httpx2", "http-client"),
    ("httpcore2", "http-client"),
    ("httpx", "http-client"),
    ("httpcore", "http-client"),
    ("qdrant_client", "qdrant"),
    ("py.warnings", "python"),
)

# 호출부가 event를 제공하지 않는 서드파티 로그에도 의미 있는 안정적 이벤트명을
# 부여한다. 애플리케이션 로그는 호출부가 지정한 구체 이벤트를 우선 사용한다.
_DEFAULT_EVENT_BY_COMPONENT: Final[Mapping[str, str]] = {
    "uvicorn": "server.lifecycle",
    "http-client": "http.client",
    "qdrant": "vector.client",
    "python": "python.warning",
}

# 외부 HTTP 라이브러리의 정상 요청 INFO 로그는 단계별 애플리케이션 로그와 중복된다.
# 기본 WARNING 정책을 적용하되 환경 변수로 필요 시 상세 로그를 다시 활성화할 수 있다.
_THIRD_PARTY_LOGGERS: Final[tuple[str, ...]] = (
    "httpx",
    "httpcore",
    "httpx2",
    "httpcore2",
    "qdrant_client",
    "urllib3",
    "asyncio",
    "watchfiles",
    "multipart",
    "python_multipart",
)

# 로그에 기록할 필요가 없고 크기·개인정보·모델 정보 노출 위험이 큰 원문 필드다.
# 호출부에서 실수로 extra에 전달해도 Formatter 경계에서 필드 자체를 제거한다.
_PROHIBITED_LOG_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "content",
        "chunk_content",
        "chunk_contents",
        "chunks",
        "question",
        "user_question",
        "prompt",
        "system_prompt",
        "ocr_text",
        "answer",
        "generation_response",
        "embedding",
        "embeddings",
        "embedding_vector",
        "embedding_vectors",
        "vector",
        "vectors",
        "payload",
        "request_body",
        "response_body",
        "request_payload",
        "response_payload",
    }
)

# 구조화 로그의 필드명이 아래 값과 일치하면 데이터 타입과 관계없이 값을 마스킹한다.
# DB 접속 정보는 비밀번호뿐 아니라 내부 주소와 계정명도 운영 인프라 정보이므로 보호한다.
_SENSITIVE_LOG_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "cookie",
        "set_cookie",
        "x_internal_token",
        "internal_token",
        "rag_ingest_token",
        "database_host",
        "database_port",
        "database_name",
        "database_user",
        "database_password",
        "database_url",
        "database_dsn",
        "db_host",
        "db_port",
        "db_name",
        "db_user",
        "db_password",
        "db_url",
        "db_dsn",
        "download_url",
        "presigned_url",
        "qdrant_api_key",
    }
)

# 새로운 인증 관련 필드가 추가되어도 일반적인 접미사를 사용하면 자동으로 마스킹한다.
# token_count처럼 단순히 token 문자열이 포함된 비민감 필드는 일치하지 않는다.
_SENSITIVE_LOG_FIELD_SUFFIXES: Final[tuple[str, ...]] = (
    "_token",
    "_password",
    "_secret",
    "_credential",
    "_api_key",
)

# Formatter 전용 중복 필드는 JSON에서도 제거한다. color_message는 Uvicorn의 실제
# message와 같은 내용을 ANSI 코드가 포함된 템플릿으로 반복하므로 저장 가치가 없다.
_NOISY_LOG_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "color_message",
    }
)

_PRESIGNED_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s\"'<>]*[?&](?:"
    r"x-amz-[a-z0-9-]+|awsaccesskeyid|signature"
    r")=[^\s\"'<>]*",
    re.IGNORECASE,
)

_DATABASE_DSN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:mysql(?:\+[a-z0-9_]+)?|mariadb(?:\+[a-z0-9_]+)?|"
    r"postgres(?:ql)?(?:\+[a-z0-9_]+)?|redis(?:\+ssl)?|"
    r"mongodb(?:\+srv)?):\/\/[^\s\"'<>]+",
    re.IGNORECASE,
)

_BEARER_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)

_SENSITIVE_ASSIGNMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>[\"']?(?:"
    r"x-internal-token|internal[_-]token|rag[_-]ingest[_-]token|"
    r"authorization|cookie|set[_-]cookie|download[_-]url|presigned[_-]url|"
    r"database[_-](?:host|port|name|user|password|url|dsn)|"
    r"db[_-](?:host|port|name|user|password|url|dsn)|"
    r"qdrant[_-]api[_-]key|api[_-]key|password|secret"
    r")[\"']?\s*[:=]\s*)"
    r"(?P<value>Bearer\s+[A-Za-z0-9._~+/=-]+|"
    r'"[^\"]*"|\'[^\']*\'|[^\s,;}]+)',
    re.IGNORECASE,
)

_SENSITIVE_QUERY_PARAMETER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>(?:[?&])(?:"
    r"x-amz-[a-z0-9-]+|awsaccesskeyid|signature|token|api_key|password"
    r")=)(?P<value>[^&#\s\"'<>]+)",
    re.IGNORECASE,
)

_ANSI_RESET: Final[str] = "\x1b[0m"
_ANSI_DIM: Final[str] = "\x1b[2m"
_ANSI_CYAN: Final[str] = "\x1b[36m"
_ANSI_BLUE: Final[str] = "\x1b[34m"
_ANSI_GREEN: Final[str] = "\x1b[32m"
_ANSI_YELLOW: Final[str] = "\x1b[33m"
_ANSI_RED: Final[str] = "\x1b[31m"
_ANSI_BOLD_RED: Final[str] = "\x1b[1;31m"

_LEVEL_COLOR: Final[Mapping[int, str]] = {
    logging.DEBUG: _ANSI_CYAN,
    logging.INFO: _ANSI_GREEN,
    logging.WARNING: _ANSI_YELLOW,
    logging.ERROR: _ANSI_RED,
    logging.CRITICAL: _ANSI_BOLD_RED,
}


class SensitiveDataJsonFormatter(JsonFormatter):
    """JSON 직렬화 전에 로그 레코드와 예외 문자열의 민감 정보를 제거한다."""

    def process_log_record(
        self,
        log_data: dict[str, Any],
    ) -> dict[str, Any]:
        """구조화 로그의 모든 중첩 필드와 문자열을 재귀적으로 정제한다."""

        sanitized_log_data = _sanitize_log_value(log_data)

        if not isinstance(sanitized_log_data, dict):
            raise TypeError("Sanitized log data must remain a dictionary.")

        return cast(
            dict[str, Any],
            sanitized_log_data,
        )

    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """JSON 로그 시각을 밀리초 정밀도의 UTC RFC 3339로 출력한다."""

        del datefmt

        return (
            datetime.fromtimestamp(
                record.created,
                tz=UTC,
            )
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def formatException(
        self,
        ei: Any,
    ) -> str:
        """Traceback과 예외 메시지를 출력하기 전에 민감 정보를 제거한다."""

        formatted_exception = super().formatException(ei)

        if isinstance(formatted_exception, list):
            formatted_exception_text = "\n".join(formatted_exception)
        else:
            formatted_exception_text = formatted_exception

        return _redact_sensitive_text(formatted_exception_text)


class SensitiveDataConsoleFormatter(logging.Formatter):
    """PowerShell에서 빠르게 훑을 수 있는 저소음 단일 행 로그를 만든다.

    고정 헤더는 로컬 시각, 레벨, 서비스/환경, 컴포넌트, 이벤트와 Request ID를
    표시한다. 뒤에는 메시지와 핵심 진단 지표만 붙인다. JSON과 같은 추적 의미를
    유지하면서 긴 logger 경로, 중복 필드와 서드파티 color_message는 제거한다.
    """

    def __init__(
        self,
        *,
        service_name: str,
        environment: str,
        timezone: ConsoleTimezone = "local",
        request_id_length: int = 8,
        use_color: bool = False,
    ) -> None:
        """Console 출력에 필요한 안정적인 표시 정책을 저장한다."""

        super().__init__()
        self._service_label = _build_service_label(service_name)
        self._environment = _truncate_text(
            _single_line_text(_redact_sensitive_text(environment.strip().lower())),
            _CONSOLE_SERVICE_MAX_LENGTH,
        )
        self._timezone = timezone
        self._request_id_length = request_id_length
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        """로그 레코드를 결정적인 단일 행 Console 문자열로 변환한다."""

        timestamp = _format_console_timestamp(
            record.created,
            timezone=self._timezone,
        )
        component = _resolve_component_name(record.name)
        event_value = record.__dict__.get(
            "event",
            _resolve_default_event(record.name),
        )
        event = _format_console_value(event_value)
        request_id = _format_request_id(
            record.__dict__.get("request_id"),
            length=self._request_id_length,
        )
        message = _truncate_text(
            _single_line_text(_redact_sensitive_text(record.getMessage())),
            _CONSOLE_MESSAGE_MAX_LENGTH,
        )

        timestamp_token = self._colorize(timestamp, _ANSI_DIM)
        level_token = self._colorize(
            f"{record.levelname:<8}",
            _LEVEL_COLOR.get(record.levelno, ""),
        )
        scope_token = self._colorize(
            f"[{self._service_label}/{self._environment}]",
            _ANSI_DIM,
        )
        component_token = self._colorize(
            f"[{component}]",
            _ANSI_BLUE,
        )
        request_token = self._colorize(
            f"req={request_id}",
            _ANSI_CYAN,
        )

        formatted_log = (
            f"{timestamp_token} {level_token} {scope_token} {component_token} "
            f"{event} {request_token} | {message}"
        )

        extra_fields = _extract_console_extra_fields(record)

        if extra_fields:
            formatted_extra = " ".join(
                _format_console_extra_field(field_name, field_value)
                for field_name, field_value in extra_fields
            )
            formatted_log = f"{formatted_log} | {formatted_extra}"

        # Traceback과 stack_info는 가독성을 위해 헤더 아래에 여러 줄로 유지한다.
        # 민감 정보와 ANSI 코드는 제거하지만 Python의 원래 예외 구조는 보존한다.
        if record.exc_info is not None:
            formatted_log = f"{formatted_log}\n{self.formatException(record.exc_info)}"

        if record.stack_info:
            formatted_log = f"{formatted_log}\n{_redact_sensitive_text(record.stack_info)}"

        return formatted_log

    def formatException(
        self,
        ei: Any,
    ) -> str:
        """콘솔 Traceback에 포함된 URL, 토큰, DSN 및 ANSI 코드를 제거한다."""

        return _redact_sensitive_text(super().formatException(ei))

    def _colorize(
        self,
        value: str,
        color_code: str,
    ) -> str:
        """색상 사용이 활성화된 경우에만 최소 범위에 ANSI 코드를 적용한다."""

        if not self._use_color or not color_code:
            return value

        return f"{color_code}{value}{_ANSI_RESET}"


class RequestContextFilter(logging.Filter):
    """모든 로그 레코드에 요청 식별자와 안정적인 기본 이벤트명을 추가한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        """호출부에서 생략한 공통 필드를 보완하고 중복 color_message를 제거한다."""

        record.__dict__.setdefault(
            "request_id",
            get_request_id(),
        )
        record.__dict__.setdefault(
            "event",
            _resolve_default_event(record.name),
        )

        # Uvicorn은 사람이 읽는 message와 ANSI 템플릿 color_message를 함께 넣는다.
        # 실제 메시지만 보존하여 Console과 JSON 양쪽에서 중복 및 Escape 노출을 막는다.
        record.__dict__.pop("color_message", None)

        return True


def configure_logging(
    *,
    log_level: str | None = None,
    log_format: str | None = None,
    service_name: str,
    environment: str,
) -> None:
    """애플리케이션 전역 로깅, 노이즈 제어와 민감 정보 보호를 구성한다."""

    logging_settings = get_logging_settings()

    configured_log_level = log_level or logging_settings.log_level
    configured_log_format = log_format or logging_settings.resolve_log_format(
        environment=environment,
    )

    resolved_log_level = _resolve_log_level(configured_log_level)
    resolved_log_format = _resolve_log_format(configured_log_format)
    third_party_log_level = _resolve_log_level(
        logging_settings.log_third_party_level,
    )

    use_color = _resolve_console_color_enabled(
        mode=logging_settings.log_color,
        stream=sys.stdout,
    )
    formatter = _create_formatter(
        log_format=resolved_log_format,
        service_name=service_name,
        environment=environment,
        console_timezone=logging_settings.log_console_timezone,
        request_id_length=logging_settings.log_request_id_length,
        use_color=use_color,
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(resolved_log_level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RequestContextFilter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(resolved_log_level)
    root_logger.addHandler(stream_handler)

    # Python warnings도 동일한 서비스·환경·Request ID 형식으로 수집한다.
    logging.captureWarnings(True)

    _configure_uvicorn_loggers(resolved_log_level)
    _configure_third_party_loggers(third_party_log_level)


def _create_formatter(
    *,
    log_format: LogFormat,
    service_name: str,
    environment: str,
    console_timezone: ConsoleTimezone = "local",
    request_id_length: int = 8,
    use_color: bool = False,
) -> logging.Formatter:
    """선택한 형식에 대응하는 민감 정보 보호 Formatter를 생성한다."""

    if log_format == "console":
        return SensitiveDataConsoleFormatter(
            service_name=service_name,
            environment=environment,
            timezone=console_timezone,
            request_id_length=request_id_length,
            use_color=use_color,
        )

    return SensitiveDataJsonFormatter(
        _JSON_LOG_FIELDS,
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        static_fields={
            "log_schema_version": 1,
            "service": service_name,
            "environment": environment,
        },
    )


def _extract_console_extra_fields(
    record: logging.LogRecord,
) -> tuple[tuple[str, object], ...]:
    """Console에 표시할 안전하고 의미 있는 extra 필드를 정렬해 반환한다."""

    sanitized_extra: dict[str, object] = {}
    is_slow_stage = record.__dict__.get("is_slow_stage") is True

    for field_name, field_value in record.__dict__.items():
        if field_name in _STANDARD_LOG_RECORD_FIELDS or field_name in {
            "event",
            "request_id",
        }:
            continue

        if field_name in _CONSOLE_IGNORED_EXTRA_FIELDS:
            continue

        # 임계값은 실제 느린 단계에서만 유용하다. 정상 단계마다 같은 값을 반복하지 않는다.
        if field_name == "slow_stage_threshold_ms" and not is_slow_stage:
            continue

        sanitized_value = _sanitize_log_value(
            field_value,
            field_name=field_name,
        )

        if sanitized_value is _DROP_LOG_FIELD:
            continue

        sanitized_extra[field_name] = sanitized_value

    ordered_extra: list[tuple[str, object]] = []

    for field_name in _CONSOLE_PREFERRED_EXTRA_FIELDS:
        if field_name in sanitized_extra:
            ordered_extra.append(
                (
                    field_name,
                    sanitized_extra.pop(field_name),
                )
            )

    ordered_extra.extend(
        sorted(
            sanitized_extra.items(),
            key=lambda item: item[0],
        )
    )

    return tuple(ordered_extra)


def _format_console_extra_field(
    field_name: str,
    field_value: object,
) -> str:
    """Console extra 필드명을 축약하고 단위를 사람이 읽기 쉽게 변환한다."""

    display_name = _CONSOLE_FIELD_ALIASES.get(field_name, field_name)

    if field_name in {
        "duration_ms",
        "total_duration_ms",
        "slow_stage_threshold_ms",
    } and isinstance(field_value, int | float):
        display_value = _format_duration_ms(float(field_value))
    elif field_name == "size_bytes" and isinstance(field_value, int):
        display_value = _format_byte_size(field_value)
    else:
        display_value = _format_console_value(field_value)

    return f"{display_name}={display_value}"


def _format_console_value(value: object) -> str:
    """구조화 값을 공백과 구분자가 모호하지 않은 안전한 문자열로 변환한다."""

    if value is None:
        return "-"

    if isinstance(value, bool):
        return str(value).lower()

    if isinstance(value, str):
        normalized_value = _truncate_text(
            _single_line_text(_redact_sensitive_text(value)),
            _CONSOLE_VALUE_MAX_LENGTH,
        )

        if not normalized_value:
            return '""'

        if any(
            character.isspace() or character in {"|", "=", '"'} for character in normalized_value
        ):
            return json.dumps(
                normalized_value,
                ensure_ascii=False,
            )

        return normalized_value

    if isinstance(value, Mapping | list | tuple):
        serialized_value = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return _truncate_text(
            _single_line_text(_redact_sensitive_text(serialized_value)),
            _CONSOLE_VALUE_MAX_LENGTH,
        )

    return _truncate_text(
        _single_line_text(_redact_sensitive_text(str(value))),
        _CONSOLE_VALUE_MAX_LENGTH,
    )


def _format_console_timestamp(
    created: float,
    *,
    timezone: ConsoleTimezone,
) -> str:
    """Console 시각을 밀리초와 명시적 UTC Offset이 포함된 형태로 반환한다."""

    if timezone == "utc":
        timestamp = datetime.fromtimestamp(created, tz=UTC)
    else:
        timestamp = datetime.fromtimestamp(created, tz=UTC).astimezone()

    return timestamp.isoformat(
        sep=" ",
        timespec="milliseconds",
    )


def _format_request_id(
    value: object,
    *,
    length: int,
) -> str:
    """Console Request ID를 추적 가능한 고정 길이 prefix로 축약한다."""

    if value is None:
        return "-"

    normalized_value = _single_line_text(
        _redact_sensitive_text(str(value).strip()),
    )

    if not normalized_value:
        return "-"

    return normalized_value[:length]


def _format_duration_ms(duration_ms: float) -> str:
    """밀리초 값을 크기에 따라 us, ms 또는 s 단위로 읽기 쉽게 표시한다."""

    if duration_ms < 1:
        return f"{duration_ms * 1000:.0f}us"

    if duration_ms < 1000:
        return f"{duration_ms:.3f}".rstrip("0").rstrip(".") + "ms"

    return f"{duration_ms / 1000:.3f}".rstrip("0").rstrip(".") + "s"


def _format_byte_size(size_bytes: int) -> str:
    """바이트 크기를 IEC 단위로 표시하되 정수 바이트 값의 의미를 보존한다."""

    if size_bytes < 1024:
        return f"{size_bytes}B"

    units = (
        "KiB",
        "MiB",
        "GiB",
        "TiB",
    )
    size = float(size_bytes)

    for unit in units:
        size /= 1024
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f}".rstrip("0").rstrip(".") + unit

    return f"{size_bytes}B"


def _build_service_label(service_name: str) -> str:
    """사람용 서비스명을 Console에서 반복 가능한 짧은 slug로 변환한다."""

    normalized_name = _redact_sensitive_text(service_name).strip().lower()
    normalized_name = re.sub(r"\bservice\b", "", normalized_name)
    normalized_name = re.sub(r"[^a-z0-9가-힣]+", "-", normalized_name)
    normalized_name = normalized_name.strip("-") or "service"

    return _truncate_text(
        normalized_name,
        _CONSOLE_SERVICE_MAX_LENGTH,
    )


def _resolve_component_name(logger_name: str) -> str:
    """긴 logger 이름을 안정적인 로컬 진단 컴포넌트명으로 변환한다."""

    for prefix, component in _COMPONENT_PREFIXES:
        if logger_name == prefix or logger_name.startswith(f"{prefix}."):
            return component

    fallback_component = logger_name.rsplit(".", maxsplit=1)[-1]
    return _truncate_text(
        _single_line_text(_redact_sensitive_text(fallback_component)),
        _CONSOLE_COMPONENT_MAX_LENGTH,
    )


def _resolve_default_event(logger_name: str) -> str:
    """명시 이벤트가 없는 로그에 logger 컴포넌트 기반 기본 이벤트를 부여한다."""

    component = _resolve_component_name(logger_name)
    return _DEFAULT_EVENT_BY_COMPONENT.get(component, "log")


def _resolve_console_color_enabled(
    *,
    mode: ConsoleColorMode,
    stream: TextIO,
) -> bool:
    """NO_COLOR, 명시 설정과 TTY 여부를 적용하여 ANSI 색상 사용을 결정한다."""

    if os.getenv("NO_COLOR") is not None:
        return False

    if mode == "always":
        return True

    if mode == "never":
        return False

    return stream.isatty()


def _normalize_log_field_name(field_name: str) -> str:
    """로그 필드명을 비교 가능한 소문자 snake_case 형태로 정규화한다."""

    return field_name.strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_log_field(field_name: str) -> bool:
    """필드명이 인증값, URL 또는 DB 접속 정보에 해당하는지 확인한다."""

    normalized_field_name = _normalize_log_field_name(field_name)

    return normalized_field_name in _SENSITIVE_LOG_FIELD_NAMES or (
        normalized_field_name.endswith(_SENSITIVE_LOG_FIELD_SUFFIXES)
    )


def _is_prohibited_log_field(field_name: str) -> bool:
    """원문·벡터·요청 본문처럼 로그에 저장하지 않을 필드인지 확인한다."""

    return _normalize_log_field_name(field_name) in _PROHIBITED_LOG_FIELD_NAMES


def _is_noisy_log_field(field_name: str) -> bool:
    """실제 메시지와 중복되는 Formatter 전용 필드인지 확인한다."""

    return _normalize_log_field_name(field_name) in _NOISY_LOG_FIELD_NAMES


def _replace_sensitive_assignment(match: re.Match[str]) -> str:
    """민감한 key-value 문자열에서 키와 구분자는 보존하고 값만 교체한다."""

    original_value = match.group("value")

    if (
        len(original_value) >= 2
        and original_value[0] == original_value[-1]
        and original_value[0] in {'"', "'"}
    ):
        redacted_value = f"{original_value[0]}{_REDACTED_VALUE}{original_value[-1]}"
    else:
        redacted_value = _REDACTED_VALUE

    return f"{match.group('prefix')}{redacted_value}"


def _redact_sensitive_text(value: str) -> str:
    """자유 형식 문자열에서 ANSI, Presigned URL, 토큰 및 DB DSN을 제거한다."""

    redacted_value = _ANSI_ESCAPE_PATTERN.sub("", value)
    redacted_value = _PRESIGNED_URL_PATTERN.sub(
        _REDACTED_PRESIGNED_URL,
        redacted_value,
    )
    redacted_value = _DATABASE_DSN_PATTERN.sub(
        _REDACTED_DATABASE_DSN,
        redacted_value,
    )
    redacted_value = _SENSITIVE_ASSIGNMENT_PATTERN.sub(
        _replace_sensitive_assignment,
        redacted_value,
    )
    redacted_value = _BEARER_TOKEN_PATTERN.sub(
        "Bearer [REDACTED]",
        redacted_value,
    )
    redacted_value = _SENSITIVE_QUERY_PARAMETER_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{_REDACTED_VALUE}",
        redacted_value,
    )

    return redacted_value


def _single_line_text(value: str) -> str:
    """로그 주입을 방지하도록 줄바꿈과 제어 문자를 가시적 문자열로 변환한다."""

    normalized_value = value.replace("\r", "\\r").replace("\n", "\\n")
    normalized_value = normalized_value.replace("\t", "\\t")

    return _CONTROL_CHARACTER_PATTERN.sub(
        lambda match: f"\\u{ord(match.group(0)):04x}",
        normalized_value,
    )


def _truncate_text(value: str, max_length: int) -> str:
    """과도한 로그 필드를 명확한 표식과 함께 결정적으로 절단한다."""

    if len(value) <= max_length:
        return value

    suffix = "...<truncated>"
    prefix_length = max(max_length - len(suffix), 0)
    return f"{value[:prefix_length]}{suffix}"


def _sanitize_log_value(
    value: object,
    *,
    field_name: str | None = None,
) -> object:
    """중첩 로그 값을 순회하여 금지 필드를 제거하고 민감값을 마스킹한다."""

    if field_name is not None and (
        _is_prohibited_log_field(field_name) or _is_noisy_log_field(field_name)
    ):
        return _DROP_LOG_FIELD

    if field_name is not None and _is_sensitive_log_field(field_name):
        return _REDACTED_VALUE

    if isinstance(value, str):
        return _redact_sensitive_text(value)

    if isinstance(value, Mapping):
        sanitized_mapping: dict[object, object] = {}

        for key, nested_value in value.items():
            sanitized_nested_value = _sanitize_log_value(
                nested_value,
                field_name=key if isinstance(key, str) else None,
            )

            if sanitized_nested_value is _DROP_LOG_FIELD:
                continue

            sanitized_mapping[key] = sanitized_nested_value

        return sanitized_mapping

    if isinstance(value, tuple):
        # tuple과 list 분기에서 서로 다른 지역 변수명을 사용한다.
        # Mypy는 동일 함수 범위에서 처음 할당된 변수의 구체 타입을 유지하므로,
        # 같은 변수명에 tuple과 list를 차례로 할당하면 호환되지 않는 재할당으로
        # 판단한다. 컨테이너별 변수명을 분리해 원래 자료형 보존 계약을 명확히 한다.
        sanitized_tuple_items = tuple(_sanitize_log_value(item) for item in value)
        return tuple(item for item in sanitized_tuple_items if item is not _DROP_LOG_FIELD)

    if isinstance(value, list):
        # 입력이 list이면 결과도 list로 유지한다. 중첩 값은 재귀적으로
        # 정제하며, 출력 금지 대상으로 판정된 항목만 결과에서 제외한다.
        sanitized_list_items = [_sanitize_log_value(item) for item in value]
        return [item for item in sanitized_list_items if item is not _DROP_LOG_FIELD]

    return value


def calculate_duration_ms(started_at: float) -> float:
    """``perf_counter`` 시작값으로부터 경과 시간을 밀리초로 계산한다."""

    return round(
        (time.perf_counter() - started_at) * 1000,
        3,
    )


def resolve_stage_log_level(
    *,
    duration_ms: float,
    slow_stage_threshold_ms: float,
) -> int:
    """처리 시간이 임계값 이상이면 WARNING, 아니면 INFO를 반환한다."""

    if duration_ms >= slow_stage_threshold_ms:
        return logging.WARNING

    return logging.INFO


def log_stage_completed(
    logger: logging.Logger,
    message: str,
    *,
    event: str,
    started_at: float,
    extra: Mapping[str, object],
    slow_stage_threshold_ms: float | None = None,
    total_duration_field: bool = False,
) -> float:
    """단계 완료 시간과 느린 단계 여부를 한 번의 구조화 로그로 기록한다."""

    duration_ms = calculate_duration_ms(started_at)
    threshold_ms = (
        slow_stage_threshold_ms
        if slow_stage_threshold_ms is not None
        else get_logging_settings().slow_stage_threshold_ms
    )
    log_level = resolve_stage_log_level(
        duration_ms=duration_ms,
        slow_stage_threshold_ms=threshold_ms,
    )

    # 현재 레벨에서 출력되지 않는 정상 단계는 extra dict를 만들지 않는다.
    # WARNING 승격이 필요한 느린 단계는 애플리케이션 레벨이 WARNING이어도 보존된다.
    if not logger.isEnabledFor(log_level):
        return duration_ms

    is_slow_stage = duration_ms >= threshold_ms
    duration_field_name = "total_duration_ms" if total_duration_field else "duration_ms"
    log_extra = {
        **dict(extra),
        "event": event,
        duration_field_name: duration_ms,
        "slow_stage_threshold_ms": threshold_ms,
        "is_slow_stage": is_slow_stage,
    }

    logger.log(
        log_level,
        message,
        extra=log_extra,
    )

    return duration_ms


def log_debug_lazy(
    logger: logging.Logger,
    message: str,
    *,
    event: str,
    extra_factory: Callable[[], Mapping[str, object]],
) -> None:
    """DEBUG가 활성화된 경우에만 진단 필드를 계산하고 로그를 생성한다."""

    if not logger.isEnabledFor(logging.DEBUG):
        return

    logger.debug(
        message,
        extra={
            **dict(extra_factory()),
            "event": event,
        },
    )


def _resolve_log_level(log_level: str) -> int:
    """문자열 로그 레벨을 logging 모듈의 정수 값으로 변환한다."""

    normalized_log_level = log_level.strip().upper()

    if not normalized_log_level:
        normalized_log_level = DEFAULT_LOG_LEVEL

    if normalized_log_level == "WARN":
        normalized_log_level = "WARNING"
    elif normalized_log_level == "FATAL":
        normalized_log_level = "CRITICAL"

    resolved_log_level = logging.getLevelName(normalized_log_level)

    if not isinstance(resolved_log_level, int):
        raise ValueError(f"Unsupported log level: {log_level!r}")

    return resolved_log_level


def _resolve_log_format(log_format: str) -> LogFormat:
    """로그 형식 문자열을 console 또는 json으로 정규화한다."""

    normalized_log_format = log_format.strip().lower()

    if normalized_log_format == "console":
        return "console"

    if normalized_log_format == "json":
        return "json"

    raise ValueError(f"Unsupported log format: {log_format!r}")


def _configure_uvicorn_loggers(log_level: int) -> None:
    """Uvicorn 로그가 애플리케이션 공통 포맷을 사용하도록 구성한다."""

    for logger_name in (
        "uvicorn",
        "uvicorn.error",
    ):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.setLevel(log_level)
        uvicorn_logger.propagate = True

    # HTTP 접근 로그는 RequestLoggingMiddleware에서 완료 중심으로 기록한다.
    # Uvicorn access log를 비활성화하여 같은 요청이 두 번 출력되지 않게 한다.
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.handlers.clear()
    uvicorn_access_logger.propagate = False
    uvicorn_access_logger.disabled = True


def _configure_third_party_loggers(log_level: int) -> None:
    """외부 라이브러리 정상 요청 노이즈를 별도 레벨로 제한한다."""

    for logger_name in _THIRD_PARTY_LOGGERS:
        third_party_logger = logging.getLogger(logger_name)
        third_party_logger.handlers.clear()
        third_party_logger.setLevel(log_level)
        third_party_logger.propagate = True
