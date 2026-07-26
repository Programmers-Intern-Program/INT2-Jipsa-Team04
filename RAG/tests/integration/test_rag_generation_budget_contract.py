"""RAG 생성 예산 초과의 HTTP 상태와 민감 정보 비노출 계약을 검증한다."""

import logging
from typing import cast

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.rag_answer import (
    get_rag_answer_service,
)
from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationBudgetExceededError,
)
from jipsa_rag.main import app
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
)
from jipsa_rag.services.rag_answer import RagAnswerService


class _BudgetFailingRagAnswerService:
    """토큰 예산 초과를 결정적으로 발생시키는 API 테스트 대역."""

    async def answer(
        self,
        request: RagAnswerRequest,
    ) -> RagAnswerResponse:
        """요청 원문을 저장하지 않고 안전한 예산 오류만 발생시킨다."""

        del request

        raise GenerationBudgetExceededError(
            limit_type="input_tokens",
        )


def _override_rag_answer_service() -> RagAnswerService:
    """FastAPI 의존성 계약에 맞춰 테스트 대역을 반환한다."""

    return cast(
        RagAnswerService,
        _BudgetFailingRagAnswerService(),
    )


def test_generation_budget_exceeded_returns_429_without_sensitive_text(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """예산 초과를 429로 변환하고 질문을 노출하지 않아야 한다."""

    question_secret = "HTTP-QUESTION-SECRET-91AF"
    app.dependency_overrides[get_rag_answer_service] = _override_rag_answer_service
    caplog.set_level(
        logging.INFO,
    )

    try:
        response = client.post(
            "/api/v1/rag/answers",
            json={
                "user_idx": 45,
                "reference_file_idxs": [
                    123,
                    456,
                ],
                "query": (f"두 PDF를 비교하여 {question_secret} 값을 알려줘"),
                "top_k": 5,
                "score_threshold": None,
            },
        )

    finally:
        app.dependency_overrides.pop(
            get_rag_answer_service,
            None,
        )

    assert response.status_code == 429

    body = response.json()

    assert body["success"] is False
    assert body["code"] == "GENERATION_BUDGET_EXCEEDED"
    assert body["message"] == "The generation budget for this answer was exceeded."
    assert question_secret not in response.text
    assert question_secret not in caplog.text
