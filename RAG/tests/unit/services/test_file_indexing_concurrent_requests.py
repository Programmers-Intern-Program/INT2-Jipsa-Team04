"""동일 파일에 대한 동시 색인 요청이 직렬화되는지 테스트한다."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest

from jipsa_rag.infrastructure.chunking.models import TextChunk
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)
from jipsa_rag.services.file_indexing import (
    FileIndexingResult,
    FileIndexingService,
)

_TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
_TEST_CONTENT = "Concurrent file indexing request test."
_TEST_CONTENT_HASH = hashlib.sha256(_TEST_CONTENT.encode("utf-8")).hexdigest()


class PerFileAsyncLock:
    """단위 테스트에서 동일 File_IDX 요청을 직렬화하는 비동기 lock 대역."""

    def __init__(self) -> None:
        """File_IDX별 asyncio.Lock 저장소를 초기화한다."""

        self._locks: dict[int, asyncio.Lock] = {}

    def hold(
        self,
        *,
        file_idx: int,
    ) -> AbstractAsyncContextManager[None]:
        """지정한 File_IDX의 비동기 lock context를 반환한다."""

        return self._hold(file_idx=file_idx)

    @asynccontextmanager
    async def _hold(
        self,
        *,
        file_idx: int,
    ) -> AsyncIterator[None]:
        """동일 File_IDX에 대한 임계 구역을 하나씩 실행한다."""

        file_lock = self._locks.setdefault(
            file_idx,
            asyncio.Lock(),
        )

        async with file_lock:
            yield


class CoordinatedLocalRepository:
    """첫 번째 prepare 호출을 중단하여 두 요청의 동시 진입을 관찰한다."""

    def __init__(self) -> None:
        """호출 횟수와 동시 실행 제어 이벤트를 초기화한다."""

        self.prepare_call_count = 0
        self.marked_document_idxs: list[int] = []

        self.first_prepare_entered = asyncio.Event()
        self.allow_first_prepare_to_finish = asyncio.Event()

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        """첫 번째 요청을 대기시킨 뒤 요청별 준비 결과를 반환한다."""

        assert metadata.file_idx == 10
        assert embedded_document.chunk_count == 1

        self.prepare_call_count += 1
        call_number = self.prepare_call_count

        if call_number == 1:
            # 첫 요청이 파일 lock 안에서 prepare 단계에 진입했음을 알린다.
            self.first_prepare_entered.set()

            # 두 번째 요청이 동시에 service.index()를 호출할 시간을 제공한다.
            # 파일 lock이 정상이라면 이 이벤트가 해제되기 전까지 두 번째
            # 요청은 prepare_indexing()에 진입할 수 없다.
            await self.allow_first_prepare_to_finish.wait()

        return PreparedLocalIndex(
            rag_document_idx=100 + call_number,
            rag_index_run_idx=200 + call_number,
            chunk_ids=(_TEST_CHUNK_ID,),
            previous_rag_document_idxs=(),
            reuses_existing_index=False,
        )

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        superseded_rag_document_idxs: tuple[int, ...],
    ) -> None:
        """성공 처리된 문서 식별자를 기록한다."""

        assert rag_index_run_idx > 200
        assert superseded_rag_document_idxs == ()

        self.marked_document_idxs.append(rag_document_idx)

    async def mark_failed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """정상 동시 요청 테스트에서는 호출될 수 없다."""

        del rag_document_idx
        del rag_index_run_idx
        del error_message

        raise AssertionError("mark_failed() must not be called.")

    async def mark_run_failed(
        self,
        *,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """정상 동시 요청 테스트에서는 호출될 수 없다."""

        del rag_index_run_idx
        del error_message

        raise AssertionError("mark_run_failed() must not be called.")


class RecordingVectorStore:
    """동시 요청 테스트에서 Qdrant 호출을 기록하는 저장소 대역."""

    def __init__(self) -> None:
        """업서트 및 활성 상태 변경 기록을 초기화한다."""

        self.upserted_document_idxs: list[int] = []
        self.activation_calls: list[tuple[tuple[int, ...], bool]] = []

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
        is_active: bool,
    ) -> None:
        """staging 문서 업서트 호출을 기록한다."""

        assert metadata.file_idx == 10
        assert embedded_document.chunk_count == 1
        assert is_active is False

        self.upserted_document_idxs.append(rag_document_idx)

    async def set_documents_active(
        self,
        *,
        rag_document_idxs: tuple[int, ...],
        is_active: bool,
    ) -> None:
        """문서 활성 상태 변경 호출을 기록한다."""

        self.activation_calls.append(
            (
                rag_document_idxs,
                is_active,
            )
        )

    async def delete_chunks(
        self,
        *,
        chunk_ids: tuple[str, ...],
    ) -> None:
        """정상 동시 요청 테스트에서는 보상 삭제가 발생하지 않아야 한다."""

        del chunk_ids

        raise AssertionError("delete_chunks() must not be called.")


def _create_metadata() -> DocumentIndexMetadata:
    """동일 파일 동시 요청에 사용할 색인 메타데이터를 생성한다."""

    return DocumentIndexMetadata(
        users_idx=1,
        file_idx=10,
        folder_idx=3,
        file_name="concurrent-test.pdf",
        file_type=DocumentType.PDF,
        file_hash="a" * 64,
        index_version=2,
        parser_type="PDF_TEXT",
        parser_version="1.0.0",
    )


def _create_embedded_document() -> EmbeddedDocument:
    """동일 파일 동시 요청에 사용할 임베딩 문서를 생성한다."""

    chunk = TextChunk(
        chunk_id=_TEST_CHUNK_ID,
        chunk_index=0,
        content=_TEST_CONTENT,
        content_hash=_TEST_CONTENT_HASH,
        start_offset=0,
        end_offset=len(_TEST_CONTENT),
        source_metadata={
            "page_number": 1,
        },
    )

    return EmbeddedDocument(
        embedding_model="test/embedding-model",
        embedding_dim=3,
        chunks=(
            EmbeddedChunk(
                chunk=chunk,
                embedding=(
                    0.1,
                    0.2,
                    0.3,
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_same_file_concurrent_requests_are_serialized() -> None:
    """동일 File_IDX 요청 두 개가 prepare 단계에 동시에 진입하지 않는다."""

    local_repository = CoordinatedLocalRepository()
    vector_store = RecordingVectorStore()

    service = FileIndexingService(
        local_repository=local_repository,
        vector_store=vector_store,
        file_lock=PerFileAsyncLock(),
    )

    metadata = _create_metadata()
    embedded_document = _create_embedded_document()

    first_request = asyncio.create_task(
        service.index(
            metadata=metadata,
            embedded_document=embedded_document,
        )
    )

    # 첫 번째 요청이 파일 lock을 획득하고 prepare 단계에 진입할 때까지 기다린다.
    await asyncio.wait_for(
        local_repository.first_prepare_entered.wait(),
        timeout=1.0,
    )

    second_request = asyncio.create_task(
        service.index(
            metadata=metadata,
            embedded_document=embedded_document,
        )
    )

    # 두 번째 Task가 실행 기회를 얻도록 이벤트 루프를 양보한다.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # 첫 번째 요청이 아직 lock을 보유하고 있으므로 두 번째 요청은
    # prepare_indexing()에 진입하지 못해야 한다.
    assert local_repository.prepare_call_count == 1
    assert second_request.done() is False

    # 첫 번째 요청을 완료시켜 파일 lock을 해제한다.
    local_repository.allow_first_prepare_to_finish.set()

    first_result, second_result = await asyncio.wait_for(
        asyncio.gather(
            first_request,
            second_request,
        ),
        timeout=1.0,
    )

    assert isinstance(first_result, FileIndexingResult)
    assert isinstance(second_result, FileIndexingResult)

    # 첫 번째 실행이 모두 끝난 뒤 두 번째 실행이 순차적으로 준비된다.
    assert local_repository.prepare_call_count == 2
    assert first_result.rag_document_idx == 101
    assert second_result.rag_document_idx == 102

    assert local_repository.marked_document_idxs == [
        101,
        102,
    ]
    assert vector_store.upserted_document_idxs == [
        101,
        102,
    ]
