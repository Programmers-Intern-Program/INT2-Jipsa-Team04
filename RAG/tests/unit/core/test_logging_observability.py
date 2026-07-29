"""로그 설정, 출력 형식, 느린 단계 경고 및 접근 로그 노이즈 제어를 검증한다."""

import json
import logging
from io import StringIO
from time import perf_counter
from typing import cast

from jipsa_rag.core.logging import (
    SensitiveDataConsoleFormatter,
    SensitiveDataJsonFormatter,
    log_debug_lazy,
    log_stage_completed,
    resolve_stage_log_level,
)
from jipsa_rag.core.logging_settings import LoggingSettings
from jipsa_rag.core.middleware import _resolve_access_log_level

_TEST_REQUEST_ID = "11111111-1111-4111-8111-111111111111"
_TEST_PRESIGNED_URL = (
    "https://private-bucket.s3.ap-northeast-2.amazonaws.com/files/test.pdf?"
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
    "X-Amz-Credential=temporary-credential&"
    "X-Amz-Signature=temporary-signature-value"
)


def _create_isolated_logger(
    *,
    stream: StringIO,
    formatter: logging.Formatter,
    level: int = logging.DEBUG,
) -> logging.Logger:
    """루트 로거 상태에 영향을 받지 않는 테스트 전용 로거를 생성한다."""

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    logger = logging.Logger("jipsa_rag.tests.logging")
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)

    return logger


def test_logging_settings_normalize_level_format_and_slow_threshold() -> None:
    """환경 변수 입력과 같은 문자열 설정을 표준 값과 숫자로 검증해야 한다."""

    settings = LoggingSettings.model_validate(
        {
            "log_level": "warn",
            "log_format": "JSON",
            "slow_stage_threshold_ms": "7500.5",
        }
    )

    assert settings.log_level == "WARNING"
    assert settings.log_format == "json"
    assert settings.slow_stage_threshold_ms == 7500.5


def test_logging_settings_select_console_for_local_and_json_for_test() -> None:
    """형식을 명시하지 않으면 실행 환경에 맞는 기본 포맷을 선택해야 한다."""

    settings = LoggingSettings(
        log_level="INFO",
        log_format=None,
    )

    assert settings.resolve_log_format(environment="local") == "console"
    assert settings.resolve_log_format(environment="development") == "console"
    assert settings.resolve_log_format(environment="test") == "json"


def test_console_formatter_keeps_common_context_and_stage_metrics() -> None:
    """콘솔 로그는 공통 추적 필드와 단계별 요약 지표를 일정한 순서로 표시해야 한다."""

    stream = StringIO()
    logger = _create_isolated_logger(
        stream=stream,
        formatter=SensitiveDataConsoleFormatter(
            service_name="Jipsa RAG Service",
            environment="local",
        ),
        level=logging.INFO,
    )

    logger.info(
        "Document embedding completed.",
        extra={
            "event": "document_embedding_completed",
            "request_id": _TEST_REQUEST_ID,
            "stage": "embedding",
            "users_idx": 7,
            "file_idx": 152,
            "chunk_count": 65,
            "batch_count": 3,
            "embedding_dim": 1024,
            "duration_ms": 812.345,
            "slow_stage_threshold_ms": 5000.0,
            "is_slow_stage": False,
        },
    )

    formatted_log = stream.getvalue()

    assert 'service="Jipsa RAG Service"' in formatted_log
    assert "environment=local" in formatted_log
    assert "event=document_embedding_completed" in formatted_log
    assert f"request_id={_TEST_REQUEST_ID}" in formatted_log
    assert "stage=embedding" in formatted_log
    assert "users_idx=7" in formatted_log
    assert "file_idx=152" in formatted_log
    assert "chunk_count=65" in formatted_log
    assert "batch_count=3" in formatted_log
    assert "embedding_dim=1024" in formatted_log
    assert "duration_ms=812.345" in formatted_log
    assert "slow_stage_threshold_ms=5000.0" in formatted_log
    assert "is_slow_stage=false" in formatted_log


def test_json_formatter_keeps_common_context_and_stage_metrics() -> None:
    """JSON 로그도 콘솔과 동일한 공통 추적 필드와 진단 지표를 유지해야 한다."""

    stream = StringIO()
    formatter = SensitiveDataJsonFormatter(
        [
            "asctime",
            "levelname",
            "name",
            "message",
            "request_id",
            "exc_info",
        ],
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        static_fields={
            "service": "Jipsa RAG Service",
            "environment": "test",
        },
    )
    logger = _create_isolated_logger(
        stream=stream,
        formatter=formatter,
        level=logging.INFO,
    )

    logger.info(
        "Local RAG DB and Qdrant indexing completed.",
        extra={
            "event": "file_indexing_completed",
            "request_id": _TEST_REQUEST_ID,
            "stage": "indexing",
            "users_idx": 7,
            "file_idx": 152,
            "rag_document_idx": 81,
            "rag_index_run_idx": 94,
            "chunk_count": 65,
            "duration_ms": 934.12,
        },
    )

    parsed: object = json.loads(stream.getvalue())
    assert isinstance(parsed, dict)
    payload = cast(dict[str, object], parsed)

    assert payload["service"] == "Jipsa RAG Service"
    assert payload["environment"] == "test"
    assert payload["event"] == "file_indexing_completed"
    assert payload["request_id"] == _TEST_REQUEST_ID
    assert payload["stage"] == "indexing"
    assert payload["rag_document_idx"] == 81
    assert payload["rag_index_run_idx"] == 94
    assert payload["chunk_count"] == 65
    assert payload["duration_ms"] == 934.12


def test_stage_log_level_promotes_only_slow_completion_to_warning() -> None:
    """정상 완료라도 임계값 이상이면 WARNING, 미만이면 INFO를 사용해야 한다."""

    assert (
        resolve_stage_log_level(
            duration_ms=4999.999,
            slow_stage_threshold_ms=5000.0,
        )
        == logging.INFO
    )
    assert (
        resolve_stage_log_level(
            duration_ms=5000.0,
            slow_stage_threshold_ms=5000.0,
        )
        == logging.WARNING
    )


def test_log_stage_completed_emits_single_warning_for_slow_stage() -> None:
    """느린 단계는 INFO와 WARNING을 중복 출력하지 않고 WARNING 한 줄만 남겨야 한다."""

    stream = StringIO()
    logger = _create_isolated_logger(
        stream=stream,
        formatter=logging.Formatter("%(levelname)s|%(event)s|%(message)s"),
        level=logging.DEBUG,
    )

    log_stage_completed(
        logger,
        "Slow stage completed.",
        event="slow_stage_completed",
        started_at=perf_counter() - 0.05,
        slow_stage_threshold_ms=1.0,
        extra={
            "stage": "test",
        },
    )

    lines = [line for line in stream.getvalue().splitlines() if line]
    assert lines == ["WARNING|slow_stage_completed|Slow stage completed."]


def test_log_debug_lazy_does_not_build_extra_when_debug_is_disabled() -> None:
    """DEBUG 비활성 상태에서는 진단 필드를 만드는 함수조차 호출하지 않아야 한다."""

    logger = logging.Logger("jipsa_rag.tests.debug-disabled")
    logger.setLevel(logging.INFO)
    factory_called = False

    def build_extra() -> dict[str, object]:
        nonlocal factory_called
        factory_called = True
        return {
            "file_idx": 152,
        }

    log_debug_lazy(
        logger,
        "Debug detail.",
        event="debug_detail",
        extra_factory=build_extra,
    )

    assert factory_called is False


def test_console_formatter_redacts_presigned_url() -> None:
    """기존 Console Formatter의 Presigned URL 마스킹 계약을 유지해야 한다."""

    stream = StringIO()
    logger = _create_isolated_logger(
        stream=stream,
        formatter=SensitiveDataConsoleFormatter(
            service_name="Jipsa RAG Service",
            environment="local",
        ),
        level=logging.INFO,
    )

    logger.info(
        "File download completed: download_url=%s",
        _TEST_PRESIGNED_URL,
        extra={
            "event": "file_download_completed",
            "request_id": _TEST_REQUEST_ID,
            "download_url": _TEST_PRESIGNED_URL,
        },
    )

    formatted_log = stream.getvalue()
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
