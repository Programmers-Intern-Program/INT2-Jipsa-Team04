"""RAG 서비스 Health API의 응답을 검증한다."""

from fastapi.testclient import TestClient

from jipsa_rag.core.config import get_settings


def test_liveness_returns_service_status(
    client: TestClient,
) -> None:
    """Liveness API가 현재 애플리케이션 상태를 반환해야 한다."""

    settings = get_settings()

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()

    assert body == {
        "status": "UP",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": "test",
    }

    # conftest.py에서 지정한 테스트 환경이 실제 Settings에도
    # 동일하게 적용되었는지 함께 확인한다.
    assert settings.app_env == "test"


def test_unknown_health_endpoint_returns_not_found(
    client: TestClient,
) -> None:
    """정의되지 않은 Health API 경로에는 404를 반환해야 한다."""

    response = client.get("/api/v1/health/unknown")

    assert response.status_code == 404