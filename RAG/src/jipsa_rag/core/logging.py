import logging
import re
import sys
import time
from collections.abc import Mapping
from typing import Any, Final, cast

from pythonjsonlogger.json import JsonFormatter

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

# 구조화 로그의 필드명이 아래 값과 일치하면
# 값의 데이터 타입과 관계없이 필드 전체를 마스킹한다.
#
# DB 접속 정보는 비밀번호뿐 아니라 내부 호스트, 포트,
# 데이터베이스명과 계정명도 운영 인프라 정보를 노출할 수 있으므로
# 모두 민감 정보로 분류한다.
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

# 프로젝트에 새로운 인증 관련 로그 필드가 추가되더라도
# 일반적인 민감 정보 접미사를 사용하면 별도 코드 수정 없이
# 자동으로 마스킹할 수 있도록 한다.
#
# token_count처럼 단순히 "token" 문자열이 포함된 비민감 필드는
# 아래 접미사와 정확히 일치하지 않으므로 마스킹하지 않는다.
_SENSITIVE_LOG_FIELD_SUFFIXES: Final[tuple[str, ...]] = (
    "_token",
    "_password",
    "_secret",
    "_credential",
    "_api_key",
)

# AWS Presigned URL은 쿼리 문자열에 서명, 자격 증명,
# 만료 시간과 같은 민감 정보가 포함된다.
#
# URL 일부만 남기는 경우 S3 객체 경로와 서명 파라미터가
# 함께 노출될 수 있으므로 URL 전체를 고정된 마스킹 문자열로 교체한다.
_PRESIGNED_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s\"'<>]*[?&](?:"
    r"x-amz-[a-z0-9-]+|awsaccesskeyid|signature"
    r")=[^\s\"'<>]*",
    re.IGNORECASE,
)

# SQLAlchemy의 비동기 드라이버가 포함된 URL도 처리할 수 있도록
# mysql+asyncmy:// 등의 스킴 변형을 허용한다.
#
# MySQL뿐 아니라 일반적으로 사용될 수 있는 MariaDB,
# PostgreSQL, Redis 및 MongoDB 접속 문자열도 함께 차단한다.
_DATABASE_DSN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:mysql(?:\+[a-z0-9_]+)?|mariadb(?:\+[a-z0-9_]+)?|"
    r"postgres(?:ql)?(?:\+[a-z0-9_]+)?|redis(?:\+ssl)?|"
    r"mongodb(?:\+srv)?):\/\/[^\s\"'<>]+",
    re.IGNORECASE,
)

# HTTP Authorization 헤더나 일반 문자열 로그에 포함된
# Bearer 인증 토큰 전체를 마스킹한다.
_BEARER_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)

# 일반 문자열 로그에서 key=value, key: value 또는 JSON 형태로
# 기록된 민감값을 찾아 키와 구분자는 유지하고 값만 마스킹한다.
#
# 구조화 extra 필드뿐 아니라 예외 메시지나 서드파티 로그에서
# 문자열로 전달된 민감 정보도 처리하기 위한 방어 계층이다.
_SENSITIVE_ASSIGNMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>[\"']?(?:"
    r"x-internal-token|internal[_-]token|rag[_-]ingest[_-]token|"
    r"authorization|cookie|set[_-]cookie|download[_-]url|presigned[_-]url|"
    r"database[_-](?:host|port|name|user|password|url|dsn)|"
    r"db[_-](?:host|port|name|user|password|url|dsn)|"
    r"qdrant[_-]api[_-]key|api[_-]key|password|secret"
    r")[\"']?\s*[:=]\s*)"
    r"(?P<value>Bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
    re.IGNORECASE,
)

# Presigned URL 전체 패턴을 찾지 못한 비정형 문자열에서도
# 대표적인 AWS 서명 및 인증 쿼리 파라미터 값이 남지 않도록
# 각 쿼리 파라미터 값을 추가로 마스킹한다.
_SENSITIVE_QUERY_PARAMETER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>(?:[?&])(?:"
    r"x-amz-[a-z0-9-]+|awsaccesskeyid|signature|token|api_key|password"
    r")=)(?P<value>[^&#\s\"'<>]+)",
    re.IGNORECASE,
)


class SensitiveDataJsonFormatter(JsonFormatter):
    """JSON 직렬화 전에 로그 레코드와 예외 문자열의 민감 정보를 제거한다."""

    def process_log_record(
        self,
        log_data: dict[str, Any],
    ) -> dict[str, Any]:
        """구조화 로그의 모든 중첩 필드와 문자열을 재귀적으로 마스킹한다.

        python-json-logger가 구성한 로그 데이터에는 message뿐 아니라
        extra로 전달한 중첩 dict, list 및 tuple 값도 포함될 수 있다.

        따라서 최상위 필드만 검사하지 않고 전체 자료 구조를 재귀적으로
        순회하여 민감한 필드명과 문자열 값을 안전한 값으로 교체한다.
        """

        sanitized_log_data = _sanitize_log_value(log_data)

        # 최상위 입력은 JsonFormatter가 생성한 dict이므로 정상적으로는
        # 항상 dict가 반환된다.
        #
        # 방어적으로 반환 타입을 확인하여 향후 마스킹 함수 수정으로
        # 최상위 JSON 로그 구조가 손상되는 회귀를 차단한다.
        if not isinstance(sanitized_log_data, dict):
            raise TypeError("Sanitized log data must remain a dictionary.")

        return cast(
            dict[str, Any],
            sanitized_log_data,
        )

    def formatException(
        self,
        ei: Any,
    ) -> str:
        """Traceback과 예외 메시지를 출력하기 전에 민감 정보를 제거한다.

        표준 logging.Formatter.formatException()의 반환 계약은 str이다.

        그러나 python-json-logger의 JsonFormatter 타입 선언에서는
        구성에 따라 str 또는 list[str]을 반환할 수 있는 것으로
        정의되어 있다.

        따라서 상위 Formatter가 list[str]을 반환하면 각 문자열을
        줄바꿈으로 연결하여 하나의 Traceback 문자열로 정규화한다.

        최종적으로 항상 str을 반환하므로 logging.Formatter의
        메서드 반환 계약과 호환되며, _redact_sensitive_text()에도
        올바른 타입만 전달된다.
        """

        formatted_exception = super().formatException(ei)

        # python-json-logger가 Traceback을 줄 단위 문자열 목록으로
        # 반환하는 경우에도 최종 JSON 로그의 exc_info는 일관되게
        # 단일 문자열이 되도록 줄바꿈으로 연결한다.
        if isinstance(formatted_exception, list):
            formatted_exception_text = "\n".join(
                formatted_exception,
            )
        else:
            formatted_exception_text = formatted_exception

        # 반환값을 str로 정규화한 후 Traceback 전체에 포함될 수 있는
        # Presigned URL, 내부 인증 토큰 및 DB 접속 정보를 제거한다.
        return _redact_sensitive_text(
            formatted_exception_text,
        )


class RequestContextFilter(logging.Filter):
    """현재 요청 식별자를 모든 로그 레코드에 추가한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        """로그에 요청 식별자가 없을 때 현재 컨텍스트 값을 추가한다."""

        record.__dict__.setdefault(
            "request_id",
            get_request_id(),
        )

        return True


def configure_logging(
    *,
    log_level: str = DEFAULT_LOG_LEVEL,
    service_name: str,
    environment: str,
) -> None:
    """애플리케이션 전역 JSON 로깅과 민감 정보 마스킹을 구성한다.

    Args:
        log_level:
            출력할 최소 로그 레벨이다.
        service_name:
            모든 로그에 포함할 서비스 이름이다.
        environment:
            모든 로그에 포함할 실행 환경 이름이다.

    Raises:
        ValueError:
            지원하지 않는 로그 레벨이 전달된 경우 발생한다.
    """

    resolved_log_level = _resolve_log_level(log_level)

    # 루트 핸들러에 민감 정보 전용 Formatter를 적용한다.
    #
    # 애플리케이션 로그뿐 아니라 Uvicorn, SQLAlchemy 및 HTTP 클라이언트처럼
    # 루트 로거로 전파되는 서드파티 로그도 동일한 마스킹 경계를 통과한다.
    formatter = SensitiveDataJsonFormatter(
        _JSON_LOG_FIELDS,
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        static_fields={
            "service": service_name,
            "environment": environment,
        },
    )

    # 서버 실행 지역과 관계없이 로그 시각을 UTC로 통일한다.
    formatter.converter = time.gmtime

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(resolved_log_level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RequestContextFilter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(resolved_log_level)
    root_logger.addHandler(stream_handler)

    _configure_uvicorn_loggers(resolved_log_level)


def _normalize_log_field_name(field_name: str) -> str:
    """로그 필드명을 비교 가능한 소문자 snake_case 형태로 정규화한다."""

    return field_name.strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_log_field(field_name: str) -> bool:
    """필드명이 인증값, URL 또는 DB 접속 정보에 해당하는지 확인한다."""

    normalized_field_name = _normalize_log_field_name(field_name)

    return normalized_field_name in _SENSITIVE_LOG_FIELD_NAMES or (
        normalized_field_name.endswith(
            _SENSITIVE_LOG_FIELD_SUFFIXES,
        )
    )


def _replace_sensitive_assignment(
    match: re.Match[str],
) -> str:
    """민감한 key-value 문자열에서 키와 구분자는 보존하고 값만 교체한다."""

    original_value = match.group("value")

    # JSON 또는 일반 문자열에서 따옴표로 감싼 값은
    # 기존 따옴표를 유지한 상태로 내부 값만 교체한다.
    #
    # 이를 통해 문자열 로그가 JSON 조각을 포함하고 있더라도
    # 마스킹 이후 원래의 표현 구조를 최대한 보존한다.
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
    """자유 형식 문자열에서 Presigned URL, 토큰 및 DB DSN을 제거한다.

    마스킹 순서는 넓은 범위의 값부터 세부적인 값 순서로 적용한다.

    먼저 Presigned URL과 DB DSN 전체를 제거한 뒤,
    key-value 형식의 민감값, Bearer 토큰 및 개별 쿼리 파라미터를 처리한다.

    이 순서를 사용하면 URL 또는 DSN 일부만 마스킹되어
    나머지 자격 증명이나 내부 주소가 남는 상황을 줄일 수 있다.
    """

    redacted_value = _PRESIGNED_URL_PATTERN.sub(
        _REDACTED_PRESIGNED_URL,
        value,
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


def _sanitize_log_value(
    value: object,
    *,
    field_name: str | None = None,
) -> object:
    """중첩 로그 값을 순회하여 민감 필드와 문자열을 안전한 값으로 바꾼다."""

    # 필드명이 민감 정보로 분류되면 값의 데이터 타입을 확인하지 않고
    # 전체 값을 고정된 마스킹 문자열로 교체한다.
    #
    # 이를 통해 DB 포트처럼 정수로 기록되는 접속 정보도 노출하지 않는다.
    if field_name is not None and _is_sensitive_log_field(field_name):
        return _REDACTED_VALUE

    # 일반 문자열 필드는 필드명이 비민감하더라도
    # 값 안에 URL, 토큰 또는 DSN이 포함될 수 있으므로
    # 자유 형식 문자열 마스킹을 추가로 수행한다.
    if isinstance(value, str):
        return _redact_sensitive_text(value)

    # 구조화 extra에 중첩된 Mapping이 포함될 수 있으므로
    # 모든 키와 값을 재귀적으로 순회한다.
    if isinstance(value, Mapping):
        return {
            key: _sanitize_log_value(
                nested_value,
                field_name=key if isinstance(key, str) else None,
            )
            for key, nested_value in value.items()
        }

    # tuple은 원본 불변 자료 구조를 유지한 상태로
    # 각 항목에 동일한 마스킹 규칙을 적용한다.
    if isinstance(value, tuple):
        return tuple(_sanitize_log_value(item) for item in value)

    # list도 항목 순서를 유지한 상태로
    # 각 항목에 동일한 마스킹 규칙을 적용한다.
    if isinstance(value, list):
        return [_sanitize_log_value(item) for item in value]

    # 정수, 실수, bool, None 등 비문자 스칼라 값은
    # 민감 필드명에 속하지 않는 경우 원본 값을 유지한다.
    return value


def _resolve_log_level(log_level: str) -> int:
    """문자열 로그 레벨을 logging 모듈의 정수 값으로 변환한다."""

    normalized_log_level = log_level.strip().upper()

    if not normalized_log_level:
        normalized_log_level = DEFAULT_LOG_LEVEL

    resolved_log_level = logging.getLevelName(normalized_log_level)

    if not isinstance(resolved_log_level, int):
        message = f"Unsupported log level: {log_level!r}"
        raise ValueError(message)

    return resolved_log_level


def _configure_uvicorn_loggers(log_level: int) -> None:
    """Uvicorn 로그가 애플리케이션 JSON 포맷을 사용하도록 구성한다."""

    for logger_name in (
        "uvicorn",
        "uvicorn.error",
    ):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.setLevel(log_level)
        uvicorn_logger.propagate = True

    # HTTP 요청 로그는 RequestLoggingMiddleware에서 기록하므로
    # Uvicorn access log를 비활성화하여 같은 요청이 중복 기록되지 않게 한다.
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.handlers.clear()
    uvicorn_access_logger.propagate = False
    uvicorn_access_logger.disabled = True
