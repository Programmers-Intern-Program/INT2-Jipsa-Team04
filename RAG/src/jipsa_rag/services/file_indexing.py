"""Local RAG DB와 Qdrant 저장을 하나의 파일 색인 흐름으로 조정한다."""

import logging
from dataclasses import dataclass

from jipsa_rag.infrastructure.embedding.models import EmbeddedDocument
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
    VectorDatabaseError,
)
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)
from jipsa_rag.infrastructure.indexing.ports import (
    ChunkVectorStore,
    LocalIndexRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileIndexingResult:
    """문서와 청크 색인이 완료된 뒤 API 계층에 반환할 결과."""

    rag_document_idx: int
    rag_index_run_idx: int
    chunk_count: int


class FileIndexingService:
    """Local RAG DB와 Qdrant 사이의 색인 및 보상 처리를 조정한다.

    신규 색인은 Qdrant에 비활성 staging Point로 먼저 저장한다.
    모든 Point 저장이 완료된 뒤 신규 문서를 활성화하고 이전 정상 문서를
    비활성화한 다음 Local RAG 상태를 성공으로 확정한다.

    어느 단계에서든 실패하면 이전 정상 문서는 다시 활성 상태로 복구하고,
    이번 실행이 만든 신규 Point만 비활성화·삭제한다. 동일한 정상 색인을
    멱등 재사용한 실행은 기존 문서와 Point를 삭제하지 않고 실행 이력만
    실패 처리한다.
    """

    def __init__(
        self,
        *,
        local_repository: LocalIndexRepository,
        vector_store: ChunkVectorStore,
    ) -> None:
        """Local RAG 저장소와 Qdrant 저장소를 주입받는다."""

        self._local_repository = local_repository
        self._vector_store = vector_store

    async def index(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> FileIndexingResult:
        """문서 색인을 staging, 활성 전환 및 보상 처리 순서로 수행한다."""

        prepared_index = await self._local_repository.prepare_indexing(
            metadata=metadata,
            embedded_document=embedded_document,
        )

        # 이미 INDEXED인 동일 식별자를 재사용하는 실행은 기존 Point를
        # 그대로 활성 상태로 업서트한다. 신규 문서는 검색에 노출되지 않도록
        # 비활성 상태로 먼저 staging한다.
        initial_is_active = prepared_index.reuses_existing_index

        try:
            await self._vector_store.upsert_document(
                rag_document_idx=(prepared_index.rag_document_idx),
                metadata=metadata,
                embedded_document=embedded_document,
                is_active=initial_is_active,
            )

        except VectorDatabaseError as error:
            # 신규 문서 업서트가 부분 성공했을 수 있으므로 이번 실행의
            # 비활성 staging Point만 보상 삭제한다.
            #
            # 기존 정상 색인을 재사용한 경우 같은 Point ID가 기존 검색
            # 데이터이므로 절대 삭제하지 않는다.
            if not prepared_index.reuses_existing_index:
                await self._delete_chunks_safely(
                    prepared_index=prepared_index,
                    users_idx=metadata.users_idx,
                    file_idx=metadata.file_idx,
                    event=("file_index_upsert_cleanup_failed"),
                )

            await self._mark_failure_state_safely(
                prepared_index=prepared_index,
                users_idx=metadata.users_idx,
                file_idx=metadata.file_idx,
                original_error=error,
            )
            raise

        try:
            if not prepared_index.reuses_existing_index:
                # 신규 Point 전체 업서트가 완료된 뒤에만 현재 문서를
                # 실제 검색 대상으로 활성화한다.
                await self._vector_store.set_documents_active(
                    rag_document_idxs=(prepared_index.rag_document_idx,),
                    is_active=True,
                )

            # 이전 정상 문서는 신규 문서가 활성화된 뒤 비활성화한다.
            #
            # 두 호출 사이의 짧은 구간에는 중복 결과가 존재할 수 있지만,
            # 신규 실패 때문에 검색 결과가 완전히 사라지는 구간은 만들지 않는다.
            await self._vector_store.set_documents_active(
                rag_document_idxs=(prepared_index.previous_rag_document_idxs),
                is_active=False,
            )

            # Qdrant 활성 전환이 모두 끝난 뒤 Local RAG 상태를 최종 확정한다.
            await self._local_repository.mark_indexed(
                rag_document_idx=(prepared_index.rag_document_idx),
                rag_index_run_idx=(prepared_index.rag_index_run_idx),
                superseded_rag_document_idxs=(prepared_index.previous_rag_document_idxs),
            )

        except (
            VectorDatabaseError,
            LocalRagStorageError,
        ) as error:
            # 이전 정상 문서 일부가 비활성화된 상태일 수 있으므로 먼저 복구한다.
            await self._restore_previous_documents_safely(
                prepared_index=prepared_index,
                users_idx=metadata.users_idx,
                file_idx=metadata.file_idx,
            )

            if not prepared_index.reuses_existing_index:
                # 현재 실행이 새로 만든 Point만 검색 대상에서 제거하고 삭제한다.
                # 이전 정상 Point에는 손대지 않는다.
                await self._remove_new_document_safely(
                    prepared_index=prepared_index,
                    users_idx=metadata.users_idx,
                    file_idx=metadata.file_idx,
                )

            await self._mark_failure_state_safely(
                prepared_index=prepared_index,
                users_idx=metadata.users_idx,
                file_idx=metadata.file_idx,
                original_error=error,
            )
            raise

        return FileIndexingResult(
            rag_document_idx=(prepared_index.rag_document_idx),
            rag_index_run_idx=(prepared_index.rag_index_run_idx),
            chunk_count=(embedded_document.chunk_count),
        )

    async def _restore_previous_documents_safely(
        self,
        *,
        prepared_index: PreparedLocalIndex,
        users_idx: int,
        file_idx: int,
    ) -> None:
        """실패 보상 중 이전 정상 문서를 다시 활성화한다."""

        if not prepared_index.previous_rag_document_idxs:
            return

        try:
            await self._vector_store.set_documents_active(
                rag_document_idxs=(prepared_index.previous_rag_document_idxs),
                is_active=True,
            )

        except VectorDatabaseError as compensation_error:
            logger.exception(
                "Failed to reactivate previous Qdrant points during index compensation.",
                extra={
                    "event": ("file_index_previous_reactivation_failed"),
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "rag_document_idx": (prepared_index.rag_document_idx),
                    "compensation_error_type": (type(compensation_error).__name__),
                },
            )

    async def _remove_new_document_safely(
        self,
        *,
        prepared_index: PreparedLocalIndex,
        users_idx: int,
        file_idx: int,
    ) -> None:
        """실패한 신규 문서 Point를 비활성화한 뒤 보상 삭제한다."""

        try:
            await self._vector_store.set_documents_active(
                rag_document_idxs=(prepared_index.rag_document_idx,),
                is_active=False,
            )

        except VectorDatabaseError as compensation_error:
            # 비활성화가 실패해도 Point ID 기반 삭제를 계속 시도한다.
            logger.exception(
                "Failed to deactivate new Qdrant points during index compensation.",
                extra={
                    "event": ("file_index_new_deactivation_failed"),
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "rag_document_idx": (prepared_index.rag_document_idx),
                    "compensation_error_type": (type(compensation_error).__name__),
                },
            )

        await self._delete_chunks_safely(
            prepared_index=prepared_index,
            users_idx=users_idx,
            file_idx=file_idx,
            event=("file_index_new_point_delete_failed"),
        )

    async def _delete_chunks_safely(
        self,
        *,
        prepared_index: PreparedLocalIndex,
        users_idx: int,
        file_idx: int,
        event: str,
    ) -> None:
        """원래 색인 오류를 보존하면서 신규 Point 보상 삭제를 시도한다."""

        try:
            await self._vector_store.delete_chunks(
                chunk_ids=prepared_index.chunk_ids,
            )

        except VectorDatabaseError as compensation_error:
            logger.exception(
                "Failed to delete staged Qdrant points during index compensation.",
                extra={
                    "event": event,
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "rag_document_idx": (prepared_index.rag_document_idx),
                    "compensation_error_type": (type(compensation_error).__name__),
                },
            )

    async def _mark_failure_state_safely(
        self,
        *,
        prepared_index: PreparedLocalIndex,
        users_idx: int,
        file_idx: int,
        original_error: Exception,
    ) -> None:
        """원래 오류를 보존하면서 Local RAG 실패 상태를 기록한다.

        동일한 정상 문서를 재사용한 실행은 문서 상태를 INDEXED로 유지하고
        이번 RAG_Index_Run만 FAILED로 바꾼다.
        """

        try:
            if prepared_index.reuses_existing_index:
                await self._local_repository.mark_run_failed(
                    rag_index_run_idx=(prepared_index.rag_index_run_idx),
                    error_message=(type(original_error).__name__),
                )
            else:
                await self._local_repository.mark_failed(
                    rag_document_idx=(prepared_index.rag_document_idx),
                    rag_index_run_idx=(prepared_index.rag_index_run_idx),
                    error_message=(type(original_error).__name__),
                )

        except LocalRagStorageError as state_error:
            logger.exception(
                "Failed to update Local RAG failure state after index error.",
                extra={
                    "event": ("file_index_failure_state_update_failed"),
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "rag_document_idx": (prepared_index.rag_document_idx),
                    "state_error_type": (type(state_error).__name__),
                },
            )
