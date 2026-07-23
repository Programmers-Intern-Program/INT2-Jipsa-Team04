"""Hugging Face TEI 검색 질의 임베딩 생성기를 테스트한다."""

import json

import httpx2
import pytest

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.embedding.query import TeiQueryEmbedder

TEST_EMBEDDING_MODEL = "test/embedding-model"
TEST_EMBEDDING_DIM = 3


def _create_settings() -> Settings:
    """외부 dotenv와 실제 TEI 서버에 의존하지 않는 테스트 설정을 생성한다."""

    return get_settings().model_copy(
        update={
            "embedding_base_url": "http://embedding.test",
            "embedding_model": TEST_EMBEDDING_MODEL,
            "embedding_dim": TEST_EMBEDDING_DIM,
            "embedding_timeout_seconds": 1.0,
        }
    )


def _read_request_payload(
    request: httpx2.Request,
) -> dict[str, object]:
    """MockTransport 요청 본문을 JSON 객체로 읽는다."""

    payload: object = json.loads(
        request.content.decode("utf-8"),
    )

    assert isinstance(payload, dict)

    return payload


@pytest.mark.asyncio
async def test_embed_adds_qwen3_query_instruction_and_returns_vector() -> None:
    """검색 질의에 Qwen3 instruction을 결합하고 벡터를 반환해야 한다."""

    received_payloads: list[dict[str, object]] = []

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/embed"

        received_payloads.append(
            _read_request_payload(request),
        )

        return httpx2.Response(
            status_code=200,
            json=[
                [
                    0.1,
                    0.2,
                    0.3,
                ]
            ],
        )

    embedder = TeiQueryEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    result = await embedder.embed(
        query="  프로젝트의 배포 절차를 알려줘  ",
    )

    assert received_payloads == [
        {
            "inputs": [
                "Instruct: Given a user question, retrieve relevant passages "
                "from the user's documents that answer the question\n"
                "Query: 프로젝트의 배포 절차를 알려줘"
            ]
        }
    ]
    assert result.embedding_model == TEST_EMBEDDING_MODEL
    assert result.embedding_dim == TEST_EMBEDDING_DIM
    assert result.vector == (
        0.1,
        0.2,
        0.3,
    )


@pytest.mark.asyncio
async def test_embed_maps_tei_service_error_to_unavailable() -> None:
    """TEI의 5xx 응답을 일시적 임베딩 서비스 장애로 변환해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=503,
            # 내부 오류 본문은 예외 객체나 외부 응답에 복사하지 않는다.
            text="sensitive internal TEI failure detail",
        )

    embedder = TeiQueryEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        EmbeddingServiceUnavailableError,
    ) as exception_info:
        await embedder.embed(
            query="검색 질의",
        )

    assert exception_info.value.status_code == 503
    assert "sensitive internal TEI failure detail" not in str(exception_info.value)


@pytest.mark.asyncio
async def test_embed_rejects_vector_with_unexpected_dimension() -> None:
    """TEI 벡터 차원이 설정과 다르면 잘못된 응답으로 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            json=[
                [
                    0.1,
                    0.2,
                ]
            ],
        )

    embedder = TeiQueryEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
    ) as exception_info:
        await embedder.embed(
            query="검색 질의",
        )

    assert exception_info.value.batch_start_index == 0
    assert exception_info.value.reason == "vector 0 has dimension 2 instead of 3"
