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
from jipsa_rag.schemas.reference_files import MAX_REFERENCE_FILE_COUNT
from jipsa_rag.services.chunk_search import ChunkSearchService

TEST_USER_IDX = 45

# API의 기본 성공 요청에서 사용할 참조문서 식별자다.
#
# Stub 검색 결과의 file_idx가 123이므로 선택 문서 범위에도
# 동일한 파일 식별자를 포함한다.
TEST_REFERENCE_FILE_IDXS = (123,)

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
    """청크 검색 API의 기본 유효 요청 본문을 반환한다.

    JSON 직렬화 이전의 요청 본문이므로 tuple 대신 실제 외부 API 계약과
    동일한 배열 형태의 list를 사용한다.

    Returns:
        인증된 청크 검색 API 호출에 사용할 유효한 JSON 요청 본문이다.
    """

    return {
        "user_idx": TEST_USER_IDX,
        "reference_file_idxs": list(TEST_REFERENCE_FILE_IDXS),
        "query": "프로젝트의 배포 절차를 알려줘",
        "top_k": 3,
        "score_threshold": 0.7,
    }


def _assert_request_validation_error(
    response_body: object,
    *,
    expected_field_prefix: str,
) -> None:
    """공통 요청 검증 오류 응답의 Envelope와 상세 오류 구조를 검증한다.

    요청 검증 오류는 단순히 ``data=null``을 반환하지 않는다. AWS Backend가
    어떤 필드에서 검증이 실패했는지 구분할 수 있도록 ``data.errors``에
    다음 정보를 포함한다.

    - field: 검증에 실패한 요청 필드 위치
    - message: 외부에 공개할 수 있는 Pydantic 검증 메시지
    - error_type: 클라이언트 분기 또는 진단에 사용할 오류 유형

    Pydantic 버전이 변경되면 영문 오류 메시지의 세부 표현은 달라질 수 있다.
    따라서 테스트에서는 전체 영문 문장을 고정하지 않고, 응답 Envelope와
    필수 상세 필드 및 오류 위치가 유지되는지를 검증한다.

    Args:
        response_body:
            TestClient 응답의 ``response.json()`` 결과다.
        expected_field_prefix:
            하나 이상의 오류 항목이 시작해야 하는 요청 필드 경로다.
            배열 원소 오류는 ``body.reference_file_idxs.0``처럼 더 상세한
            경로를 사용할 수 있으므로 완전 일치 대신 prefix로 검증한다.
    """

    # TestClient의 response.json()은 정적 타입상 Any일 수 있으므로
    # 먼저 런타임 객체 형태를 검증한 뒤 필드에 접근한다.
    assert isinstance(response_body, dict)

    assert response_body["success"] is False
    assert response_body["code"] == "REQUEST_VALIDATION_FAILED"
    assert response_body["message"] == "Request validation failed."

    validation_data = response_body["data"]

    # 현재 공통 요청 검증 계약에서는 data가 null이 아니라
    # 구조화된 검증 오류 목록을 포함해야 한다.
    assert isinstance(validation_data, dict)

    errors = validation_data.get("errors")

    assert isinstance(errors, list)
    assert errors

    expected_field_found = False

    for error in errors:
        assert isinstance(error, dict)

        # 내부 예외 객체, 입력값 원문 또는 정의되지 않은 필드가
        # 검증 오류 응답에 추가되지 않도록 공개 필드를 제한한다.
        assert set(error) == {
            "field",
            "message",
            "error_type",
        }

        field = error["field"]
        message = error["message"]
        error_type = error["error_type"]

        assert isinstance(field, str)
        assert field
        assert isinstance(message, str)
        assert message
        assert isinstance(error_type, str)
        assert error_type

        if field.startswith(expected_field_prefix):
            expected_field_found = True

    # 요청한 필드와 무관한 검증 오류만 반환되면 API와 스키마 연결이
    # 잘못된 것이므로 최소 하나는 예상 필드 경로와 일치해야 한다.
    assert expected_field_found


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

    # 외부 JSON 배열이 검증된 뒤 불변 tuple로 변환되고,
    # 서비스 계층까지 값과 순서가 보존되어야 한다.
    assert request.reference_file_idxs == TEST_REFERENCE_FILE_IDXS

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


def test_search_chunks_rejects_missing_reference_file_idxs(
    client: TestClient,
    stub_chunk_search_service: StubChunkSearchService,
) -> None:
    """참조문서 필드가 없는 요청을 서비스 호출 전에 거부해야 한다.

    참조문서 미선택 요청을 사용자의 전체 문서 검색으로 암묵적으로
    처리해서는 안 되므로 필드가 생략된 요청도 422로 실패해야 한다.
    """

    request_body = _valid_request_body()
    request_body.pop("reference_file_idxs")

    response = client.post(
        "/api/v1/chunks/search",
        json=request_body,
    )

    assert response.status_code == 422

    # REQUEST_VALIDATION_FAILED 응답은 data=null이 아니라
    # 실패 필드와 오류 유형을 data.errors에 포함하는 공통 계약을 사용한다.
    _assert_request_validation_error(
        response.json(),
        expected_field_prefix="body.reference_file_idxs",
    )

    # 요청 스키마 검증에서 실패했으므로 실제 검색 서비스는
    # 한 번도 호출되지 않아야 한다.
    assert stub_chunk_search_service.requests == []


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        # 참조문서가 하나도 선택되지 않은 요청은 허용하지 않는다.
        ("reference_file_idxs", []),
        # 동일한 파일 식별자를 중복 선택한 요청은 허용하지 않는다.
        ("reference_file_idxs", [123, 123]),
        # File.File_IDX는 0보다 큰 정수여야 한다.
        ("reference_file_idxs", [0]),
        ("reference_file_idxs", [-1]),
        # bool은 Python에서 int의 하위 타입이지만 외부 식별자로 허용하지 않는다.
        ("reference_file_idxs", [True]),
        # 실수 또는 문자열을 정수 식별자로 암묵 변환하지 않는다.
        ("reference_file_idxs", [123.0]),
        ("reference_file_idxs", ["123"]),
        # 최대 선택 개수를 한 개 초과한 요청을 거부한다.
        (
            "reference_file_idxs",
            list(
                range(
                    1,
                    MAX_REFERENCE_FILE_COUNT + 2,
                )
            ),
        ),
        ("top_k", 0),
        ("top_k", 21),
        ("score_threshold", -1.01),
        ("score_threshold", 1.01),
    ],
)
def test_search_chunks_rejects_invalid_request_constraints(
    client: TestClient,
    stub_chunk_search_service: StubChunkSearchService,
    field_name: str,
    invalid_value: object,
) -> None:
    """참조문서와 검색 조건이 요청 계약을 벗어나면 거부해야 한다."""

    request_body = _valid_request_body()
    request_body[field_name] = invalid_value

    response = client.post(
        "/api/v1/chunks/search",
        json=request_body,
    )

    assert response.status_code == 422

    # Pydantic이 배열 원소와 배열 전체 제약을 함께 검사하면 하나의 입력에
    # 둘 이상의 오류가 반환될 수 있다. 따라서 오류 개수를 고정하지 않고
    # 해당 요청 필드에 대한 오류가 하나 이상 존재하는지를 검증한다.
    _assert_request_validation_error(
        response.json(),
        expected_field_prefix=f"body.{field_name}",
    )

    # 검증에 실패한 요청은 실제 임베딩 또는 Qdrant 검색으로 전달되지 않는다.
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
