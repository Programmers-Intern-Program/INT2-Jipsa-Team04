"""RAG 답변 API의 인증, 요청 범위, 성공 응답과 오류 변환을 테스트한다."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest
from fastapi.testclient import TestClient

# API v1 통합 라우터가 실제로 등록하는 엔드포인트 모듈에서
# Dependency Override 대상 함수를 가져온다.
#
# FastAPI의 dependency_overrides는 함수 이름이 아니라 함수 객체 자체를
# 키로 사용한다. 등록되지 않은 rag_answers 모듈의 동명 함수를 사용하면
# Stub이 적용되지 않고 실제 TEI, Qdrant 및 Claude 의존성이 실행된다.
from jipsa_rag.api.v1.endpoints.rag_answer import get_rag_answer_service
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.infrastructure.generation.exceptions import GenerationTimeoutError
from jipsa_rag.main import app
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagAnswerSource,
    RagAnswerStatus,
    RagAnswerUsage,
)
from jipsa_rag.services.rag_answer import RagAnswerService

_TEST_USER_IDX = 45
_TEST_MODEL = "claude-sonnet-5"


class _StubRagAnswerService:
    """요청 범위에 맞는 결정적 응답 또는 준비된 예외를 반환한다.

    실제 TEI, Qdrant 및 Claude를 호출하지 않고 API 계층의 요청 전달,
    응답 직렬화 및 예외 변환 계약만 검증하기 위한 테스트 대역이다.
    """

    def __init__(self) -> None:
        """요청 기록과 선택적 오류를 초기화한다."""

        self.requests: list[RagAnswerRequest] = []
        self.error: Exception | None = None

    async def answer(
        self,
        request: RagAnswerRequest,
    ) -> RagAnswerResponse:
        """검증된 요청을 기록하고 첫 참조문서 기반 응답을 반환한다.

        테스트에서 설정한 오류가 있으면 실제 검색이나 생성 작업 없이
        해당 오류를 발생시켜 엔드포인트의 예외 변환만 검증한다.
        """

        # 호출 이후 외부 코드가 요청 모델을 변경하더라도 테스트 기록이
        # 영향을 받지 않도록 서비스 경계에서 받은 값을 깊은 복사한다.
        self.requests.append(
            request.model_copy(
                deep=True,
            )
        )

        if self.error is not None:
            raise self.error

        file_idx = request.reference_file_idxs[0]

        return RagAnswerResponse(
            answer=(f"참조문서 {file_idx}의 근거를 사용한 답변입니다. [SOURCE-1]"),
            status=RagAnswerStatus.ANSWERED,
            sources=(
                RagAnswerSource(
                    source_id="SOURCE-1",
                    chunk_id=(f"{file_idx:08d}-1111-1111-1111-111111111111"),
                    rag_document_idx=100,
                    file_idx=file_idx,
                    folder_idx=9,
                    file_name=f"참조문서-{file_idx}.pdf",
                    file_type=SupportedFileType.PDF,
                    chunk_index=0,
                    score=0.92,
                    page=2,
                    slide_no=None,
                    sheet_name=None,
                    section_title="테스트 근거",
                    excerpt=(f"참조문서 {file_idx}의 테스트 근거입니다."),
                ),
            ),
            model=_TEST_MODEL,
            usage=RagAnswerUsage(
                input_tokens=120,
                output_tokens=30,
            ),
            stop_reason="end_turn",
        )


@pytest.fixture
def stub_rag_answer_service(
    client: TestClient,
) -> Iterator[_StubRagAnswerService]:
    """실제 RAG 답변 서비스를 요청 범위 Stub으로 교체한다.

    통합 라우터가 실제로 사용하는 rag_answer 모듈의
    get_rag_answer_service 함수 객체를 Override한다.

    이를 통해 단위 테스트에서 다음 외부 의존성이 실행되지 않도록 한다.

    - TEI 질의 임베딩
    - Qdrant 청크 검색
    - Claude 답변 생성
    """

    stub_service = _StubRagAnswerService()

    def get_stub_rag_answer_service() -> RagAnswerService:
        """FastAPI Dependency Override용 답변 서비스 대역을 반환한다."""

        return cast(
            RagAnswerService,
            stub_service,
        )

    app.dependency_overrides[get_rag_answer_service] = get_stub_rag_answer_service

    try:
        yield stub_service
    finally:
        # 다른 API 테스트에서 같은 전역 FastAPI 앱을 재사용하므로
        # 테스트 종료 시 Override를 반드시 제거한다.
        app.dependency_overrides.pop(
            get_rag_answer_service,
            None,
        )


@contextmanager
def without_default_internal_token(
    client: TestClient,
) -> Iterator[None]:
    """공통 TestClient의 기본 X-Internal-Token을 일시적으로 제거한다."""

    original_token = client.headers.get(
        "X-Internal-Token",
    )

    if original_token is not None:
        del client.headers["X-Internal-Token"]

    try:
        yield
    finally:
        if original_token is not None:
            client.headers["X-Internal-Token"] = original_token


def _valid_request_body(
    *,
    reference_file_idxs: list[int] | None = None,
    query: str = "프로젝트의 로컬 실행 방법을 알려줘",
) -> dict[str, object]:
    """RAG 답변 API의 기본 유효 요청 본문을 반환한다.

    Args:
        reference_file_idxs:
            질문 전송 시점에 선택된 File.File_IDX 목록이다.
            None이면 기본 테스트 범위인 ``[123, 456]``을 사용한다.
            빈 리스트는 참조문서 미선택 계약을 검증할 때 그대로 보존한다.
        query:
            API에 전달할 사용자 질문이다.

    Returns:
        외부 JSON 요청과 동일한 형태의 사전 객체다.
    """

    selected_file_idxs = [123, 456] if reference_file_idxs is None else reference_file_idxs

    return {
        "user_idx": _TEST_USER_IDX,
        "reference_file_idxs": selected_file_idxs,
        "query": query,
        "top_k": 3,
        "score_threshold": 0.7,
    }


def test_create_rag_answer_returns_authenticated_answer(
    client: TestClient,
    stub_rag_answer_service: _StubRagAnswerService,
) -> None:
    """유효한 내부 토큰과 참조문서가 있으면 답변과 출처를 반환한다."""

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": "RAG_ANSWER_COMPLETED",
        "message": "The RAG answer request was processed.",
        "data": {
            "answer": ("참조문서 123의 근거를 사용한 답변입니다. [SOURCE-1]"),
            "status": "answered",
            "sources": [
                {
                    "source_id": "SOURCE-1",
                    "chunk_id": ("00000123-1111-1111-1111-111111111111"),
                    "rag_document_idx": 100,
                    "file_idx": 123,
                    "folder_idx": 9,
                    "file_name": "참조문서-123.pdf",
                    "file_type": "pdf",
                    "chunk_index": 0,
                    "score": 0.92,
                    "page": 2,
                    "slide_no": None,
                    "sheet_name": None,
                    "section_title": "테스트 근거",
                    "excerpt": ("참조문서 123의 테스트 근거입니다."),
                }
            ],
            "model": _TEST_MODEL,
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
            },
            "stop_reason": "end_turn",
        },
    }

    # Stub이 적용되지 않았다면 실제 외부 서비스 오류가 먼저 발생하므로,
    # 요청 기록 개수 검증으로 Dependency Override 적용 여부도 확인한다.
    assert len(stub_rag_answer_service.requests) == 1

    request = stub_rag_answer_service.requests[0]

    assert request.user_idx == _TEST_USER_IDX

    # 외부 JSON 배열은 스키마 검증 후 불변 tuple로 변환되어
    # 질문 전송 시점의 참조문서 범위를 보존해야 한다.
    assert request.reference_file_idxs == (
        123,
        456,
    )
    assert request.query == "프로젝트의 로컬 실행 방법을 알려줘"
    assert request.top_k == 3
    assert request.score_threshold == 0.7


def test_create_rag_answer_applies_selection_changes_from_next_question(
    client: TestClient,
    stub_rag_answer_service: _StubRagAnswerService,
) -> None:
    """참조문서 추가와 해제는 다음 질문의 요청 범위에만 적용한다."""

    first_response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            reference_file_idxs=[123],
            query="첫 번째 질문",
        ),
    )

    second_response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            reference_file_idxs=[
                123,
                456,
            ],
            query="참조문서를 추가한 두 번째 질문",
        ),
    )

    third_response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            reference_file_idxs=[456],
            query="일부 참조문서를 해제한 세 번째 질문",
        ),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert third_response.status_code == 200

    # 각 HTTP 요청이 전송될 때의 참조문서 목록을 독립적인 검색 범위로
    # 전달해야 하며, 이전 또는 다음 요청 상태와 합쳐져서는 안 된다.
    assert tuple(request.reference_file_idxs for request in stub_rag_answer_service.requests) == (
        (123,),
        (
            123,
            456,
        ),
        (456,),
    )

    # 각 응답은 해당 질문이 전송될 때 확정된 요청 범위를 사용한다.
    assert first_response.json()["data"]["sources"][0]["file_idx"] == 123
    assert second_response.json()["data"]["sources"][0]["file_idx"] == 123
    assert third_response.json()["data"]["sources"][0]["file_idx"] == 456


def test_create_rag_answer_rejects_missing_token_before_service_call(
    client: TestClient,
    stub_rag_answer_service: _StubRagAnswerService,
) -> None:
    """X-Internal-Token이 없으면 서비스 호출 전에 401로 거부한다."""

    with without_default_internal_token(
        client,
    ):
        response = client.post(
            "/api/v1/rag/answers",
            json=_valid_request_body(),
        )

    assert response.status_code == 401
    assert response.json() == {
        "success": False,
        "code": "UNAUTHORIZED",
        "message": "Authentication is required.",
        "data": None,
    }

    # 라우터 통합 지점의 인증 Dependency에서 종료되어야 하므로
    # 답변 서비스는 호출되지 않는다.
    assert stub_rag_answer_service.requests == []


def test_create_rag_answer_rejects_invalid_token_before_service_call(
    client: TestClient,
    stub_rag_answer_service: _StubRagAnswerService,
) -> None:
    """잘못된 X-Internal-Token도 서비스 호출 전에 거부한다."""

    invalid_token = "invalid-internal-token-that-must-not-leak"

    response = client.post(
        "/api/v1/rag/answers",
        headers={
            "X-Internal-Token": invalid_token,
        },
        json=_valid_request_body(),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"

    # 내부 인증 토큰은 외부 응답에 반사되어서는 안 된다.
    assert invalid_token not in response.text
    assert stub_rag_answer_service.requests == []


def test_create_rag_answer_rejects_empty_reference_files_without_full_search(
    client: TestClient,
    stub_rag_answer_service: _StubRagAnswerService,
) -> None:
    """빈 참조문서 요청을 전체 문서 검색으로 변환하지 않는다."""

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            reference_file_idxs=[],
        ),
    )

    assert response.status_code == 422

    # RAG 답변 API의 참조문서 생략, null 및 빈 배열은 일반 요청 검증
    # 오류가 아니라 전용 REFERENCE_DOCUMENT_REQUIRED 계약을 사용한다.
    assert response.json() == {
        "success": False,
        "code": "REFERENCE_DOCUMENT_REQUIRED",
        "message": ("At least one reference document must be selected."),
        "data": None,
    }

    # 요청 스키마 검증에서 실패하므로 TEI, Qdrant 및 Claude를 연결하는
    # 답변 서비스는 한 번도 호출되지 않아야 한다.
    assert stub_rag_answer_service.requests == []


def test_create_rag_answer_converts_generation_timeout(
    client: TestClient,
    stub_rag_answer_service: _StubRagAnswerService,
) -> None:
    """Claude 요청 시간 초과를 공개 가능한 공통 오류로 변환한다."""

    stub_rag_answer_service.error = GenerationTimeoutError(
        "Generation provider request timed out.",
        provider="anthropic",
    )

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(),
    )

    # 실제 등록된 rag_answer 엔드포인트는 GenerationTimeoutError를
    # GENERATION_SERVICE_TIMEOUT으로 변환한다.
    #
    # HTTP 상태와 공개 메시지를 중복 문자열로 작성하지 않고 ErrorCode에서
    # 직접 가져오면 공통 오류 계약 변경 시 테스트가 잘못된 옛 값을
    # 독립적으로 유지하는 문제를 방지할 수 있다.
    expected_error = ErrorCode.GENERATION_SERVICE_TIMEOUT

    assert response.status_code == expected_error.status_code
    assert response.json() == {
        "success": False,
        "code": expected_error.code,
        "message": expected_error.message,
        "data": None,
    }

    # Stub 서비스가 정확히 한 번 호출된 뒤 준비한 GenerationTimeoutError가
    # API 계층에서 공통 오류로 변환되었는지 확인한다.
    assert len(stub_rag_answer_service.requests) == 1
