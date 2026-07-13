from fastapi.testclient import TestClient


def test_liveness_returns_service_status(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "UP"
    assert body["service"] == "Jipsa RAG Service"
    assert body["version"] == "0.1.0"
    assert body["environment"] in {
        "local",
        "development",
        "test",
        "production",
    }
