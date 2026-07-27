"""사용자, 활성 상태와 선택 문서 범위를 강제하여 Qdrant 청크를 검색한다.

검색 필터는 호출자가 전달하는 선택 옵션이 아니라 Local RAG의 보안 경계다.
따라서 Repository가 직접 다음 세 조건을 하나의 AND 필터로 구성한다.

- ``users_idx == 요청 사용자``
- ``is_active == true``
- ``file_idx IN 요청 시점 선택 문서``

Qdrant 응답을 받은 뒤에도 같은 범위를 재검증한다. 일반 파서 청크와 OCR 청크는
동일한 collection에서 검색하며, ``source_metadata``를 보존해 상위 계층이 공통
Source Locator를 생성할 수 있게 한다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import ceil
from typing import Final, cast

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse,
)

from jipsa_rag.core.config import Settings
from jipsa_rag.infrastructure.embedding.query import QueryEmbedding
from jipsa_rag.infrastructure.indexing.exceptions import (
    InvalidVectorSearchResultError,
    VectorCollectionConfigurationError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.infrastructure.indexing.source_metadata import (
    JsonValue,
    normalize_source_metadata,
)

_USERS_IDX_PAYLOAD_KEY: Final[str] = "users_idx"
_FILE_IDX_PAYLOAD_KEY: Final[str] = "file_idx"
_IS_ACTIVE_PAYLOAD_KEY: Final[str] = "is_active"
_MAX_SEARCH_LIMIT: Final[int] = 20

# source_metadata가 없는 과거 point에서도 공통 locator를 복원하기 위해 읽는
# top-level 위치 필드다. 새 point에서는 nested source_metadata가 우선한다.
_LOCATION_PAYLOAD_KEYS: Final[tuple[str, ...]] = (
    # 공통 및 PDF
    "page",
    "unit_type",
    "location_kind",
    "structure_path",
    "content_origin",
    # DOCX
    "section_index",
    "block_index",
    "paragraph_index",
    "table_index",
    "heading_level",
    "section_heading_level",
    "section_title",
    "row_number",
    "column_number",
    "row_count",
    "column_count",
    # PPTX
    "slide_no",
    "slide_number",
    "shape_index",
    "shape_id",
    "shape_path",
    "shape_name",
    "shape_type_name",
    "coordinate_space",
    "shape_left_emu",
    "shape_top_emu",
    "shape_width_emu",
    "shape_height_emu",
    # XLSX
    "sheet_name",
    "sheet_number",
    "sheet_index",
    "start_row",
    "end_row",
    "start_column",
    "end_column",
    "start_cell",
    "end_cell",
    "cell_range",
    "cell_coordinates",
    "merged_ranges",
    "merged_cell_ranges",
    # TXT
    "line_number",
    "line_start",
    "line_end",
    "line_start_number",
    "line_end_number",
    "source_char_start",
    "source_char_end",
    "text_char_start",
    "text_char_end",
    # OCR
    "image_ordinal",
    "image_index",
    "page_image_index",
    "image_order",
    "image_id",
    "image_kind",
    "ocr_engine",
    "ocr_mean_confidence",
)


@dataclass(frozen=True, slots=True)
class ChunkSearchHit:
    """검증된 Qdrant Point payload를 서비스 계층 값으로 변환한 결과."""

    chunk_id: str
    score: float
    users_idx: int
    rag_document_idx: int
    file_idx: int
    folder_idx: int | None
    file_name: str
    file_type: str
    chunk_index: int
    content: str
    token_count: int | None
    page: int | None
    slide_no: int | None
    sheet_name: str | None
    section_title: str | None
    parser_version: str
    embedding_model: str
    index_version: int
    source_metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """서비스 계층으로 전달하기 전에 타입과 범위를 방어적으로 검증한다."""

        _validate_required_text_value(self.chunk_id, field_name="chunk_id")
        if not math.isfinite(self.score) or not -1.0 <= self.score <= 1.0:
            raise ValueError("score must be finite and between -1.0 and 1.0.")

        _validate_positive_integer(self.users_idx, field_name="users_idx")
        _validate_positive_integer(self.rag_document_idx, field_name="rag_document_idx")
        _validate_positive_integer(self.file_idx, field_name="file_idx")
        _validate_positive_integer(self.index_version, field_name="index_version")
        _validate_optional_integer(self.folder_idx, field_name="folder_idx", minimum=1)
        _validate_non_negative_integer(self.chunk_index, field_name="chunk_index")
        _validate_optional_integer(self.token_count, field_name="token_count", minimum=0)
        _validate_optional_integer(self.page, field_name="page", minimum=1)
        _validate_optional_integer(self.slide_no, field_name="slide_no", minimum=1)

        _validate_required_text_value(self.file_name, field_name="file_name")
        _validate_required_text_value(self.file_type, field_name="file_type")
        _validate_required_text_value(self.content, field_name="content")
        _validate_required_text_value(self.parser_version, field_name="parser_version")
        _validate_required_text_value(self.embedding_model, field_name="embedding_model")
        _validate_optional_text_value(self.sheet_name, field_name="sheet_name")
        _validate_optional_text_value(self.section_title, field_name="section_title")

        # mutable dict가 frozen dataclass 내부에서 바뀌지 않도록 독립 복사본으로
        # 고정한다. JSON 호환성은 normalize_source_metadata가 이미 검증한다.
        object.__setattr__(self, "source_metadata", dict(self.source_metadata))


def build_user_active_reference_chunk_filter(
    *,
    user_idx: int,
    reference_file_idxs: tuple[int, ...],
) -> models.Filter:
    """사용자, 활성 상태 및 선택 문서 조건을 하나의 AND 필터로 구성한다."""

    _validate_positive_integer(user_idx, field_name="user_idx")
    _validate_reference_file_idxs(reference_file_idxs)

    return models.Filter(
        must=[
            models.FieldCondition(
                key=_USERS_IDX_PAYLOAD_KEY,
                match=models.MatchValue(value=user_idx),
            ),
            models.FieldCondition(
                key=_IS_ACTIVE_PAYLOAD_KEY,
                match=models.MatchValue(value=True),
            ),
            models.FieldCondition(
                key=_FILE_IDX_PAYLOAD_KEY,
                match=models.MatchAny(any=list(reference_file_idxs)),
            ),
        ]
    )


class QdrantChunkSearchRepository:
    """질의 벡터로 선택된 활성 문서의 일반·OCR 청크를 함께 검색한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client

    async def search(
        self,
        *,
        user_idx: int,
        reference_file_idxs: tuple[int, ...],
        query_embedding: QueryEmbedding,
        limit: int,
        score_threshold: float | None = None,
    ) -> tuple[ChunkSearchHit, ...]:
        """현재 사용자와 선택 문서 범위의 활성 청크만 관련도 순으로 반환한다.

        ``content_origin``에 대한 제외 필터를 만들지 않는다. 따라서 일반 파서
        텍스트와 ``content_origin=ocr`` 청크가 같은 벡터 검색 후보가 된다.
        """

        _validate_positive_integer(user_idx, field_name="user_idx")
        _validate_reference_file_idxs(reference_file_idxs)
        _validate_search_limit(limit)
        _validate_score_threshold(score_threshold)
        self._validate_query_embedding(query_embedding)

        expected_reference_file_idxs = frozenset(reference_file_idxs)
        query_filter = build_user_active_reference_chunk_filter(
            user_idx=user_idx,
            reference_file_idxs=reference_file_idxs,
        )
        client = self._get_client()

        try:
            response = await client.query_points(
                collection_name=self._settings.qdrant_collection,
                query=list(query_embedding.vector),
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
        except UnexpectedResponse as error:
            raise _convert_unexpected_response(
                error,
                operation="search_chunks",
            ) from error
        except ResponseHandlingException as error:
            raise VectorDatabaseUnavailableError("search_chunks") from error

        return tuple(
            _to_chunk_search_hit(
                point=point,
                expected_user_idx=user_idx,
                expected_reference_file_idxs=expected_reference_file_idxs,
                expected_embedding_model=query_embedding.embedding_model,
            )
            for point in response.points
        )

    async def close(self) -> None:
        """Repository가 직접 생성한 Qdrant 클라이언트만 종료한다."""

        if not self._owns_client or self._client is None:
            return
        await self._client.close()
        self._client = None

    def _get_client(self) -> AsyncQdrantClient:
        """최초 유효 검색 시점에 소유 클라이언트를 지연 생성한다."""

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

    def _validate_query_embedding(self, query_embedding: QueryEmbedding) -> None:
        """질의 벡터가 현재 collection 모델·차원 계약과 일치하는지 검증한다."""

        if query_embedding.embedding_model != self._settings.embedding_model:
            raise VectorCollectionConfigurationError("query_embedding_model_mismatch")
        if query_embedding.embedding_dim != self._settings.embedding_dim:
            raise VectorCollectionConfigurationError("query_embedding_dim_mismatch")
        if len(query_embedding.vector) != self._settings.embedding_dim:
            raise VectorCollectionConfigurationError("query_vector_dim_mismatch")


def _to_chunk_search_hit(
    *,
    point: models.ScoredPoint,
    expected_user_idx: int,
    expected_reference_file_idxs: frozenset[int],
    expected_embedding_model: str,
) -> ChunkSearchHit:
    """Qdrant ScoredPoint를 범위와 메타데이터가 검증된 hit으로 변환한다."""

    raw_payload = point.payload
    if not isinstance(raw_payload, Mapping):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    payload = cast(Mapping[str, object], raw_payload)

    users_idx = _required_int(payload, _USERS_IDX_PAYLOAD_KEY, minimum=1)
    file_idx = _required_int(payload, _FILE_IDX_PAYLOAD_KEY, minimum=1)
    is_active = _required_bool(payload, _IS_ACTIVE_PAYLOAD_KEY)
    chunk_id = _required_text(payload, "chunk_id")
    embedding_model = _required_text(payload, "embedding_model")

    # Qdrant 필터가 적용되었더라도 payload를 다시 검증하여 잘못된 테스트 대역,
    # 손상 point 또는 향후 구현 변경이 보안 경계를 우회하지 못하게 한다.
    if users_idx != expected_user_idx or not is_active:
        raise InvalidVectorSearchResultError("search_scope_contract_violation")
    if file_idx not in expected_reference_file_idxs:
        raise InvalidVectorSearchResultError(
            "search_reference_file_scope_contract_violation"
        )
    if embedding_model != expected_embedding_model:
        raise InvalidVectorSearchResultError("search_embedding_model_mismatch")
    if str(point.id) != chunk_id:
        raise InvalidVectorSearchResultError("search_chunk_id_mismatch")

    try:
        source_metadata = _extract_source_metadata(payload)
        return ChunkSearchHit(
            chunk_id=chunk_id,
            score=float(point.score),
            users_idx=users_idx,
            rag_document_idx=_required_int(
                payload,
                "rag_document_idx",
                minimum=1,
            ),
            file_idx=file_idx,
            folder_idx=_optional_int(payload, "folder_idx", minimum=1),
            file_name=_required_text(payload, "file_name"),
            file_type=_required_text(payload, "file_type"),
            chunk_index=_required_int(payload, "chunk_index", minimum=0),
            content=_required_text(payload, "content", preserve_original=True),
            token_count=_optional_int(payload, "token_count", minimum=0),
            page=_optional_int(payload, "page", minimum=1),
            slide_no=_optional_int(payload, "slide_no", minimum=1),
            sheet_name=_optional_text(payload, "sheet_name"),
            section_title=_optional_text(payload, "section_title"),
            parser_version=_required_text(payload, "parser_version"),
            embedding_model=embedding_model,
            index_version=_required_int(payload, "index_version", minimum=1),
            source_metadata=source_metadata,
        )
    except (TypeError, ValueError) as error:
        raise InvalidVectorSearchResultError("invalid_search_result_value") from error


def _extract_source_metadata(payload: Mapping[str, object]) -> dict[str, JsonValue]:
    """nested source_metadata를 우선 읽고 legacy top-level 위치를 보완한다."""

    raw_metadata = payload.get("source_metadata")
    if raw_metadata is None:
        metadata: dict[str, JsonValue] = {}
    elif isinstance(raw_metadata, Mapping):
        try:
            metadata = normalize_source_metadata(
                cast(Mapping[str, object], raw_metadata)
            )
        except ValueError as error:
            raise InvalidVectorSearchResultError(
                "invalid_search_source_metadata"
            ) from error
    else:
        raise InvalidVectorSearchResultError("invalid_search_source_metadata")

    # 새 nested 값이 존재하면 절대 top-level 값으로 덮어쓰지 않는다.
    # legacy page는 source locator builder가 이해하는 page_number로 정규화한다.
    for key in _LOCATION_PAYLOAD_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        target_key = "page_number" if key == "page" else key
        metadata.setdefault(target_key, _normalize_payload_scalar(value))

    return metadata


def _normalize_payload_scalar(value: object) -> JsonValue:
    """legacy top-level 위치 값을 JSON 표준 값으로 검증한다."""

    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, Mapping):
        return normalize_source_metadata(cast(Mapping[str, object], value))
    if isinstance(value, (list, tuple)):
        normalized: list[JsonValue] = []
        for item in value:
            normalized.append(_normalize_payload_scalar(item))
        return normalized
    raise InvalidVectorSearchResultError("invalid_search_source_metadata")


def _required_int(
    payload: Mapping[str, object],
    key: str,
    *,
    minimum: int,
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    return value


def _optional_int(
    payload: Mapping[str, object],
    key: str,
    *,
    minimum: int,
) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    return value


def _required_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    return value


def _required_text(
    payload: Mapping[str, object],
    key: str,
    *,
    preserve_original: bool = False,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    normalized = value.strip()
    if not normalized:
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    return value if preserve_original else normalized


def _optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    normalized = value.strip()
    if not normalized:
        raise InvalidVectorSearchResultError("invalid_search_result_payload")
    return normalized


def _validate_positive_integer(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _validate_non_negative_integer(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")


def _validate_optional_integer(
    value: int | None,
    *,
    field_name: str,
    minimum: int,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"{field_name} must be an integer greater than or equal to {minimum}, or null."
        )


def _validate_required_text_value(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty.")


def _validate_optional_text_value(value: str | None, *, field_name: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must not be empty when provided.")


def _validate_reference_file_idxs(reference_file_idxs: tuple[int, ...]) -> None:
    """빈 선택 범위를 전체 문서 검색으로 해석하지 못하도록 강제한다."""

    if not isinstance(reference_file_idxs, tuple) or not reference_file_idxs:
        raise ValueError("reference_file_idxs must be a non-empty tuple.")
    seen_file_idxs: set[int] = set()
    for file_idx in reference_file_idxs:
        _validate_positive_integer(file_idx, field_name="reference_file_idx")
        if file_idx in seen_file_idxs:
            raise ValueError("reference_file_idxs must contain unique values.")
        seen_file_idxs.add(file_idx)


def _validate_search_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_SEARCH_LIMIT}.")


def _validate_score_threshold(score_threshold: float | None) -> None:
    if score_threshold is None:
        return
    if isinstance(score_threshold, bool) or not isinstance(score_threshold, int | float):
        raise ValueError("score_threshold must be numeric or null.")
    normalized = float(score_threshold)
    if not math.isfinite(normalized):
        raise ValueError("score_threshold must be finite.")
    if not -1.0 <= normalized <= 1.0:
        raise ValueError("score_threshold must be between -1.0 and 1.0.")


def _convert_unexpected_response(
    error: UnexpectedResponse,
    *,
    operation: str,
) -> VectorDatabaseUnavailableError | VectorDatabaseRejectedError:
    status_code = error.status_code
    if status_code in {408, 429} or (
        status_code is not None and status_code >= 500
    ):
        return VectorDatabaseUnavailableError(operation, status_code=status_code)
    return VectorDatabaseRejectedError(operation, status_code=status_code)
