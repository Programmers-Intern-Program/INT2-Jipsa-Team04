"""파일 색인 서비스가 의존하는 저장소 인터페이스를 정의한다."""

from typing import Protocol

from jipsa_rag.infrastructure.embedding.models import EmbeddedDocument
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)


class LocalIndexRepository(Protocol):
    """Local RAG DB 문서·청크·실행 이력 저장 인터페이스."""

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        """문서와 청크를 저장하고 색인 실행을 RUNNING으로 준비한다."""

        ...

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
    ) -> None:
        """문서와 색인 실행을 성공 상태로 변경한다."""

        ...

    async def mark_failed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """문서와 색인 실행을 실패 상태로 변경한다."""

        ...


class ChunkVectorStore(Protocol):
    """청크 임베딩과 검색 payload를 저장하는 VectorDB 인터페이스."""

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> None:
        """문서의 모든 청크 Point를 VectorDB에 업서트한다."""

        ...

    async def delete_chunks(
        self,
        *,
        chunk_ids: tuple[str, ...],
    ) -> None:
        """보상 처리 대상 청크 Point를 VectorDB에서 삭제한다."""

        ...
