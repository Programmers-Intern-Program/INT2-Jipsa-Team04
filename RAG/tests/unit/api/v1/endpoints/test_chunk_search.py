"""관련 청크 검색 API의 인증, 검증, 성공 응답과 예외 변환을 테스트한다."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.chunk_search import (
    get_chunk_search_service,
)
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    InvalidVectorSearchResultError,
    VectorCollectionConfigurationError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.main import app
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.services.chunk_search import ChunkSearchService

TEST_USER_IDX = 45
TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"


class StubChunkSearchService:
    """검색 결과 또는 준비된 예외를 반환하는 API 테스트 대역."""

    def __init__(self) -> None:
        """기본 성공 응답, 선택적 오류와 호출 기록을 초기화한다."""

        self.requests: list[ChunkSearchRequest] = []
        self.error: Exception | None = None
        self.response = ChunkSearchResponse(
            user_idx=TEST_USER_IDX,
            result_count=1,
            results=(
                ChunkSearchResult(
                    chunk_id=TEST_CHUNK_ID,
                    score=0.92,
                    rag_document_idx=100,
                    file_idx=123,
                    folder_idx=9,
                    file_name="프로젝트 가이드.pdf",
                    file_type=SupportedFileType.PDF,
                    chunk_index=0,
                    content=("프로젝트 배포 절차는 로컬 RAG 실행 후 진행합니다."),
                    token_count=64,
                    page=2,
                    slide_no=None,
                    sheet_name=None,
                    section_title="배포 절차",
                    parser_version="1.0.0",
                    embedding_model="test/embedding-model",
                    index_version=2,
                ),
            ),
        )

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """요청을 기록하고 설정된 예외 또는 성공 응답을 반환한다."""

        self.requests.append(request)

        if self.error is not None:
            raise self.error

        return self.response


@pytest.fixture
def stub_chunk_search_service(
    client: TestClient,
) -> Iterator[StubChunkSearchService]:
    """실제 TEI와 Qdrant 의존성을 검색 서비스 Stub으로 교체한다."""

    stub_service = StubChunkSearchService()

    def get_stub_chunk_search_service() -> ChunkSearchService:
        """FastAPI dependency override용 검색 서비스 대역을 반환한다."""

        return cast(
            ChunkSearchService,
            stub_service,
        )

    app.dependency_overrides[get_chunk_search_service] = get_stub_chunk_search_service

    try:
        yield stub_service
    finally:
        app.dependency_overrides.pop(
            get_chunk_search_service,
            None,
        )


@contextmanager
def without_default_internal_token(
    client: TestClient,
) -> Iterator[None]:
    """공통 TestClient의 기본 내부 토큰을 테스트 중에만 제거한다."""

    original_token = client.headers.get("X-Internal-Token")

    if original_token is not None:
        del client.headers["X-Internal-Token"]

    try:
        yield
    finally:
        if original_token is not None:
            client.headers["X-Internal-Token"] = original_token


def _valid_request_body() -> dict[str, object]:
    """청크 검색 API의 기본 유효 요청 본문을 반환한다."""

    return {
        "user_idx": TEST_USER_IDX,
        "query": "프로젝트의 배포 절차를 알려줘",
        "top_k": 3,
        "score_threshold": 0.7,
    }


def test_search_chunks_returns_authenticated_search_result(
    client: TestClient,
    stub_chunk_search_service: StubChunkSearchService,
) -> None:
    """유효한 내부 토큰과 요청이면 공통 성공 응답을 반환해야 한다."""

    response = client.post(
        "/api/v1/chunks/search",
        json=_valid_request_body(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": "CHUNK_SEARCH_COMPLETED",
        "message": "Relevant document chunks were retrieved.",
        "data": {
            "user_idx": TEST_USER_IDX,
            "result_count": 1,
            "results": [
                {
                    "chunk_id": TEST_CHUNK_ID,
                    "score": 0.92,
                    "rag_document_idx": 100,
                    "file_idx": 123,
                    "folder_idx": 9,
                    "file_name": "프로젝트 가이드.pdf",
                    "file_type": "pdf",
                    "chunk_index": 0,
                    "content": ("프로젝트 배포 절차는 로컬 RAG 실행 후 진행합니다."),
                    "token_count": 64,
                    "page": 2,
                    "slide_no": None,
                    "sheet_name": None,
                    "section_title": "배포 절차",
                    "parser_version": "1.0.0",
                    "embedding_model": "test/embedding-model",
                    "index_version": 2,
                }
            ],
        },
    }

    assert len(stub_chunk_search_service.requests) == 1
    request = stub_chunk_search_service.requests[0]
    assert request.user_idx == TEST_USER_IDX
    assert request.query == "프로젝트의 배포 절차를 알려줘"
    assert request.top_k == 3
    assert request.score_threshold == 0.7


def test_search_chunks_rejects_missing_token_before_service_call(
    client: TestClient,
    stub_chunk_search_service: StubChunkSearchService,
) -> None:
    """내부 토큰이 없으면 검색 서비스 실행 전에 401로 거부해야 한다."""

    with without_default_internal_token(client):
        response = client.post(
            "/api/v1/chunks/search",
            json=_valid_request_body(),
        )

    assert response.status_code == 401
    assert response.json() == {
        "success": False,
        "code": "UNAUTHORIZED",
        "message": "Authentication is required.",
        "data": None,
    }
    assert stub_chunk_search_service.requests == []


def test_search_chunks_rejects_invalid_token_before_service_call(
    client: TestClient,
    stub_chunk_search_service: StubChunkSearchService,
) -> None:
    """내부 토큰 불일치도 서비스 실행 전에 401로 처리해야 한다."""

    invalid_token = "invalid-internal-token-that-must-not-leak"

    response = client.post(
        "/api/v1/chunks/search",
        headers={
            "X-Internal-Token": invalid_token,
        },
        json=_valid_request_body(),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert invalid_token not in response.text
    assert stub_chunk_search_service.requests == []


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("top_k", 0),
        ("top_k", 21),
        ("score_threshold", -1.01),
        ("score_threshold", 1.01),
    ],
)
def test_search_chunks_rejects_invalid_search_constraints(
    client: TestClient,
    stub_chunk_search_service: StubChunkSearchService,
    field_name: str,
    invalid_value: int | float,
) -> None:
    """top_k와 최소 유사도 범위를 벗어난 요청을 거부해야 한다."""

    request_body = _valid_request_body()
    request_body[field_name] = invalid_value

    response = client.post(
        "/api/v1/chunks/search",
        json=request_body,
    )

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert response.json()["message"] == "Request validation failed."
    assert stub_chunk_search_service.requests == []


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            EmbeddingServiceTimeoutError(),
            504,
            "EMBEDDING_SERVICE_TIMEOUT",
        ),
        (
            EmbeddingServiceUnavailableError(
                status_code=503,
            ),
            503,
            "EMBEDDING_SERVICE_UNAVAILABLE",
        ),
        (
            EmbeddingServiceRejectedError(
                status_code=400,
            ),
            502,
            "EMBEDDING_REQUEST_REJECTED",
        ),
        (
            InvalidEmbeddingResponseError(
                reason="invalid vector dimension",
                batch_start_index=0,
            ),
            502,
            "INVALID_EMBEDDING_RESPONSE",
        ),
        (
            VectorDatabaseUnavailableError(
                "search_chunks",
                status_code=503,
            ),
            503,
            "VECTOR_DATABASE_UNAVAILABLE",
        ),
        (
            VectorDatabaseRejectedError(
                "search_chunks",
                status_code=400,
            ),
            502,
            "VECTOR_SEARCH_FAILED",
        ),
        (
            VectorCollectionConfigurationError(
                "query_embedding_dim_mismatch",
            ),
            502,
            "VECTOR_SEARCH_FAILED",
        ),
        (
            InvalidVectorSearchResultError(
                "invalid_search_result_payload",
            ),
            502,
            "INVALID_VECTOR_SEARCH_RESULT",
        ),
    ],
)
def test_search_chunks_maps_internal_errors_to_public_errors(
    client: TestClient,
    stub_chunk_search_service: StubChunkSearchService,
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    """TEI와 Qdrant 내부 오류를 고정된 외부 오류로 변환해야 한다."""

    stub_chunk_search_service.error = error

    response = client.post(
        "/api/v1/chunks/search",
        json=_valid_request_body(),
    )

    assert response.status_code == expected_status
    assert response.json()["success"] is False
    assert response.json()["code"] == expected_code
    assert response.json()["data"] is None

    # 저장소 작업명과 내부 검증 사유는 외부 응답에 노출하지 않는다.
    assert "search_chunks" not in response.text
    assert "invalid vector dimension" not in response.text
    assert "invalid_search_result_payload" not in response.text
