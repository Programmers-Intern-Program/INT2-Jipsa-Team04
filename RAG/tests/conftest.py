"""pytest 공통 테스트 환경과 fixture를 정의한다."""

import os
from collections.abc import Iterator
from typing import Final

import pytest
from fastapi.testclient import TestClient

# 테스트가 .env.local이나 .env.development를 실수로 사용하는 것을 방지한다.
#
# jipsa_rag.main을 import하면 Settings가 로드되고 SQLAlchemy 엔진이
# 생성되므로, 애플리케이션 모듈을 import하기 전에 반드시 test 환경을 지정한다.
os.environ["JIPSA_RAG_APP_ENV"] = "test"


# 테스트에서만 사용하는 내부 인제스트 토큰이다.
#
# 실제 운영 또는 개발 환경의 내부 토큰을 테스트 코드에 작성하지 않는다.
#
# Settings의 최소 길이 검증과 동일하게 32자 이상의 값을 사용한다.
TEST_RAG_INGEST_TOKEN: Final[str] = "test-rag-ingest-token-0123456789abcdef"

# jipsa_rag.main을 import하기 전에 환경 변수를 설정해야
# create_app()에서 호출되는 get_settings()가 테스트 토큰을 읽을 수 있다.
os.environ["RAG_INGEST_TOKEN"] = TEST_RAG_INGEST_TOKEN


@pytest.fixture
def client() -> Iterator[TestClient]:
    """테스트 환경 설정이 적용된 FastAPI 테스트 클라이언트를 제공한다."""

    # jipsa_rag.main은 import 시점에 설정과 DB 엔진을 초기화한다.
    # 따라서 test 환경 변수와 내부 토큰을 설정한 뒤 여기에서 지연 import한다.
    from jipsa_rag.main import app

    # TestClient를 context manager로 사용하면 FastAPI lifespan이 실행된다.
    # fixture 종료 시 lifespan 종료 처리와 DB 연결 풀 정리도 수행된다.
    with TestClient(app) as test_client:
        # 기존 파일 처리 API 테스트가 인증 헤더 추가만으로 대량 수정되지 않도록
        # 모든 기본 테스트 요청에 테스트 전용 내부 토큰을 등록한다.
        #
        # 특정 인증 실패 테스트에서는 해당 테스트 범위에서만
        # 이 기본 헤더를 제거하거나 다른 값으로 덮어쓴다.
        test_client.headers["X-Internal-Token"] = TEST_RAG_INGEST_TOKEN

        yield test_client
