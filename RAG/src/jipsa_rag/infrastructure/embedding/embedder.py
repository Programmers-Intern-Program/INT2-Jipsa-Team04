"""청크 임베딩 생성기가 구현해야 하는 공통 인터페이스를 정의한다."""

from typing import Protocol

from jipsa_rag.infrastructure.chunking.models import ChunkedDocument
from jipsa_rag.infrastructure.embedding.models import EmbeddedDocument


class ChunkEmbedder(Protocol):
    """ChunkedDocument를 EmbeddedDocument로 변환하는 인터페이스."""

    async def embed(
        self,
        *,
        document: ChunkedDocument,
    ) -> EmbeddedDocument:
        """문서의 모든 텍스트 청크에 대해 임베딩을 생성한다.

        Args:
            document:
                CharacterTextChunker 등이 반환한 문서 전체 청킹 결과다.

        Returns:
            각 TextChunk와 임베딩 벡터가 결합된 EmbeddedDocument다.

        Notes:
            구현체는 입력 청크의 순서와 Chunk ID를 변경하지 않아야 한다.

            반환되는 벡터 수는 입력 청크 수와 같아야 하며,
            모든 벡터의 차원은 설정된 embedding_dim과 같아야 한다.
        """

        ...