"""RAG 답변 API의 Claude 인용 실패 및 근거 부족 응답 계약을 테스트한다."""

import logging
from collections.abc import Iterator
from typing import Final, cast

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.rag_answer import get_rag_answer_service
from jipsa_rag.main import app
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagAnswerStatus,
)
from jipsa_rag.services.rag_answer import (
    RagAnswerService,
    RagAnswerServiceError,
)

_TEST_USER_IDX: Final[int] = 45
_TEST_FILE_IDX: Final[int] = 123
_TEST_SENSITIVE_QUESTION: Final[str] = (
    "민감한 API 질문 원문이며 오류 응답이나 로그에 노출되면 안 됩니다."
)
_TEST_SENSITIVE_PROMPT: Final[str] = (
    "민감한 Claude 프롬프트 원문이며 API 계층이 기록하면 안 됩니다."
)
_TEST_SENSITIVE_CLAUDE_ANSWER: Final[str] = (
    "민감한 Claude 답변 원문이며 존재하지 않는 출처를 포함합니다. [SOURCE-999]"
)
_TEST_SENSITIVE_API_KEY: Final[str] = "sk-ant-api-test-secret-that-must-not-be-logged"
_TEST_INSUFFICIENT_EVIDENCE_ANSWER: Final[str] = "제공된 문서 근거만으로는 답변할 수 없습니다."


class _CitationContractRagAnswerService:
    """실제 검색 및 Claude 호출 없이 API 변환 계약만 검증하는 대역."""

    def __init__(self) -> None:
        """호출 기록과 테스트별 응답 또는 오류 상태를 초기화한다."""

        self.requests: list[RagAnswerRequest] = []
        self.response: RagAnswerResponse | None = None
        self.error: RagAnswerServiceError | None = None

        # 엔드포인트가 서비스 객체 전체를 로그에 기록하는 회귀를 탐지하기 위해
        # 외부에 노출되면 안 되는 테스트 전용 값을 속성으로 보관한다.
        self.prompt = _TEST_SENSITIVE_PROMPT
        self.claude_answer = _TEST_SENSITIVE_CLAUDE_ANSWER
        self.api_key = _TEST_SENSITIVE_API_KEY

    async def answer(
        self,
        request: RagAnswerRequest,
    ) -> RagAnswerResponse:
        """요청을 기록한 뒤 준비된 오류 또는 응답을 반환한다."""

        self.requests.append(request)

        if self.error is not None:
            raise self.error

        if self.response is None:
            raise AssertionError("A response or error must be configured before the API call.")

        return self.response


@pytest.fixture
def citation_contract_service(
    client: TestClient,
) -> Iterator[_CitationContractRagAnswerService]:
    """RAG 답변 서비스 의존성을 인용 계약 테스트 대역으로 교체한다."""

    stub_service = _CitationContractRagAnswerService()

    def get_stub_rag_answer_service() -> RagAnswerService:
        """FastAPI dependency override에 사용할 타입 호환 서비스 대역을 반환한다."""

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


def _valid_request_body(
    *,
    query: str = "선택 문서의 내용을 알려줘",
) -> dict[str, object]:
    """AWS Backend가 전달하는 형식과 동일한 유효한 요청 본문을 생성한다."""

    return {
        "user_idx": _TEST_USER_IDX,
        "reference_file_idxs": [_TEST_FILE_IDX],
        "query": query,
        "top_k": 3,
        "score_threshold": 0.7,
    }


def _render_log_records(
    records: list[logging.LogRecord],
) -> str:
    """로그 메시지와 extra 필드를 민감 정보 검사 문자열로 변환한다."""

    return "\n".join(repr(record.__dict__) for record in records)


def test_invalid_citation_returns_invalid_generation_response_without_data_leak(
    client: TestClient,
    citation_contract_service: _CitationContractRagAnswerService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """잘못된 Claude 인용은 200 정상 응답이 아니라 안전한 502 오류여야 한다."""

    citation_contract_service.error = RagAnswerServiceError(
        operation="answer_citation_validation_failed",
    )

    caplog.set_level(logging.DEBUG)

    response = client.post(
        "/api/v1/rag/answers",
        json=_valid_request_body(
            query=_TEST_SENSITIVE_QUESTION,
        ),
    )

    assert response.status_code == 502
    assert response.json() == {
        "success": False,
        "code": "INVALID_GENERATION_RESPONSE",
        "message": "The generation service returned an invalid response.",
        "data": None,
    }

    # 요청 검증과 서비스 호출은 완료되었지만 인용 검증 실패 이후의
    # RAG_ANSWER_COMPLETED 정상 응답은 생성되지 않아야 한다.
    assert len(citation_contract_service.requests) == 1
    assert citation_contract_service.requests[0].query == _TEST_SENSITIVE_QUESTION
    assert "RAG_ANSWER_COMPLETED" not in response.text

    rendered_logs = _render_log_records(caplog.records)

    sensitive_values = (
        _TEST_SENSITIVE_QUESTION,
        _TEST_SENSITIVE_PROMPT,
        _TEST_SENSITIVE_CLAUDE_ANSWER,
        _TEST_SENSITIVE_API_KEY,
        "SOURCE-999",
    )

    for sensitive_value in sensitive_values:
        assert sensitive_value not in response.text
        assert sensitive_value not in rendered_logs

    # 실제 Claude 응답이나 프롬프트 대신 서비스가 정의한 안전한 작업 식별자만
    # 장애 분석용 로그 컨텍스트에 포함한다.
    assert "answer_citation_validation_failed" in rendered_logs


def test_generated_insufficient_evidence_is_returned_as_successful_status(
    client: TestClient,
    citation_contract_service: _CitationContractRagAnswerService,
) -> None:
    """Claude 고정 문구로 생성된 근거 부족 결과도 200 상태 계약을 유지해야 한다."""

    citation_contract_service.response = RagAnswerResponse(
        answer=_TEST_INSUFFICIENT_EVIDENCE_ANSWER,
        status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
    )

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
            "answer": _TEST_INSUFFICIENT_EVIDENCE_ANSWER,
            "status": "insufficient_evidence",
            "cited_source_ids": [],
            "sources": [],
            "model": None,
            "usage": None,
            "stop_reason": None,
        },
    }

    assert len(citation_contract_service.requests) == 1
