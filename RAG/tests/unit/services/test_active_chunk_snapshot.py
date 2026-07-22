"""최신 활성 청크 스냅샷의 File_IDX lock 경계를 테스트한다."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)

import pytest

from jipsa_rag.infrastructure.indexing.chunk_snapshot_models import (
    IndexedChunkSnapshot,
    IndexedDocumentSnapshot,
)
from jipsa_rag.services.active_chunk_snapshot import (
    ActiveChunkSnapshotService,
)

_TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
_TEST_CONTENT = "최신 활성 청크"
_TEST_CONTENT_HASH = hashlib.sha256(
    _TEST_CONTENT.encode(
        "utf-8",
    )
).hexdigest()


def _create_snapshot() -> IndexedDocumentSnapshot:
    """성공 콜백 잠금 범위 검증에 사용할 최신 활성 스냅샷을 생성한다."""

    return IndexedDocumentSnapshot(
        rag_document_idx=200,
        users_idx=45,
        file_idx=123,
        index_version=2,
        chunk_count=1,
        chunks=(
            IndexedChunkSnapshot(
                chunk_id=_TEST_CHUNK_ID,
                chunk_index=0,
                content=_TEST_CONTENT,
                content_hash=_TEST_CONTENT_HASH,
                token_count=None,
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )


class StubActiveChunkSnapshotRepository:
    """호출 순서를 기록하며 최신 활성 스냅샷을 반환하는 저장소 대역."""

    def __init__(
        self,
        *,
        snapshot: IndexedDocumentSnapshot,
        events: list[str],
    ) -> None:
        """반환할 스냅샷과 공유 이벤트 목록을 저장한다."""

        self._snapshot = snapshot
        self._events = events

    async def fetch_latest_active_chunk_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
    ) -> IndexedDocumentSnapshot:
        """현재 파일 범위를 검증한 뒤 저장소 조회 이벤트를 기록한다."""

        assert users_idx == 45
        assert file_idx == 123

        self._events.append("snapshot_fetched")

        return self._snapshot


class RecordingFileIndexLock:
    """File_IDX lock 획득과 해제 순서를 기록하는 대역."""

    def __init__(
        self,
        *,
        events: list[str],
    ) -> None:
        """공유 이벤트 목록을 저장한다."""

        self._events = events

    def hold(
        self,
        *,
        file_idx: int,
    ) -> AbstractAsyncContextManager[None]:
        """지정한 파일의 기록용 비동기 lock context를 반환한다."""

        return self._hold(
            file_idx=file_idx,
        )

    @asynccontextmanager
    async def _hold(
        self,
        *,
        file_idx: int,
    ) -> AsyncIterator[None]:
        """lock 획득과 해제를 성공 콜백 이벤트 전후에 기록한다."""

        assert file_idx == 123

        self._events.append("lock_acquired")

        try:
            yield
        finally:
            self._events.append("lock_released")


@pytest.mark.asyncio
async def test_holds_file_lock_until_success_callback_scope_finishes() -> None:
    """최신 청크 조회부터 성공 콜백 완료까지 File_IDX lock을 유지해야 한다."""

    events: list[str] = []
    snapshot = _create_snapshot()
    service = ActiveChunkSnapshotService(
        repository=StubActiveChunkSnapshotRepository(
            snapshot=snapshot,
            events=events,
        ),
        file_lock=RecordingFileIndexLock(
            events=events,
        ),
    )

    async with service.hold_latest_active_snapshot(
        users_idx=45,
        file_idx=123,
    ) as active_snapshot:
        assert active_snapshot is snapshot
        assert events == [
            "lock_acquired",
            "snapshot_fetched",
        ]

        # 실제 ingest endpoint는 이 위치에서 성공 콜백을 전송한다.
        # 콜백 처리 중에는 아직 lock 해제 이벤트가 없어야 한다.
        events.append("success_callback_completed")

        assert "lock_released" not in events

    assert events == [
        "lock_acquired",
        "snapshot_fetched",
        "success_callback_completed",
        "lock_released",
    ]


@pytest.mark.asyncio
async def test_snapshot_only_compatibility_method_releases_lock_after_read() -> None:
    """호환용 단순 조회 메서드도 동일한 저장소와 lock을 사용해야 한다."""

    events: list[str] = []
    snapshot = _create_snapshot()
    service = ActiveChunkSnapshotService(
        repository=StubActiveChunkSnapshotRepository(
            snapshot=snapshot,
            events=events,
        ),
        file_lock=RecordingFileIndexLock(
            events=events,
        ),
    )

    active_snapshot = await service.fetch_latest_active_snapshot(
        users_idx=45,
        file_idx=123,
    )

    assert active_snapshot is snapshot
    assert events == [
        "lock_acquired",
        "snapshot_fetched",
        "lock_released",
    ]
