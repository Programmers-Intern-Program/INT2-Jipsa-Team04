"""실제 ASGI 요청에서 완료 중심 접근 로그와 Health Check 저소음을 검증한다."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from jipsa_rag.core import middleware as middleware_module
from jipsa_rag.core.logging import RequestContextFilter
from jipsa_rag.core.middleware import RequestLoggingMiddleware
from jipsa_rag.core.request_context import REQUEST_ID_HEADER

_REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def _read_log_field(
    record: logging.LogRecord,
    field_name: str,
) -> object:
    """구조화 ``extra``로 추가된 로그 필드를 누락 여부까지 검증하며 읽는다.

    ``logging.LogRecord``의 정적 타입에는 애플리케이션이 ``extra``로 주입한
    ``event``, ``request_id`` 등의 필드가 선언되어 있지 않다. 테스트에서 상수 이름을
    ``getattr()``로 읽으면 Ruff B009 규칙을 위반하고, 직접 속성 접근은
    Mypy strict에서
    정의되지 않은 속성으로 판단될 수 있다. 따라서 실제 저장 위치인 ``__dict__``를
    사용하고, 필드가 누락되면 명확한 AssertionError를 발생시킨다.
    """

    try:
        return record.__dict__[field_name]
    except KeyError as error:
        raise AssertionError(f"로그 필드가 누락되었습니다: {field_name}") from error


class _RecordHandler(logging.Handler):
    """포맷 이전 LogRecord를 순서대로 보관하는 테스트 Handler."""

    def __init__(self) -> None:
        """빈 레코드 목록으로 Handler를 초기화한다."""

        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """원문이나 본문을 직렬화하지 않고 LogRecord 참조만 저장한다."""

        self.records.append(record)


class _StatusApp:
    """요청 경로와 관계없이 지정된 HTTP 상태를 반환하는 최소 ASGI 앱."""

    def __init__(self, status_code: int) -> None:
        """응답에 사용할 상태 코드를 저장한다."""

        self._status_code = status_code

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """TestClient lifespan과 HTTP 요청을 모두 처리한다."""

        if scope["type"] == "lifespan":
            await self._handle_lifespan(
                receive=receive,
                send=send,
            )
            return

        if scope["type"] != "http":
            return

        await send(
            {
                "type": "http.response.start",
                "status": self._status_code,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"ok",
            }
        )

    async def _handle_lifespan(
        self,
        *,
        receive: Receive,
        send: Send,
    ) -> None:
        """TestClient의 시작과 종료 handshake에 응답한다."""

        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


@contextmanager
def _capture_middleware_records(
    *,
    level: int,
) -> Iterator[list[logging.LogRecord]]:
    """미들웨어 Logger 상태를 격리하고 테스트 종료 후 정확히 복원한다."""

    target_logger = middleware_module.logger
    previous_handlers = tuple(target_logger.handlers)
    previous_level = target_logger.level
    previous_propagate = target_logger.propagate
    previous_disabled = target_logger.disabled

    handler = _RecordHandler()
    handler.addFilter(RequestContextFilter())

    target_logger.handlers.clear()
    target_logger.addHandler(handler)
    target_logger.setLevel(level)
    target_logger.propagate = False
    target_logger.disabled = False

    try:
        yield handler.records
    finally:
        target_logger.handlers.clear()
        target_logger.handlers.extend(previous_handlers)
        target_logger.setLevel(previous_level)
        target_logger.propagate = previous_propagate
        target_logger.disabled = previous_disabled
        handler.close()


def _build_client(status_code: int) -> TestClient:
    """지정 상태를 반환하는 앱에 실제 요청 로깅 미들웨어를 적용한다."""

    app: ASGIApp = RequestLoggingMiddleware(_StatusApp(status_code))
    return TestClient(app)


def test_successful_normal_request_emits_only_completion_at_info() -> None:
    """INFO 운영에서는 정상 요청 시작 로그 없이 완료 로그 한 줄만 남겨야 한다."""

    with (
        _capture_middleware_records(level=logging.INFO) as records,
        _build_client(200) as client,
    ):
        response = client.get(
            "/api/v1/files/process",
            headers={
                REQUEST_ID_HEADER: _REQUEST_ID,
            },
        )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == _REQUEST_ID
    assert [_read_log_field(record, "event") for record in records] == ["http_request_completed"]

    completed_record = records[0]
    assert completed_record.levelno == logging.INFO
    assert _read_log_field(completed_record, "request_id") == _REQUEST_ID
    assert _read_log_field(completed_record, "method") == "GET"
    assert _read_log_field(completed_record, "path") == "/api/v1/files/process"
    assert _read_log_field(completed_record, "status_code") == 200
    assert isinstance(_read_log_field(completed_record, "duration_ms"), float)

    # 접근 로그는 요청·응답 본문을 수집하지 않는다.
    assert "request_body" not in completed_record.__dict__
    assert "response_body" not in completed_record.__dict__


def test_successful_health_check_is_omitted_at_info() -> None:
    """정상 Health Check는 INFO Logger에서 실제 출력 레코드를 만들지 않아야 한다."""

    with (
        _capture_middleware_records(level=logging.INFO) as records,
        _build_client(200) as client,
    ):
        response = client.get(
            "/api/v1/health/live",
            headers={
                REQUEST_ID_HEADER: _REQUEST_ID,
            },
        )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == _REQUEST_ID
    assert records == []


def test_health_check_error_remains_visible_at_error() -> None:
    """Health Check 실패는 저소음 정책과 관계없이 ERROR로 남아야 한다."""

    with (
        _capture_middleware_records(level=logging.INFO) as records,
        _build_client(503) as client,
    ):
        response = client.get(
            "/api/v1/health/ready",
            headers={
                REQUEST_ID_HEADER: _REQUEST_ID,
            },
        )

    assert response.status_code == 503
    assert [_read_log_field(record, "event") for record in records] == ["http_request_completed"]
    assert records[0].levelno == logging.ERROR
    assert _read_log_field(records[0], "status_code") == 503


def test_debug_mode_keeps_health_start_and_completion_for_diagnosis() -> None:
    """DEBUG를 명시한 경우에는 Health Check 요청 흐름을 두 단계로 확인할 수 있다."""

    with (
        _capture_middleware_records(level=logging.DEBUG) as records,
        _build_client(200) as client,
    ):
        response = client.get(
            "/api/v1/health/live",
            headers={
                REQUEST_ID_HEADER: _REQUEST_ID,
            },
        )

    assert response.status_code == 200
    assert [_read_log_field(record, "event") for record in records] == [
        "http_request_started",
        "http_request_completed",
    ]
    assert all(record.levelno == logging.DEBUG for record in records)
    assert all(_read_log_field(record, "request_id") == _REQUEST_ID for record in records)
