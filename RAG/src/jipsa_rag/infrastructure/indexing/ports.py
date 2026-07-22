"""파일 색인 서비스가 의존하는 저장소 인터페이스를 정의한다."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from jipsa_rag.infrastructure.embedding.models import EmbeddedDocument
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)


class FileIndexLock(Protocol):
    """동일 File_IDX 색인의 임계 구역을 직렬화하는 lock 인터페이스."""

    def hold(
        self,
        *,
        file_idx: int,
    ) -> AbstractAsyncContextManager[None]:
        """지정한 파일의 색인 lock을 보유하는 비동기 context를 반환한다."""

        ...


class LocalIndexRepository(Protocol):
    """Local RAG DB 문서·청크·실행 이력 저장 인터페이스."""

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        """문서와 청크를 준비하고 색인 실행을 RUNNING으로 기록한다."""

        ...

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        superseded_rag_document_idxs: tuple[int, ...],
    ) -> None:
        """현재 문서·실행을 성공 처리하고 대체된 이전 문서를 soft delete한다."""

        ...

    async def mark_failed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """신규 문서와 색인 실행을 실패 상태로 변경한다."""

        ...

    async def mark_run_failed(
        self,
        *,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """기존 정상 문서를 유지하면서 이번 색인 실행만 실패 처리한다."""

        ...


class ChunkVectorStore(Protocol):
    """청크 임베딩과 검색 payload를 저장하는 VectorDB 인터페이스."""

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
        is_active: bool,
    ) -> None:
        """문서의 모든 청크 Point를 지정한 활성 상태로 업서트한다."""

        ...

    async def set_documents_active(
        self,
        *,
        rag_document_idxs: tuple[int, ...],
        is_active: bool,
    ) -> None:
        """문서 식별자에 속한 모든 Point의 활성 상태를 일괄 변경한다."""

        ...

    async def delete_chunks(
        self,
        *,
        chunk_ids: tuple[str, ...],
    ) -> None:
        """보상 처리 대상 청크 Point를 VectorDB에서 삭제한다."""

        ...
