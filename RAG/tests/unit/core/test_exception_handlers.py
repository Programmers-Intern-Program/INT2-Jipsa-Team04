from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exception_handlers import register_exception_handlers
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.core.middleware import RequestLoggingMiddleware
from jipsa_rag.core.request_context import REQUEST_ID_HEADER


def create_exception_test_app() -> FastAPI:
    """공통 예외 처리 구조를 검증하기 위한 테스트 애플리케이션을 생성한다."""

    application = FastAPI()

    # 운영 애플리케이션과 동일한 순서로 미들웨어와 예외 처리기를 등록한다.
    application.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(application)

    @application.get("/application-exception")
    async def raise_application_exception() -> None:
        """의도적으로 애플리케이션 정의 예외를 발생시킨다."""

        raise AppException(
            ErrorCode.RESOURCE_NOT_FOUND,
            public_message="The requested test resource was not found.",
            log_context={
                "resource_id": 100,
            },
        )

    @application.get("/validation-exception")
    async def validate_query_value(
        value: int,
    ) -> dict[str, int]:
        """정수 Query Parameter 검증을 위한 테스트 응답을 반환한다."""

        return {
            "value": value,
        }

    @application.get("/unexpected-exception")
    async def raise_unexpected_exception() -> None:
        """외부에 노출되면 안 되는 내부 오류를 의도적으로 발생시킨다."""

        raise RuntimeError(
            "Sensitive database password and internal path information.",
        )

    return application


@pytest.fixture
def exception_client() -> Iterator[TestClient]:
    """서버 내부 예외를 HTTP 500 응답으로 확인할 테스트 클라이언트를 제공한다."""

    application = create_exception_test_app()

    # 기본값 True에서는 서버 예외가 테스트 코드로 다시 전달된다.
    # False를 사용해야 실제 클라이언트가 받는 500 응답을 검증할 수 있다.
    with TestClient(
        application,
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


def test_application_exception_returns_common_error_response(
    exception_client: TestClient,
) -> None:
    """애플리케이션 정의 예외가 공통 오류 응답으로 변환되는지 확인한다."""

    response = exception_client.get(
        "/application-exception",
    )

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "code": "RESOURCE_NOT_FOUND",
        "message": "The requested test resource was not found.",
        "data": None,
    }

    request_id = response.headers[REQUEST_ID_HEADER]

    assert str(UUID(request_id)) == request_id


def test_request_validation_exception_returns_safe_error_details(
    exception_client: TestClient,
) -> None:
    """요청값 검증 오류가 안전한 필드 정보만 반환하는지 확인한다."""

    response = exception_client.get(
        "/validation-exception",
        params={
            "value": "not-an-integer",
        },
    )

    assert response.status_code == 422

    body = response.json()

    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["message"] == "Request validation failed."

    assert body["data"]["errors"] == [
        {
            "field": "query.value",
            "message": "Input should be a valid integer, unable to parse string as an integer",
            "error_type": "int_parsing",
        }
    ]

    # 요청 원문 값은 공통 검증 오류 응답에 포함하지 않는다.
    assert "not-an-integer" not in response.text


def test_not_found_exception_returns_common_error_response(
    exception_client: TestClient,
) -> None:
    """존재하지 않는 API 경로가 공통 404 오류 응답을 반환하는지 확인한다."""

    response = exception_client.get(
        "/does-not-exist",
    )

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "code": "RESOURCE_NOT_FOUND",
        "message": "The requested resource was not found.",
        "data": None,
    }


def test_unexpected_exception_hides_internal_details(
    exception_client: TestClient,
) -> None:
    """처리되지 않은 예외의 내부 정보가 외부 응답에 노출되지 않는지 확인한다."""

    response = exception_client.get(
        "/unexpected-exception",
    )

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An internal server error occurred.",
        "data": None,
    }

    # 실제 예외 메시지, 예외 클래스명 및 내부 정보는 응답에 없어야 한다.
    assert "Sensitive database password" not in response.text
    assert "RuntimeError" not in response.text
    assert "internal path" not in response.text

    request_id = response.headers[REQUEST_ID_HEADER]

    assert str(UUID(request_id)) == request_id


def test_error_response_preserves_valid_request_id(
    exception_client: TestClient,
) -> None:
    """오류 응답에서도 상위 서버 요청 식별자가 유지되는지 확인한다."""

    request_id = "1d22203f-1a6c-490a-838c-f90d40324118"

    response = exception_client.get(
        "/application-exception",
        headers={
            REQUEST_ID_HEADER: request_id,
        },
    )

    assert response.headers[REQUEST_ID_HEADER] == request_id
