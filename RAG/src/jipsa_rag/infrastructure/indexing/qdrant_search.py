"""사용자, 활성 상태와 참조문서 범위를 강제하여 Qdrant 청크를 검색한다."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
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

# 검색 결과 payload에서 반드시 존재해야 하는 사용자, 파일 및 활성 상태 필드다.
#
# API 요청은 user_idx 단수형을 사용하지만 기존 Qdrant payload 계약은
# AWS Users 테이블 외부 참조라는 의미로 users_idx 복수형을 사용한다.
_USERS_IDX_PAYLOAD_KEY: Final[str] = "users_idx"
_FILE_IDX_PAYLOAD_KEY: Final[str] = "file_idx"
_IS_ACTIVE_PAYLOAD_KEY: Final[str] = "is_active"

# Qdrant query_points 요청에 허용할 최대 검색 결과 수다.
#
# API 요청 스키마의 top_k 상한과 같은 값으로 유지하여
# 인프라 계층이 API 계층을 거치지 않은 과도한 검색도 방어한다.
_MAX_SEARCH_LIMIT: Final[int] = 20


@dataclass(frozen=True, slots=True)
class ChunkSearchHit:
    """Qdrant Point payload를 검색 도메인 값으로 변환한 결과."""

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

    def __post_init__(self) -> None:
        """검색 결과가 서비스 계층으로 전달 가능한 상태인지 검증한다.

        서로 다른 타입을 하나의 반복문 변수로 검증하면 strict Mypy가
        첫 번째 타입으로 변수를 고정하여 이후 타입을 거부할 수 있다.

        따라서 정수, 선택적 정수, 문자열 및 선택적 문자열을 각각
        타입 전용 검증 함수로 분리하여 런타임 검증과 정적 타입 검사를
        동시에 만족하도록 한다.
        """

        _validate_required_text_value(
            self.chunk_id,
            field_name="chunk_id",
        )

        if not math.isfinite(self.score):
            raise ValueError("score must be finite.")

        if not -1.0 <= self.score <= 1.0:
            raise ValueError("score must be between -1.0 and 1.0.")

        _validate_positive_integer(
            self.users_idx,
            field_name="users_idx",
        )

        _validate_positive_integer(
            self.rag_document_idx,
            field_name="rag_document_idx",
        )

        _validate_positive_integer(
            self.file_idx,
            field_name="file_idx",
        )

        _validate_positive_integer(
            self.index_version,
            field_name="index_version",
        )

        _validate_optional_integer(
            self.folder_idx,
            field_name="folder_idx",
            minimum=1,
        )

        _validate_non_negative_integer(
            self.chunk_index,
            field_name="chunk_index",
        )

        _validate_optional_integer(
            self.token_count,
            field_name="token_count",
            minimum=0,
        )

        _validate_optional_integer(
            self.page,
            field_name="page",
            minimum=1,
        )

        _validate_optional_integer(
            self.slide_no,
            field_name="slide_no",
            minimum=1,
        )

        _validate_required_text_value(
            self.file_name,
            field_name="file_name",
        )

        _validate_required_text_value(
            self.file_type,
            field_name="file_type",
        )

        _validate_required_text_value(
            self.parser_version,
            field_name="parser_version",
        )

        _validate_required_text_value(
            self.embedding_model,
            field_name="embedding_model",
        )

        # content는 원문 위치와 LLM 인용 근거로 사용하므로
        # strip한 문자열로 교체하지 않고 검색 가능한 문자가
        # 하나 이상 존재하는지만 확인한다.
        _validate_required_text_value(
            self.content,
            field_name="content",
        )

        _validate_optional_text_value(
            self.sheet_name,
            field_name="sheet_name",
        )

        _validate_optional_text_value(
            self.section_title,
            field_name="section_title",
        )


def build_user_active_reference_chunk_filter(
    *,
    user_idx: int,
    reference_file_idxs: tuple[int, ...],
) -> models.Filter:
    """사용자, 활성 상태 및 참조문서 조건을 AND로 결합한다.

    세 조건을 하나의 ``must`` 절에 넣어 모든 조건을 동시에 만족하는
    청크만 검색되도록 한다.

    - users_idx == 요청 user_idx
    - is_active == true
    - file_idx IN 요청 reference_file_idxs

    검색 호출자가 임의의 필터를 전달하여 사용자 또는 참조문서 범위를
    완화할 수 없도록 Repository 내부에서 필터를 직접 생성한다.
    """

    _validate_positive_integer(
        user_idx,
        field_name="user_idx",
    )

    _validate_reference_file_idxs(
        reference_file_idxs,
    )

    return models.Filter(
        must=[
            models.FieldCondition(
                key=_USERS_IDX_PAYLOAD_KEY,
                match=models.MatchValue(
                    value=user_idx,
                ),
            ),
            models.FieldCondition(
                key=_IS_ACTIVE_PAYLOAD_KEY,
                match=models.MatchValue(
                    value=True,
                ),
            ),
            models.FieldCondition(
                key=_FILE_IDX_PAYLOAD_KEY,
                # MatchAny는 Qdrant의 IN 조건에 해당한다.
                #
                # 외부 요청의 JSON 배열은 스키마 계층에서 불변 tuple로
                # 변환되므로 Qdrant 모델에 전달할 때만 새 list를 생성한다.
                match=models.MatchAny(
                    any=list(reference_file_idxs),
                ),
            ),
        ]
    )


class QdrantChunkSearchRepository:
    """질의 벡터와 요청별 검색 범위로 활성 청크를 검색한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        """Qdrant 설정과 선택적인 테스트 클라이언트를 주입받는다."""

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
            # qdrant-client timeout은 정수 초를 사용한다.
            #
            # 설정값을 내림하면 관리자가 지정한 시간보다 짧아질 수 있으므로
            # 올림한 뒤 최소 1초를 보장한다.
            timeout=max(
                1,
                ceil(settings.qdrant_timeout_seconds),
            ),
        )

    async def search(
        self,
        *,
        user_idx: int,
        reference_file_idxs: tuple[int, ...],
        query_embedding: QueryEmbedding,
        limit: int,
        score_threshold: float | None = None,
    ) -> tuple[ChunkSearchHit, ...]:
        """요청 사용자의 선택된 활성 문서 청크만 관련도 순으로 조회한다."""

        _validate_positive_integer(
            user_idx,
            field_name="user_idx",
        )

        _validate_reference_file_idxs(
            reference_file_idxs,
        )

        _validate_search_limit(
            limit,
        )

        _validate_score_threshold(
            score_threshold,
        )

        self._validate_query_embedding(
            query_embedding,
        )

        # 현재 검색 호출의 참조문서 범위를 불변 집합으로 고정한다.
        #
        # 필터 생성과 검색 결과 재검증이 같은 스냅샷을 사용하므로,
        # 다른 질문의 참조문서 목록이나 이후 선택 변경이 현재 검색에
        # 섞이지 않는다.
        expected_reference_file_idxs = frozenset(reference_file_idxs)

        # 사용자 식별자, 활성 상태 및 참조문서 범위는 요청자가 완화할 수 있는
        # 선택적 조건이 아니라 검색 보안 경계이므로 Repository가 직접 생성한다.
        query_filter = build_user_active_reference_chunk_filter(
            user_idx=user_idx,
            reference_file_idxs=reference_file_idxs,
        )

        try:
            response = await self._client.query_points(
                collection_name=self._settings.qdrant_collection,
                query=list(query_embedding.vector),
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                # 검색 응답에 임베딩 벡터를 포함하지 않아
                # 응답 크기와 민감한 내부 데이터 노출 가능성을 줄인다.
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
        """Repository가 직접 생성한 Qdrant 클라이언트 연결을 종료한다."""

        if self._owns_client:
            await self._client.close()

    def _validate_query_embedding(
        self,
        query_embedding: QueryEmbedding,
    ) -> None:
        """질의 벡터가 현재 Qdrant Collection 계약과 일치하는지 검증한다."""

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
    """Qdrant ScoredPoint를 검증된 ChunkSearchHit으로 변환한다."""

    raw_payload = point.payload

    if not isinstance(
        raw_payload,
        Mapping,
    ):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")

    payload = cast(
        Mapping[str, object],
        raw_payload,
    )

    users_idx = _required_int(
        payload,
        _USERS_IDX_PAYLOAD_KEY,
        minimum=1,
    )

    file_idx = _required_int(
        payload,
        _FILE_IDX_PAYLOAD_KEY,
        minimum=1,
    )

    is_active = _required_bool(
        payload,
        _IS_ACTIVE_PAYLOAD_KEY,
    )

    chunk_id = _required_text(
        payload,
        "chunk_id",
    )

    embedding_model = _required_text(
        payload,
        "embedding_model",
    )

    # Qdrant Filter가 적용되었더라도 검색 결과를 외부 계층으로 넘기기 전에
    # 사용자와 활성 상태를 다시 확인하여 잘못된 payload나 클라이언트 대역이
    # 다른 사용자의 청크 또는 비활성 청크를 반환하는 상황을 방어한다.
    if users_idx != expected_user_idx or not is_active:
        raise InvalidVectorSearchResultError("search_scope_contract_violation")

    # Qdrant MatchAny 필터가 적용되었더라도 file_idx를 다시 검증한다.
    #
    # 이를 통해 잘못된 payload, Qdrant 클라이언트 대역 또는 향후 저장소 구현
    # 변경이 선택하지 않은 문서의 청크를 반환하는 상황을 차단한다.
    if file_idx not in expected_reference_file_idxs:
        raise InvalidVectorSearchResultError("search_reference_file_scope_contract_violation")

    if embedding_model != expected_embedding_model:
        raise InvalidVectorSearchResultError("search_embedding_model_mismatch")

    # 색인 계층은 Chunk_ID를 Qdrant Point ID로 그대로 사용한다.
    # payload와 Point ID가 다르면 Local RAG DB와 VectorDB 연결 계약이 깨진 상태다.
    if str(point.id) != chunk_id:
        raise InvalidVectorSearchResultError("search_chunk_id_mismatch")

    try:
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
            folder_idx=_optional_int(
                payload,
                "folder_idx",
                minimum=1,
            ),
            file_name=_required_text(
                payload,
                "file_name",
            ),
            file_type=_required_text(
                payload,
                "file_type",
            ),
            chunk_index=_required_int(
                payload,
                "chunk_index",
                minimum=0,
            ),
            content=_required_text(
                payload,
                "content",
                preserve_original=True,
            ),
            token_count=_optional_int(
                payload,
                "token_count",
                minimum=0,
            ),
            page=_optional_int(
                payload,
                "page",
                minimum=1,
            ),
            slide_no=_optional_int(
                payload,
                "slide_no",
                minimum=1,
            ),
            sheet_name=_optional_text(
                payload,
                "sheet_name",
            ),
            section_title=_optional_text(
                payload,
                "section_title",
            ),
            parser_version=_required_text(
                payload,
                "parser_version",
            ),
            embedding_model=embedding_model,
            index_version=_required_int(
                payload,
                "index_version",
                minimum=1,
            ),
        )

    except (
        TypeError,
        ValueError,
    ) as error:
        # 잘못된 payload 값이나 점수는 외부 응답에 그대로 노출하지 않는다.
        raise InvalidVectorSearchResultError("invalid_search_result_value") from error


def _required_int(
    payload: Mapping[str, object],
    key: str,
    *,
    minimum: int,
) -> int:
    """payload에서 지정 최솟값 이상의 필수 정수를 읽는다."""

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
    """payload에서 null 또는 지정 최솟값 이상의 정수를 읽는다."""

    value = payload.get(key)

    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidVectorSearchResultError("invalid_search_result_payload")

    return value


def _required_bool(
    payload: Mapping[str, object],
    key: str,
) -> bool:
    """payload에서 필수 bool 값을 읽는다."""

    value = payload.get(key)

    if not isinstance(
        value,
        bool,
    ):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")

    return value


def _required_text(
    payload: Mapping[str, object],
    key: str,
    *,
    preserve_original: bool = False,
) -> str:
    """payload에서 비어 있지 않은 필수 문자열을 읽는다."""

    value = payload.get(key)

    if not isinstance(
        value,
        str,
    ):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")

    normalized_value = value.strip()

    if not normalized_value:
        raise InvalidVectorSearchResultError("invalid_search_result_payload")

    if preserve_original:
        return value

    return normalized_value


def _optional_text(
    payload: Mapping[str, object],
    key: str,
) -> str | None:
    """payload에서 null 또는 비어 있지 않은 문자열을 읽는다."""

    value = payload.get(key)

    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        raise InvalidVectorSearchResultError("invalid_search_result_payload")

    normalized_value = value.strip()

    if not normalized_value:
        raise InvalidVectorSearchResultError("invalid_search_result_payload")

    return normalized_value


def _validate_positive_integer(
    value: int,
    *,
    field_name: str,
) -> None:
    """bool을 제외한 양의 정수인지 검증한다."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _validate_non_negative_integer(
    value: int,
    *,
    field_name: str,
) -> None:
    """bool을 제외한 0 이상의 정수인지 검증한다."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")


def _validate_optional_integer(
    value: int | None,
    *,
    field_name: str,
    minimum: int,
) -> None:
    """선택적 정수가 null이거나 지정 최솟값 이상인지 검증한다."""

    if value is None:
        return

    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"{field_name} must be an integer greater than or equal to {minimum}, or null."
        )


def _validate_required_text_value(
    value: str,
    *,
    field_name: str,
) -> None:
    """필수 문자열에 공백 이외의 문자가 존재하는지 검증한다."""

    if not value.strip():
        raise ValueError(f"{field_name} must not be empty.")


def _validate_optional_text_value(
    value: str | None,
    *,
    field_name: str,
) -> None:
    """선택적 문자열이 null이거나 비어 있지 않은 문자열인지 검증한다."""

    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must not be empty when provided.")


def _validate_reference_file_idxs(
    reference_file_idxs: tuple[int, ...],
) -> None:
    """참조문서 범위가 비어 있지 않은 고유한 양의 정수 tuple인지 검증한다.

    요청 스키마가 동일한 계약을 먼저 검증하지만 Repository를 직접 호출하는
    내부 코드와 테스트 대역도 빈 범위를 전체 문서 검색으로 해석하지 못하도록
    인프라 경계에서 다시 검증한다.
    """

    if not isinstance(reference_file_idxs, tuple) or not reference_file_idxs:
        raise ValueError("reference_file_idxs must be a non-empty tuple.")

    seen_file_idxs: set[int] = set()

    for file_idx in reference_file_idxs:
        _validate_positive_integer(
            file_idx,
            field_name="reference_file_idx",
        )

        if file_idx in seen_file_idxs:
            raise ValueError("reference_file_idxs must contain unique values.")

        seen_file_idxs.add(file_idx)


def _validate_search_limit(
    limit: int,
) -> None:
    """Qdrant 검색 결과 수 제한이 허용 범위인지 검증한다."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_SEARCH_LIMIT}.")


def _validate_score_threshold(
    score_threshold: float | None,
) -> None:
    """선택적 Cosine 점수 임계값이 유한한 -1부터 1 사이 값인지 검증한다."""

    if score_threshold is None:
        return

    if isinstance(score_threshold, bool) or not isinstance(
        score_threshold,
        (
            int,
            float,
        ),
    ):
        raise ValueError("score_threshold must be numeric or null.")

    normalized_threshold = float(score_threshold)

    if not math.isfinite(normalized_threshold):
        raise ValueError("score_threshold must be finite.")

    if not -1.0 <= normalized_threshold <= 1.0:
        raise ValueError("score_threshold must be between -1.0 and 1.0.")


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
