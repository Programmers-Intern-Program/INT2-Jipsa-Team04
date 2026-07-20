"""Hugging Face TEI 청크 임베딩 생성기를 테스트한다."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

import httpx2
import pytest

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    TextChunk,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.embedding.tei import TeiChunkEmbedder


def _create_settings(
    *,
    embedding_dim: int = 3,
    embedding_batch_size: int = 2,
) -> Settings:
    """외부 dotenv 파일을 수정하지 않는 TEI 단위 테스트 설정을 생성한다."""

    # conftest.py가 테스트 모듈 import 전에 JIPSA_RAG_APP_ENV=test를
    # 설정하므로 get_settings()는 .env.test 설정을 사용한다.
    #
    # 테스트에서 필요한 임베딩 관련 값만 복사본에 덮어써서
    # 실제 TEI 서버 주소나 운영 모델 설정에 의존하지 않도록 한다.
    return get_settings().model_copy(
        update={
            "embedding_base_url": "http://embedding.test",
            "embedding_model": "test/embedding-model",
            "embedding_dim": embedding_dim,
            "embedding_batch_size": embedding_batch_size,
            "embedding_timeout_seconds": 1.0,
        }
    )


def _create_text_chunk(
    *,
    chunk_index: int,
    content: str,
) -> TextChunk:
    """테스트에서 사용할 결정적 TextChunk를 생성한다."""

    start_offset = chunk_index * 100
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # 테스트를 반복 실행해도 동일한 입력에서 동일한 Chunk ID가
    # 만들어지도록 임의 UUID가 아닌 UUIDv5를 사용한다.
    chunk_id = str(
        uuid5(
            NAMESPACE_URL,
            f"test-chunk-{chunk_index}-{content_hash}",
        )
    )

    return TextChunk(
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        content=content,
        content_hash=content_hash,
        start_offset=start_offset,
        end_offset=start_offset + len(content),
        source_metadata={
            "page_number": 1,
        },
    )


def _create_chunked_document(
    *contents: str,
) -> ChunkedDocument:
    """입력 문자열마다 하나의 TextChunk가 포함된 문서를 생성한다."""

    chunks = tuple(
        _create_text_chunk(
            chunk_index=chunk_index,
            content=content,
        )
        for chunk_index, content in enumerate(contents)
    )

    return ChunkedDocument(
        file_type=DocumentType.PDF,
        chunks=chunks,
        source_unit_count=1,
        text_unit_count=1,
    )


def _read_request_inputs(
    request: httpx2.Request,
) -> list[str]:
    """MockTransport 요청 본문에서 TEI inputs 문자열 목록을 읽는다."""

    payload: object = json.loads(
        request.content.decode("utf-8"),
    )

    assert isinstance(payload, dict)

    inputs = payload.get("inputs")

    assert isinstance(inputs, list)

    normalized_inputs: list[str] = []

    for value in inputs:
        assert isinstance(value, str)
        normalized_inputs.append(value)

    return normalized_inputs


@pytest.mark.asyncio
async def test_embed_generates_vectors_in_configured_batches() -> None:
    """모든 청크를 설정된 배치 크기로 나누어 임베딩해야 한다."""

    received_batches: list[list[str]] = []

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        # 문서 임베딩 요청은 TEI의 /embed 엔드포인트를 사용해야 한다.
        assert request.method == "POST"
        assert request.url.path == "/embed"

        inputs = _read_request_inputs(request)
        received_batches.append(inputs)

        # 실제 TEI 서버를 실행하지 않고 입력 문자열마다
        # 고정된 3차원 테스트 벡터를 반환한다.
        return httpx2.Response(
            status_code=200,
            json=[
                [
                    float(len(content)),
                    1.0,
                    -1.0,
                ]
                for content in inputs
            ],
        )

    document = _create_chunked_document(
        "first chunk",
        "second chunk",
        "third chunk",
    )

    embedder = TeiChunkEmbedder(
        _create_settings(
            embedding_dim=3,
            embedding_batch_size=2,
        ),
        transport=httpx2.MockTransport(handler),
    )

    result = await embedder.embed(
        document=document,
    )

    # 세 청크를 배치 크기 2로 처리하므로
    # 첫 요청에는 두 청크, 두 번째 요청에는 한 청크가 포함되어야 한다.
    assert received_batches == [
        [
            "first chunk",
            "second chunk",
        ],
        [
            "third chunk",
        ],
    ]

    assert result.embedding_model == "test/embedding-model"
    assert result.embedding_dim == 3
    assert result.chunk_count == 3

    # 임베딩 생성 과정에서 원본 청크의 순서와 Chunk ID가
    # 변경되지 않았는지 확인한다.
    assert tuple(embedded_chunk.chunk_id for embedded_chunk in result.chunks) == tuple(
        chunk.chunk_id for chunk in document.chunks
    )

    assert result.chunks[0].embedding == (
        float(len("first chunk")),
        1.0,
        -1.0,
    )
    assert result.chunks[1].embedding == (
        float(len("second chunk")),
        1.0,
        -1.0,
    )
    assert result.chunks[2].embedding == (
        float(len("third chunk")),
        1.0,
        -1.0,
    )


@pytest.mark.asyncio
async def test_embed_sends_only_chunk_content_as_document_input() -> None:
    """문서 임베딩에는 질의용 instruction 없이 청크 원문만 전달해야 한다."""

    received_payloads: list[object] = []

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        payload: object = json.loads(
            request.content.decode("utf-8"),
        )
        received_payloads.append(payload)

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

    document = _create_chunked_document(
        "original document chunk",
    )

    embedder = TeiChunkEmbedder(
        _create_settings(
            embedding_dim=3,
            embedding_batch_size=2,
        ),
        transport=httpx2.MockTransport(handler),
    )

    await embedder.embed(
        document=document,
    )

    # 문서 청크 임베딩에는 검색 질의용 instruction이나
    # 모델 이름을 요청 본문에 추가하지 않는다.
    #
    # 모델 선택은 TEI 컨테이너 시작 설정에서 고정한다.
    assert received_payloads == [
        {
            "inputs": [
                "original document chunk",
            ],
        }
    ]


@pytest.mark.asyncio
async def test_embed_preserves_chunk_metadata() -> None:
    """임베딩 결과가 원본 TextChunk와 메타데이터를 유지해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
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

    document = _create_chunked_document(
        "first chunk",
    )

    source_chunk = document.chunks[0]

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    result = await embedder.embed(
        document=document,
    )

    embedded_chunk = result.chunks[0]

    assert embedded_chunk.chunk is source_chunk
    assert embedded_chunk.chunk_id == source_chunk.chunk_id
    assert embedded_chunk.chunk_index == source_chunk.chunk_index
    assert embedded_chunk.chunk.content == source_chunk.content
    assert embedded_chunk.chunk.content_hash == source_chunk.content_hash
    assert embedded_chunk.chunk.source_metadata == source_chunk.source_metadata


@pytest.mark.asyncio
async def test_embed_rejects_vector_count_mismatch() -> None:
    """TEI 벡터 수가 요청한 청크 수와 다르면 응답을 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        # 두 청크를 요청했지만 벡터 하나만 반환하는 잘못된 응답이다.
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

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="expected 2 vectors but received 1",
    ):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
                "second chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_rejects_vector_dimension_mismatch() -> None:
    """TEI 벡터 차원이 설정된 embedding_dim과 다르면 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        # 설정은 3차원이지만 TEI가 2차원 벡터를 반환하는 상황을 재현한다.
        return httpx2.Response(
            status_code=200,
            json=[
                [
                    0.1,
                    0.2,
                ]
            ],
        )

    embedder = TeiChunkEmbedder(
        _create_settings(
            embedding_dim=3,
        ),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="dimension 2 instead of 3",
    ):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_rejects_non_numeric_vector_value() -> None:
    """TEI 벡터에 숫자가 아닌 값이 포함되면 응답을 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            json=[
                [
                    0.1,
                    "invalid",
                    0.3,
                ]
            ],
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="is not numeric",
    ):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_rejects_boolean_vector_value() -> None:
    """bool 값을 숫자 벡터 성분으로 허용하지 않아야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        # Python에서 bool은 int의 하위 타입이므로
        # 임베딩 값 검증에서 명시적으로 제외해야 한다.
        return httpx2.Response(
            status_code=200,
            json=[
                [
                    0.1,
                    True,
                    0.3,
                ]
            ],
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="is not numeric",
    ):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_rejects_non_list_response_root() -> None:
    """TEI 응답 최상위 값이 배열이 아니면 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            json={
                "embeddings": [
                    [
                        0.1,
                        0.2,
                        0.3,
                    ]
                ]
            },
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="response root must be a list",
    ):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_rejects_non_list_vector() -> None:
    """개별 임베딩 벡터가 배열이 아니면 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            json=[
                {
                    "values": [
                        0.1,
                        0.2,
                        0.3,
                    ]
                }
            ],
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="vector 0 must be a list",
    ):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_rejects_invalid_json_response() -> None:
    """TEI 성공 응답 본문이 JSON이 아니면 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            content=b"not-json",
            headers={
                "Content-Type": "text/plain",
            },
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(
        InvalidEmbeddingResponseError,
        match="response body is not valid JSON",
    ):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_converts_timeout_to_embedding_error() -> None:
    """TEI 요청 시간 초과를 임베딩 계층 예외로 변환해야 한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        raise httpx2.ReadTimeout(
            "Embedding request timed out.",
            request=request,
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(EmbeddingServiceTimeoutError):
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )


@pytest.mark.asyncio
async def test_embed_converts_connection_error_to_unavailable_error() -> None:
    """TEI 연결 실패를 서비스 사용 불가 예외로 변환해야 한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        raise httpx2.ConnectError(
            "Embedding service connection failed.",
            request=request,
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(EmbeddingServiceUnavailableError) as exception_info:
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )

    # HTTP 응답을 받기 전에 연결이 실패했으므로 상태 코드는 존재하지 않는다.
    assert exception_info.value.status_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code",
    [
        429,
        500,
        503,
    ],
)
async def test_embed_converts_retryable_response_to_unavailable_error(
    status_code: int,
) -> None:
    """TEI 과부하와 서버 오류를 서비스 사용 불가 예외로 변환해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=status_code,
            json={
                "error": "temporarily unavailable",
            },
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(EmbeddingServiceUnavailableError) as exception_info:
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )

    assert exception_info.value.status_code == status_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code",
    [
        400,
        401,
        403,
        404,
        422,
    ],
)
async def test_embed_converts_non_retryable_response_to_rejected_error(
    status_code: int,
) -> None:
    """TEI의 재시도 불가능한 4xx 응답을 요청 거부 예외로 변환해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=status_code,
            json={
                "error": "invalid request",
            },
        )

    embedder = TeiChunkEmbedder(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(EmbeddingServiceRejectedError) as exception_info:
        await embedder.embed(
            document=_create_chunked_document(
                "first chunk",
            ),
        )

    assert exception_info.value.status_code == status_code
