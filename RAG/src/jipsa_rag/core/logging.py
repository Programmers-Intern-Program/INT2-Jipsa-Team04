import logging
import sys
import time

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
    """애플리케이션 전역 JSON 로깅을 구성한다.

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

    formatter = JsonFormatter(
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
