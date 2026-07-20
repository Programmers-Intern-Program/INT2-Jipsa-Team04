"""청크 임베딩과 검색 메타데이터를 Qdrant에 저장한다."""

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

# 사용자·파일 범위 검색과 활성 색인 필터에 자주 사용되는 payload 필드다.
#
# Content, 파일명 및 원본 위치 정보는 반환용 payload로 저장하지만
# equality/range 필터의 주요 대상이 아니므로 초기 인덱스 목록에서 제외한다.
_PAYLOAD_INDEXES: Final[tuple[tuple[str, models.PayloadSchemaType], ...]] = (
    ("users_idx", models.PayloadSchemaType.INTEGER),
    ("file_idx", models.PayloadSchemaType.INTEGER),
    ("folder_idx", models.PayloadSchemaType.INTEGER),
    ("rag_document_idx", models.PayloadSchemaType.INTEGER),
    ("is_active", models.PayloadSchemaType.BOOL),
    ("index_version", models.PayloadSchemaType.INTEGER),
    ("embedding_model", models.PayloadSchemaType.KEYWORD),
    ("file_type", models.PayloadSchemaType.KEYWORD),
)


class QdrantChunkVectorStore:
    """Qdrant Collection 생성과 청크 Point 업서트를 담당한다.

    Local RAG DB의 Chunk_ID를 Qdrant Point ID로 그대로 사용한다.
    따라서 Local RAG_Chunk와 VectorDB Point를 별도 매핑 테이블 없이
    안정적으로 연결할 수 있다.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        """Qdrant 연결 설정과 선택적인 테스트 클라이언트를 주입받는다."""

        self._settings = settings
        self._owns_client = client is None
        self._client = client or AsyncQdrantClient(
            url=settings.qdrant_url,
            grpc_port=settings.qdrant_grpc_port,
            prefer_grpc=settings.qdrant_prefer_grpc,
            api_key=(
                settings.qdrant_api_key.get_secret_value()
                if settings.qdrant_api_key is not None
                else None
            ),
            # qdrant-client의 timeout 인수는 정수 초를 사용한다.
            # 소수 설정값을 내림하면 관리자가 지정한 제한보다 짧아질 수 있으므로
            # 올림한 양수 값으로 전달한다.
            timeout=max(1, ceil(settings.qdrant_timeout_seconds)),
        )

        # Collection 존재 확인과 payload index 생성은 프로세스에서 한 번만 수행한다.
        self._collection_ready = False
        self._collection_lock = asyncio.Lock()

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> None:
        """문서의 모든 청크 벡터와 검색 payload를 하나의 Collection에 업서트한다."""

        self._validate_embedding_configuration(embedded_document)
        await self._ensure_collection()

        created_at = datetime.now(UTC).isoformat()

        points = [
            models.PointStruct(
                # CharacterTextChunker가 생성한 UUID 문자열을 Point ID로 사용한다.
                # 동일한 문서와 동일한 청크를 재처리하면 같은 Point가 갱신된다.
                id=embedded_chunk.chunk_id,
                vector=list(embedded_chunk.embedding),
                payload=_build_payload(
                    rag_document_idx=rag_document_idx,
                    metadata=metadata,
                    embedded_document=embedded_document,
                    embedded_chunk_index=embedded_chunk.chunk_index,
                    chunk=embedded_chunk.chunk,
                    created_at=created_at,
                ),
            )
            for embedded_chunk in embedded_document.chunks
        ]

        try:
            await self._client.upsert(
                collection_name=self._settings.qdrant_collection,
                points=points,
                # wait=True를 사용하여 서버가 변경을 적용한 뒤 성공을 반환하게 한다.
                # Local RAG DB의 INDEXED 상태는 이 호출이 끝난 후에만 기록한다.
                wait=True,
            )
        except UnexpectedResponse as error:
            raise _convert_unexpected_response(
                error,
                operation="upsert_document",
            ) from error
        except ResponseHandlingException as error:
            raise VectorDatabaseUnavailableError("upsert_document") from error

    async def delete_chunks(
        self,
        *,
        chunk_ids: tuple[str, ...],
    ) -> None:
        """Local RAG 성공 상태 반영 실패 시 업서트한 Point를 보상 삭제한다."""

        if not chunk_ids:
            return

        try:
            await self._client.delete(
                collection_name=self._settings.qdrant_collection,
                points_selector=models.PointIdsList(
                    points=list(chunk_ids),
                ),
                wait=True,
            )
        except UnexpectedResponse as error:
            raise _convert_unexpected_response(
                error,
                operation="delete_chunks",
            ) from error
        except ResponseHandlingException as error:
            raise VectorDatabaseUnavailableError("delete_chunks") from error

    async def close(self) -> None:
        """이 저장소가 직접 생성한 Qdrant 클라이언트 연결을 종료한다."""

        if self._owns_client:
            await self._client.close()

    async def _ensure_collection(self) -> None:
        """Collection과 필터용 payload index를 요청 처리 전에 준비한다."""

        if self._collection_ready:
            return

        async with self._collection_lock:
            # lock을 기다리는 동안 다른 요청이 준비를 완료했을 수 있으므로
            # 임계 구역 진입 후 상태를 다시 확인한다.
            if self._collection_ready:
                return

            try:
                collection_exists = await self._client.collection_exists(
                    self._settings.qdrant_collection
                )

                if not collection_exists:
                    try:
                        await self._client.create_collection(
                            collection_name=self._settings.qdrant_collection,
                            vectors_config=models.VectorParams(
                                size=self._settings.embedding_dim,
                                distance=_resolve_distance(self._settings.embedding_distance),
                            ),
                        )
                    except UnexpectedResponse as error:
                        # 여러 RAG 프로세스가 동시에 시작하면 다른 프로세스가
                        # 먼저 동일 Collection을 생성하여 409를 반환할 수 있다.
                        # 이 경우에는 Collection이 준비된 것으로 간주한다.
                        if error.status_code != 409:
                            raise

                for field_name, field_schema in _PAYLOAD_INDEXES:
                    try:
                        await self._client.create_payload_index(
                            collection_name=self._settings.qdrant_collection,
                            field_name=field_name,
                            field_schema=field_schema,
                            wait=True,
                        )
                    except UnexpectedResponse as error:
                        # 동일한 payload index가 이미 존재하는 409 응답은
                        # 멱등적인 초기화 과정의 정상 경합으로 처리한다.
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

    def _validate_embedding_configuration(
        self,
        embedded_document: EmbeddedDocument,
    ) -> None:
        """TEI 결과와 Qdrant Collection 설정의 모델·차원을 검증한다."""

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
    created_at: str,
) -> dict[str, object]:
    """청크와 문서 정보를 Qdrant 검색 payload로 변환한다."""

    source_metadata: Mapping[str, object] = chunk.source_metadata

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
        "page": _metadata_int(
            source_metadata,
            "page_number",
            minimum=1,
        ),
        "slide_no": (
            _metadata_int(
                source_metadata,
                "slide_number",
                minimum=1,
            )
            or _metadata_int(
                source_metadata,
                "slide_no",
                minimum=1,
            )
        ),
        "sheet_name": _metadata_text(source_metadata, "sheet_name"),
        "section_title": _metadata_text(source_metadata, "section_title"),
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "content_hash": chunk.content_hash,
        "embedding_model": embedded_document.embedding_model,
        "embedding_dim": embedded_document.embedding_dim,
        "index_version": metadata.index_version,
        "is_active": True,
        "created_at": created_at,
    }


def _resolve_distance(distance: str) -> models.Distance:
    """환경 설정의 거리 함수 문자열을 Qdrant enum으로 변환한다."""

    if distance == "cosine":
        return models.Distance.COSINE

    # Settings가 Literal과 validator로 현재 cosine만 허용하지만,
    # 향후 설정 타입이 확장되었을 때 잘못된 기본값으로 저장하지 않는다.
    raise VectorCollectionConfigurationError("unsupported_distance")


def _convert_unexpected_response(
    error: UnexpectedResponse,
    *,
    operation: str,
) -> VectorDatabaseUnavailableError | VectorDatabaseRejectedError:
    """Qdrant HTTP 오류를 재시도 가능 여부에 따라 분류한다."""

    status_code = error.status_code

    if status_code in {
        408,
        429,
    } or (status_code is not None and status_code >= 500):
        return VectorDatabaseUnavailableError(
            operation,
            status_code=status_code,
        )

    return VectorDatabaseRejectedError(
        operation,
        status_code=status_code,
    )


def _metadata_int(
    metadata: Mapping[str, object],
    key: str,
    *,
    minimum: int,
) -> int | None:
    """청크 메타데이터에서 지정한 최솟값 이상의 정수를 읽는다."""

    value = metadata.get(key)

    if isinstance(value, bool) or not isinstance(value, int):
        return None

    return value if value >= minimum else None


def _metadata_text(
    metadata: Mapping[str, object],
    key: str,
) -> str | None:
    """청크 메타데이터에서 비어 있지 않은 문자열을 읽는다."""

    value = metadata.get(key)

    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    return normalized_value or None


_qdrant_vector_store: QdrantChunkVectorStore | None = None


def get_qdrant_vector_store() -> QdrantChunkVectorStore:
    """애플리케이션 프로세스에서 재사용할 Qdrant 저장소를 반환한다."""

    global _qdrant_vector_store

    if _qdrant_vector_store is None:
        _qdrant_vector_store = QdrantChunkVectorStore(get_settings())

    return _qdrant_vector_store


async def close_qdrant_vector_store() -> None:
    """생성된 Qdrant 저장소가 있을 때만 클라이언트를 종료한다."""

    global _qdrant_vector_store

    if _qdrant_vector_store is None:
        return

    await _qdrant_vector_store.close()
    _qdrant_vector_store = None
