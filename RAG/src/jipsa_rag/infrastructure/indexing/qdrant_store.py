"""청크 임베딩과 구조 위치 payload를 Qdrant에 저장한다.

Local RAG DB의 Chunk_ID를 Qdrant Point ID로 그대로 사용한다. 신규 색인은 먼저
``is_active=False``로 staging하고 모든 Point가 저장된 뒤 활성화하므로, 중간 실패가
기존 정상 검색 결과를 제거하지 않는다.

PPTX 도형 좌표, XLSX 셀 범위, TXT 문자 범위처럼 형식마다 다른 위치 정보는
``source_metadata`` 객체에 전체 보존한다. 자주 조회하거나 운영 확인에 사용하는
페이지·슬라이드·시트·줄·unit 종류는 top-level payload에도 투영하여 필터와 확인을
쉽게 한다.
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from math import ceil
from typing import Final

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse,
)

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.chunking.models import TextChunk
from jipsa_rag.infrastructure.embedding.models import EmbeddedDocument
from jipsa_rag.infrastructure.indexing.exceptions import (
    VectorCollectionConfigurationError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata
from jipsa_rag.infrastructure.indexing.source_metadata import (
    metadata_float,
    metadata_int,
    metadata_text,
    normalize_source_metadata,
)

# Equality/range 필터가 실제로 필요한 top-level 필드만 인덱싱한다. 전체
# source_metadata의 모든 키를 인덱싱하면 형식별 고유 좌표가 늘 때마다 Qdrant
# 스키마 관리 비용이 증가하므로 원본 객체는 저장만 하고 대표 필드만 투영한다.
_PAYLOAD_INDEXES: Final[tuple[tuple[str, models.PayloadSchemaType], ...]] = (
    # 기존 Collection의 필터·테스트 계약을 유지한다. 새 형식별 위치 필드는
    # payload에 저장하지만 기본 검색 경로에서 equality filter로 사용하지 않으므로
    # 불필요한 인덱스를 만들지 않는다. 운영 조회가 실제로 필요해지면 별도
    # Index_Version/Collection 마이그레이션과 함께 추가한다.
    ("users_idx", models.PayloadSchemaType.INTEGER),
    ("file_idx", models.PayloadSchemaType.INTEGER),
    ("folder_idx", models.PayloadSchemaType.INTEGER),
    ("rag_document_idx", models.PayloadSchemaType.INTEGER),
    ("is_active", models.PayloadSchemaType.BOOL),
    ("index_version", models.PayloadSchemaType.INTEGER),
    ("embedding_model", models.PayloadSchemaType.KEYWORD),
    ("file_type", models.PayloadSchemaType.KEYWORD),
    ("parser_version", models.PayloadSchemaType.KEYWORD),
)


class QdrantChunkVectorStore:
    """Qdrant Collection 준비, Point 업서트와 활성 색인 전환을 담당한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        """설정과 선택적인 외부 클라이언트를 주입받아 지연 초기화한다."""

        self._settings = settings
        self._owns_client = client is None
        self._client = client
        self._collection_ready = False
        self._collection_lock = asyncio.Lock()

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
        is_active: bool,
    ) -> None:
        """문서의 모든 청크 벡터와 확장 payload를 지정 활성 상태로 업서트한다."""

        self._validate_embedding_configuration(embedded_document)
        client = await self._ensure_collection()
        created_at = datetime.now(UTC).isoformat()

        try:
            points = [
                models.PointStruct(
                    id=embedded_chunk.chunk_id,
                    vector=list(embedded_chunk.embedding),
                    payload=_build_payload(
                        rag_document_idx=rag_document_idx,
                        metadata=metadata,
                        embedded_document=embedded_document,
                        embedded_chunk_index=embedded_chunk.chunk_index,
                        chunk=embedded_chunk.chunk,
                        is_active=is_active,
                        created_at=created_at,
                    ),
                )
                for embedded_chunk in embedded_document.chunks
            ]
        except ValueError as error:
            # JSON 비호환 메타데이터는 Qdrant HTTP 요청 전에 저장 계약 오류로 변환한다.
            raise VectorCollectionConfigurationError("invalid_source_metadata") from error

        try:
            await client.upsert(
                collection_name=self._settings.qdrant_collection,
                points=points,
                wait=True,
            )
        except UnexpectedResponse as error:
            raise _convert_unexpected_response(error, operation="upsert_document") from error
        except ResponseHandlingException as error:
            raise VectorDatabaseUnavailableError("upsert_document") from error

    async def set_documents_active(
        self,
        *,
        rag_document_idxs: tuple[int, ...],
        is_active: bool,
    ) -> None:
        """지정한 Local RAG 문서에 속한 모든 Point의 활성 상태를 변경한다."""

        normalized_document_ids = _normalize_document_ids(rag_document_idxs)
        if not normalized_document_ids:
            return

        client = await self._ensure_collection()
        try:
            await client.set_payload(
                collection_name=self._settings.qdrant_collection,
                payload={"is_active": is_active},
                points=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="rag_document_idx",
                            match=models.MatchAny(any=list(normalized_document_ids)),
                        )
                    ]
                ),
                wait=True,
            )
        except UnexpectedResponse as error:
            raise _convert_unexpected_response(
                error,
                operation="set_documents_active",
            ) from error
        except ResponseHandlingException as error:
            raise VectorDatabaseUnavailableError("set_documents_active") from error

    async def delete_chunks(self, *, chunk_ids: tuple[str, ...]) -> None:
        """실패한 신규 색인의 staging Point를 Chunk ID로 보상 삭제한다."""

        if not chunk_ids:
            return

        client = self._get_client()
        try:
            await client.delete(
                collection_name=self._settings.qdrant_collection,
                points_selector=models.PointIdsList(points=list(chunk_ids)),
                wait=True,
            )
        except UnexpectedResponse as error:
            raise _convert_unexpected_response(error, operation="delete_chunks") from error
        except ResponseHandlingException as error:
            raise VectorDatabaseUnavailableError("delete_chunks") from error

    async def close(self) -> None:
        """이 저장소가 실제 생성한 Qdrant 클라이언트만 종료한다."""

        if not self._owns_client or self._client is None:
            return

        await self._client.close()
        self._client = None
        self._collection_ready = False

    def _get_client(self) -> AsyncQdrantClient:
        """최초 실제 Qdrant 연산에서 소유 클라이언트를 한 번만 생성한다."""

        if self._client is None:
            self._client = AsyncQdrantClient(
                url=self._settings.qdrant_url,
                grpc_port=self._settings.qdrant_grpc_port,
                prefer_grpc=self._settings.qdrant_prefer_grpc,
                api_key=(
                    self._settings.qdrant_api_key.get_secret_value()
                    if self._settings.qdrant_api_key is not None
                    else None
                ),
                timeout=max(1, ceil(self._settings.qdrant_timeout_seconds)),
            )
        return self._client

    async def _ensure_collection(self) -> AsyncQdrantClient:
        """Collection과 구조 검색용 payload index를 한 번 준비한다."""

        client = self._get_client()
        if self._collection_ready:
            return client

        async with self._collection_lock:
            if self._collection_ready:
                return client

            try:
                collection_exists = await client.collection_exists(self._settings.qdrant_collection)
                if not collection_exists:
                    try:
                        await client.create_collection(
                            collection_name=self._settings.qdrant_collection,
                            vectors_config=models.VectorParams(
                                size=self._settings.embedding_dim,
                                distance=_resolve_distance(self._settings.embedding_distance),
                            ),
                        )
                    except UnexpectedResponse as error:
                        # 여러 프로세스가 동시에 생성한 409는 정상적인 멱등 경합이다.
                        if error.status_code != 409:
                            raise

                for field_name, field_schema in _PAYLOAD_INDEXES:
                    try:
                        await client.create_payload_index(
                            collection_name=self._settings.qdrant_collection,
                            field_name=field_name,
                            field_schema=field_schema,
                            wait=True,
                        )
                    except UnexpectedResponse as error:
                        if error.status_code != 409:
                            raise
            except UnexpectedResponse as error:
                raise _convert_unexpected_response(
                    error,
                    operation="ensure_collection",
                ) from error
            except ResponseHandlingException as error:
                raise VectorDatabaseUnavailableError("ensure_collection") from error

            self._collection_ready = True

        return client

    def _validate_embedding_configuration(
        self,
        embedded_document: EmbeddedDocument,
    ) -> None:
        """TEI 결과와 Qdrant Collection의 모델·차원 계약을 확인한다."""

        if embedded_document.embedding_model != self._settings.embedding_model:
            raise VectorCollectionConfigurationError("embedding_model_mismatch")
        if embedded_document.embedding_dim != self._settings.embedding_dim:
            raise VectorCollectionConfigurationError("embedding_dim_mismatch")


def _build_payload(
    *,
    rag_document_idx: int,
    metadata: DocumentIndexMetadata,
    embedded_document: EmbeddedDocument,
    embedded_chunk_index: int,
    chunk: TextChunk,
    is_active: bool,
    created_at: str,
) -> dict[str, object]:
    """청크와 문서 정보를 Qdrant의 다중 형식 검색 payload로 변환한다."""

    source_metadata: Mapping[str, object] = chunk.source_metadata
    normalized_source_metadata = normalize_source_metadata(source_metadata)

    return {
        "chunk_id": chunk.chunk_id,
        "rag_document_idx": rag_document_idx,
        "file_idx": metadata.file_idx,
        "users_idx": metadata.users_idx,
        "folder_idx": metadata.folder_idx,
        "chunk_index": embedded_chunk_index,
        "content": chunk.content,
        "token_count": chunk.token_count,
        "file_name": metadata.file_name,
        "file_type": metadata.file_type.value,
        "file_hash": metadata.file_hash,
        "parser_type": metadata.parser_type,
        "parser_version": metadata.parser_version,
        "page": metadata_int(source_metadata, "page_number", minimum=1),
        "slide_no": (
            metadata_int(source_metadata, "slide_number", minimum=1)
            or metadata_int(source_metadata, "slide_no", minimum=1)
        ),
        "sheet_name": metadata_text(source_metadata, "sheet_name"),
        "sheet_number": (
            metadata_int(source_metadata, "sheet_number", minimum=1)
            or metadata_int(source_metadata, "sheet_index", minimum=1)
        ),
        "cell_range": metadata_text(source_metadata, "cell_range"),
        "line_number": metadata_int(source_metadata, "line_number", minimum=1),
        "source_char_start": metadata_int(
            source_metadata,
            "source_char_start",
            minimum=0,
        ),
        "source_char_end": metadata_int(
            source_metadata,
            "source_char_end",
            minimum=0,
        ),
        "shape_path": metadata_text(source_metadata, "shape_path"),
        "shape_left_emu": metadata_int(
            source_metadata,
            "shape_left_emu",
            minimum=0,
        ),
        "shape_top_emu": metadata_int(
            source_metadata,
            "shape_top_emu",
            minimum=0,
        ),
        "shape_width_emu": metadata_int(
            source_metadata,
            "shape_width_emu",
            minimum=0,
        ),
        "shape_height_emu": metadata_int(
            source_metadata,
            "shape_height_emu",
            minimum=0,
        ),
        "shape_left_ratio": metadata_float(source_metadata, "shape_left_ratio"),
        "shape_top_ratio": metadata_float(source_metadata, "shape_top_ratio"),
        "unit_type": metadata_text(source_metadata, "unit_type"),
        "location_kind": metadata_text(source_metadata, "location_kind"),
        "structure_path": metadata_text(source_metadata, "structure_path"),
        "section_title": metadata_text(source_metadata, "section_title"),
        "chunking_strategy": metadata_text(
            source_metadata,
            "chunking_strategy",
        ),
        "chunking_strategy_version": metadata_text(
            source_metadata,
            "chunking_strategy_version",
        ),
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "content_hash": chunk.content_hash,
        "source_metadata": normalized_source_metadata,
        "embedding_model": embedded_document.embedding_model,
        "embedding_dim": embedded_document.embedding_dim,
        "index_version": metadata.index_version,
        "is_active": is_active,
        "created_at": created_at,
    }


def _normalize_document_ids(document_ids: tuple[int, ...]) -> tuple[int, ...]:
    """Qdrant filter에 사용할 양의 고유 문서 식별자를 검증한다."""

    normalized_ids = tuple(document_ids)
    if any(
        isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0
        for document_id in normalized_ids
    ):
        raise ValueError("rag_document_idxs must contain positive integers.")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("rag_document_idxs must be unique.")
    return normalized_ids


def _resolve_distance(distance: str) -> models.Distance:
    """환경 설정의 거리 함수 문자열을 Qdrant enum으로 변환한다."""

    if distance == "cosine":
        return models.Distance.COSINE
    raise VectorCollectionConfigurationError("unsupported_distance")


def _convert_unexpected_response(
    error: UnexpectedResponse,
    *,
    operation: str,
) -> VectorDatabaseUnavailableError | VectorDatabaseRejectedError:
    """Qdrant HTTP 오류를 재시도 가능 여부에 따라 분류한다."""

    status_code = error.status_code
    if status_code in {408, 429} or (status_code is not None and status_code >= 500):
        return VectorDatabaseUnavailableError(operation, status_code=status_code)
    return VectorDatabaseRejectedError(operation, status_code=status_code)


_qdrant_vector_store: QdrantChunkVectorStore | None = None


def get_qdrant_vector_store() -> QdrantChunkVectorStore:
    """프로세스에서 재사용할 지연 초기화 Qdrant 저장소를 반환한다."""

    global _qdrant_vector_store
    if _qdrant_vector_store is None:
        _qdrant_vector_store = QdrantChunkVectorStore(get_settings())
    return _qdrant_vector_store


async def close_qdrant_vector_store() -> None:
    """생성된 Qdrant 저장소와 소유 클라이언트를 안전하게 종료한다."""

    global _qdrant_vector_store
    if _qdrant_vector_store is None:
        return
    await _qdrant_vector_store.close()
    _qdrant_vector_store = None
