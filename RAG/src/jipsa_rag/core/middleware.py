import logging
from time import perf_counter

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jipsa_rag.core.request_context import (
    REQUEST_ID_HEADER,
    reset_request_id,
    resolve_request_id,
    set_request_id,
)

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    """HTTP 요청 식별자와 접근 로그를 관리하는 ASGI 미들웨어."""

    def __init__(self, app: ASGIApp) -> None:
        """다음 ASGI 애플리케이션을 저장한다."""

        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """HTTP 요청을 추적하고 요청 처리 결과를 로그로 기록한다."""

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        request_id = resolve_request_id(
            request.headers.get(REQUEST_ID_HEADER),
        )

        # FastAPI 예외 처리기와 엔드포인트에서도 같은 요청 식별자를
        # 사용할 수 있도록 ASGI 요청 상태에 저장한다.
        scope.setdefault("state", {})["request_id"] = request_id

        request_context_token = set_request_id(request_id)
        started_at = perf_counter()

        status_code: int | None = None
        response_started = False

        logger.info(
            "HTTP request started.",
            extra={
                "event": "http_request_started",
                "method": request.method,
                "path": request.url.path,
            },
        )

        async def send_with_request_id(message: Message) -> None:
            """응답 상태 코드를 수집하고 요청 식별자 헤더를 추가한다."""

            nonlocal response_started
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True

                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id

            await send(message)

        try:
            await self.app(
                scope,
                receive,
                send_with_request_id,
            )
        except Exception:
            duration_ms = _calculate_duration_ms(started_at)

            # 상세 스택 트레이스는 전역 예외 처리기에서 한 번만 기록한다.
            # 미들웨어는 요청 단위 실패 정보만 기록하여 중복 로그를 줄인다.
            logger.error(
                "HTTP request failed before completion.",
                extra={
                    "event": "http_request_failed",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code or 500,
                    "duration_ms": duration_ms,
                    "response_started": response_started,
                },
            )

            raise
        else:
            completed_status_code = status_code or 500
            duration_ms = _calculate_duration_ms(started_at)

            logger.log(
                _resolve_access_log_level(completed_status_code),
                "HTTP request completed.",
                extra={
                    "event": "http_request_completed",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": completed_status_code,
                    "duration_ms": duration_ms,
                },
            )
        finally:
            # 비동기 워커가 다음 요청을 처리할 때 이전 요청 식별자가
            # 남아 있지 않도록 반드시 실행 컨텍스트를 복원한다.
            reset_request_id(request_context_token)


def _calculate_duration_ms(started_at: float) -> float:
    """요청 처리 시간을 밀리초 단위로 계산한다."""

    duration_seconds = perf_counter() - started_at

    return round(duration_seconds * 1000, 3)


def _resolve_access_log_level(status_code: int) -> int:
    """HTTP 상태 코드에 대응하는 접근 로그 레벨을 반환한다."""

    if status_code >= 500:
        return logging.ERROR

    if status_code >= 400:
        return logging.WARNING

    return logging.INFO
