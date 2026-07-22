"""재색인과 동일 파일 중복 요청의 청크 동기화 회귀 동작을 테스트한다."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import cast

import pytest

import jipsa_rag.api.ingest as ingest_module
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.infrastructure.chunking.character import CharacterTextChunker
from jipsa_rag.infrastructure.document.parser_factory import (
    DocumentParserFactory,
)
from jipsa_rag.infrastructure.embedding.tei import TeiChunkEmbedder
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader
from jipsa_rag.infrastructure.indexing.chunk_snapshot_models import (
    IndexedChunkSnapshot,
    IndexedDocumentSnapshot,
)
from jipsa_rag.schemas.common import ApiResponse
from jipsa_rag.schemas.file_processing import (
    FileProcessingCompletedResponse,
    FileProcessingRequest,
)
from jipsa_rag.schemas.ingestion import ChunkSynchronizationRequest
from jipsa_rag.services.active_chunk_snapshot import ActiveChunkSnapshotService
from jipsa_rag.services.file_indexing import FileIndexingService

_TEST_USERS_IDX = 45
_TEST_FILE_IDX = 123
_TEST_FOLDER_IDX = 9
_TEST_INDEX_VERSION = 2

_OLD_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
_LATEST_FIRST_CHUNK_ID = "22222222-2222-2222-2222-222222222222"
_LATEST_SECOND_CHUNK_ID = "33333333-3333-3333-3333-333333333333"


@dataclass(
    frozen=True,
    slots=True,
)
class RecordedIngestCompleteCallback:
    """애플리케이션 서버 대역이 수신한 완료 콜백 한 건을 보관한다."""

    file_idx: int
    success: bool
    index_version: int | None
    chunks: tuple[ChunkSynchronizationRequest, ...] | None
    error_message: str | None


class RecordingApplicationServerIngestClient:
    """manifest와 완료 콜백을 메모리에서 처리하는 애플리케이션 서버 대역.

    동일 파일 중복 요청 테스트에서는 첫 번째 성공 콜백을 의도적으로
    중단시켜 두 번째 요청이 같은 File_IDX lock을 기다리는지 확인한다.
    """

    def __init__(
        self,
        *,
        manifest: FileProcessingRequest,
        block_first_success_callback: bool = False,
    ) -> None:
        """반환할 manifest와 첫 성공 콜백 중단 여부를 저장한다."""

        self._manifest = manifest
        self._block_first_success_callback = block_first_success_callback

        self.fetch_manifest_call_count = 0
        self.callbacks: list[RecordedIngestCompleteCallback] = []

        self.first_success_callback_started = asyncio.Event()
        self.allow_first_success_callback_to_finish = asyncio.Event()

        self._success_callback_call_count = 0
        self._active_success_callback_count = 0
        self.max_active_success_callback_count = 0

    async def fetch_manifest(
        self,
        *,
        file_idx: int,
    ) -> FileProcessingRequest:
        """요청 파일을 검증하고 고정된 최신 manifest를 반환한다."""

        assert file_idx == self._manifest.file_idx

        self.fetch_manifest_call_count += 1

        return self._manifest

    async def notify_ingest_complete(
        self,
        *,
        file_idx: int,
        success: bool,
        index_version: int | None = None,
        chunks: tuple[ChunkSynchronizationRequest, ...] | None = None,
        error_message: str | None = None,
    ) -> None:
        """완료 콜백을 기록하고 선택적으로 첫 성공 콜백을 중단한다."""

        # 호출자가 list와 같은 변경 가능한 컬렉션을 전달하더라도
        # 테스트 기록에는 콜백 호출 시점의 불변 스냅샷을 보관한다.
        normalized_chunks = tuple(chunks) if chunks is not None else None

        callback = RecordedIngestCompleteCallback(
            file_idx=file_idx,
            success=success,
            index_version=index_version,
            chunks=normalized_chunks,
            error_message=error_message,
        )
        self.callbacks.append(callback)

        if not success:
            return

        self._success_callback_call_count += 1
        success_callback_call_number = self._success_callback_call_count

        self._active_success_callback_count += 1
        self.max_active_success_callback_count = max(
            self.max_active_success_callback_count,
            self._active_success_callback_count,
        )

        try:
            if self._block_first_success_callback and success_callback_call_number == 1:
                # 이벤트를 먼저 설정하여 테스트가 첫 번째 성공 콜백이
                # 실제로 시작된 시점을 결정적으로 기다릴 수 있게 한다.
                self.first_success_callback_started.set()

                # 첫 번째 콜백을 File_IDX lock 내부에서 중단한다.
                #
                # 이 상태에서 두 번째 요청을 시작하여 같은 파일의
                # 다음 성공 콜백이 동시에 실행되지 않는지 검증한다.
                await self.allow_first_success_callback_to_finish.wait()
        finally:
            self._active_success_callback_count -= 1


class RecordingLatestSnapshotRepository:
    """현재 파일의 최신 활성 청크 스냅샷을 반환하는 저장소 대역."""

    def __init__(
        self,
        *,
        snapshot: IndexedDocumentSnapshot,
    ) -> None:
        """반환할 최신 스냅샷과 조회 횟수를 초기화한다."""

        self._snapshot = snapshot
        self.fetch_call_count = 0

    async def fetch_latest_active_chunk_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
    ) -> IndexedDocumentSnapshot:
        """사용자와 파일 범위를 검증한 뒤 최신 스냅샷을 반환한다."""

        assert users_idx == self._snapshot.users_idx
        assert file_idx == self._snapshot.file_idx

        self.fetch_call_count += 1

        return self._snapshot


class SerializingFileIndexLock:
    """동일 File_IDX 요청을 직렬화하고 lock 이벤트를 기록하는 대역."""

    def __init__(
        self,
        *,
        expected_file_idx: int,
    ) -> None:
        """테스트 대상 파일과 내부 비동기 lock을 초기화한다."""

        self._expected_file_idx = expected_file_idx
        self._lock = asyncio.Lock()
        self._attempt_count = 0

        self.events: list[str] = []
        self.second_lock_attempted = asyncio.Event()

    def hold(
        self,
        *,
        file_idx: int,
    ) -> AbstractAsyncContextManager[None]:
        """지정한 File_IDX에 대한 기록용 비동기 context를 반환한다."""

        return self._hold(
            file_idx=file_idx,
        )

    @asynccontextmanager
    async def _hold(
        self,
        *,
        file_idx: int,
    ) -> AsyncIterator[None]:
        """lock 시도, 획득 및 해제 순서를 기록한다."""

        assert file_idx == self._expected_file_idx

        self._attempt_count += 1
        attempt_number = self._attempt_count

        self.events.append(
            f"lock_attempt:{attempt_number}",
        )

        if attempt_number == 2:
            # 두 번째 요청이 첫 번째 요청의 lock 해제를 기다리기 시작했음을
            # 테스트가 안정적으로 확인할 수 있도록 이벤트를 제공한다.
            self.second_lock_attempted.set()

        async with self._lock:
            self.events.append(
                f"lock_acquired:{attempt_number}",
            )

            try:
                yield
            finally:
                self.events.append(
                    f"lock_released:{attempt_number}",
                )


def _create_manifest() -> FileProcessingRequest:
    """재색인 및 중복 요청 테스트에서 사용할 최신 manifest를 생성한다."""

    return FileProcessingRequest(
        file_idx=_TEST_FILE_IDX,
        user_idx=_TEST_USERS_IDX,
        folder_idx=_TEST_FOLDER_IDX,
        file_name="meeting.pdf",
        file_type="pdf",
        download_url=(
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
            "files/meeting.pdf?X-Amz-Signature=test"
        ),
        url_expires_in=900,
    )


def _create_processing_response(
    *,
    rag_document_idx: int,
) -> ApiResponse[FileProcessingCompletedResponse]:
    """파일 처리 파이프라인이 반환하는 정상 색인 응답을 생성한다."""

    return ApiResponse[FileProcessingCompletedResponse](
        success=True,
        code="FILE_INDEXING_COMPLETED",
        message=("File download, parsing, chunking, embedding, and indexing completed."),
        data=FileProcessingCompletedResponse(
            rag_document_idx=rag_document_idx,
            file_idx=_TEST_FILE_IDX,
            user_idx=_TEST_USERS_IDX,
            folder_idx=_TEST_FOLDER_IDX,
            file_name="meeting.pdf",
            file_type="pdf",
            file_size_bytes=1024,
            page_count=2,
            text_unit_count=2,
            chunk_count=2,
            embedding_model="test/embedding-model",
            embedding_dim=3,
            processing_status="INDEXED",
        ),
    )


def _create_snapshot(
    *,
    rag_document_idx: int,
    chunk_values: tuple[tuple[str, str, int], ...],
) -> IndexedDocumentSnapshot:
    """지정한 청크로 최신 활성 문서 스냅샷을 생성한다."""

    chunks = tuple(
        IndexedChunkSnapshot(
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            content=content,
            content_hash=hashlib.sha256(
                content.encode(
                    "utf-8",
                )
            ).hexdigest(),
            token_count=None,
            source_metadata={
                "page_number": page_number,
            },
        )
        for chunk_index, (
            chunk_id,
            content,
            page_number,
        ) in enumerate(chunk_values)
    )

    return IndexedDocumentSnapshot(
        rag_document_idx=rag_document_idx,
        users_idx=_TEST_USERS_IDX,
        file_idx=_TEST_FILE_IDX,
        index_version=_TEST_INDEX_VERSION,
        chunk_count=len(chunks),
        chunks=chunks,
    )


async def _call_ingest_file(
    *,
    request: FileProcessingRequest,
    active_chunk_snapshot_service: ActiveChunkSnapshotService,
    application_server_client: RecordingApplicationServerIngestClient,
) -> ApiResponse[FileProcessingCompletedResponse]:
    """외부 인프라 객체 없이 ingest orchestration 함수를 직접 호출한다.

    파일 처리 함수는 각 테스트에서 monkeypatch로 교체한다. 따라서 아래의
    다운로드, 파서, 청커, 임베더 및 색인 서비스 객체는 실제로 사용되지 않는다.

    다만 ingest_file()의 명시적인 타입 계약을 유지하기 위해 각 concrete type으로
    cast한 자리표시자를 전달한다.
    """

    return await ingest_module.ingest_file(
        request=request,
        file_downloader=cast(
            HttpFileDownloader,
            object(),
        ),
        document_parser_factory=cast(
            DocumentParserFactory,
            object(),
        ),
        document_chunker=cast(
            CharacterTextChunker,
            object(),
        ),
        chunk_embedder=cast(
            TeiChunkEmbedder,
            object(),
        ),
        file_indexing_service=cast(
            FileIndexingService,
            object(),
        ),
        active_chunk_snapshot_service=active_chunk_snapshot_service,
        application_server_client=cast(
            ApplicationServerIngestClient,
            application_server_client,
        ),
    )


@pytest.mark.asyncio
async def test_reindex_callback_uses_latest_active_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이전 요청이 늦게 콜백해도 최신 재색인 청크 전체를 전달해야 한다."""

    manifest = _create_manifest()

    # 현재 요청의 처리 결과보다 더 최신인 재색인 문서 스냅샷이다.
    #
    # 현재 처리 결과가 RAG_Document_IDX 100을 반환하더라도
    # 성공 콜백은 최신 활성 문서인 RAG_Document_IDX 200의
    # 청크를 전달해야 한다.
    latest_snapshot = _create_snapshot(
        rag_document_idx=200,
        chunk_values=(
            (
                _LATEST_FIRST_CHUNK_ID,
                "최신 재색인의 첫 번째 청크",
                1,
            ),
            (
                _LATEST_SECOND_CHUNK_ID,
                "최신 재색인의 두 번째 청크",
                2,
            ),
        ),
    )

    snapshot_repository = RecordingLatestSnapshotRepository(
        snapshot=latest_snapshot,
    )
    file_lock = SerializingFileIndexLock(
        expected_file_idx=_TEST_FILE_IDX,
    )
    snapshot_service = ActiveChunkSnapshotService(
        repository=snapshot_repository,
        file_lock=file_lock,
    )
    application_server_client = RecordingApplicationServerIngestClient(
        manifest=manifest,
    )

    async def process_previous_request(
        request: FileProcessingRequest,
        file_downloader: HttpFileDownloader,
        document_parser_factory: DocumentParserFactory,
        document_chunker: CharacterTextChunker,
        chunk_embedder: TeiChunkEmbedder,
        file_indexing_service: FileIndexingService,
    ) -> ApiResponse[FileProcessingCompletedResponse]:
        """최신 재색인보다 먼저 완료된 이전 요청의 처리 결과를 반환한다."""

        assert request is manifest

        del file_downloader
        del document_parser_factory
        del document_chunker
        del chunk_embedder
        del file_indexing_service

        # 현재 요청의 처리 결과는 과거 RAG_Document_IDX를 가리킨다.
        #
        # ingest endpoint는 이 값으로 청크를 조회하지 않고
        # 파일 범위의 최신 활성 스냅샷을 다시 조회해야 한다.
        return _create_processing_response(
            rag_document_idx=100,
        )

    monkeypatch.setattr(
        ingest_module,
        "process_file_processing_request",
        process_previous_request,
    )

    response = await _call_ingest_file(
        request=manifest,
        active_chunk_snapshot_service=snapshot_service,
        application_server_client=application_server_client,
    )

    processing_data = response.data

    assert processing_data is not None

    # /ingest의 직접 응답은 현재 요청의 처리 결과를 유지한다.
    assert processing_data.rag_document_idx == 100

    # 성공 콜백용 청크는 현재 처리 결과의 문서 ID가 아니라
    # 최신 활성 스냅샷 저장소에서 한 번 조회해야 한다.
    assert snapshot_repository.fetch_call_count == 1
    assert file_lock.events == [
        "lock_attempt:1",
        "lock_acquired:1",
        "lock_released:1",
    ]

    assert len(application_server_client.callbacks) == 1

    callback = application_server_client.callbacks[0]
    callback_chunks = callback.chunks

    assert callback.file_idx == _TEST_FILE_IDX
    assert callback.success is True
    assert callback.index_version == _TEST_INDEX_VERSION
    assert callback.error_message is None
    assert callback_chunks is not None

    callback_chunk_ids = tuple(chunk.chunk_id for chunk in callback_chunks)
    callback_contents = tuple(chunk.content for chunk in callback_chunks)

    # 과거 처리 요청의 청크가 아니라 최신 재색인 청크 전체가
    # 문서 순서대로 성공 콜백에 포함되어야 한다.
    assert callback_chunk_ids == (
        _LATEST_FIRST_CHUNK_ID,
        _LATEST_SECOND_CHUNK_ID,
    )
    assert _OLD_CHUNK_ID not in callback_chunk_ids
    assert callback_contents == (
        "최신 재색인의 첫 번째 청크",
        "최신 재색인의 두 번째 청크",
    )


@pytest.mark.asyncio
async def test_duplicate_same_file_requests_serialize_chunk_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """동일 파일 중복 요청은 콜백을 직렬화하고 같은 청크 ID를 전달해야 한다."""

    manifest = _create_manifest()

    # 동일 파일과 동일 색인 입력이 멱등하게 재사용한 최신 문서다.
    #
    # 두 요청은 같은 RAG_Document와 결정적인 Chunk_ID 집합을
    # 성공 콜백으로 전달해야 한다.
    latest_snapshot = _create_snapshot(
        rag_document_idx=300,
        chunk_values=(
            (
                _LATEST_FIRST_CHUNK_ID,
                "동일 파일의 첫 번째 결정적 청크",
                1,
            ),
            (
                _LATEST_SECOND_CHUNK_ID,
                "동일 파일의 두 번째 결정적 청크",
                2,
            ),
        ),
    )

    snapshot_repository = RecordingLatestSnapshotRepository(
        snapshot=latest_snapshot,
    )
    file_lock = SerializingFileIndexLock(
        expected_file_idx=_TEST_FILE_IDX,
    )
    snapshot_service = ActiveChunkSnapshotService(
        repository=snapshot_repository,
        file_lock=file_lock,
    )
    application_server_client = RecordingApplicationServerIngestClient(
        manifest=manifest,
        block_first_success_callback=True,
    )

    process_call_count = 0

    async def process_duplicate_request(
        request: FileProcessingRequest,
        file_downloader: HttpFileDownloader,
        document_parser_factory: DocumentParserFactory,
        document_chunker: CharacterTextChunker,
        chunk_embedder: TeiChunkEmbedder,
        file_indexing_service: FileIndexingService,
    ) -> ApiResponse[FileProcessingCompletedResponse]:
        """멱등 재사용된 동일 RAG_Document 처리 결과를 반환한다."""

        nonlocal process_call_count

        assert request is manifest

        del file_downloader
        del document_parser_factory
        del document_chunker
        del chunk_embedder
        del file_indexing_service

        process_call_count += 1

        return _create_processing_response(
            rag_document_idx=latest_snapshot.rag_document_idx,
        )

    monkeypatch.setattr(
        ingest_module,
        "process_file_processing_request",
        process_duplicate_request,
    )

    first_request = asyncio.create_task(
        _call_ingest_file(
            request=manifest,
            active_chunk_snapshot_service=snapshot_service,
            application_server_client=application_server_client,
        )
    )

    # 첫 번째 요청이 최신 스냅샷을 조회하고 성공 콜백을 시작할 때까지
    # 기다린다. 이 시점의 콜백은 File_IDX lock 안에서 중단되어 있다.
    await asyncio.wait_for(
        application_server_client.first_success_callback_started.wait(),
        timeout=5.0,
    )

    second_request = asyncio.create_task(
        _call_ingest_file(
            request=manifest,
            active_chunk_snapshot_service=snapshot_service,
            application_server_client=application_server_client,
        )
    )

    # 두 번째 요청이 같은 File_IDX lock 획득을 시도할 때까지 기다린다.
    await asyncio.wait_for(
        file_lock.second_lock_attempted.wait(),
        timeout=5.0,
    )

    # 첫 번째 성공 콜백이 끝나기 전에는 두 번째 요청이 최신 스냅샷을
    # 조회하거나 성공 콜백을 시작할 수 없다.
    assert len(application_server_client.callbacks) == 1
    assert snapshot_repository.fetch_call_count == 1
    assert application_server_client.max_active_success_callback_count == 1
    assert not second_request.done()

    # 첫 번째 콜백을 종료하여 첫 번째 요청이 File_IDX lock을 해제하게 한다.
    application_server_client.allow_first_success_callback_to_finish.set()

    first_response, second_response = await asyncio.wait_for(
        asyncio.gather(
            first_request,
            second_request,
        ),
        timeout=5.0,
    )

    first_processing_data = first_response.data
    second_processing_data = second_response.data

    assert first_processing_data is not None
    assert second_processing_data is not None

    assert first_processing_data.rag_document_idx == latest_snapshot.rag_document_idx
    assert second_processing_data.rag_document_idx == latest_snapshot.rag_document_idx

    assert process_call_count == 2
    assert application_server_client.fetch_manifest_call_count == 2
    assert snapshot_repository.fetch_call_count == 2

    # 두 성공 콜백이 같은 시각에 실행된 적이 없어야 한다.
    assert application_server_client.max_active_success_callback_count == 1

    # 두 번째 요청은 첫 번째 요청이 lock을 해제한 이후에만
    # 최신 활성 스냅샷 조회 및 성공 콜백 임계 구역에 진입해야 한다.
    assert file_lock.events == [
        "lock_attempt:1",
        "lock_acquired:1",
        "lock_attempt:2",
        "lock_released:1",
        "lock_acquired:2",
        "lock_released:2",
    ]

    assert len(application_server_client.callbacks) == 2

    first_callback, second_callback = application_server_client.callbacks

    first_callback_chunks = first_callback.chunks
    second_callback_chunks = second_callback.chunks

    assert first_callback.success is True
    assert second_callback.success is True
    assert first_callback.index_version == _TEST_INDEX_VERSION
    assert second_callback.index_version == _TEST_INDEX_VERSION
    assert first_callback.error_message is None
    assert second_callback.error_message is None
    assert first_callback_chunks is not None
    assert second_callback_chunks is not None

    first_chunk_ids = tuple(chunk.chunk_id for chunk in first_callback_chunks)
    second_chunk_ids = tuple(chunk.chunk_id for chunk in second_callback_chunks)

    # 동일 파일과 동일 색인 입력은 결정적인 Chunk_ID를 재사용하므로
    # 중복 요청의 두 성공 콜백도 완전히 같은 식별자 순서를 전달해야 한다.
    assert first_chunk_ids == (
        _LATEST_FIRST_CHUNK_ID,
        _LATEST_SECOND_CHUNK_ID,
    )
    assert second_chunk_ids == first_chunk_ids

    first_payload = tuple(
        chunk.model_dump(
            mode="json",
        )
        for chunk in first_callback_chunks
    )
    second_payload = tuple(
        chunk.model_dump(
            mode="json",
        )
        for chunk in second_callback_chunks
    )

    # HTTP 재시도나 동일 파일 중복 요청처럼 성공 콜백이 반복되어도
    # 애플리케이션 서버가 같은 최신 스냅샷을 멱등하게 받을 수 있어야 한다.
    assert second_payload == first_payload
