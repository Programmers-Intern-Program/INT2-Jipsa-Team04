"""파일 색인 서비스가 File_IDX lock 안에서 저장 흐름을 수행하는지 테스트한다."""

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
from jipsa_rag.services.file_indexing import FileIndexingService

_TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
_TEST_CONTENT = "Concurrent indexing test content."
_TEST_CONTENT_HASH = hashlib.sha256(_TEST_CONTENT.encode("utf-8")).hexdigest()


class RecordingFileIndexLock:
    """임계 구역 진입과 종료 순서를 기록하는 lock 대역."""

    def __init__(self, events: list[str]) -> None:
        """공유 이벤트 목록을 저장한다."""

        self._events = events

    def hold(
        self,
        *,
        file_idx: int,
    ) -> AbstractAsyncContextManager[None]:
        """지정한 파일의 기록용 context를 반환한다."""

        return self._hold(file_idx=file_idx)

    @asynccontextmanager
    async def _hold(
        self,
        *,
        file_idx: int,
    ) -> AsyncIterator[None]:
        """색인 실행 전후에 lock 이벤트를 기록한다."""

        assert file_idx == 10
        self._events.append("lock_enter")

        try:
            yield
        finally:
            self._events.append("lock_exit")


class RecordingLocalRepository:
    """Local RAG 저장 단계 순서를 기록하는 저장소 대역."""

    def __init__(self, events: list[str]) -> None:
        """공유 이벤트 목록을 저장한다."""

        self._events = events

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        """신규 색인 준비 이벤트를 기록한다."""

        assert metadata.file_idx == 10
        assert embedded_document.chunk_count == 1
        self._events.append("prepare_indexing")

        return PreparedLocalIndex(
            rag_document_idx=100,
            rag_index_run_idx=200,
            chunk_ids=(_TEST_CHUNK_ID,),
            previous_rag_document_idxs=(90,),
            reuses_existing_index=False,
        )

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        superseded_rag_document_idxs: tuple[int, ...],
    ) -> None:
        """성공 상태 확정 이벤트를 기록한다."""

        assert rag_document_idx == 100
        assert rag_index_run_idx == 200
        assert superseded_rag_document_idxs == (90,)
        self._events.append("mark_indexed")

    async def mark_failed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """이 테스트에서 호출되면 실패시킨다."""

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
        """이 테스트에서 호출되면 실패시킨다."""

        del rag_index_run_idx
        del error_message
        raise AssertionError("mark_run_failed() must not be called.")


class RecordingVectorStore:
    """Qdrant 단계 순서를 기록하는 저장소 대역."""

    def __init__(self, events: list[str]) -> None:
        """공유 이벤트 목록을 저장한다."""

        self._events = events

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
        is_active: bool,
    ) -> None:
        """staging 업서트 이벤트를 기록한다."""

        assert rag_document_idx == 100
        assert metadata.file_idx == 10
        assert embedded_document.chunk_count == 1
        assert is_active is False
        self._events.append("upsert_document")

    async def set_documents_active(
        self,
        *,
        rag_document_idxs: tuple[int, ...],
        is_active: bool,
    ) -> None:
        """신규 및 이전 문서 활성 상태 변경 이벤트를 기록한다."""

        self._events.append(f"set_active:{rag_document_idxs}:{is_active}")

    async def delete_chunks(
        self,
        *,
        chunk_ids: tuple[str, ...],
    ) -> None:
        """이 테스트에서 호출되면 실패시킨다."""

        del chunk_ids
        raise AssertionError("delete_chunks() must not be called.")


def _create_metadata() -> DocumentIndexMetadata:
    """테스트용 문서 색인 메타데이터를 생성한다."""

    return DocumentIndexMetadata(
        users_idx=1,
        file_idx=10,
        folder_idx=3,
        file_name="project-guide.pdf",
        file_type=DocumentType.PDF,
        file_hash="a" * 64,
        index_version=2,
        parser_type="PDF_TEXT",
        parser_version="1.0.0",
    )


def _create_embedded_document() -> EmbeddedDocument:
    """단일 청크와 임베딩을 가진 테스트 문서를 생성한다."""

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
                embedding=(0.1, 0.2, 0.3),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_indexing_flow_runs_entirely_inside_file_lock() -> None:
    """prepare부터 최종 상태 확정까지 File_IDX lock 내부에서 실행한다."""

    events: list[str] = []
    service = FileIndexingService(
        local_repository=RecordingLocalRepository(events),
        vector_store=RecordingVectorStore(events),
        file_lock=RecordingFileIndexLock(events),
    )

    result = await service.index(
        metadata=_create_metadata(),
        embedded_document=_create_embedded_document(),
    )

    assert events == [
        "lock_enter",
        "prepare_indexing",
        "upsert_document",
        "set_active:(100,):True",
        "set_active:(90,):False",
        "mark_indexed",
        "lock_exit",
    ]
    assert result.rag_document_idx == 100
    assert result.rag_index_run_idx == 200
    assert result.chunk_count == 1
