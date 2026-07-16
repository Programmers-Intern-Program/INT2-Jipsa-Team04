"""Hugging Face TEI를 이용하여 청크별 임베딩을 생성한다."""

from http import HTTPStatus
from typing import Final, cast

import httpx2

from jipsa_rag.core.config import Settings
from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    TextChunk,
)
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
    EmbeddingVector,
)

_TEI_EMBED_PATH: Final[str] = "/embed"


class TeiChunkEmbedder:
    """Hugging Face Text Embeddings Inference HTTP 클라이언트."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """임베딩 설정과 테스트용 HTTP transport를 주입받는다."""

        self._settings = settings
        self._transport = transport

    async def embed(
        self,
        *,
        document: ChunkedDocument,
    ) -> EmbeddedDocument:
        """문서의 모든 청크를 배치 단위로 TEI에 전달한다."""

        timeout_seconds = self._settings.embedding_timeout_seconds

        timeout = httpx2.Timeout(
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )

        embedded_chunks: list[EmbeddedChunk] = []

        async with httpx2.AsyncClient(
            base_url=self._settings.embedding_base_url,
            timeout=timeout,
            follow_redirects=False,
            # 로컬 TEI 연결이 시스템 프록시 설정의 영향을 받지 않도록 한다.
            trust_env=False,
            transport=self._transport,
        ) as client:
            for batch_start_index in range(
                0,
                document.chunk_count,
                self._settings.embedding_batch_size,
            ):
                batch_end_index = min(
                    batch_start_index + self._settings.embedding_batch_size,
                    document.chunk_count,
                )

                chunk_batch = document.chunks[
                    batch_start_index:batch_end_index
                ]

                embedding_batch = await self._request_embedding_batch(
                    client=client,
                    chunks=chunk_batch,
                    batch_start_index=batch_start_index,
                )

                # 응답 벡터 개수는 _parse_embedding_response에서 이미
                # 검증했으므로 strict=True로 일대일 결합한다.
                for chunk, embedding in zip(
                    chunk_batch,
                    embedding_batch,
                    strict=True,
                ):
                    embedded_chunks.append(
                        EmbeddedChunk(
                            chunk=chunk,
                            embedding=embedding,
                        )
                    )

        return EmbeddedDocument(
            embedding_model=self._settings.embedding_model,
            embedding_dim=self._settings.embedding_dim,
            chunks=tuple(embedded_chunks),
        )

    async def _request_embedding_batch(
        self,
        *,
        client: httpx2.AsyncClient,
        chunks: tuple[TextChunk, ...],
        batch_start_index: int,
    ) -> tuple[EmbeddingVector, ...]:
        """단일 청크 배치를 TEI /embed 엔드포인트에 요청한다."""

        # Qwen3 임베딩에서 검색 문서에는 질의용 instruction을 붙이지 않는다.
        #
        # 검색어 임베딩을 구현할 때만 검색 목적에 맞는 instruction을
        # 별도 query embedder에서 적용한다.
        inputs = [chunk.content for chunk in chunks]

        try:
            response = await client.post(
                _TEI_EMBED_PATH,
                json={
                    "inputs": inputs,
                },
            )

        except httpx2.TimeoutException as error:
            raise EmbeddingServiceTimeoutError from error

        except httpx2.RequestError as error:
            raise EmbeddingServiceUnavailableError from error

        status_code = response.status_code

        if (
            status_code == HTTPStatus.TOO_MANY_REQUESTS
            or status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        ):
            raise EmbeddingServiceUnavailableError(
                status_code=status_code,
            )

        if not HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES:
            # TEI 응답 본문에는 내부 모델 정보 등이 포함될 수 있으므로
            # 예외나 로그 컨텍스트에 원문 응답을 저장하지 않는다.
            raise EmbeddingServiceRejectedError(
                status_code=status_code,
            )

        try:
            payload: object = response.json()
        except ValueError as error:
            raise InvalidEmbeddingResponseError(
                reason="response body is not valid JSON",
                batch_start_index=batch_start_index,
            ) from error

        return self._parse_embedding_response(
            payload=payload,
            expected_count=len(chunks),
            batch_start_index=batch_start_index,
        )

    def _parse_embedding_response(
        self,
        *,
        payload: object,
        expected_count: int,
        batch_start_index: int,
    ) -> tuple[EmbeddingVector, ...]:
        """TEI 응답의 벡터 개수, 값 타입 및 차원을 검증한다."""

        if not isinstance(payload, list):
            raise InvalidEmbeddingResponseError(
                reason="response root must be a list",
                batch_start_index=batch_start_index,
            )

        raw_vectors = cast(list[object], payload)

        if len(raw_vectors) != expected_count:
            raise InvalidEmbeddingResponseError(
                reason=(
                    f"expected {expected_count} vectors "
                    f"but received {len(raw_vectors)}"
                ),
                batch_start_index=batch_start_index,
            )

        normalized_vectors: list[EmbeddingVector] = []

        for vector_offset, raw_vector in enumerate(raw_vectors):
            if not isinstance(raw_vector, list):
                raise InvalidEmbeddingResponseError(
                    reason=f"vector {vector_offset} must be a list",
                    batch_start_index=batch_start_index,
                )

            raw_values = cast(list[object], raw_vector)

            if len(raw_values) != self._settings.embedding_dim:
                raise InvalidEmbeddingResponseError(
                    reason=(
                        f"vector {vector_offset} has dimension "
                        f"{len(raw_values)} instead of "
                        f"{self._settings.embedding_dim}"
                    ),
                    batch_start_index=batch_start_index,
                )

            normalized_vector: list[float] = []

            for value_offset, raw_value in enumerate(raw_values):
                if isinstance(raw_value, bool) or not isinstance(
                    raw_value,
                    (
                        int,
                        float,
                    ),
                ):
                    raise InvalidEmbeddingResponseError(
                        reason=(
                            f"vector {vector_offset} value "
                            f"{value_offset} is not numeric"
                        ),
                        batch_start_index=batch_start_index,
                    )

                normalized_vector.append(float(raw_value))

            normalized_vectors.append(tuple(normalized_vector))

        return tuple(normalized_vectors)