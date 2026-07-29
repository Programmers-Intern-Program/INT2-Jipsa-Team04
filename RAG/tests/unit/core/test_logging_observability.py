"""세계적 수준의 Console·JSON 로그 품질과 저소음 관측성 계약을 검증한다."""

import json
import logging
import re
from io import StringIO
from time import perf_counter
from typing import cast

import pytest

from jipsa_rag.core.logging import (
    RequestContextFilter,
    SensitiveDataConsoleFormatter,
    SensitiveDataJsonFormatter,
    _resolve_console_color_enabled,
    log_debug_lazy,
    log_stage_completed,
    resolve_stage_log_level,
)
from jipsa_rag.core.logging_settings import LoggingSettings
from jipsa_rag.core.middleware import _resolve_access_log_level

_TEST_REQUEST_ID = "cf729dcf-df11-423a-bc00-5840557a0454"
_TEST_PRESIGNED_URL = (
    "https://private-bucket.s3.ap-northeast-2.amazonaws.com/files/test.pdf?"
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
    "X-Amz-Credential=temporary-credential&"
    "X-Amz-Signature=temporary-signature-value"
)


class _InteractiveStringIO(StringIO):
    """TTY 자동 색상 결정 테스트에 사용하는 대화형 스트림 대역."""

    def isatty(self) -> bool:
        """실제 PowerShell 터미널처럼 대화형 스트림임을 반환한다."""

        return True


def _create_isolated_logger(
    *,
    stream: StringIO,
    formatter: logging.Formatter,
    level: int = logging.DEBUG,
    add_context_filter: bool = False,
) -> logging.Logger:
    """루트 로거 상태에 영향을 받지 않는 테스트 전용 로거를 생성한다."""

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    if add_context_filter:
        handler.addFilter(RequestContextFilter())

    logger = logging.Logger("jipsa_rag.tests.logging")
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)

    return logger


def _create_record(
    *,
    name: str,
    level: int,
    message: str,
    created: float,
    extra: dict[str, object],
) -> logging.LogRecord:
    """결정적인 시각과 extra 필드를 가진 LogRecord를 생성한다."""

    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.created = created
    record.__dict__.update(extra)

    return record


def test_logging_settings_normalize_world_class_console_options() -> None:
    """환경 변수와 같은 입력을 표준 로그 설정으로 정규화해야 한다."""

    settings = LoggingSettings.model_validate(
        {
            "log_level": "warn",
            "log_format": "CONSOLE",
            "log_console_timezone": "UTC",
            "log_color": "NEVER",
            "log_request_id_length": "12",
            "log_third_party_level": "error",
            "slow_stage_threshold_ms": "7500.5",
        }
    )

    assert settings.log_level == "WARNING"
    assert settings.log_format == "console"
    assert settings.log_console_timezone == "utc"
    assert settings.log_color == "never"
    assert settings.log_request_id_length == 12
    assert settings.log_third_party_level == "ERROR"
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


def test_console_formatter_produces_compact_deterministic_stage_log() -> None:
    """Console 로그는 핵심 정보를 짧은 고정 순서와 사람이 읽는 단위로 표시해야 한다."""

    formatter = SensitiveDataConsoleFormatter(
        service_name="Jipsa RAG Service",
        environment="local",
        timezone="utc",
        request_id_length=8,
        use_color=False,
    )
    record = _create_record(
        name="jipsa_rag.api.v1.endpoints.file_processing",
        level=logging.INFO,
        message="File download completed.",
        created=1753769635.910,
        extra={
            "event": "file_download_completed",
            "request_id": _TEST_REQUEST_ID,
            "stage": "download",
            "users_idx": 95223,
            "file_idx": 952301,
            "file_type": "pdf",
            "size_bytes": 2144,
            "duration_ms": 1.91,
            "slow_stage_threshold_ms": 5000.0,
            "is_slow_stage": False,
        },
    )

    formatted_log = formatter.format(record)

    assert formatted_log == (
        "2025-07-29 06:13:55.910+00:00 INFO     "
        "[jipsa-rag/local] [file-processing] "
        "file_download_completed req=cf729dcf | File download completed. | "
        "user=95223 file=952301 type=pdf size=2.09KiB duration=1.91ms"
    )

    # stage와 정상 임계값 필드는 이벤트 및 duration과 중복되므로 Console에서 제외한다.
    assert "stage=" not in formatted_log
    assert "slow_stage_threshold_ms" not in formatted_log
    assert "is_slow_stage" not in formatted_log


def test_console_formatter_highlights_slow_stage_without_duplicate_noise() -> None:
    """느린 단계는 WARNING과 임계값을 표시하되 불필요한 bool 필드는 숨겨야 한다."""

    formatter = SensitiveDataConsoleFormatter(
        service_name="Jipsa RAG Service",
        environment="local",
        timezone="utc",
        request_id_length=8,
        use_color=False,
    )
    record = _create_record(
        name="jipsa_rag.api.v1.endpoints.file_processing",
        level=logging.WARNING,
        message="Document parsing and OCR phase completed.",
        created=1753769635.910,
        extra={
            "event": "document_parsing_ocr_completed",
            "request_id": _TEST_REQUEST_ID,
            "stage": "parsing_ocr",
            "file_idx": 952306,
            "file_type": "PDF",
            "structure_unit_count": 2,
            "text_unit_count": 2,
            "duration_ms": 5730.275,
            "slow_stage_threshold_ms": 5000.0,
            "is_slow_stage": True,
        },
    )

    formatted_log = formatter.format(record)

    assert "WARNING " in formatted_log
    assert "file=952306" in formatted_log
    assert "units=2" in formatted_log
    assert "text_units=2" in formatted_log
    assert "duration=5.73s" in formatted_log
    assert "slow_threshold=5s" in formatted_log
    assert "is_slow_stage" not in formatted_log


def test_console_formatter_removes_ansi_and_prevents_log_injection() -> None:
    """메시지의 ANSI와 줄바꿈이 다음 로그 행처럼 보이지 않도록 정제해야 한다."""

    stream = StringIO()
    logger = _create_isolated_logger(
        stream=stream,
        formatter=SensitiveDataConsoleFormatter(
            service_name="Jipsa RAG Service",
            environment="local",
            timezone="utc",
            use_color=False,
        ),
        level=logging.INFO,
    )

    logger.info(
        "\x1b[31mUpstream failed\x1b[0m\nFAKE ERROR token",
        extra={
            "event": "upstream_message",
            "request_id": _TEST_REQUEST_ID,
        },
    )

    formatted_log = stream.getvalue().rstrip("\n")

    assert "\x1b" not in formatted_log
    assert "Upstream failed\\nFAKE ERROR token" in formatted_log
    assert len(formatted_log.splitlines()) == 1


def test_console_formatter_drops_uvicorn_color_message() -> None:
    """Uvicorn color_message는 실제 message와 중복되므로 출력하지 않아야 한다."""

    stream = StringIO()
    logger = logging.Logger("uvicorn.error")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        SensitiveDataConsoleFormatter(
            service_name="Jipsa RAG Service",
            environment="local",
            timezone="utc",
            use_color=False,
        )
    )
    handler.addFilter(RequestContextFilter())
    logger.addHandler(handler)

    logger.info(
        "Uvicorn running on http://0.0.0.0:8077",
        extra={
            "color_message": "\x1b[1mUvicorn running on %s\x1b[0m",
        },
    )

    formatted_log = stream.getvalue()

    assert "server.lifecycle" in formatted_log
    assert "color_message" not in formatted_log
    assert "\\u001b" not in formatted_log
    assert formatted_log.count("Uvicorn running") == 1


def test_json_formatter_uses_rfc3339_and_stable_schema() -> None:
    """JSON 로그는 RFC 3339 UTC 시각과 버전이 있는 구조화 계약을 유지해야 한다."""

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
            "log_schema_version": 1,
            "service": "Jipsa RAG Service",
            "environment": "production",
        },
    )
    record = _create_record(
        name="jipsa_rag.api.ingest",
        level=logging.INFO,
        message="Application server manifest fetch completed.",
        created=1753769635.910,
        extra={
            "event": "ingest_manifest_fetch_completed",
            "request_id": _TEST_REQUEST_ID,
            "file_idx": 952301,
            "duration_ms": 0.722,
        },
    )

    payload_object: object = json.loads(formatter.format(record))
    assert isinstance(payload_object, dict)
    payload = cast(dict[str, object], payload_object)

    assert payload["timestamp"] == "2025-07-29T06:13:55.910Z"
    assert payload["log_schema_version"] == 1
    assert payload["service"] == "Jipsa RAG Service"
    assert payload["environment"] == "production"
    assert payload["event"] == "ingest_manifest_fetch_completed"
    assert payload["request_id"] == _TEST_REQUEST_ID
    assert payload["file_idx"] == 952301
    assert payload["duration_ms"] == 0.722


def test_json_formatter_removes_color_message_and_prohibited_fields() -> None:
    """JSON에서도 중복 색상 필드와 원문·벡터 필드를 완전히 제외해야 한다."""

    formatter = SensitiveDataJsonFormatter(
        [
            "asctime",
            "levelname",
            "name",
            "message",
            "request_id",
            "exc_info",
        ]
    )
    record = _create_record(
        name="uvicorn.error",
        level=logging.INFO,
        message="Server started.",
        created=1753769635.910,
        extra={
            "event": "server.lifecycle",
            "request_id": None,
            "color_message": "\x1b[32mServer started.\x1b[0m",
            "content": "private chunk",
            "embedding_vector": [0.1, 0.2, 0.3],
        },
    )

    payload_object: object = json.loads(formatter.format(record))
    assert isinstance(payload_object, dict)
    payload = cast(dict[str, object], payload_object)

    assert "color_message" not in payload
    assert "content" not in payload
    assert "embedding_vector" not in payload


def test_console_formatter_redacts_presigned_url() -> None:
    """Console 메시지와 extra의 Presigned URL을 모두 마스킹해야 한다."""

    stream = StringIO()
    logger = _create_isolated_logger(
        stream=stream,
        formatter=SensitiveDataConsoleFormatter(
            service_name="Jipsa RAG Service",
            environment="local",
            timezone="utc",
            use_color=False,
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
    """DEBUG 비활성 상태에서는 진단 필드를 만드는 함수도 호출하지 않아야 한다."""

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


def test_successful_health_check_uses_debug_but_errors_remain_visible() -> None:
    """정상 Health Check만 DEBUG로 낮추고 오류는 WARNING/ERROR로 유지해야 한다."""

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


def test_console_output_contains_no_raw_control_sequences() -> None:
    """대표 Console 로그는 ANSI 비활성 상태에서 제어 시퀀스를 포함하지 않아야 한다."""

    formatter = SensitiveDataConsoleFormatter(
        service_name="Jipsa RAG Service",
        environment="local",
        timezone="utc",
        request_id_length=8,
        use_color=False,
    )
    record = _create_record(
        name="jipsa_rag.main",
        level=logging.INFO,
        message="Application startup completed.",
        created=1753769635.910,
        extra={
            "event": "application_startup_completed",
            "request_id": None,
        },
    )

    formatted_log = formatter.format(record)

    assert re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", formatted_log) is None


def test_console_color_auto_respects_tty_and_no_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto 색상은 TTY에서만 켜지고 NO_COLOR가 있으면 반드시 꺼져야 한다."""

    stream = _InteractiveStringIO()

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _resolve_console_color_enabled(mode="auto", stream=stream) is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert _resolve_console_color_enabled(mode="always", stream=stream) is False


def test_console_formatter_truncates_oversized_extra_value() -> None:
    """대형 extra 값은 한 줄 로그의 메모리와 가독성 예산을 넘지 않아야 한다."""

    formatter = SensitiveDataConsoleFormatter(
        service_name="Jipsa RAG Service",
        environment="local",
        timezone="utc",
        use_color=False,
    )
    record = _create_record(
        name="jipsa_rag.services.example",
        level=logging.INFO,
        message="Diagnostic summary.",
        created=1753769635.910,
        extra={
            "event": "diagnostic_summary",
            "request_id": _TEST_REQUEST_ID,
            "diagnostic": "x" * 500,
        },
    )

    formatted_log = formatter.format(record)

    assert "...<truncated>" in formatted_log
    assert "x" * 300 not in formatted_log
