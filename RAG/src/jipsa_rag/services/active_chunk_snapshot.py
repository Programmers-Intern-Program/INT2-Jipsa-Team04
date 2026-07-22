"""최신 활성 청크 스냅샷 조회와 성공 콜백 경계를 파일 단위 lock으로 보호한다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    ) -> IndexedDocumentSnapshot:
        """파일 범위에서 가장 최신인 성공 색인의 전체 청크를 반환한다."""

        ...


class ActiveChunkSnapshotService:
    """파일별 최종 색인과 성공 콜백 사이의 스냅샷 일관성을 보장한다.

    FileIndexingService가 색인 완료 후 file lock을 해제한 직후 다른 재색인
    요청이 시작될 수 있다.

    성공 콜백에 넣을 청크를 조회한 직후 lock을 해제하면, 콜백 전송 전에
    더 최신 재색인이 완료되어 이전 청크 스냅샷이 늦게 전달될 수 있다.

    따라서 이 서비스는 최신 활성 청크 조회부터 성공 콜백 전송 완료까지
    같은 File_IDX advisory lock을 유지할 수 있는 비동기 context manager를
    제공한다.
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

    @asynccontextmanager
    async def hold_latest_active_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
    ) -> AsyncIterator[IndexedDocumentSnapshot]:
        """최신 활성 청크 조회와 후속 성공 콜백을 하나의 lock 범위로 묶는다.

        호출자는 반환된 스냅샷으로 payload를 만든 뒤 반드시 ``async with``
        블록 안에서 성공 콜백까지 전송해야 한다.

        같은 파일의 재색인은 이 context가 종료된 뒤에만 색인 임계 구역에
        진입할 수 있으므로, 조회한 스냅샷이 콜백 전송 중 이전 버전이 되는
        경쟁 조건을 차단한다.
        """

        async with self._file_lock.hold(
            file_idx=file_idx,
        ):
            snapshot = await self._repository.fetch_latest_active_chunk_snapshot(
                users_idx=users_idx,
                file_idx=file_idx,
            )

            yield snapshot

    async def fetch_latest_active_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
    ) -> IndexedDocumentSnapshot:
        """최신 활성 청크 스냅샷만 조회하는 호환용 메서드다.

        성공 콜백과의 원자적 경계가 필요한 호출자는 이 메서드가 아니라
        ``hold_latest_active_snapshot()``을 사용해야 한다.
        """

        async with self.hold_latest_active_snapshot(
            users_idx=users_idx,
            file_idx=file_idx,
        ) as snapshot:
            return snapshot
