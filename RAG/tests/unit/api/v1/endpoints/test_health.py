from uuid import UUID

from fastapi.testclient import TestClient

from jipsa_rag.core.request_context import REQUEST_ID_HEADER


def test_liveness_returns_common_success_response(
    client: TestClient,
) -> None:
    """생존 상태 API가 테스트 환경의 공통 성공 응답을 반환하는지 확인한다."""

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200

    body = response.json()

    # 모든 정상 API 응답이 공유하는 최상위 공통 응답 구조를 확인한다.
    assert body["success"] is True
    assert body["code"] == "SUCCESS"
    assert body["message"] == "RAG service is running."

    # 테스트 실행 시 conftest와 테스트 환경 설정에서 주입한
    # 애플리케이션 정보를 Health 응답이 그대로 반환하는지 확인한다.
    assert body["data"] == {
        "status": "UP",
        "service": "Jipsa RAG Service Test",
        "version": "0.1.0",
        "environment": "test",
    }


def test_liveness_returns_generated_request_id(
    client: TestClient,
) -> None:
    """요청 식별자가 없으면 서버가 새로운 UUID를 생성하는지 확인한다."""

    response = client.get("/api/v1/health/live")

    request_id = response.headers[REQUEST_ID_HEADER]

    # UUID 객체 생성에 성공하고 다시 문자열로 변환한 값이 같으면
    # 서버가 표준 UUID 형식의 요청 식별자를 생성했다는 의미이다.
    assert str(UUID(request_id)) == request_id


def test_liveness_preserves_valid_request_id(
    client: TestClient,
) -> None:
    """상위 서버가 전달한 유효한 요청 식별자를 그대로 사용하는지 확인한다."""

    request_id = "d7fcf7d5-82ab-4466-9131-c238d45e42ac"

    response = client.get(
        "/api/v1/health/live",
        headers={
            REQUEST_ID_HEADER: request_id,
        },
    )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == request_id


def test_liveness_replaces_invalid_request_id(
    client: TestClient,
) -> None:
    """유효하지 않은 요청 식별자를 신뢰하지 않고 새 UUID로 교체하는지 확인한다."""

    invalid_request_id = "invalid-request-id"

    response = client.get(
        "/api/v1/health/live",
        headers={
            REQUEST_ID_HEADER: invalid_request_id,
        },
    )

    generated_request_id = response.headers[REQUEST_ID_HEADER]

    assert response.status_code == 200
    assert generated_request_id != invalid_request_id
    assert str(UUID(generated_request_id)) == generated_request_id
