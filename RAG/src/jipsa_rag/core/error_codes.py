from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """외부 API 오류 응답을 구성하기 위한 고정 오류 정의."""

    status_code: int
    code: str
    message: str


class ErrorCode(Enum):
    """Jipsa RAG 서비스에서 공통으로 사용하는 오류 코드."""

    INVALID_REQUEST = ErrorDefinition(
        status_code=HTTPStatus.BAD_REQUEST,
        code="INVALID_REQUEST",
        message="The request is invalid.",
    )

    INVALID_FILE_URL = ErrorDefinition(
        status_code=HTTPStatus.BAD_REQUEST,
        code="INVALID_FILE_URL",
        message="The file URL is invalid.",
    )

    UNAUTHORIZED = ErrorDefinition(
        status_code=HTTPStatus.UNAUTHORIZED,
        code="UNAUTHORIZED",
        message="Authentication is required.",
    )

    FORBIDDEN = ErrorDefinition(
        status_code=HTTPStatus.FORBIDDEN,
        code="FORBIDDEN",
        message="You do not have permission to access this resource.",
    )

    RESOURCE_NOT_FOUND = ErrorDefinition(
        status_code=HTTPStatus.NOT_FOUND,
        code="RESOURCE_NOT_FOUND",
        message="The requested resource was not found.",
    )

    METHOD_NOT_ALLOWED = ErrorDefinition(
        status_code=HTTPStatus.METHOD_NOT_ALLOWED,
        code="METHOD_NOT_ALLOWED",
        message="The requested HTTP method is not allowed.",
    )

    CONFLICT = ErrorDefinition(
        status_code=HTTPStatus.CONFLICT,
        code="CONFLICT",
        message="The request conflicts with the current resource state.",
    )

    FILE_TOO_LARGE = ErrorDefinition(
        status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        code="FILE_TOO_LARGE",
        message="The file exceeds the maximum allowed size.",
    )

    REQUEST_VALIDATION_FAILED = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="REQUEST_VALIDATION_FAILED",
        message="Request validation failed.",
    )

    INVALID_FILE = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="INVALID_FILE",
        message="The downloaded file is invalid.",
    )

    FILE_HASH_MISMATCH = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="FILE_HASH_MISMATCH",
        message="The downloaded file hash does not match.",
    )

    UNSUPPORTED_FILE_MEDIA_TYPE = ErrorDefinition(
        status_code=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        code="UNSUPPORTED_FILE_MEDIA_TYPE",
        message="The downloaded file type is not supported.",
    )

    TOO_MANY_REQUESTS = ErrorDefinition(
        status_code=HTTPStatus.TOO_MANY_REQUESTS,
        code="TOO_MANY_REQUESTS",
        message="Too many requests have been received.",
    )

    FILE_DOWNLOAD_FAILED = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="FILE_DOWNLOAD_FAILED",
        message="The source file could not be downloaded.",
    )

    SERVICE_UNAVAILABLE = ErrorDefinition(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        code="SERVICE_UNAVAILABLE",
        message="The service is temporarily unavailable.",
    )

    FILE_DOWNLOAD_TIMEOUT = ErrorDefinition(
        status_code=HTTPStatus.GATEWAY_TIMEOUT,
        code="FILE_DOWNLOAD_TIMEOUT",
        message="The source file download timed out.",
    )

    INTERNAL_SERVER_ERROR = ErrorDefinition(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="An internal server error occurred.",
    )

    @property
    def status_code(self) -> int:
        """오류에 대응하는 HTTP 상태 코드를 반환한다."""

        return int(self.value.status_code)

    @property
    def code(self) -> str:
        """외부 응답에서 사용할 오류 코드 문자열을 반환한다."""

        return self.value.code

    @property
    def message(self) -> str:
        """외부 응답에서 사용할 기본 오류 메시지를 반환한다."""

        return self.value.message
