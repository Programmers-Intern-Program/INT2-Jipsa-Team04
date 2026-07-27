"""사용자 질의를 임베딩하고 선택 문서 범위에서 일반·OCR 청크를 검색한다."""

from typing import Protocol

from jipsa_rag.infrastructure.embedding.query import QueryEmbedding
from jipsa_rag.infrastructure.indexing.exceptions import (
    InvalidVectorSearchResultError,
)
from jipsa_rag.infrastructure.indexing.qdrant_search import ChunkSearchHit
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.source_locator import build_source_locator


class QueryEmbedder(Protocol):
    """검색 서비스가 필요로 하는 질의 임베딩 생성 계약."""

    async def embed(self, *, query: str) -> QueryEmbedding:
        """사용자 질의를 검색용 임베딩으로 변환한다."""
        ...


class ChunkSearchRepository(Protocol):
    """사용자와 선택 문서 범위를 강제하는 청크 저장소 계약."""

    async def search(
        self,
        *,
        user_idx: int,
        reference_file_idxs: tuple[int, ...],
        query_embedding: QueryEmbedding,
        limit: int,
        score_threshold: float | None = None,
    ) -> tuple[ChunkSearchHit, ...]:
        """활성 일반 텍스트와 OCR 청크를 같은 관련도 검색으로 반환한다."""
        ...


class ChunkSearchService:
    """질의 임베딩 생성과 Qdrant 범위 검색을 조정한다."""

    def __init__(
        self,
        *,
        query_embedder: QueryEmbedder,
        repository: ChunkSearchRepository,
    ) -> None:
        self._query_embedder = query_embedder
        self._repository = repository

    async def search(self, request: ChunkSearchRequest) -> ChunkSearchResponse:
        """전송 시점 선택 문서 범위에서 검증된 혼합 형식 청크를 반환한다."""

        request_snapshot = request.model_copy(deep=True)
        user_idx = request_snapshot.user_idx
        reference_file_idxs = tuple(request_snapshot.reference_file_idxs)
        reference_file_idx_set = frozenset(reference_file_idxs)

        query_embedding = await self._query_embedder.embed(
            query=request_snapshot.query,
        )
        hits = await self._repository.search(
            user_idx=user_idx,
            reference_file_idxs=reference_file_idxs,
            query_embedding=query_embedding,
            limit=request_snapshot.top_k,
            score_threshold=request_snapshot.score_threshold,
        )

        _validate_search_hits(
            hits=hits,
            user_idx=user_idx,
            reference_file_idxs=reference_file_idx_set,
            top_k=request_snapshot.top_k,
            score_threshold=request_snapshot.score_threshold,
        )

        try:
            results = tuple(_to_chunk_search_result(hit) for hit in hits)
            return ChunkSearchResponse(
                user_idx=user_idx,
                result_count=len(results),
                results=results,
            )
        except ValueError:
            # Pydantic 오류에 청크 원문이나 OCR 결과가 들어갈 수 있으므로
            # 예외 체인을 외부에 전달하지 않는다.
            raise InvalidVectorSearchResultError(
                "invalid_search_response_schema"
            ) from None


def _validate_search_hits(
    *,
    hits: tuple[ChunkSearchHit, ...],
    user_idx: int,
    reference_file_idxs: frozenset[int],
    top_k: int,
    score_threshold: float | None,
) -> None:
    """Repository 결과가 사용자·문서·점수·정렬 계약을 지키는지 재검증한다."""

    if len(hits) > top_k:
        raise InvalidVectorSearchResultError("search_result_limit_contract_violation")

    seen_chunk_ids: set[str] = set()
    previous_score: float | None = None

    for hit in hits:
        if hit.users_idx != user_idx:
            raise InvalidVectorSearchResultError("search_user_scope_contract_violation")
        if hit.file_idx not in reference_file_idxs:
            raise InvalidVectorSearchResultError(
                "search_reference_file_scope_contract_violation"
            )
        if score_threshold is not None and hit.score < score_threshold:
            raise InvalidVectorSearchResultError(
                "search_score_threshold_contract_violation"
            )
        if previous_score is not None and hit.score > previous_score:
            raise InvalidVectorSearchResultError("search_score_order_contract_violation")
        if hit.chunk_id in seen_chunk_ids:
            raise InvalidVectorSearchResultError("duplicate_search_chunk_id")

        seen_chunk_ids.add(hit.chunk_id)
        previous_score = hit.score


def _to_chunk_search_result(hit: ChunkSearchHit) -> ChunkSearchResult:
    """저장소 hit을 공통 Source Locator가 포함된 검색 응답으로 변환한다."""

    file_type = SupportedFileType(hit.file_type.strip().lower())
    locator = build_source_locator(
        file_type=file_type,
        source_metadata=hit.source_metadata,
        legacy_page=hit.page,
        legacy_slide_no=hit.slide_no,
        legacy_sheet_name=hit.sheet_name,
        legacy_section_title=hit.section_title,
    )

    return ChunkSearchResult(
        chunk_id=hit.chunk_id,
        score=hit.score,
        rag_document_idx=hit.rag_document_idx,
        file_idx=hit.file_idx,
        folder_idx=hit.folder_idx,
        file_name=hit.file_name,
        file_type=file_type,
        chunk_index=hit.chunk_index,
        content=hit.content,
        token_count=hit.token_count,
        page=hit.page,
        slide_no=hit.slide_no,
        sheet_name=hit.sheet_name,
        section_title=hit.section_title,
        source_locator=locator,
        parser_version=hit.parser_version,
        embedding_model=hit.embedding_model,
        index_version=hit.index_version,
    )
