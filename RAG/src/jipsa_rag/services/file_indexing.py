"""Local RAG DB와 Qdrant 저장을 하나의 파일 색인 흐름으로 조정한다."""

import logging
from dataclasses import dataclass

from jipsa_rag.infrastructure.embedding.models import EmbeddedDocument
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
    VectorDatabaseError,
)
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata
from jipsa_rag.infrastructure.indexing.ports import (
    ChunkVectorStore,
    LocalIndexRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileIndexingResult:
    """Local RAG DB와 Qdrant 저장이 모두 완료된 색인 결과."""

    rag_document_idx: int
    rag_index_run_idx: int
    chunk_count: int

    def __post_init__(self) -> None:
        """응답에 사용할 식별자와 청크 개수를 검증한다."""

        if self.rag_document_idx <= 0:
            raise ValueError("rag_document_idx must be greater than zero.")

        if self.rag_index_run_idx <= 0:
            raise ValueError("rag_index_run_idx must be greater than zero.")

        if self.chunk_count <= 0:
            raise ValueError("chunk_count must be greater than zero.")


class FileIndexingService:
    """문서·청크 저장, 벡터 업서트 및 최종 상태 갱신을 조정한다.

    Local RAG DB와 Qdrant는 서로 다른 저장소이므로 하나의 원자적
    트랜잭션으로 묶을 수 없다.

    따라서 다음 순서를 사용한다.

    1. Local RAG DB에 문서와 청크를 저장하고 실행 상태를 RUNNING으로 기록한다.
    2. Qdrant에 임베딩과 검색 payload를 업서트한다.
    3. Qdrant 성공 후 Local RAG 상태를 INDEXED/SUCCESS로 확정한다.
    4. Qdrant 실패 시 Local RAG 상태를 FAILED로 변경한다.
    5. Qdrant 성공 후 Local 상태 확정이 실패하면 Point를 보상 삭제한다.

    S3_Key는 AWS 서버 DB가 관리하므로 이 서비스의 입력과 저장 흐름에
    포함하지 않는다.
    """

    def __init__(
        self,
        *,
        local_repository: LocalIndexRepository,
        vector_store: ChunkVectorStore,
    ) -> None:
        """Local RAG 저장소와 VectorDB 저장소 구현체를 주입받는다."""

        self._local_repository = local_repository
        self._vector_store = vector_store

    async def index(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> FileIndexingResult:
        """Local RAG DB를 기준 데이터로 저장한 뒤 Qdrant 색인을 완료한다."""

        prepared_index = await self._local_repository.prepare_indexing(
            metadata=metadata,
            embedded_document=embedded_document,
        )

        try:
            await self._vector_store.upsert_document(
                rag_document_idx=prepared_index.rag_document_idx,
                metadata=metadata,
                embedded_document=embedded_document,
            )
        except VectorDatabaseError as error:
            # Qdrant 실패 원문, 벡터 및 청크 내용을 Error_Message에 저장하지 않는다.
            # 운영자가 실패 종류를 식별할 수 있는 안전한 예외 타입 이름만 기록한다.
            await self._mark_failed_safely(
                rag_document_idx=prepared_index.rag_document_idx,
                rag_index_run_idx=prepared_index.rag_index_run_idx,
                error_message=type(error).__name__,
                users_idx=metadata.users_idx,
                file_idx=metadata.file_idx,
            )
            raise

        try:
            await self._local_repository.mark_indexed(
                rag_document_idx=prepared_index.rag_document_idx,
                rag_index_run_idx=prepared_index.rag_index_run_idx,
            )
        except LocalRagStorageError as error:
            # Qdrant 적재는 성공했지만 Local RAG DB가 성공 상태를 기록하지 못하면
            # 미완료 문서가 검색 결과에 노출되지 않도록 방금 업서트한 Point를 삭제한다.
            try:
                await self._vector_store.delete_chunks(
                    chunk_ids=prepared_index.chunk_ids,
                )
            except VectorDatabaseError:
                # 보상 삭제 실패는 원래 Local RAG 오류를 대체하지 않는다.
                # 문서 원문이나 벡터를 로그에 포함하지 않고 식별자만 기록한다.
                logger.exception(
                    "Failed to compensate Qdrant points after Local RAG state failure.",
                    extra={
                        "event": "qdrant_compensation_failed",
                        "users_idx": metadata.users_idx,
                        "file_idx": metadata.file_idx,
                        "rag_document_idx": prepared_index.rag_document_idx,
                    },
                )

            # 가능하면 실행 이력을 FAILED로 전환한다.
            # 데이터베이스 자체가 계속 실패하는 경우에는 로그만 남기고
            # 최초 mark_indexed 오류를 그대로 상위 계층에 전달한다.
            await self._mark_failed_safely(
                rag_document_idx=prepared_index.rag_document_idx,
                rag_index_run_idx=prepared_index.rag_index_run_idx,
                error_message=type(error).__name__,
                users_idx=metadata.users_idx,
                file_idx=metadata.file_idx,
            )
            raise

        return FileIndexingResult(
            rag_document_idx=prepared_index.rag_document_idx,
            rag_index_run_idx=prepared_index.rag_index_run_idx,
            chunk_count=embedded_document.chunk_count,
        )

    async def _mark_failed_safely(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
        users_idx: int,
        file_idx: int,
    ) -> None:
        """실패 상태 기록 오류가 최초 저장 오류를 덮어쓰지 않도록 처리한다."""

        try:
            await self._local_repository.mark_failed(
                rag_document_idx=rag_document_idx,
                rag_index_run_idx=rag_index_run_idx,
                error_message=error_message,
            )
        except LocalRagStorageError:
            logger.exception(
                "Failed to persist file indexing failure state.",
                extra={
                    "event": "file_index_failure_state_update_failed",
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "rag_document_idx": rag_document_idx,
                },
            )
