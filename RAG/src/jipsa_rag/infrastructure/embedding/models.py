"""청크별 임베딩 생성 결과 모델을 정의한다."""

import math
from dataclasses import dataclass

from jipsa_rag.infrastructure.chunking.models import TextChunk

type EmbeddingVector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """원본 TextChunk와 해당 청크의 임베딩 벡터를 결합한다."""

    chunk: TextChunk
    embedding: EmbeddingVector

    def __post_init__(self) -> None:
        """임베딩 벡터가 비어 있지 않고 유한한 숫자로 구성되었는지 검증한다."""

        normalized_embedding: list[float] = []

        for value in self.embedding:
            # bool은 int의 하위 타입이지만 임베딩 벡터 값으로는 허용하지 않는다.
            if isinstance(value, bool) or not isinstance(
                value,
                (
                    int,
                    float,
                ),
            ):
                raise ValueError("embedding values must be numeric.")

            normalized_value = float(value)

            # NaN과 양수·음수 무한대는 Qdrant에 저장하거나
            # 벡터 유사도 계산에 사용할 수 없는 값이다.
            if not math.isfinite(normalized_value):
                raise ValueError("embedding values must be finite.")

            normalized_embedding.append(normalized_value)

        if not normalized_embedding:
            raise ValueError("embedding must contain at least one value.")

        object.__setattr__(
            self,
            "embedding",
            tuple(normalized_embedding),
        )

    @property
    def chunk_id(self) -> str:
        """Local RAG DB와 VectorDB에서 사용할 Chunk ID를 반환한다."""

        return self.chunk.chunk_id

    @property
    def chunk_index(self) -> int:
        """문서 안에서의 청크 순번을 반환한다."""

        return self.chunk.chunk_index


@dataclass(frozen=True, slots=True)
class EmbeddedDocument:
    """문서 하나에서 생성된 전체 청크 임베딩 결과."""

    embedding_model: str
    embedding_dim: int
    chunks: tuple[EmbeddedChunk, ...]

    def __post_init__(self) -> None:
        """모델명, 차원, 청크 순서 및 벡터 크기의 일관성을 검증한다."""

        normalized_model = self.embedding_model.strip()
        normalized_chunks = tuple(self.chunks)

        if not normalized_model:
            raise ValueError("embedding_model must not be empty.")

        if self.embedding_dim <= 0:
            raise ValueError("embedding_dim must be greater than zero.")

        if not normalized_chunks:
            raise ValueError("chunks must contain at least one embedded chunk.")

        expected_chunk_indexes = tuple(range(len(normalized_chunks)))
        actual_chunk_indexes = tuple(
            embedded_chunk.chunk_index for embedded_chunk in normalized_chunks
        )

        if actual_chunk_indexes != expected_chunk_indexes:
            raise ValueError("embedded chunk indexes must start at zero and be contiguous.")

        chunk_ids = tuple(embedded_chunk.chunk_id for embedded_chunk in normalized_chunks)

        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("embedded chunk IDs must be unique.")

        for embedded_chunk in normalized_chunks:
            actual_dimension = len(embedded_chunk.embedding)

            if actual_dimension != self.embedding_dim:
                raise ValueError("embedding dimension does not match the configured embedding_dim.")

        object.__setattr__(
            self,
            "embedding_model",
            normalized_model,
        )
        object.__setattr__(
            self,
            "chunks",
            normalized_chunks,
        )

    @property
    def chunk_count(self) -> int:
        """임베딩이 생성된 전체 청크 수를 반환한다."""

        return len(self.chunks)
