"""POST /ingest 엔드포인트와 내부 토큰 검증을 테스트한다."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from jipsa_rag.core.config import get_settings
from jipsa_rag.main import app


@contextmanager
def without_default_internal_token(
    client: TestClient,
) -> Iterator[None]:
    """TestClient의 기본 내부 토큰 헤더를 테스트 중에만 제거한다.

    tests/conftest.py는 기존 파일 처리 테스트가 모두 인증을 통과하도록
    TestClient에 기본 X-Internal-Token 헤더를 설정한다.

    인증 누락 케이스를 검증하는 테스트에서는 해당 헤더를 잠시 제거하고,
    테스트 종료 후 반드시 원래 값으로 복구한다.
    """

    original_token = client.headers.get("X-Internal-Token")

    if original_token is not None:
        del client.headers["X-Internal-Token"]

    try:
        yield
    finally:
        if original_token is not None:
            client.headers["X-Internal-Token"] = original_token


def test_rag_ingest_token_is_loaded_as_secret(
    client: TestClient,
) -> None:
    """환경 변수의 내부 토큰이 SecretStr로 안전하게 로드되어야 한다."""

    settings = get_settings()
    configured_token = settings.rag_ingest_token

    assert configured_token is not None
    assert configured_token.get_secret_value() == client.headers["X-Internal-Token"]

    # Settings 객체 표현에는 실제 토큰 원문이 포함되면 안 된다.
    assert configured_token.get_secret_value() not in repr(settings)


def test_ingest_route_accepts_valid_internal_token(
    client: TestClient,
) -> None:
    """정상 내부 토큰이면 인증을 통과하고 요청 본문 검증까지 진행해야 한다."""

    # 빈 JSON을 전달하여 실제 파일 다운로드, TEI 요청, DB 저장 또는
    # Qdrant 저장을 실행하지 않고 라우트 등록과 인증 통과만 검증한다.
    #
    # 인증이 성공하면 다음 단계인 FileProcessingRequest 검증에서
    # 필수 필드 누락으로 422가 반환되어야 한다.
    response = client.post(
        "/ingest",
        json={},
    )

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["message"] == "Request validation failed."


def test_ingest_rejects_missing_internal_token(
    client: TestClient,
) -> None:
    """X-Internal-Token 헤더가 없으면 요청 본문 처리 전에 거부해야 한다."""

    with without_default_internal_token(client):
        response = client.post(
            "/ingest",
            json={},
        )

    assert response.status_code == 401
    assert response.json() == {
        "success": False,
        "code": "UNAUTHORIZED",
        "message": "Authentication is required.",
        "data": None,
    }


def test_ingest_rejects_invalid_internal_token(
    client: TestClient,
) -> None:
    """내부 토큰이 일치하지 않으면 401 오류를 반환해야 한다."""

    invalid_token = "invalid-internal-token-value-that-must-not-leak"

    response = client.post(
        "/ingest",
        headers={
            "X-Internal-Token": invalid_token,
        },
        json={},
    )

    assert response.status_code == 401
    assert response.json() == {
        "success": False,
        "code": "UNAUTHORIZED",
        "message": "Authentication is required.",
        "data": None,
    }

    # 전달된 토큰 원문이 오류 응답 본문에 포함되면 안 된다.
    assert invalid_token not in response.text


def test_existing_file_processing_route_rejects_missing_internal_token(
    client: TestClient,
) -> None:
    """기존 파일 처리 경로를 통한 내부 인증 우회를 허용하지 않아야 한다."""

    with without_default_internal_token(client):
        response = client.post(
            "/api/v1/files/process",
            json={},
        )

    assert response.status_code == 401
    assert response.json() == {
        "success": False,
        "code": "UNAUTHORIZED",
        "message": "Authentication is required.",
        "data": None,
    }


def test_health_route_does_not_require_internal_token(
    client: TestClient,
) -> None:
    """Liveness 헬스 체크는 내부 토큰 없이 접근할 수 있어야 한다."""

    with without_default_internal_token(client):
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_ingest_fails_closed_when_server_token_is_not_configured(
    client: TestClient,
) -> None:
    """서버 토큰이 미설정이면 인증을 우회하지 않고 503을 반환해야 한다."""

    # 기존 Settings 값을 유지하되 내부 토큰만 미설정 상태로 만든다.
    #
    # model_copy의 update는 테스트에서 의도한 서버 설정 상태를
    # 명확하게 구성하기 위해 사용한다.
    settings_without_token = get_settings().model_copy(
        update={
            "rag_ingest_token": None,
        }
    )

    # verify_rag_ingest_token이 사용하는 get_settings 의존성만
    # 테스트용 Settings 객체로 교체한다.
    app.dependency_overrides[get_settings] = lambda: settings_without_token

    try:
        response = client.post(
            "/ingest",
            json={},
        )
    finally:
        # 다른 테스트가 토큰 미설정 Settings를 재사용하지 않도록
        # dependency override를 반드시 제거한다.
        app.dependency_overrides.pop(
            get_settings,
            None,
        )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "code": "SERVICE_UNAVAILABLE",
        "message": "The service is temporarily unavailable.",
        "data": None,
    }
