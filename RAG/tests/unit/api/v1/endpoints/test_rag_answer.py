"""선택 참조문서 기반 RAG 답변 API의 인증, 검증 및 응답 계약을 테스트한다."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final, cast

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.rag_answer import (
    get_rag_answer_service,
)
from jipsa_rag.main import app
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagAnswerSource,
    RagAnswerStatus,
    RagAnswerUsage,
)
from jipsa_rag.services.rag_answer import (
    RagAnswerService,
    RagAnswerServiceError,
)

_TEST_USER_IDX: Final[int] = 45
_TEST_REFERENCE_FILE_IDXS: Final[tuple[int, ...]] = (
    123,
    456,
)
_TEST_CHUNK_ID: Final[str] = "11111111-1111-1111-1111-111111111111"

# API 응답과 로그에 노출되면 안 되는 값을 고유한 문자열로 정의한다.
#
# 실제 질문, 문서 원문 또는 API Key를 테스트 코드에 사용하지 않으며,
# 서비스 경계의 비노출 계약만 검증할 수 있는 가짜 값을 사용한다.
_TEST_SENSITIVE_QUESTION: Final[str] = "sensitive-question-value-that-must-not-be-logged"
_TEST_SENSITIVE_CHUNK: Final[str] = "sensitive-chunk-value-that-must-not-be-logged"
_TEST_SENSITIVE_API_KEY: Final[str] = "sk-ant-sensitive-api-key-that-must-not-be-logged"

# reference_file_idxs 필드를 완전히 생략하는 매개변수 테스트에 사용하는
# 내부 sentinel이다. JSON 요청 본문에는 이 객체를 전달하지 않는다.
_MISSING_REFERENCE_FILE_IDXS = object()


class StubRagAnswerService:
    """준비된 답변 또는 예외를 반환하고 API가 전달한 요청을 기록한다."""

    def __init__(self) -> None:
        """기본 정상 답변, 선택적 오류 및 호출 기록을 초기화한다."""

        self.requests: list[RagAnswerRequest] = []
        self.error: Exception | None = None

        # 실제 서비스 객체를 실수로 로그에 기록하는 구현을 탐지하기 위해
        # 테스트 전용 가짜 API Key를 속성으로 보관한다.
        self.api_key = _TEST_SENSITIVE_API_KEY

        self.response = _create_answered_response()

    async def answer(
        self,
        request: RagAnswerRequest,
    ) -> RagAnswerResponse:
        """요청을 기록한 뒤 실제 TEI, Qdrant 또는 Claude 호출 없이 응답한다."""

        self.requests.append(
            request,
        )

        if self.error is not None:
            raise self.error

        return self.response


def _create_source(
    *,
    excerpt: str = ("로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다."),
) -> RagAnswerSource:
    """파일명, 페이지, 섹션 및 발췌문을 포함한 공개 출처를 생성한다."""

    return RagAnswerSource(
        source_id="SOURCE-1",
        chunk_id=_TEST_CHUNK_ID,
        rag_document_idx=100,
        file_idx=123,
        folder_idx=9,
        file_name="프로젝트 가이드.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        score=0.92,
        page=2,
        slide_no=None,
        sheet_name=None,
        section_title="로컬 실행 방법",
        excerpt=excerpt,
    )


def _create_answered_response() -> RagAnswerResponse:
    """Claude 생성이 완료된 정상 답변 응답을 생성한다."""

    return RagAnswerResponse(
        answer=("로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다. [SOURCE-1]"),
        status=RagAnswerStatus.ANSWERED,
        sources=(_create_source(),),
        model="claude-sonnet-5",
        usage=RagAnswerUsage(
            input_tokens=120,
            output_tokens=30,
        ),
        stop_reason="end_turn",
    )


def _create_insufficient_evidence_response() -> RagAnswerResponse:
    """검색 결과가 없을 때 반환할 생성 호출 없는 근거 부족 응답을 생성한다."""

    return RagAnswerResponse(
        answer=("제공된 문서 근거만으로는 답변할 수 없습니다."),
        status=(RagAnswerStatus.INSUFFICIENT_EVIDENCE),
    )


@pytest.fixture
def stub_rag_answer_service(
    client: TestClient,
) -> Iterator[StubRagAnswerService]:
    """실제 검색 및 Claude 의존성을 답변 서비스 Stub으로 교체한다."""

    stub_service = StubRagAnswerService()

    def get_stub_rag_answer_service() -> RagAnswerService:
        """FastAPI dependency override용 답변 서비스 대역을 반환한다."""

        return cast(
            RagAnswerService,
            stub_service,
        )

    app.dependency_overrides[get_rag_answer_service] = get_stub_rag_answer_service

    try:
        yield stub_service
    finally:
        app.dependency_overrides.pop(
            get_rag_answer_service,
            None,
        )


@contextmanager
def without_default_internal_token(
    client: TestClient,
) -> Iterator[None]:
    """공통 TestClient의 기본 X-Internal-Token을 테스트 중에만 제거한다."""

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
    query: str = ("프로젝트의 로컬 실행 방법을 알려줘"),
    reference_file_idxs: object = (_TEST_REFERENCE_FILE_IDXS),
) -> dict[str, object]:
    """외부 JSON 계약과 동일한 RAG 답변 요청 본문을 생성한다."""

    request_body: dict[str, object] = {
        "user_idx": _TEST_USER_IDX,
        "query": query,
        "top_k": 3,
        "score_threshold": 0.7,
    }

    if reference_file_idxs is not _MISSING_REFERENCE_FILE_IDXS:
        # tuple을 새 list로 변환하여 실제 JSON 배열 입력 경로를 검증한다.
        if isinstance(
            reference_file_idxs,
            tuple,
        ):
            request_body["reference_file_idxs"] = list(
                reference_file_idxs,
            )
        else:
            request_body["reference_file_idxs"] = reference_file_idxs

    return request_body


def _render_log_records(
    records: list[logging.LogRecord],
) -> str:
    """로그 메시지와 extra 전체를 민감 정보 검사 문자열로 변환한다."""

    return "\n".join(repr(record.__dict__) for record in records)


def test_answer_question_returns_answer_and_public_sources(
    client: TestClient,
    stub_rag_answer_service: StubRagAnswerService,
) -> None:
    """정상 답변에 파일명, 페이지, 섹션 및 청크 발췌문을 포함해야 한다."""

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": "RAG_ANSWER_COMPLETED",
        "message": ("The RAG answer request was processed."),
        "data": {
            "answer": ("로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다. [SOURCE-1]"),
            "status": "answered",
            "sources": [
                {
                    "source_id": "SOURCE-1",
                    "chunk_id": _TEST_CHUNK_ID,
                    "rag_document_idx": 100,
                    "file_idx": 123,
                    "folder_idx": 9,
                    "file_name": ("프로젝트 가이드.pdf"),
                    "file_type": "pdf",
                    "chunk_index": 0,
                    "score": 0.92,
                    "page": 2,
                    "slide_no": None,
                    "sheet_name": None,
                    "section_title": ("로컬 실행 방법"),
                    "excerpt": ("로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다."),
                }
            ],
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
            },
            "stop_reason": "end_turn",
        },
    }

    assert len(stub_rag_answer_service.requests) == 1

    request = stub_rag_answer_service.requests[0]

    # 외부 JSON 배열이 요청 스키마 검증을 거친 뒤
    # 질문 전송 시점의 불변 tuple로 보존되어야 한다.
    assert request.reference_file_idxs == _TEST_REFERENCE_FILE_IDXS
    assert request.query == ("프로젝트의 로컬 실행 방법을 알려줘")


def test_answer_question_returns_insufficient_evidence_response(
    client: TestClient,
    stub_rag_answer_service: StubRagAnswerService,
) -> None:
    """선택 문서 검색 결과가 없으면 생성 메타데이터 없는 응답을 반환해야 한다."""

    stub_rag_answer_service.response = _create_insufficient_evidence_response()

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": "RAG_ANSWER_COMPLETED",
        "message": ("The RAG answer request was processed."),
        "data": {
            "answer": ("제공된 문서 근거만으로는 답변할 수 없습니다."),
            "status": "insufficient_evidence",
            "sources": [],
            "model": None,
            "usage": None,
            "stop_reason": None,
        },
    }

    assert len(stub_rag_answer_service.requests) == 1


@pytest.mark.parametrize(
    "reference_file_idxs",
    [
        pytest.param(
            _MISSING_REFERENCE_FILE_IDXS,
            id="missing",
        ),
        pytest.param(
            None,
            id="null",
        ),
        pytest.param(
            [],
            id="empty",
        ),
    ],
)
def test_answer_question_returns_reference_document_required(
    client: TestClient,
    stub_rag_answer_service: StubRagAnswerService,
    reference_file_idxs: object,
) -> None:
    """참조문서가 선택되지 않은 요청은 전용 오류 코드로 거부해야 한다."""

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            reference_file_idxs=(reference_file_idxs),
        ),
    )

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "code": "REFERENCE_DOCUMENT_REQUIRED",
        "message": ("At least one reference document must be selected."),
        "data": None,
    }

    # 요청 스키마 검증에서 종료되므로 검색 및 Claude를 포함하는
    # 답변 서비스는 호출되지 않아야 한다.
    assert stub_rag_answer_service.requests == []


def test_answer_question_keeps_invalid_reference_values_as_validation_error(
    client: TestClient,
    stub_rag_answer_service: StubRagAnswerService,
) -> None:
    """중복 식별자는 미선택이 아니라 일반 요청 검증 오류로 처리해야 한다."""

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            reference_file_idxs=[
                123,
                123,
            ],
        ),
    )

    assert response.status_code == 422
    assert response.json()["code"] == ("REQUEST_VALIDATION_FAILED")
    assert stub_rag_answer_service.requests == []


def test_answer_question_rejects_missing_internal_token(
    client: TestClient,
    stub_rag_answer_service: StubRagAnswerService,
) -> None:
    """X-Internal-Token이 없으면 답변 서비스 실행 전에 401로 거부해야 한다."""

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
    assert stub_rag_answer_service.requests == []


def test_answer_question_does_not_log_sensitive_request_or_service_values(
    client: TestClient,
    stub_rag_answer_service: StubRagAnswerService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """질문, 청크, 프롬프트 대체값 및 API Key를 API 로그에 남기지 않아야 한다."""

    stub_rag_answer_service.response = RagAnswerResponse(
        answer="안전한 테스트 답변 [SOURCE-1]",
        status=RagAnswerStatus.ANSWERED,
        sources=(
            _create_source(
                excerpt=(_TEST_SENSITIVE_CHUNK),
            ),
        ),
        model="claude-sonnet-5",
        usage=RagAnswerUsage(
            input_tokens=10,
            output_tokens=5,
        ),
        stop_reason="end_turn",
    )

    caplog.set_level(
        logging.DEBUG,
    )

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            query=_TEST_SENSITIVE_QUESTION,
        ),
    )

    assert response.status_code == 200

    rendered_logs = _render_log_records(
        caplog.records,
    )

    assert _TEST_SENSITIVE_QUESTION not in rendered_logs
    assert _TEST_SENSITIVE_CHUNK not in rendered_logs
    assert _TEST_SENSITIVE_API_KEY not in rendered_logs


def test_answer_question_sanitizes_service_error(
    client: TestClient,
    stub_rag_answer_service: StubRagAnswerService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """서비스 계약 오류 응답과 로그에 질문 및 하위 민감값을 노출하지 않아야 한다."""

    stub_rag_answer_service.error = RagAnswerServiceError(
        operation="prompt_build_failed",
    )

    caplog.set_level(
        logging.DEBUG,
    )

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            query=_TEST_SENSITIVE_QUESTION,
        ),
    )

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "code": "INTERNAL_SERVER_ERROR",
        "message": ("An internal server error occurred."),
        "data": None,
    }

    rendered_logs = _render_log_records(
        caplog.records,
    )

    assert _TEST_SENSITIVE_QUESTION not in response.text
    assert _TEST_SENSITIVE_API_KEY not in response.text
    assert _TEST_SENSITIVE_QUESTION not in rendered_logs
    assert _TEST_SENSITIVE_API_KEY not in rendered_logs

    # 안전한 작업 식별자는 장애 분석을 위해 로그에 남겨도 된다.
    assert "prompt_build_failed" in (rendered_logs)
