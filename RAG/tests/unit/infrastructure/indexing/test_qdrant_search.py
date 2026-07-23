"""Qdrant 관련 청크 검색 저장소의 필터, 제한과 예외 변환을 테스트한다."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import pytest
from httpx import Headers
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.embedding.query import QueryEmbedding
from jipsa_rag.infrastructure.indexing.exceptions import (
    InvalidVectorSearchResultError,
    VectorDatabaseRejectedError,
)
from jipsa_rag.infrastructure.indexing.qdrant_search import (
    QdrantChunkSearchRepository,
)

TEST_COLLECTION = "test_rag_chunk_search"
TEST_EMBEDDING_MODEL = "test/embedding-model"
TEST_EMBEDDING_DIM = 3
TEST_USER_IDX = 45
TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"


@dataclass(slots=True)
class FakeQueryResponse:
    """query_points 응답에서 사용하는 points 필드만 제공한다."""

    points: list[models.ScoredPoint]


class FakeAsyncQdrantClient:
    """실제 Qdrant 없이 검색 요청과 선택적 오류를 기록하는 테스트 대역."""

    def __init__(self) -> None:
        """호출 기록, 응답과 오류 상태를 초기화한다."""

        self.query_points_calls: list[dict[str, object]] = []
        self.query_points_result = FakeQueryResponse(points=[])
        self.query_points_error: Exception | None = None
        self.close_called = False

    async def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        query_filter: models.Filter,
        limit: int,
        score_threshold: float | None,
        with_payload: bool,
        with_vectors: bool,
    ) -> FakeQueryResponse:
        """검색 호출을 기록하고 준비된 결과 또는 예외를 반환한다."""

        if self.query_points_error is not None:
            raise self.query_points_error

        self.query_points_calls.append(
            {
                "collection_name": collection_name,
                "query": query,
                "query_filter": query_filter,
                "limit": limit,
                "score_threshold": score_threshold,
                "with_payload": with_payload,
                "with_vectors": with_vectors,
            }
        )

        return self.query_points_result

    async def close(self) -> None:
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


def _create_query_embedding() -> QueryEmbedding:
    """검색 저장소 테스트용 질의 벡터를 생성한다."""

    return QueryEmbedding(
        embedding_model=TEST_EMBEDDING_MODEL,
        embedding_dim=TEST_EMBEDDING_DIM,
        vector=(
            1.0,
            0.0,
            0.0,
        ),
    )


def _create_scored_point(
    *,
    users_idx: int = TEST_USER_IDX,
    is_active: bool = True,
    score: float = 0.91,
) -> models.ScoredPoint:
    """검색 계약을 만족하는 Qdrant ScoredPoint를 생성한다."""

    return models.ScoredPoint(
        id=TEST_CHUNK_ID,
        version=1,
        score=score,
        payload={
            "chunk_id": TEST_CHUNK_ID,
            "rag_document_idx": 100,
            "file_idx": 123,
            "users_idx": users_idx,
            "folder_idx": 9,
            "chunk_index": 0,
            "content": ("프로젝트 배포 절차는 로컬 RAG 실행 후 진행합니다."),
            "token_count": 64,
            "file_name": "프로젝트 가이드.pdf",
            "file_type": "PDF",
            "page": 2,
            "slide_no": None,
            "sheet_name": None,
            "section_title": "배포 절차",
            "parser_version": "1.0.0",
            "embedding_model": TEST_EMBEDDING_MODEL,
            "index_version": 2,
            "is_active": is_active,
        },
        vector=None,
    )


def _extract_filter_conditions(
    query_filter: models.Filter,
) -> dict[str, models.FieldCondition]:
    """사용자·활성 상태 must 조건을 필드명 기준으로 반환한다."""

    must_conditions = query_filter.must

    assert isinstance(
        must_conditions,
        Sequence,
    )

    extracted: dict[str, models.FieldCondition] = {}

    for condition in must_conditions:
        assert isinstance(
            condition,
            models.FieldCondition,
        )
        extracted[condition.key] = condition

    return extracted


@pytest.mark.asyncio
async def test_search_applies_user_active_filter_top_k_and_threshold() -> None:
    """사용자·활성 필터와 검색 제한을 Qdrant 요청에 적용해야 한다."""

    fake_client = FakeAsyncQdrantClient()
    fake_client.query_points_result = FakeQueryResponse(
        points=[
            _create_scored_point(),
        ]
    )

    repository = QdrantChunkSearchRepository(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    result = await repository.search(
        user_idx=TEST_USER_IDX,
        query_embedding=_create_query_embedding(),
        limit=3,
        score_threshold=0.7,
    )

    assert len(fake_client.query_points_calls) == 1

    call = fake_client.query_points_calls[0]
    assert call["collection_name"] == TEST_COLLECTION
    assert call["query"] == [
        1.0,
        0.0,
        0.0,
    ]
    assert call["limit"] == 3
    assert call["score_threshold"] == 0.7
    assert call["with_payload"] is True
    assert call["with_vectors"] is False

    query_filter = cast(
        models.Filter,
        call["query_filter"],
    )
    conditions = _extract_filter_conditions(query_filter)

    user_condition = conditions["users_idx"]
    assert isinstance(
        user_condition.match,
        models.MatchValue,
    )
    assert user_condition.match.value == TEST_USER_IDX

    active_condition = conditions["is_active"]
    assert isinstance(
        active_condition.match,
        models.MatchValue,
    )
    assert active_condition.match.value is True

    assert len(result) == 1
    assert result[0].chunk_id == TEST_CHUNK_ID
    assert result[0].users_idx == TEST_USER_IDX
    assert result[0].score == 0.91
    assert result[0].content == ("프로젝트 배포 절차는 로컬 RAG 실행 후 진행합니다.")


@pytest.mark.asyncio
async def test_search_rejects_cross_user_payload_even_after_filtering() -> None:
    """Qdrant 응답 payload가 다른 사용자면 이중 검증에서 거부해야 한다."""

    fake_client = FakeAsyncQdrantClient()
    fake_client.query_points_result = FakeQueryResponse(
        points=[
            _create_scored_point(
                users_idx=999,
            ),
        ]
    )

    repository = QdrantChunkSearchRepository(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    with pytest.raises(
        InvalidVectorSearchResultError,
    ) as exception_info:
        await repository.search(
            user_idx=TEST_USER_IDX,
            query_embedding=_create_query_embedding(),
            limit=3,
        )

    assert exception_info.value.operation == "search_scope_contract_violation"


@pytest.mark.asyncio
async def test_search_maps_qdrant_400_to_rejected_error() -> None:
    """Qdrant의 영구적인 4xx 응답을 검색 거부 오류로 변환해야 한다."""

    fake_client = FakeAsyncQdrantClient()
    fake_client.query_points_error = UnexpectedResponse(
        status_code=400,
        reason_phrase="Bad Request",
        content=b"{}",
        headers=Headers(),
    )

    repository = QdrantChunkSearchRepository(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    with pytest.raises(
        VectorDatabaseRejectedError,
    ) as exception_info:
        await repository.search(
            user_idx=TEST_USER_IDX,
            query_embedding=_create_query_embedding(),
            limit=3,
        )

    assert exception_info.value.operation == "search_chunks"
    assert exception_info.value.status_code == 400


@pytest.mark.asyncio
async def test_search_rejects_limit_above_repository_maximum() -> None:
    """API 계층을 우회한 과도한 검색 제한도 저장소에서 거부해야 한다."""

    fake_client = FakeAsyncQdrantClient()
    repository = QdrantChunkSearchRepository(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    with pytest.raises(
        ValueError,
        match="limit must be between 1 and 20",
    ):
        await repository.search(
            user_idx=TEST_USER_IDX,
            query_embedding=_create_query_embedding(),
            limit=21,
        )

    assert fake_client.query_points_calls == []


@pytest.mark.asyncio
async def test_close_does_not_close_injected_qdrant_client() -> None:
    """외부에서 주입한 클라이언트의 소유권을 가져가지 않아야 한다."""

    fake_client = FakeAsyncQdrantClient()
    repository = QdrantChunkSearchRepository(
        _create_settings(),
        client=cast(
            AsyncQdrantClient,
            fake_client,
        ),
    )

    await repository.close()

    assert fake_client.close_called is False
