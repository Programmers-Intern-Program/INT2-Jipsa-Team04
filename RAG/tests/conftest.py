from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """FastAPI 테스트 클라이언트를 제공한다."""

    with TestClient(app) as test_client:
        yield test_client
