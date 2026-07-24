"""실제 Qdrant 로컬 모드와 요청별 참조문서 청크 검색을 통합 테스트한다."""

from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.embedding.query import QueryEmbedding
from jipsa_rag.infrastructure.indexing.qdrant_search import (
    QdrantChunkSearchRepository,
)
from jipsa_rag.schemas.chunk_search import ChunkSearchRequest
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.services.chunk_search import ChunkSearchService

TEST_USER_IDX = 45
TEST_OTHER_USER_IDX = 999
TEST_REFERENCE_FILE_IDXS = (
    123,
    456,
)
TEST_EMBEDDING_MODEL = "test/embedding-model"
TEST_EMBEDDING_DIM = 3


class StaticQueryEmbedder:
    """통합 테스트에서 TEI 대신 결정적인 질의 벡터를 반환한다.

    이 테스트의 범위는 서비스와 실제 Qdrant 검색 필터·점수 제한의
    연결이다. TEI HTTP 계약은 별도의 질의 임베딩 단위 테스트에서
    검증한다.
    """

    async def embed(
        self,
        *,
        query: str,
    ) -> QueryEmbedding:
        """비어 있지 않은 질의에 대해 고정된 3차원 벡터를 반환한다."""

        if not query.strip():
            raise ValueError(
                "query must not be empty."
            )

        return QueryEmbedding(
            embedding_model=TEST_EMBEDDING_MODEL,
            embedding_dim=TEST_EMBEDDING_DIM,
            vector=(
                1.0,
                0.0,
                0.0,
            ),
        )


def _create_settings(
    *,
    collection_name: str,
) -> Settings:
    """Qdrant 로컬 모드용 임베딩과 Collection 설정을 생성한다."""

    return get_settings().model_copy(
        update={
            "embedding_model": TEST_EMBEDDING_MODEL,
            "embedding_dim": TEST_EMBEDDING_DIM,
            "qdrant_collection": collection_name,
            "qdrant_prefer_grpc": False,
        }
    )


def _create_point(
    *,
    chunk_id: str,
    users_idx: int,
    file_idx: int,
    vector: list[float],
    score_order: int,
    is_active: bool,
) -> models.PointStruct:
    """실제 검색 저장소 계약을 만족하는 Qdrant Point를 생성한다."""

    return models.PointStruct(
        id=chunk_id,
        vector=vector,
        payload={
            "chunk_id": chunk_id,
            "rag_document_idx": 100 + score_order,
            "file_idx": file_idx,
            "users_idx": users_idx,
            "folder_idx": 9,
            "chunk_index": score_order,
            "content": f"통합 테스트 청크 {score_order}",
            "token_count": 10,
            "file_name": f"프로젝트 가이드-{file_idx}.pdf",
            "file_type": "PDF",
            "page": score_order + 1,
            "slide_no": None,
            "sheet_name": None,
            "section_title": "배포 절차",
            "parser_version": "1.0.0",
            "embedding_model": TEST_EMBEDDING_MODEL,
            "index_version": 2,
            "is_active": is_active,
        },
    )


@pytest.mark.asyncio
async def test_search_integrates_all_search_constraints() -> None:
    """실제 Qdrant에서 모든 검색 조건이 동시에 적용되어야 한다.

    검증 조건:
    - user_idx가 요청 사용자와 동일할 것
    - is_active가 true일 것
    - file_idx가 요청 reference_file_idxs에 포함될 것
    - Cosine 점수가 score_threshold 이상일 것
    - 최종 반환 개수가 top_k 이하일 것
    """

    collection_name = (
        f"test_chunk_search_{uuid4().hex}"
    )
    client = AsyncQdrantClient(
        location=":memory:"
    )
    settings = _create_settings(
        collection_name=collection_name,
    )

    exact_chunk_id = (
        "11111111-1111-1111-1111-111111111111"
    )
    near_chunk_id = (
        "22222222-2222-2222-2222-222222222222"
    )
    below_threshold_chunk_id = (
        "33333333-3333-3333-3333-333333333333"
    )
    inactive_chunk_id = (
        "44444444-4444-4444-4444-444444444444"
    )
    other_user_chunk_id = (
        "55555555-5555-5555-5555-555555555555"
    )
    unselected_file_chunk_id = (
        "66666666-6666-6666-6666-666666666666"
    )

    try:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=TEST_EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )

        await client.upsert(
            collection_name=collection_name,
            points=[
                # 사용자·활성·참조문서·임계값 조건을 모두 만족하는
                # 첫 번째 선택 문서의 정확 일치 청크다.
                _create_point(
                    chunk_id=exact_chunk_id,
                    users_idx=TEST_USER_IDX,
                    file_idx=123,
                    vector=[
                        1.0,
                        0.0,
                        0.0,
                    ],
                    score_order=0,
                    is_active=True,
                ),
                # 두 번째 선택 문서에 속하며 Cosine 점수 0.8로
                # 임계값 0.75를 통과한다.
                _create_point(
                    chunk_id=near_chunk_id,
                    users_idx=TEST_USER_IDX,
                    file_idx=456,
                    vector=[
                        0.8,
                        0.6,
                        0.0,
                    ],
                    score_order=1,
                    is_active=True,
                ),
                # 선택 문서에 속하지만 Cosine 점수 0.6으로
                # 최소 점수 조건에서 제외된다.
                _create_point(
                    chunk_id=below_threshold_chunk_id,
                    users_idx=TEST_USER_IDX,
                    file_idx=123,
                    vector=[
                        0.6,
                        0.8,
                        0.0,
                    ],
                    score_order=2,
                    is_active=True,
                ),
                # 관련도는 높고 선택 문서에 속하지만 비활성 청크이므로 제외된다.
                _create_point(
                    chunk_id=inactive_chunk_id,
                    users_idx=TEST_USER_IDX,
                    file_idx=123,
                    vector=[
                        1.0,
                        0.0,
                        0.0,
                    ],
                    score_order=3,
                    is_active=False,
                ),
                # 관련도는 높고 선택된 file_idx를 사용하지만
                # 다른 사용자 청크이므로 제외된다.
                _create_point(
                    chunk_id=other_user_chunk_id,
                    users_idx=TEST_OTHER_USER_IDX,
                    file_idx=123,
                    vector=[
                        1.0,
                        0.0,
                        0.0,
                    ],
                    score_order=4,
                    is_active=True,
                ),
                # 사용자와 활성 상태 및 관련도는 모두 만족하지만
                # 선택하지 않은 문서의 청크이므로 제외되어야 한다.
                _create_point(
                    chunk_id=unselected_file_chunk_id,
                    users_idx=TEST_USER_IDX,
                    file_idx=999,
                    vector=[
                        1.0,
                        0.0,
                        0.0,
                    ],
                    score_order=5,
                    is_active=True,
                ),
            ],
            wait=True,
        )

        repository = QdrantChunkSearchRepository(
            settings,
            # 외부에서 주입한 클라이언트는 Repository가 종료하지 않는다.
            client=client,
        )
        service = ChunkSearchService(
            query_embedder=StaticQueryEmbedder(),
            repository=repository,
        )

        result = await service.search(
            ChunkSearchRequest(
                user_idx=TEST_USER_IDX,
                reference_file_idxs=(
                    TEST_REFERENCE_FILE_IDXS
                ),
                query="프로젝트 배포 절차",
                top_k=2,
                score_threshold=0.75,
            )
        )

        assert result.user_idx == TEST_USER_IDX
        assert result.result_count == 2

        assert tuple(
            chunk.chunk_id
            for chunk in result.results
        ) == (
            exact_chunk_id,
            near_chunk_id,
        )

        assert {
            chunk.file_idx
            for chunk in result.results
        } == {
            123,
            456,
        }

        assert all(
            chunk.score >= 0.75
            for chunk in result.results
        )

        assert (
            result.results[0].score
            >= result.results[1].score
        )

        assert all(
            chunk.file_type
            is SupportedFileType.PDF
            for chunk in result.results
        )

        returned_chunk_ids = {
            chunk.chunk_id
            for chunk in result.results
        }

        assert (
            below_threshold_chunk_id
            not in returned_chunk_ids
        )
        assert (
            inactive_chunk_id
            not in returned_chunk_ids
        )
        assert (
            other_user_chunk_id
            not in returned_chunk_ids
        )
        assert (
            unselected_file_chunk_id
            not in returned_chunk_ids
        )

    finally:
        await client.close()