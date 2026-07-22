"""최신 활성 청크 스냅샷 조회를 파일 단위 lock으로 보호한다."""

from typing import Protocol

from jipsa_rag.infrastructure.indexing.chunk_snapshot_models import (
    IndexedDocumentSnapshot,
)
from jipsa_rag.infrastructure.indexing.ports import FileIndexLock


class ActiveChunkSnapshotRepository(Protocol):
    """최종 활성 문서의 전체 청크를 조회하는 저장소 인터페이스."""

    async def fetch_latest_active_chunk_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
        rag_document_idx: int,
    ) -> IndexedDocumentSnapshot:
        """최종 문서가 최신 활성 색인이면 전체 청크를 반환한다."""

        ...


class ActiveChunkSnapshotService:
    """파일별 최종 색인과 성공 콜백 사이의 스냅샷 일관성을 보장한다.

    FileIndexingService가 색인 완료 후 file lock을 해제한 직후 다른 재색인
    요청이 시작될 수 있다.

    따라서 성공 콜백에 넣을 청크를 읽을 때 같은 File_IDX advisory lock을
    다시 획득한다. lock 획득 전에 더 최신 재색인이 완료되어 기존 문서가
    대체된 경우 저장소 조회가 실패하므로 오래된 청크를 성공 payload로
    전송하지 않는다.
    """

    def __init__(
        self,
        *,
        repository: ActiveChunkSnapshotRepository,
        file_lock: FileIndexLock,
    ) -> None:
        """활성 청크 저장소와 File_IDX별 lock을 주입받는다."""

        self._repository = repository
        self._file_lock = file_lock

    async def fetch_latest_active_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
        rag_document_idx: int,
    ) -> IndexedDocumentSnapshot:
        """파일 lock 안에서 최종 문서의 최신 활성 청크 전체를 조회한다."""

        async with self._file_lock.hold(
            file_idx=file_idx,
        ):
            return await self._repository.fetch_latest_active_chunk_snapshot(
                users_idx=users_idx,
                file_idx=file_idx,
                rag_document_idx=rag_document_idx,
            )
