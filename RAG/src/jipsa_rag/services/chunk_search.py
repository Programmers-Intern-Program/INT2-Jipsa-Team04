"""사용자 질의를 임베딩하고 요청별 참조문서 범위에서 관련 청크를 검색한다."""

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


class QueryEmbedder(Protocol):
    """검색 서비스가 필요로 하는 질의 임베딩 생성 계약."""

    async def embed(
        self,
        *,
        query: str,
    ) -> QueryEmbedding:
        """사용자 질의를 검색용 임베딩으로 변환한다."""

        ...


class ChunkSearchRepository(Protocol):
    """검색 서비스가 필요로 하는 요청별 참조문서 청크 검색 계약."""

    async def search(
        self,
        *,
        user_idx: int,
        reference_file_idxs: tuple[int, ...],
        query_embedding: QueryEmbedding,
        limit: int,
        score_threshold: float | None = None,
    ) -> tuple[ChunkSearchHit, ...]:
        """사용자, 활성 상태, 참조문서 및 최소 점수를 적용하여 청크를 조회한다."""

        ...


class ChunkSearchService:
    """질의 임베딩 생성과 Qdrant 검색을 하나의 유스케이스로 조정한다."""

    def __init__(
        self,
        *,
        query_embedder: QueryEmbedder,
        repository: ChunkSearchRepository,
    ) -> None:
        """외부 의존성을 Protocol 계약으로 주입받는다."""

        self._query_embedder = query_embedder
        self._repository = repository

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """전송 시점의 참조문서 범위에서 검증된 관련 청크를 반환한다."""

        # 각 검색 호출이 전달받은 요청을 독립적인 스냅샷으로 사용한다.
        #
        # Pydantic 요청 객체는 현재 필드가 재할당 가능한 모델이므로,
        # 임베딩 생성 중 다른 내부 코드가 같은 객체를 변경하더라도 현재 질문의
        # 사용자, 질의 및 참조문서 범위가 바뀌지 않도록 첫 await 전에 복사한다.
        #
        # FastAPI에서는 요청마다 별도의 모델이 생성되지만, 서비스 계층에서도
        # 같은 계약을 강제하여 직접 호출과 테스트 대역까지 안전하게 처리한다.
        request_snapshot = request.model_copy(
            deep=True,
        )

        user_idx = request_snapshot.user_idx
        reference_file_idxs = tuple(
            request_snapshot.reference_file_idxs
        )
        reference_file_idx_set = frozenset(
            reference_file_idxs
        )
        query = request_snapshot.query
        top_k = request_snapshot.top_k
        score_threshold = request_snapshot.score_threshold

        query_embedding = await self._query_embedder.embed(
            query=query,
        )

        hits = await self._repository.search(
            user_idx=user_idx,
            reference_file_idxs=reference_file_idxs,
            query_embedding=query_embedding,
            limit=top_k,
            score_threshold=score_threshold,
        )

        # Qdrant Repository가 이미 동일 계약을 강제하지만 서비스 경계에서도
        # 결과 개수, 사용자 범위, 참조문서 범위, 점수 임계값과 정렬 순서를
        # 다시 확인한다.
        #
        # 이 방어 검증은 향후 Repository 구현체가 교체되거나 테스트 대역이
        # 잘못된 값을 반환하더라도 선택하지 않은 문서 또는 다른 사용자의
        # 청크가 API 응답으로 전달되는 상황을 차단한다.
        _validate_search_hits(
            hits=hits,
            user_idx=user_idx,
            reference_file_idxs=reference_file_idx_set,
            top_k=top_k,
            score_threshold=score_threshold,
        )

        try:
            results = tuple(
                _to_chunk_search_result(hit)
                for hit in hits
            )

            return ChunkSearchResponse(
                user_idx=user_idx,
                result_count=len(results),
                results=results,
            )

        except ValueError:
            # Pydantic ValidationError도 ValueError를 상속하며 입력값을
            # 오류 상세에 포함할 수 있다.
            #
            # 검색 청크 원문이 예외 체인이나 전역 로그에 노출되지 않도록
            # 원인 예외를 연결하지 않고 안전한 저장소 계약 오류로 변환한다.
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
    """Repository 결과가 현재 질문의 검색 범위 계약을 위반하지 않는지 검증한다."""

    if len(hits) > top_k:
        raise InvalidVectorSearchResultError(
            "search_result_limit_contract_violation"
        )

    seen_chunk_ids: set[str] = set()
    previous_score: float | None = None

    for hit in hits:
        if hit.users_idx != user_idx:
            raise InvalidVectorSearchResultError(
                "search_user_scope_contract_violation"
            )

        if hit.file_idx not in reference_file_idxs:
            raise InvalidVectorSearchResultError(
                "search_reference_file_scope_contract_violation"
            )

        if (
            score_threshold is not None
            and hit.score < score_threshold
        ):
            raise InvalidVectorSearchResultError(
                "search_score_threshold_contract_violation"
            )

        # Qdrant는 관련도 점수 내림차순으로 결과를 반환한다.
        # 앞선 점수보다 더 높은 점수가 뒤에서 나타나면 정렬 계약 위반이다.
        if (
            previous_score is not None
            and hit.score > previous_score
        ):
            raise InvalidVectorSearchResultError(
                "search_score_order_contract_violation"
            )

        if hit.chunk_id in seen_chunk_ids:
            raise InvalidVectorSearchResultError(
                "duplicate_search_chunk_id"
            )

        seen_chunk_ids.add(
            hit.chunk_id
        )
        previous_score = hit.score


def _to_chunk_search_result(
    hit: ChunkSearchHit,
) -> ChunkSearchResult:
    """내부 검색 결과를 외부 공개용 청크 응답으로 변환한다."""

    # Qdrant payload는 DocumentType.value를 저장하므로 현재 값은 "PDF"처럼
    # 대문자일 수 있다. 외부 API 계약은 "pdf" 확장자 형식을 사용하므로
    # 명시적으로 소문자 정규화 후 SupportedFileType으로 검증한다.
    file_type = SupportedFileType(
        hit.file_type.strip().lower(),
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
        parser_version=hit.parser_version,
        embedding_model=hit.embedding_model,
        index_version=hit.index_version,
    )