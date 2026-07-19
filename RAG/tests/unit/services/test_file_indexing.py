"""Local RAG DB와 Qdrant 사이의 파일 색인 조정 로직을 테스트한다."""

import hashlib

import pytest

from jipsa_rag.infrastructure.chunking.models import TextChunk
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)
from jipsa_rag.services.file_indexing import FileIndexingService

TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
TEST_CONTENT = "Searchable test content."
TEST_CONTENT_HASH = hashlib.sha256(TEST_CONTENT.encode("utf-8")).hexdigest()
TEST_FILE_HASH = "a" * 64


class StubLocalIndexRepository:
    """서비스 테스트에서 Local RAG DB 호출을 기록하는 저장소 대역."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.prepare_error: LocalRagStorageError | None = None
        self.mark_indexed_error: LocalRagStorageError | None = None
        self.mark_failed_error: LocalRagStorageError | None = None
        self.failed_messages: list[str] = []

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        self.events.append("prepare_indexing")

        assert metadata.file_idx == 10
        assert embedded_document.chunk_count == 1

        if self.prepare_error is not None:
            raise self.prepare_error

        return PreparedLocalIndex(
            rag_document_idx=100,
            rag_index_run_idx=200,
            chunk_ids=(TEST_CHUNK_ID,),
        )

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
    ) -> None:
        self.events.append("mark_indexed")

        assert rag_document_idx == 100
        assert rag_index_run_idx == 200

        if self.mark_indexed_error is not None:
            raise self.mark_indexed_error

    async def mark_failed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        self.events.append("mark_failed")
        self.failed_messages.append(error_message)

        assert rag_document_idx == 100
        assert rag_index_run_idx == 200

        if self.mark_failed_error is not None:
            raise self.mark_failed_error


class StubChunkVectorStore:
    """서비스 테스트에서 Qdrant 호출을 기록하는 저장소 대역."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.upsert_error: VectorDatabaseUnavailableError | None = None
        self.delete_error: VectorDatabaseUnavailableError | None = None
        self.deleted_chunk_ids: list[tuple[str, ...]] = []

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> None:
        self.events.append("upsert_document")

        assert rag_document_idx == 100
        assert metadata.file_idx == 10
        assert embedded_document.chunk_count == 1

        if self.upsert_error is not None:
            raise self.upsert_error

    async def delete_chunks(
        self,
        *,
        chunk_ids: tuple[str, ...],
    ) -> None:
        self.events.append("delete_chunks")
        self.deleted_chunk_ids.append(chunk_ids)

        if self.delete_error is not None:
            raise self.delete_error


def _create_metadata() -> DocumentIndexMetadata:
    """서비스 테스트에서 공통으로 사용할 문서 메타데이터를 생성한다."""

    return DocumentIndexMetadata(
        users_idx=1,
        file_idx=10,
        folder_idx=3,
        file_name="project-guide.pdf",
        file_type=DocumentType.PDF,
        file_hash=TEST_FILE_HASH,
        index_version=1,
        parser_type="PDF_TEXT",
        parser_version="1.0.0",
    )


def _create_embedded_document() -> EmbeddedDocument:
    """단일 청크와 3차원 테스트 벡터를 생성한다."""

    chunk = TextChunk(
        chunk_id=TEST_CHUNK_ID,
        chunk_index=0,
        content=TEST_CONTENT,
        content_hash=TEST_CONTENT_HASH,
        start_offset=0,
        end_offset=len(TEST_CONTENT),
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
async def test_index_stores_local_data_upserts_vectors_and_marks_success() -> None:
    """정상 처리 시 Local 준비, Qdrant 업서트, Local 성공 순서를 유지한다."""

    local_repository = StubLocalIndexRepository()
    vector_store = StubChunkVectorStore()
    service = FileIndexingService(
        local_repository=local_repository,
        vector_store=vector_store,
    )

    result = await service.index(
        metadata=_create_metadata(),
        embedded_document=_create_embedded_document(),
    )

    assert local_repository.events == [
        "prepare_indexing",
        "mark_indexed",
    ]
    assert vector_store.events == ["upsert_document"]
    assert result.rag_document_idx == 100
    assert result.rag_index_run_idx == 200
    assert result.chunk_count == 1


@pytest.mark.asyncio
async def test_index_marks_local_state_failed_when_qdrant_upsert_fails() -> None:
    """Qdrant 실패 시 Local 문서와 실행 이력을 FAILED로 전환한다."""

    local_repository = StubLocalIndexRepository()
    vector_store = StubChunkVectorStore()
    vector_store.upsert_error = VectorDatabaseUnavailableError(
        "upsert_document",
        status_code=503,
    )

    service = FileIndexingService(
        local_repository=local_repository,
        vector_store=vector_store,
    )

    with pytest.raises(VectorDatabaseUnavailableError):
        await service.index(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert local_repository.events == [
        "prepare_indexing",
        "mark_failed",
    ]
    assert local_repository.failed_messages == ["VectorDatabaseUnavailableError"]
    assert vector_store.events == ["upsert_document"]


@pytest.mark.asyncio
async def test_index_compensates_vectors_when_local_success_update_fails() -> None:
    """Qdrant 성공 후 Local 상태 확정 실패 시 Point를 보상 삭제한다."""

    local_repository = StubLocalIndexRepository()
    local_repository.mark_indexed_error = LocalRagStorageError("mark_indexed")
    vector_store = StubChunkVectorStore()

    service = FileIndexingService(
        local_repository=local_repository,
        vector_store=vector_store,
    )

    with pytest.raises(LocalRagStorageError):
        await service.index(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert local_repository.events == [
        "prepare_indexing",
        "mark_indexed",
        "mark_failed",
    ]
    assert local_repository.failed_messages == ["LocalRagStorageError"]
    assert vector_store.events == [
        "upsert_document",
        "delete_chunks",
    ]
    assert vector_store.deleted_chunk_ids == [(TEST_CHUNK_ID,)]


@pytest.mark.asyncio
async def test_index_does_not_call_qdrant_when_local_prepare_fails() -> None:
    """Local 문서·청크 준비 실패 시 Qdrant 요청을 시작하지 않는다."""

    local_repository = StubLocalIndexRepository()
    local_repository.prepare_error = LocalRagStorageError("prepare_indexing")
    vector_store = StubChunkVectorStore()

    service = FileIndexingService(
        local_repository=local_repository,
        vector_store=vector_store,
    )

    with pytest.raises(LocalRagStorageError):
        await service.index(
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
        )

    assert local_repository.events == ["prepare_indexing"]
    assert vector_store.events == []
