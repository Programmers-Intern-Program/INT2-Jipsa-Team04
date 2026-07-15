"""pytest 공통 테스트 환경과 fixture를 정의한다."""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

# 테스트가 .env.local이나 .env.development를 실수로 사용하는 것을 방지한다.
#
# jipsa_rag.main을 import하면 Settings가 로드되고 SQLAlchemy 엔진이
# 생성되므로, 애플리케이션 모듈을 import하기 전에 반드시 test 환경을 지정한다.
os.environ["JIPSA_RAG_APP_ENV"] = "test"


@pytest.fixture
def client() -> Iterator[TestClient]:
    """테스트 환경 설정이 적용된 FastAPI 테스트 클라이언트를 제공한다."""

    # jipsa_rag.main은 import 시점에 설정과 DB 엔진을 초기화한다.
    # 따라서 test 환경 변수를 설정한 뒤 여기에서 지연 import한다.
    from jipsa_rag.main import app

    # TestClient를 context manager로 사용하면 FastAPI lifespan이 실행된다.
    # fixture 종료 시 lifespan 종료 처리와 DB 연결 풀 정리도 수행된다.
    with TestClient(app) as test_client:
        yield test_client
