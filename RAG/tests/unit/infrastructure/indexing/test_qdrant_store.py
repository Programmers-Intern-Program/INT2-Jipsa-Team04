"""Qdrant Collection 준비와 청크 Point 상태 전환 동작을 테스트한다."""

import hashlib
from collections.abc import Sequence
from typing import cast

import pytest
from httpx import Headers
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse,
)

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.chunking.models import TextChunk
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    VectorCollectionConfigurationError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata
from jipsa_rag.infrastructure.indexing.qdrant_store import (
    QdrantChunkVectorStore,
)

TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
TEST_CONTENT = "Qdrant point payload test content."
TEST_CONTENT_HASH = hashlib.sha256(TEST_CONTENT.encode("utf-8")).hexdigest()
TEST_FILE_HASH = "a" * 64
TEST_PARSER_VERSION = "1.0.0"
TEST_EMBEDDING_MODEL = "test/embedding-model"
TEST_EMBEDDING_DIM = 3
TEST_INDEX_VERSION = 2
TEST_COLLECTION = "test_rag_chunk_vector"


class FakeAsyncQdrantClient:
    """실제 Qdrant 없이 Collection과 Point 요청을 기록하는 클라이언트 대역."""

    def __init__(
        self,
    ) -> None:
        """Qdrant 호출 기록과 선택적 예외 상태를 초기화한다."""

        self.collection_exists_result = False
        self.collection_exists_calls: list[str] = []

        self.create_collection_calls: list[dict[str, object]] = []

        self.create_payload_index_calls: list[dict[str, object]] = []

        self.upsert_calls: list[dict[str, object]] = []

        self.set_payload_calls: list[dict[str, object]] = []

        self.delete_calls: list[dict[str, object]] = []

        self.upsert_error: Exception | None = None
        self.set_payload_error: Exception | None = None
        self.delete_error: Exception | None = None

        self.close_called = False

    async def collection_exists(
        self,
        collection_name: str,
    ) -> bool:
        """Collection 존재 확인 요청을 기록하고 설정된 결과를 반환한다."""

        self.collection_exists_calls.append(
            collection_name,
        )

        return self.collection_exists_result

    async def create_collection(
        self,
        *,
        collection_name: str,
        vectors_config: models.VectorParams,
    ) -> bool:
        """Collection 생성 요청을 기록한다."""

        self.create_collection_calls.append(
            {
                "collection_name": collection_name,
                "vectors_config": vectors_config,
            }
        )

        return True

    async def create_payload_index(
        self,
        *,
        collection_name: str,
        field_name: str,
        field_schema: models.PayloadSchemaType,
        wait: bool,
    ) -> models.UpdateResult:
        """Payload 인덱스 생성 요청을 기록한다."""

        self.create_payload_index_calls.append(
            {
                "collection_name": collection_name,
                "field_name": field_name,
                "field_schema": field_schema,
                "wait": wait,
            }
        )

        return models.UpdateResult(
            operation_id=1,
            status=models.UpdateStatus.COMPLETED,
        )

    async def upsert(
        self,
        *,
        collection_name: str,
        points: list[models.PointStruct],
        wait: bool,
    ) -> models.UpdateResult:
        """Point 업서트 요청을 기록하거나 설정된 예외를 발생시킨다."""

        if self.upsert_error is not None:
            raise self.upsert_error

        self.upsert_calls.append(
            {
                "collection_name": collection_name,
                "points": points,
                "wait": wait,
            }
        )

        return models.UpdateResult(
            operation_id=2,
            status=models.UpdateStatus.COMPLETED,
        )

    async def set_payload(
        self,
        *,
        collection_name: str,
        payload: dict[str, object],
        points: models.Filter,
        wait: bool,
    ) -> models.UpdateResult:
        """문서 단위 활성 상태 변경 요청을 기록한다."""

        if self.set_payload_error is not None:
            raise self.set_payload_error

        self.set_payload_calls.append(
            {
                "collection_name": collection_name,
                "payload": payload,
                "points": points,
                "wait": wait,
            }
        )

        return models.UpdateResult(
            operation_id=3,
            status=models.UpdateStatus.COMPLETED,
        )

    async def delete(
        self,
        *,
        collection_name: str,
        points_selector: models.PointIdsList,
        wait: bool,
    ) -> models.UpdateResult:
        """Point 삭제 요청을 기록하거나 설정된 예외를 발생시킨다."""

        if self.delete_error is not None:
            raise self.delete_error

        self.delete_calls.append(
            {
                "collection_name": collection_name,
                "points_selector": points_selector,
                "wait": wait,
            }
        )

        return models.UpdateResult(
            operation_id=4,
            status=models.UpdateStatus.COMPLETED,
        )

    async def close(
        self,
    ) -> None:
        """클라이언트 종료 호출 여부를 기록한다."""

        self.close_called = True


def _create_settings() -> Settings:
    """3차원 테스트 모델과 전용 Collection 설정을 생성한다."""

    return get_settings().model_copy(
        update={
            "embedding_model": TEST_EMBEDDING_MODEL,
            "embedding_dim": TEST_EMBEDDING_DIM,
            "qdrant_collection": TEST_COLLECTION,
        }
    )


def _create_metadata() -> DocumentIndexMetadata:
    """Qdrant payload 테스트용 문서 메타데이터를 생성한다."""

    return DocumentIndexMetadata(
        users_idx=1,
        file_idx=10,
        folder_idx=3,
        file_name="project-guide.pdf",
        file_type=DocumentType.PDF,
        file_hash=TEST_FILE_HASH,
        index_version=TEST_INDEX_VERSION,
        parser_type="PDF_TEXT",
        parser_version=TEST_PARSER_VERSION,
    )


def _create_embedded_document() -> EmbeddedDocument:
    """페이지 위치를 가진 단일 청크 임베딩 결과를 생성한다."""

    chunk = TextChunk(
        chunk_id=TEST_CHUNK_ID,
        chunk_index=0,
        content=TEST_CONTENT,
        content_hash=TEST_CONTENT_HASH,
        start_offset=10,
        end_offset=10 + len(TEST_CONTENT),
        source_metadata={
            "page_number": 2,
            "source_unit_index": 1,
            "unit_start_offset": 0,
            "unit_end_offset": len(TEST_CONTENT),
        },
    )

    return EmbeddedDocument(
        embedding_model=TEST_EMBEDDING_MODEL,
        embedding_dim=TEST_EMBEDDING_DIM,
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


def _extract_single_field_condition(
    point_filter: models.Filter,
) -> models.FieldCondition:
    """문서 활성 상태 변경 Filter의 단일 FieldCondition을 반환한다."""

    must_conditions = point_filter.must

    assert isinstance(
        must_conditions,
        Sequence,
    )
    assert len(must_conditions) == 1

    condition = must_conditions[0]

    assert isinstance(
        condition,
        models.FieldCondition,
    )

    return condition


@pytest.mark.asyncio
async def test_upsert_document_creates_collection_indexes_and_inactive_point() -> None:
    """새 색인을 검색 비활성 상태로 저장하고 버전 정체성을 payload에 기록한다."""

    fake_client = FakeAsyncQdrantClient()

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    await vector_store.upsert_document(
        rag_document_idx=100,
        metadata=_create_metadata(),
        embedded_document=_create_embedded_document(),
        is_active=False,
    )

    assert fake_client.collection_exists_calls == [
        TEST_COLLECTION,
    ]
    assert len(fake_client.create_collection_calls) == 1

    vectors_config = cast(
        models.VectorParams,
        fake_client.create_collection_calls[0]["vectors_config"],
    )

    assert vectors_config.size == TEST_EMBEDDING_DIM
    assert vectors_config.distance is models.Distance.COSINE

    indexed_fields = {
        cast(
            str,
            call["field_name"],
        )
        for call in fake_client.create_payload_index_calls
    }

    assert indexed_fields == {
        "users_idx",
        "file_idx",
        "folder_idx",
        "rag_document_idx",
        "is_active",
        "index_version",
        "embedding_model",
        "file_type",
        "parser_version",
    }

    assert len(fake_client.upsert_calls) == 1

    upsert_call = fake_client.upsert_calls[0]

    points = cast(
        list[models.PointStruct],
        upsert_call["points"],
    )

    assert upsert_call["collection_name"] == TEST_COLLECTION
    assert upsert_call["wait"] is True
    assert len(points) == 1

    point = points[0]
    payload = point.payload

    assert point.id == TEST_CHUNK_ID
    assert point.vector == [
        0.1,
        0.2,
        0.3,
    ]
    assert payload is not None

    assert payload["chunk_id"] == TEST_CHUNK_ID
    assert payload["rag_document_idx"] == 100
    assert payload["users_idx"] == 1
    assert payload["file_idx"] == 10
    assert payload["folder_idx"] == 3
    assert payload["content"] == TEST_CONTENT
    assert payload["page"] == 2
    assert payload["file_hash"] == TEST_FILE_HASH
    assert payload["parser_version"] == TEST_PARSER_VERSION
    assert payload["embedding_model"] == TEST_EMBEDDING_MODEL
    assert payload["embedding_dim"] == TEST_EMBEDDING_DIM
    assert payload["index_version"] == TEST_INDEX_VERSION
    assert payload["is_active"] is False

    # S3 객체 위치는 AWS 서버 DB가 관리하므로
    # Qdrant payload에 Presigned URL이나 S3_Key를 복제하지 않는다.
    assert "s3_key" not in payload
    assert "file_url" not in payload


@pytest.mark.asyncio
async def test_upsert_document_can_refresh_reused_point_as_active() -> None:
    """정상 색인을 재사용할 때 동일 Point를 활성 상태로 갱신할 수 있다."""

    fake_client = FakeAsyncQdrantClient()

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    await vector_store.upsert_document(
        rag_document_idx=100,
        metadata=_create_metadata(),
        embedded_document=_create_embedded_document(),
        is_active=True,
    )

    points = cast(
        list[models.PointStruct],
        fake_client.upsert_calls[0]["points"],
    )

    payload = points[0].payload

    assert payload is not None
    assert payload["is_active"] is True


@pytest.mark.asyncio
async def test_upsert_document_prepares_collection_only_once() -> None:
    """같은 저장소 인스턴스에서 Collection 초기화를 반복하지 않는다."""

    fake_client = FakeAsyncQdrantClient()

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    for _ in range(2):
        await vector_store.upsert_document(
            rag_document_idx=100,
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
            is_active=False,
        )

    assert fake_client.collection_exists_calls == [
        TEST_COLLECTION,
    ]
    assert len(fake_client.create_collection_calls) == 1
    assert len(fake_client.upsert_calls) == 2


@pytest.mark.asyncio
async def test_upsert_document_rejects_embedding_dimension_mismatch() -> None:
    """TEI 결과 차원과 Qdrant 설정이 다르면 네트워크 요청 전에 실패한다."""

    fake_client = FakeAsyncQdrantClient()

    settings = _create_settings().model_copy(
        update={
            "embedding_dim": 4,
        }
    )

    vector_store = QdrantChunkVectorStore(
        settings,
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    with pytest.raises(
        VectorCollectionConfigurationError,
    ) as exception_info:
        await vector_store.upsert_document(
            rag_document_idx=100,
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
            is_active=False,
        )

    assert exception_info.value.operation == "embedding_dim_mismatch"
    assert fake_client.collection_exists_calls == []
    assert fake_client.upsert_calls == []


@pytest.mark.asyncio
async def test_upsert_document_maps_response_handling_error_to_unavailable() -> None:
    """Qdrant 연결 계층 오류를 일시적 사용 불가 예외로 변환한다."""

    fake_client = FakeAsyncQdrantClient()
    fake_client.collection_exists_result = True
    fake_client.upsert_error = ResponseHandlingException(
        Exception(
            "connection failed",
        )
    )

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    with pytest.raises(
        VectorDatabaseUnavailableError,
    ) as exception_info:
        await vector_store.upsert_document(
            rag_document_idx=100,
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
            is_active=False,
        )

    assert exception_info.value.operation == "upsert_document"


@pytest.mark.asyncio
async def test_upsert_document_maps_qdrant_400_to_rejected() -> None:
    """Qdrant 4xx 응답을 재시도 불가능한 벡터 저장 거부로 변환한다."""

    fake_client = FakeAsyncQdrantClient()
    fake_client.collection_exists_result = True
    fake_client.upsert_error = UnexpectedResponse(
        status_code=400,
        reason_phrase="Bad Request",
        content=b"{}",
        headers=Headers(),
    )

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    with pytest.raises(
        VectorDatabaseRejectedError,
    ) as exception_info:
        await vector_store.upsert_document(
            rag_document_idx=100,
            metadata=_create_metadata(),
            embedded_document=_create_embedded_document(),
            is_active=False,
        )

    assert exception_info.value.operation == "upsert_document"
    assert exception_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_documents_active_updates_matching_document_payload() -> None:
    """문서 식별자 Filter로 이전 또는 새 Point의 활성 상태를 일괄 변경한다."""

    fake_client = FakeAsyncQdrantClient()

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    await vector_store.set_documents_active(
        rag_document_idxs=(
            90,
            91,
        ),
        is_active=False,
    )

    assert len(fake_client.set_payload_calls) == 1

    set_payload_call = fake_client.set_payload_calls[0]

    assert set_payload_call["collection_name"] == TEST_COLLECTION
    assert set_payload_call["payload"] == {
        "is_active": False,
    }
    assert set_payload_call["wait"] is True

    point_filter = cast(
        models.Filter,
        set_payload_call["points"],
    )

    condition = _extract_single_field_condition(
        point_filter,
    )

    assert condition.key == "rag_document_idx"
    assert isinstance(
        condition.match,
        models.MatchAny,
    )
    assert condition.match.any == [
        90,
        91,
    ]


@pytest.mark.asyncio
async def test_set_documents_active_skips_empty_document_ids() -> None:
    """대상 문서가 없으면 Collection 조회와 payload 변경을 수행하지 않는다."""

    fake_client = FakeAsyncQdrantClient()

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    await vector_store.set_documents_active(
        rag_document_idxs=(),
        is_active=True,
    )

    assert fake_client.collection_exists_calls == []
    assert fake_client.set_payload_calls == []


@pytest.mark.asyncio
async def test_delete_chunks_uses_point_id_list_and_waits_for_completion() -> None:
    """보상 삭제 시 Local RAG Chunk_ID 목록을 Qdrant Point ID로 전달한다."""

    fake_client = FakeAsyncQdrantClient()

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    await vector_store.delete_chunks(
        chunk_ids=(TEST_CHUNK_ID,),
    )

    assert len(fake_client.delete_calls) == 1

    delete_call = fake_client.delete_calls[0]

    points_selector = cast(
        models.PointIdsList,
        delete_call["points_selector"],
    )

    assert delete_call["collection_name"] == TEST_COLLECTION
    assert delete_call["wait"] is True
    assert points_selector.points == [
        TEST_CHUNK_ID,
    ]


@pytest.mark.asyncio
async def test_delete_chunks_skips_empty_chunk_ids() -> None:
    """삭제 대상 Chunk ID가 없으면 Qdrant 요청을 수행하지 않는다."""

    fake_client = FakeAsyncQdrantClient()

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    await vector_store.delete_chunks(
        chunk_ids=(),
    )

    assert fake_client.delete_calls == []
