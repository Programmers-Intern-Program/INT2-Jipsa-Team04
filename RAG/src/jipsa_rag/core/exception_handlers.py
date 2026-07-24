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

# RAG 답변 API 앞에는 환경별 API 버전 prefix가 붙을 수 있다.
#
# 전체 경로를 하드코딩하지 않고 suffix만 비교하면 기본 /api/v1뿐 아니라
# 테스트 또는 배포 환경에서 prefix가 변경되어도 같은 오류 계약을 유지한다.
_RAG_ANSWER_PATH_SUFFIX = "/rag/answers"

# Pydantic 요청 검증 오류의 위치에서 확인할 참조문서 필드명이다.
_REFERENCE_FILE_IDXS_FIELD = "reference_file_idxs"


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

    app_exception = cast(
        AppException,
        exc,
    )

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

    validation_exception = cast(
        RequestValidationError,
        exc,
    )

    # 답변 API에서 참조문서 필드가 없거나 비어 있으면 일반 스키마 오류보다
    # 구체적인 REFERENCE_DOCUMENT_REQUIRED를 반환한다.
    #
    # 이 분기는 검증 오류의 위치, 타입 및 입력 컨테이너의 비어 있음만 확인한다.
    # 질문 원문, 참조문서 식별자 목록 또는 전체 요청 본문은 로그와 응답에
    # 복사하지 않는다.
    if _is_reference_document_required_error(
        request=request,
        exc=validation_exception,
    ):
        logger.warning(
            "RAG answer request did not include a reference document.",
            extra={
                "event": "reference_document_required",
                "request_id": _get_request_id(request),
                "method": request.method,
                "path": request.url.path,
                "status_code": (ErrorCode.REFERENCE_DOCUMENT_REQUIRED.status_code),
                "error_code": (ErrorCode.REFERENCE_DOCUMENT_REQUIRED.code),
            },
        )

        return _create_error_response(
            request=request,
            status_code=(ErrorCode.REFERENCE_DOCUMENT_REQUIRED.status_code),
            error_code=ErrorCode.REFERENCE_DOCUMENT_REQUIRED,
        )

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
            "status_code": (ErrorCode.REQUEST_VALIDATION_FAILED.status_code),
            "error_code": (ErrorCode.REQUEST_VALIDATION_FAILED.code),
            "error_count": len(validation_items),
        },
    )

    response_body = ApiResponse[ValidationErrorData](
        success=False,
        code=ErrorCode.REQUEST_VALIDATION_FAILED.code,
        message=ErrorCode.REQUEST_VALIDATION_FAILED.message,
        data=ValidationErrorData(
            errors=validation_items,
        ),
    )

    return JSONResponse(
        status_code=(ErrorCode.REQUEST_VALIDATION_FAILED.status_code),
        content=response_body.model_dump(
            mode="json",
        ),
        headers=_create_response_headers(
            request,
        ),
    )


async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """라우팅 및 HTTP 계층에서 발생한 예외를 공통 오류 응답으로 변환한다."""

    http_exception = cast(
        StarletteHTTPException,
        exc,
    )

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
            "status_code": (ErrorCode.INTERNAL_SERVER_ERROR.status_code),
            "error_code": (ErrorCode.INTERNAL_SERVER_ERROR.code),
        },
        exc_info=(
            type(exc),
            exc,
            exc.__traceback__,
        ),
    )

    return _create_error_response(
        request=request,
        status_code=(ErrorCode.INTERNAL_SERVER_ERROR.status_code),
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
        content=response_body.model_dump(
            mode="json",
        ),
        headers=_create_response_headers(
            request,
            headers=headers,
        ),
    )


def _is_reference_document_required_error(
    *,
    request: Request,
    exc: RequestValidationError,
) -> bool:
    """답변 API의 참조문서 미선택 검증 오류인지 확인한다.

    다음 입력만 참조문서 미선택으로 분류한다.

    - ``reference_file_idxs`` 필드 자체가 없는 요청
    - ``reference_file_idxs`` 값이 ``null``인 요청
    - ``reference_file_idxs`` 값이 빈 배열 또는 빈 tuple인 요청

    중복 식별자, 0 이하 식별자, 문자열 식별자 및 최대 개수 초과처럼
    참조문서는 전달됐지만 값이 잘못된 경우에는 기존
    ``REQUEST_VALIDATION_FAILED`` 계약을 유지한다.

    검증 오류 객체의 ``input``에는 전체 요청 본문이나 사용자 질문이
    포함될 수 있으므로 값을 문자열로 변환하거나 로그에 기록하지 않는다.
    """

    normalized_path = request.url.path.rstrip(
        "/",
    )

    if not normalized_path.endswith(
        _RAG_ANSWER_PATH_SUFFIX,
    ):
        return False

    for error in exc.errors():
        location = tuple(
            error.get(
                "loc",
                (),
            )
        )

        if not location or location[-1] != _REFERENCE_FILE_IDXS_FIELD:
            continue

        error_type = str(
            error.get(
                "type",
                "",
            )
        )

        if error_type == "missing":
            return True

        input_value = error.get(
            "input",
        )

        if input_value is None:
            return True

        if (
            isinstance(
                input_value,
                (
                    list,
                    tuple,
                ),
            )
            and not input_value
        ):
            return True

    return False


def _create_validation_error_items(
    exc: RequestValidationError,
) -> list[ValidationErrorItem]:
    """FastAPI 검증 오류에서 외부 공개가 가능한 정보만 추출한다."""

    validation_items: list[ValidationErrorItem] = []

    for error in exc.errors():
        location = ".".join(
            str(location_part)
            for location_part in error.get(
                "loc",
                (),
            )
        )

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


def _resolve_http_error_code(
    status_code: int,
) -> ErrorCode:
    """HTTP 상태 코드에 대응하는 공통 오류 코드를 반환한다."""

    matched_error_code = HTTP_STATUS_ERROR_CODES.get(
        status_code,
    )

    if matched_error_code is not None:
        return matched_error_code

    if 400 <= status_code < 500:
        return ErrorCode.INVALID_REQUEST

    return ErrorCode.INTERNAL_SERVER_ERROR


def _get_request_id(
    request: Request,
) -> str | None:
    """요청 상태 또는 현재 실행 컨텍스트의 요청 식별자를 반환한다."""

    request_state_id = getattr(
        request.state,
        "request_id",
        None,
    )

    if isinstance(
        request_state_id,
        str,
    ):
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

    response_headers = dict(
        headers or {},
    )

    request_id = _get_request_id(
        request,
    )

    if request_id is not None:
        response_headers[REQUEST_ID_HEADER] = request_id

    return response_headers
