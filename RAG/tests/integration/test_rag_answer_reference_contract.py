"""RAG 답변 API의 참조문서 추가·해제 및 빈 목록 계약을 통합 테스트한다."""

from collections.abc import Iterator
from typing import cast

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.rag_answer import (
    get_rag_answer_service,
)
from jipsa_rag.main import app
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagAnswerStatus,
)
from jipsa_rag.services.rag_answer import RagAnswerService

_TEST_USER_IDX = 45
_INSUFFICIENT_EVIDENCE_ANSWER = "제공된 문서 근거만으로는 답변할 수 없습니다."


class _RecordingRagAnswerService:
    """실제 TEI, Qdrant 및 Claude 호출 없이 검증된 요청을 기록한다."""

    def __init__(self) -> None:
        """요청별 참조문서 범위를 확인할 호출 기록을 초기화한다."""

        self.requests: list[RagAnswerRequest] = []

    async def answer(
        self,
        request: RagAnswerRequest,
    ) -> RagAnswerResponse:
        """요청을 기록하고 결정적인 근거 부족 응답을 반환한다."""

        self.requests.append(request)

        return RagAnswerResponse(
            answer=_INSUFFICIENT_EVIDENCE_ANSWER,
            status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
        )


@pytest.fixture
def recording_rag_answer_service(
    client: TestClient,
) -> Iterator[_RecordingRagAnswerService]:
    """답변 API의 서비스 의존성을 호출 기록용 테스트 대역으로 교체한다."""

    stub_service = _RecordingRagAnswerService()

    def get_stub_rag_answer_service() -> RagAnswerService:
        """FastAPI dependency override에 사용할 서비스 대역을 반환한다."""

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
    reference_file_idxs: list[int],
    query: str,
) -> dict[str, object]:
    """AWS Backend가 전송할 유효한 JSON 요청 본문을 생성한다."""

    return {
        "user_idx": _TEST_USER_IDX,
        "reference_file_idxs": reference_file_idxs,
        "query": query,
        "top_k": 5,
        "score_threshold": 0.6,
    }


def test_answer_api_applies_added_and_removed_reference_files_per_request(
    client: TestClient,
    recording_rag_answer_service: _RecordingRagAnswerService,
) -> None:
    """각 HTTP 요청은 전송 시점의 참조문서 목록을 독립적으로 사용해야 한다."""

    request_bodies = (
        _valid_request_body(
            reference_file_idxs=[123],
            query="첫 번째 참조문서만 사용해줘",
        ),
        _valid_request_body(
            reference_file_idxs=[
                123,
                456,
            ],
            query="두 번째 참조문서를 추가해서 사용해줘",
        ),
        _valid_request_body(
            reference_file_idxs=[456],
            query="첫 번째 참조문서를 해제하고 사용해줘",
        ),
    )

    responses = [
        client.post(
            "/api/v1/rag/answers",
            json=request_body,
        )
        for request_body in request_bodies
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["code"] == "RAG_ANSWER_COMPLETED" for response in responses)

    # FastAPI가 외부 JSON 배열을 불변 tuple로 검증한 뒤,
    # 각 요청의 선택 범위를 서비스까지 순서대로 전달해야 한다.
    assert tuple(
        request.reference_file_idxs for request in recording_rag_answer_service.requests
    ) == (
        (123,),
        (
            123,
            456,
        ),
        (456,),
    )

    # sources가 빈 리스트이므로 Mypy strict 모드에서는 리스트 원소 타입을
    # 주변 문맥만으로 확정할 수 없다. 응답 JSON 객체라는 사실을 명시해
    # 빈 컬렉션을 포함한 예상 응답 전체의 타입을 안정적으로 고정한다.
    expected_data: dict[str, object] = {
        "answer": _INSUFFICIENT_EVIDENCE_ANSWER,
        "status": "insufficient_evidence",
        "sources": [],
        "model": None,
        "usage": None,
        "stop_reason": None,
    }

    assert all(response.json()["data"] == expected_data for response in responses)


@pytest.mark.parametrize(
    "reference_file_mode",
    [
        "missing",
        "null",
        "empty",
    ],
)
def test_answer_api_returns_reference_document_required_for_empty_scope(
    client: TestClient,
    recording_rag_answer_service: _RecordingRagAnswerService,
    reference_file_mode: str,
) -> None:
    """참조문서가 없으면 전체 문서 검색으로 전환하지 않고 422를 반환해야 한다."""

    request_body = _valid_request_body(
        reference_file_idxs=[123],
        query="참조문서 없이 답변해줘",
    )

    if reference_file_mode == "missing":
        request_body.pop("reference_file_idxs")
    elif reference_file_mode == "null":
        request_body["reference_file_idxs"] = None
    else:
        request_body["reference_file_idxs"] = []

    response = client.post(
        "/api/v1/rag/answers",
        json=request_body,
    )

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "code": "REFERENCE_DOCUMENT_REQUIRED",
        "message": "At least one reference document must be selected.",
        "data": None,
    }

    # 요청 검증 단계에서 종료되므로 검색, 프롬프트 구성 및 Claude 호출을
    # 담당하는 답변 서비스에는 요청이 전달되지 않아야 한다.
    assert recording_rag_answer_service.requests == []
