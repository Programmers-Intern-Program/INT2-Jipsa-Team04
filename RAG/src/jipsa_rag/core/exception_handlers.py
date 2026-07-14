import logging
from collections.abc import Mapping
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.core.request_context import (
    REQUEST_ID_HEADER,
)
from jipsa_rag.core.request_context import (
    get_request_id as get_context_request_id,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
    ValidationErrorItem,
)

logger = logging.getLogger(__name__)

HTTP_STATUS_ERROR_CODES = {error_code.status_code: error_code for error_code in ErrorCode}


def register_exception_handlers(app: FastAPI) -> None:
    """FastAPI 애플리케이션에 공통 예외 처리기를 등록한다."""

    app.add_exception_handler(
        AppException,
        app_exception_handler,
    )
    app.add_exception_handler(
        RequestValidationError,
        request_validation_exception_handler,
    )
    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,
    )
    app.add_exception_handler(
        Exception,
        unexpected_exception_handler,
    )


async def app_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """의도적으로 발생한 애플리케이션 예외를 공통 오류 응답으로 변환한다."""

    app_exception = cast(AppException, exc)

    log_extra = {
        "event": "application_exception",
        "request_id": _get_request_id(request),
        "method": request.method,
        "path": request.url.path,
        "status_code": app_exception.status_code,
        "error_code": app_exception.code,
        "context": app_exception.log_context,
    }

    if app_exception.status_code >= 500:
        logger.error(
            "Application exception occurred.",
            extra=log_extra,
            exc_info=(
                type(app_exception),
                app_exception,
                app_exception.__traceback__,
            ),
        )
    else:
        logger.warning(
            "Application exception occurred.",
            extra=log_extra,
        )

    return _create_error_response(
        request=request,
        status_code=app_exception.status_code,
        error_code=app_exception.error_code,
        message=app_exception.public_message,
    )


async def request_validation_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """FastAPI 요청값 검증 오류를 안전한 공통 오류 응답으로 변환한다."""

    validation_exception = cast(RequestValidationError, exc)

    validation_items = _create_validation_error_items(
        validation_exception,
    )

    logger.warning(
        "Request validation failed.",
        extra={
            "event": "request_validation_failed",
            "request_id": _get_request_id(request),
            "method": request.method,
            "path": request.url.path,
            "status_code": ErrorCode.REQUEST_VALIDATION_FAILED.status_code,
            "error_code": ErrorCode.REQUEST_VALIDATION_FAILED.code,
            "error_count": len(validation_items),
        },
    )

    response_body = ApiResponse[ValidationErrorData](
        success=False,
        code=ErrorCode.REQUEST_VALIDATION_FAILED.code,
        message=ErrorCode.REQUEST_VALIDATION_FAILED.message,
        data=ValidationErrorData(errors=validation_items),
    )

    return JSONResponse(
        status_code=ErrorCode.REQUEST_VALIDATION_FAILED.status_code,
        content=response_body.model_dump(mode="json"),
        headers=_create_response_headers(request),
    )


async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """라우팅 및 HTTP 계층에서 발생한 예외를 공통 오류 응답으로 변환한다."""

    http_exception = cast(StarletteHTTPException, exc)

    error_code = _resolve_http_error_code(
        http_exception.status_code,
    )

    log_extra = {
        "event": "http_exception",
        "request_id": _get_request_id(request),
        "method": request.method,
        "path": request.url.path,
        "status_code": http_exception.status_code,
        "error_code": error_code.code,
    }

    if http_exception.status_code >= 500:
        logger.error(
            "HTTP exception occurred.",
            extra=log_extra,
            exc_info=(
                type(http_exception),
                http_exception,
                http_exception.__traceback__,
            ),
        )
    else:
        logger.warning(
            "HTTP exception occurred.",
            extra=log_extra,
        )

    return _create_error_response(
        request=request,
        status_code=http_exception.status_code,
        error_code=error_code,
        headers=http_exception.headers,
    )


async def unexpected_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """예상하지 못한 예외를 내부 서버 오류 응답으로 변환한다."""

    logger.error(
        "Unexpected exception occurred.",
        extra={
            "event": "unexpected_exception",
            "request_id": _get_request_id(request),
            "method": request.method,
            "path": request.url.path,
            "status_code": ErrorCode.INTERNAL_SERVER_ERROR.status_code,
            "error_code": ErrorCode.INTERNAL_SERVER_ERROR.code,
        },
        exc_info=(
            type(exc),
            exc,
            exc.__traceback__,
        ),
    )

    return _create_error_response(
        request=request,
        status_code=ErrorCode.INTERNAL_SERVER_ERROR.status_code,
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
    )


def _create_error_response(
    *,
    request: Request,
    status_code: int,
    error_code: ErrorCode,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """공통 오류 응답을 생성한다."""

    response_body = ApiResponse[None](
        success=False,
        code=error_code.code,
        message=message or error_code.message,
        data=None,
    )

    return JSONResponse(
        status_code=status_code,
        content=response_body.model_dump(mode="json"),
        headers=_create_response_headers(
            request,
            headers=headers,
        ),
    )


def _create_validation_error_items(
    exc: RequestValidationError,
) -> list[ValidationErrorItem]:
    """FastAPI 검증 오류에서 외부 공개가 가능한 정보만 추출한다."""

    validation_items: list[ValidationErrorItem] = []

    for error in exc.errors():
        location = ".".join(str(location_part) for location_part in error.get("loc", ()))

        validation_items.append(
            ValidationErrorItem(
                field=location or "request",
                message=str(
                    error.get(
                        "msg",
                        "Invalid request value.",
                    )
                ),
                error_type=str(
                    error.get(
                        "type",
                        "value_error",
                    )
                ),
            )
        )

    return validation_items


def _resolve_http_error_code(status_code: int) -> ErrorCode:
    """HTTP 상태 코드에 대응하는 공통 오류 코드를 반환한다."""

    matched_error_code = HTTP_STATUS_ERROR_CODES.get(status_code)

    if matched_error_code is not None:
        return matched_error_code

    if 400 <= status_code < 500:
        return ErrorCode.INVALID_REQUEST

    return ErrorCode.INTERNAL_SERVER_ERROR


def _get_request_id(request: Request) -> str | None:
    """요청 상태 또는 현재 실행 컨텍스트의 요청 식별자를 반환한다."""

    request_state_id = getattr(
        request.state,
        "request_id",
        None,
    )

    if isinstance(request_state_id, str):
        normalized_request_id = request_state_id.strip()

        if normalized_request_id:
            return normalized_request_id

    return get_context_request_id()


def _create_response_headers(
    request: Request,
    *,
    headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """기존 HTTP 헤더와 요청 식별자 헤더를 결합한다."""

    response_headers = dict(headers or {})

    request_id = _get_request_id(request)

    if request_id is not None:
        response_headers[REQUEST_ID_HEADER] = request_id

    return response_headers
