"""pytest 공통 테스트 환경과 fixture를 정의한다."""

import os
from collections.abc import Iterator
from typing import Final

import pytest
from fastapi.testclient import TestClient

# 테스트가 .env.local이나 .env.development를 실수로 사용하는 것을 방지한다.
#
# jipsa_rag.main을 import하면 Settings가 로드되고 SQLAlchemy 엔진이
# 생성되므로 애플리케이션 모듈을 import하기 전에 test 환경을 지정한다.
os.environ["JIPSA_RAG_APP_ENV"] = "test"


# 테스트에서만 사용하는 백엔드 -> RAG 인제스트 인증 토큰이다.
#
# 실제 환경의 RAG_INGEST_TOKEN을 테스트 코드에 작성하지 않는다.
TEST_RAG_INGEST_TOKEN: Final[str] = "test-rag-ingest-token-0123456789abcdef"

# 테스트에서만 사용하는 RAG -> 백엔드 내부 API 인증 토큰이다.
#
# 실제 환경의 INTERNAL_TOKEN을 테스트 코드에 작성하지 않는다.
TEST_INTERNAL_TOKEN: Final[str] = "test-application-internal-token-0123456789abcdef"

# jipsa_rag.main을 import하기 전에 환경 변수를 설정해야
# create_app()에서 호출되는 get_settings()가 테스트 토큰을 읽을 수 있다.
os.environ["RAG_INGEST_TOKEN"] = TEST_RAG_INGEST_TOKEN
os.environ["INTERNAL_TOKEN"] = TEST_INTERNAL_TOKEN


@pytest.fixture
def client() -> Iterator[TestClient]:
    """테스트 환경 설정이 적용된 FastAPI 테스트 클라이언트를 제공한다."""

    # jipsa_rag.main은 import 시점에 설정과 DB 엔진을 초기화한다.
    # 따라서 test 환경 변수와 내부 토큰을 설정한 뒤 지연 import한다.
    from jipsa_rag.main import app

    # TestClient를 context manager로 사용하면 FastAPI lifespan이 실행된다.
    # fixture 종료 시 lifespan 종료 처리와 DB 연결 풀 정리도 수행된다.
    with TestClient(app) as test_client:
        # 기존 파일 처리 API 테스트가 인증 헤더 추가만으로
        # 대량 수정되지 않도록 모든 기본 테스트 요청에
        # 테스트 전용 인제스트 토큰을 등록한다.
        #
        # RAG가 백엔드를 호출할 때 사용하는 TEST_INTERNAL_TOKEN과
        # 백엔드가 RAG를 호출할 때 사용하는 TEST_RAG_INGEST_TOKEN은
        # 서로 다른 방향의 인증값이므로 혼용하지 않는다.
        test_client.headers["X-Internal-Token"] = TEST_RAG_INGEST_TOKEN

        yield test_client
