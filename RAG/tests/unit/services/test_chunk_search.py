"""청크 검색 서비스의 유스케이스 조정과 방어 검증을 테스트한다."""

import pytest

from jipsa_rag.infrastructure.embedding.query import QueryEmbedding
from jipsa_rag.infrastructure.indexing.exceptions import (
    InvalidVectorSearchResultError,
)
from jipsa_rag.infrastructure.indexing.qdrant_search import ChunkSearchHit
from jipsa_rag.schemas.chunk_search import ChunkSearchRequest
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.services.chunk_search import ChunkSearchService

TEST_USER_IDX = 45
TEST_EMBEDDING_MODEL = "test/embedding-model"
TEST_EMBEDDING_DIM = 3


class StubQueryEmbedder:
    """입력 질의를 기록하고 고정 벡터를 반환하는 테스트 대역."""

    def __init__(self) -> None:
        """호출 기록을 초기화한다."""

        self.received_queries: list[str] = []

    async def embed(
        self,
        *,
        query: str,
    ) -> QueryEmbedding:
        """질의를 기록한 뒤 결정적인 3차원 벡터를 반환한다."""

        self.received_queries.append(query)

        return QueryEmbedding(
            embedding_model=TEST_EMBEDDING_MODEL,
            embedding_dim=TEST_EMBEDDING_DIM,
            vector=(
                1.0,
                0.0,
                0.0,
            ),
        )


class StubChunkSearchRepository:
    """검색 인수를 기록하고 준비된 청크 목록을 반환하는 테스트 대역."""

    def __init__(
        self,
        hits: tuple[ChunkSearchHit, ...],
    ) -> None:
        """반환할 검색 결과와 호출 기록을 초기화한다."""

        self._hits = hits
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        user_idx: int,
        query_embedding: QueryEmbedding,
        limit: int,
        score_threshold: float | None = None,
    ) -> tuple[ChunkSearchHit, ...]:
        """호출 인수를 기록하고 준비된 검색 결과를 반환한다."""

        self.calls.append(
            {
                "user_idx": user_idx,
                "query_embedding": query_embedding,
                "limit": limit,
                "score_threshold": score_threshold,
            }
        )

        return self._hits


def _create_hit(
    *,
    chunk_id: str = "11111111-1111-1111-1111-111111111111",
    users_idx: int = TEST_USER_IDX,
    score: float = 0.92,
    chunk_index: int = 0,
) -> ChunkSearchHit:
    """서비스 테스트에 사용할 유효한 Qdrant 검색 결과를 생성한다."""

    return ChunkSearchHit(
        chunk_id=chunk_id,
        score=score,
        users_idx=users_idx,
        rag_document_idx=100,
        file_idx=123,
        folder_idx=9,
        file_name="프로젝트 가이드.pdf",
        # 실제 Qdrant payload는 DocumentType.value를 저장하므로 대문자다.
        file_type="PDF",
        chunk_index=chunk_index,
        content=("로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다."),
        token_count=128,
        page=2,
        slide_no=None,
        sheet_name=None,
        section_title="로컬 실행 방법",
        parser_version="1.0.0",
        embedding_model=TEST_EMBEDDING_MODEL,
        index_version=2,
    )


@pytest.mark.asyncio
async def test_search_passes_constraints_and_maps_response() -> None:
    """검색 제약을 저장소에 전달하고 외부 응답으로 변환해야 한다."""

    hit = _create_hit()
    query_embedder = StubQueryEmbedder()
    repository = StubChunkSearchRepository((hit,))
    service = ChunkSearchService(
        query_embedder=query_embedder,
        repository=repository,
    )

    request = ChunkSearchRequest(
        user_idx=TEST_USER_IDX,
        query="프로젝트 배포 절차를 알려줘",
        top_k=3,
        score_threshold=0.7,
    )

    result = await service.search(request)

    assert query_embedder.received_queries == [
        "프로젝트 배포 절차를 알려줘",
    ]
    assert len(repository.calls) == 1

    repository_call = repository.calls[0]
    assert repository_call["user_idx"] == TEST_USER_IDX
    assert repository_call["limit"] == 3
    assert repository_call["score_threshold"] == 0.7

    query_embedding = repository_call["query_embedding"]
    assert isinstance(query_embedding, QueryEmbedding)
    assert query_embedding.embedding_model == TEST_EMBEDDING_MODEL
    assert query_embedding.vector == (
        1.0,
        0.0,
        0.0,
    )

    assert result.user_idx == TEST_USER_IDX
    assert result.result_count == 1
    assert result.results[0].chunk_id == hit.chunk_id
    assert result.results[0].score == hit.score
    assert result.results[0].file_type is SupportedFileType.PDF


@pytest.mark.asyncio
async def test_search_rejects_result_from_another_user() -> None:
    """다른 사용자의 청크를 반환하면 응답 생성을 중단해야 한다."""

    repository = StubChunkSearchRepository(
        (
            _create_hit(
                users_idx=999,
            ),
        )
    )
    service = ChunkSearchService(
        query_embedder=StubQueryEmbedder(),
        repository=repository,
    )

    with pytest.raises(
        InvalidVectorSearchResultError,
    ) as exception_info:
        await service.search(
            ChunkSearchRequest(
                user_idx=TEST_USER_IDX,
                query="검색 질의",
            )
        )

    assert exception_info.value.operation == "search_user_scope_contract_violation"


@pytest.mark.asyncio
async def test_search_rejects_result_below_score_threshold() -> None:
    """최소 점수보다 낮은 결과를 계약 오류로 처리해야 한다."""

    repository = StubChunkSearchRepository(
        (
            _create_hit(
                score=0.69,
            ),
        )
    )
    service = ChunkSearchService(
        query_embedder=StubQueryEmbedder(),
        repository=repository,
    )

    with pytest.raises(
        InvalidVectorSearchResultError,
    ) as exception_info:
        await service.search(
            ChunkSearchRequest(
                user_idx=TEST_USER_IDX,
                query="검색 질의",
                score_threshold=0.7,
            )
        )

    assert exception_info.value.operation == "search_score_threshold_contract_violation"


@pytest.mark.asyncio
async def test_search_rejects_results_not_sorted_descending() -> None:
    """점수 내림차순 계약이 깨진 결과를 외부에 반환하지 않아야 한다."""

    repository = StubChunkSearchRepository(
        (
            _create_hit(
                chunk_id="11111111-1111-1111-1111-111111111111",
                score=0.70,
                chunk_index=0,
            ),
            _create_hit(
                chunk_id="22222222-2222-2222-2222-222222222222",
                score=0.80,
                chunk_index=1,
            ),
        )
    )
    service = ChunkSearchService(
        query_embedder=StubQueryEmbedder(),
        repository=repository,
    )

    with pytest.raises(
        InvalidVectorSearchResultError,
    ) as exception_info:
        await service.search(
            ChunkSearchRequest(
                user_idx=TEST_USER_IDX,
                query="검색 질의",
                top_k=2,
            )
        )

    assert exception_info.value.operation == "search_score_order_contract_violation"


@pytest.mark.asyncio
async def test_search_rejects_more_results_than_top_k() -> None:
    """저장소가 top_k보다 많은 결과를 반환하면 거부해야 한다."""

    repository = StubChunkSearchRepository(
        (
            _create_hit(
                chunk_id="11111111-1111-1111-1111-111111111111",
                score=0.90,
                chunk_index=0,
            ),
            _create_hit(
                chunk_id="22222222-2222-2222-2222-222222222222",
                score=0.80,
                chunk_index=1,
            ),
        )
    )
    service = ChunkSearchService(
        query_embedder=StubQueryEmbedder(),
        repository=repository,
    )

    with pytest.raises(
        InvalidVectorSearchResultError,
    ) as exception_info:
        await service.search(
            ChunkSearchRequest(
                user_idx=TEST_USER_IDX,
                query="검색 질의",
                top_k=1,
            )
        )

    assert exception_info.value.operation == "search_result_limit_contract_violation"
