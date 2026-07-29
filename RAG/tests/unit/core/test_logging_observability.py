"""로그 설정, 콘솔 포맷 및 접근 로그 노이즈 제어를 검증한다."""

import logging
from io import StringIO

from jipsa_rag.core.logging import SensitiveDataConsoleFormatter
from jipsa_rag.core.logging_settings import LoggingSettings
from jipsa_rag.core.middleware import _resolve_access_log_level

_TEST_PRESIGNED_URL = (
    "https://private-bucket.s3.ap-northeast-2.amazonaws.com/files/test.pdf?"
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
    "X-Amz-Credential=temporary-credential&"
    "X-Amz-Signature=temporary-signature-value"
)


def test_logging_settings_normalize_level_and_format() -> None:
    """환경 변수 입력과 같은 문자열 설정을 표준 값으로 정규화해야 한다."""

    settings = LoggingSettings.model_validate(
        {
            "log_level": "warn",
            "log_format": "JSON",
        }
    )

    assert settings.log_level == "WARNING"
    assert settings.log_format == "json"


def test_logging_settings_select_console_for_local_and_json_for_test() -> None:
    """형식을 명시하지 않으면 실행 환경에 맞는 기본 포맷을 선택해야 한다."""

    settings = LoggingSettings(
        log_level="INFO",
        log_format=None,
    )

    assert settings.resolve_log_format(environment="local") == "console"
    assert settings.resolve_log_format(environment="development") == "console"
    assert settings.resolve_log_format(environment="test") == "json"


def test_console_formatter_keeps_required_context_and_redacts_sensitive_values() -> None:
    """콘솔 로그는 공통 추적 필드를 유지하면서 민감 정보를 제거해야 한다."""

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        SensitiveDataConsoleFormatter(
            service_name="Jipsa RAG Service",
            environment="local",
        )
    )

    logger = logging.Logger("jipsa_rag.tests.logging")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)

    logger.info(
        "File download completed: download_url=%s",
        _TEST_PRESIGNED_URL,
        extra={
            "event": "file_download_completed",
            "request_id": "11111111-1111-4111-8111-111111111111",
            "users_idx": 7,
            "file_idx": 152,
            "file_type": "PDF",
            "size_bytes": 4096,
            "duration_ms": 12.345,
            "download_url": _TEST_PRESIGNED_URL,
        },
    )

    formatted_log = stream.getvalue()

    assert 'service="Jipsa RAG Service"' in formatted_log
    assert "environment=local" in formatted_log
    assert "event=file_download_completed" in formatted_log
    assert "request_id=11111111-1111-4111-8111-111111111111" in formatted_log
    assert "users_idx=7" in formatted_log
    assert "file_idx=152" in formatted_log
    assert "file_type=PDF" in formatted_log
    assert "size_bytes=4096" in formatted_log
    assert "duration_ms=12.345" in formatted_log
    assert "download_url=[REDACTED]" in formatted_log
    assert _TEST_PRESIGNED_URL not in formatted_log
    assert "temporary-credential" not in formatted_log
    assert "temporary-signature-value" not in formatted_log


def test_successful_health_check_uses_debug_but_errors_remain_visible() -> None:
    """정상 Health Check만 DEBUG로 낮추고 오류는 WARNING/ERROR를 유지해야 한다."""

    assert (
        _resolve_access_log_level(
            status_code=200,
            path="/api/v1/health/live",
        )
        == logging.DEBUG
    )
    assert (
        _resolve_access_log_level(
            status_code=503,
            path="/api/v1/health/ready",
        )
        == logging.ERROR
    )
    assert (
        _resolve_access_log_level(
            status_code=200,
            path="/api/v1/files/process",
        )
        == logging.INFO
    )
