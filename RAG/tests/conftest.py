"""pytest 공통 테스트 환경과 fixture를 정의한다.

로컬 E2E 실행 스크립트는 실제 Qdrant, CUDA TEI와 Local RAG DB에 연결하기 위해
.env.local 값을 현재 PowerShell 프로세스에 주입한다. 같은 터미널에서 일반
단위·통합 테스트를 이어서 실행하면 OS 환경 변수가 .env.test보다 우선하여
로컬 서비스명이나 시작 시 DB 검사 설정이 테스트 프로필에 섞일 수 있다.

이 모듈은 애플리케이션 모듈이 import되기 전에 테스트 자체의 고정 설정만
명시적으로 덮어쓴다. 실제 E2E가 사용하는 Qdrant, TEI, DB 접속값은 변경하지
않으므로 JIPSA_RAG_RUN_E2E=1 실행에서는 현재 프로세스의 실제 인프라 설정을
그대로 사용할 수 있다.
"""

import os
from collections.abc import Iterator
from typing import Final

import pytest
from fastapi.testclient import TestClient

# 테스트에서만 사용하는 백엔드 -> RAG 인제스트 인증 토큰이다.
#
# 실제 환경의 RAG_INGEST_TOKEN을 테스트 코드에 작성하지 않는다.
TEST_RAG_INGEST_TOKEN: Final[str] = "test-rag-ingest-token-0123456789abcdef"

# 테스트에서만 사용하는 RAG -> 백엔드 내부 API 인증 토큰이다.
#
# 실제 환경의 INTERNAL_TOKEN을 테스트 코드에 작성하지 않는다.
TEST_INTERNAL_TOKEN: Final[str] = "test-application-internal-token-0123456789abcdef"

# 모든 pytest 실행에서 반드시 고정해야 하는 테스트 프로필 설정이다.
#
# pydantic-settings는 OS 환경 변수를 dotenv보다 우선한다. 따라서 앞선 실제
# E2E 실행이 .env.local을 PowerShell 프로세스에 주입한 경우, APP_ENV만 test로
# 바꿔도 JIPSA_RAG_APP_NAME이나 DATABASE_CHECK_ON_STARTUP 같은 값은 로컬
# 설정으로 남는다.
#
# 이 값들은 테스트 결과의 결정성과 격리를 위해 무조건 덮어쓴다.
_TEST_PROFILE_OVERRIDES: Final[dict[str, str]] = {
    "JIPSA_RAG_APP_ENV": "test",
    "JIPSA_RAG_APP_NAME": "Jipsa RAG Service Test",
    "JIPSA_RAG_APP_VERSION": "0.1.0",
    "JIPSA_RAG_API_V1_PREFIX": "/api/v1",
    "JIPSA_RAG_DEBUG": "false",
    "JIPSA_RAG_DATABASE_ECHO": "false",
    "JIPSA_RAG_DATABASE_CHECK_ON_STARTUP": "false",
    "RAG_INGEST_TOKEN": TEST_RAG_INGEST_TOKEN,
    "INTERNAL_TOKEN": TEST_INTERNAL_TOKEN,
}

# jipsa_rag.main을 import하면 Settings와 SQLAlchemy 엔진이 생성된다.
# 테스트 모듈 수집 중 main이 먼저 import되는 상황을 차단하기 위해 환경 변수는
# fixture 실행 시점이 아니라 conftest import 시점에 즉시 적용한다.
for variable_name, variable_value in _TEST_PROFILE_OVERRIDES.items():
    os.environ[variable_name] = variable_value

# 실제 E2E에 필요한 다음 종류의 값은 의도적으로 덮어쓰지 않는다.
#
# - JIPSA_RAG_DATABASE_HOST, PORT, NAME, USER, PASSWORD
# - JIPSA_RAG_QDRANT_URL
# - JIPSA_RAG_EMBEDDING_BASE_URL
# - ANTHROPIC_API_KEY
#
# 일반 테스트에서는 .env.test가 Mock 또는 테스트 전용 값을 제공하고,
# 실제 E2E에서는 실행 스크립트가 .env.local의 실제 값을 프로세스에 주입한다.


@pytest.fixture
def client() -> Iterator[TestClient]:
    """테스트 환경 설정이 적용된 FastAPI 테스트 클라이언트를 제공한다."""

    # jipsa_rag.main은 import 시점에 설정과 DB 엔진을 초기화한다.
    # 따라서 위 테스트 프로필과 내부 토큰을 설정한 뒤 지연 import한다.
    from jipsa_rag.main import app

    # TestClient를 context manager로 사용하면 FastAPI lifespan이 실행된다.
    # fixture 종료 시 lifespan 종료 처리와 DB 연결 풀 정리도 수행된다.
    with TestClient(app) as test_client:
        # 기존 파일 처리 API 테스트가 인증 헤더 추가만으로 대량 수정되지 않도록
        # 모든 기본 테스트 요청에 테스트 전용 인제스트 토큰을 등록한다.
        #
        # RAG가 백엔드를 호출할 때 사용하는 TEST_INTERNAL_TOKEN과
        # 백엔드가 RAG를 호출할 때 사용하는 TEST_RAG_INGEST_TOKEN은
        # 서로 다른 방향의 인증값이므로 혼용하지 않는다.
        test_client.headers["X-Internal-Token"] = TEST_RAG_INGEST_TOKEN

        yield test_client
