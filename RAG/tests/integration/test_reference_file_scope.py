"""실제 Qdrant 로컬 모드에서 요청별 참조문서 검색 범위를 통합 테스트한다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.embedding.query import QueryEmbedding
from jipsa_rag.infrastructure.indexing.qdrant_search import (
    QdrantChunkSearchRepository,
)
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
)
from jipsa_rag.services.chunk_search import ChunkSearchService

_TEST_USER_IDX = 45
_TEST_EMBEDDING_MODEL = "test/embedding-model"
_TEST_EMBEDDING_DIM = 3


class _StaticQueryEmbedder:
    """TEI 호출 없이 검색 순서를 결정할 고정 질의 벡터를 반환한다."""

    async def embed(
        self,
        *,
        query: str,
    ) -> QueryEmbedding:
        """비어 있지 않은 질문을 항상 같은 3차원 단위 벡터로 변환한다."""

        if not query.strip():
            raise ValueError("query must not be empty.")

        return QueryEmbedding(
            embedding_model=_TEST_EMBEDDING_MODEL,
            embedding_dim=_TEST_EMBEDDING_DIM,
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
    """각 테스트 전용 Qdrant Collection 설정을 생성한다."""

    return get_settings().model_copy(
        update={
            "embedding_model": _TEST_EMBEDDING_MODEL,
            "embedding_dim": _TEST_EMBEDDING_DIM,
            "qdrant_collection": collection_name,
            "qdrant_prefer_grpc": False,
        }
    )


def _create_point(
    *,
    chunk_id: str,
    file_idx: int,
    vector: list[float],
    chunk_index: int,
) -> models.PointStruct:
    """검색 저장소가 검증하는 필수 payload를 가진 활성 청크를 생성한다."""

    return models.PointStruct(
        id=chunk_id,
        vector=vector,
        payload={
            "chunk_id": chunk_id,
            "rag_document_idx": 100 + chunk_index,
            "file_idx": file_idx,
            "users_idx": _TEST_USER_IDX,
            "folder_idx": 9,
            "chunk_index": chunk_index,
            "content": f"참조문서 {file_idx}의 통합 테스트 청크",
            "token_count": 16,
            "file_name": f"참조문서-{file_idx}.pdf",
            "file_type": "PDF",
            "page": chunk_index + 1,
            "slide_no": None,
            "sheet_name": None,
            "section_title": "참조문서 검색 범위",
            "parser_version": "1.0.0",
            "embedding_model": _TEST_EMBEDDING_MODEL,
            "index_version": 2,
            "is_active": True,
        },
    )


@asynccontextmanager
async def _create_search_service(
    *,
    points: list[models.PointStruct],
) -> AsyncIterator[ChunkSearchService]:
    """메모리 Qdrant에 Point를 저장하고 실제 검색 서비스를 제공한다."""

    collection_name = f"test_reference_scope_{uuid4().hex}"
    client = AsyncQdrantClient(location=":memory:")
    settings = _create_settings(
        collection_name=collection_name,
    )

    try:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=_TEST_EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        await client.upsert(
            collection_name=collection_name,
            points=points,
            wait=True,
        )

        repository = QdrantChunkSearchRepository(
            settings,
            # 테스트가 생성한 클라이언트의 생명주기는 이 context manager가 관리한다.
            client=client,
        )

        yield ChunkSearchService(
            query_embedder=_StaticQueryEmbedder(),
            repository=repository,
        )
    finally:
        await client.close()


def _returned_file_idxs(
    response: ChunkSearchResponse,
) -> frozenset[int]:
    """검색 응답에 포함된 File.File_IDX 집합을 반환한다."""

    return frozenset(result.file_idx for result in response.results)


@pytest.mark.asyncio
async def test_unselected_document_is_never_returned() -> None:
    """미선택 문서가 더 높은 유사도를 가져도 검색 결과에 포함하지 않아야 한다."""

    selected_chunk_id = "11111111-1111-1111-1111-111111111111"
    unselected_chunk_id = "22222222-2222-2222-2222-222222222222"

    async with _create_search_service(
        points=[
            # 선택 문서 청크는 질의 벡터와 Cosine 유사도 0.8을 갖는다.
            _create_point(
                chunk_id=selected_chunk_id,
                file_idx=123,
                vector=[
                    0.8,
                    0.6,
                    0.0,
                ],
                chunk_index=0,
            ),
            # 미선택 문서 청크는 Cosine 유사도 1.0으로 더 관련성이 높다.
            #
            # file_idx 필터가 누락되면 이 청크가 첫 번째 결과가 되므로,
            # 선택 문서 범위 강제가 실제 Qdrant 검색에 적용되는지 명확히 검증한다.
            _create_point(
                chunk_id=unselected_chunk_id,
                file_idx=999,
                vector=[
                    1.0,
                    0.0,
                    0.0,
                ],
                chunk_index=1,
            ),
        ]
    ) as service:
        response = await service.search(
            ChunkSearchRequest(
                user_idx=_TEST_USER_IDX,
                reference_file_idxs=(123,),
                query="선택한 문서의 내용을 알려줘",
                top_k=10,
            )
        )

    assert response.result_count == 1
    assert response.results[0].chunk_id == selected_chunk_id
    assert _returned_file_idxs(response) == frozenset({123})

    returned_chunk_ids = {result.chunk_id for result in response.results}

    assert unselected_chunk_id not in returned_chunk_ids


@pytest.mark.asyncio
async def test_added_and_removed_reference_files_apply_per_request() -> None:
    """참조문서 추가와 해제는 각각 다음 검색 요청의 범위에만 적용되어야 한다."""

    async with _create_search_service(
        points=[
            _create_point(
                chunk_id="33333333-3333-3333-3333-333333333333",
                file_idx=123,
                vector=[
                    1.0,
                    0.0,
                    0.0,
                ],
                chunk_index=0,
            ),
            _create_point(
                chunk_id="44444444-4444-4444-4444-444444444444",
                file_idx=456,
                vector=[
                    1.0,
                    0.0,
                    0.0,
                ],
                chunk_index=1,
            ),
        ]
    ) as service:
        initial_response = await service.search(
            ChunkSearchRequest(
                user_idx=_TEST_USER_IDX,
                reference_file_idxs=(123,),
                query="첫 번째 참조문서만 검색해줘",
                top_k=10,
            )
        )
        added_response = await service.search(
            ChunkSearchRequest(
                user_idx=_TEST_USER_IDX,
                reference_file_idxs=(
                    123,
                    456,
                ),
                query="두 참조문서를 함께 검색해줘",
                top_k=10,
            )
        )
        removed_response = await service.search(
            ChunkSearchRequest(
                user_idx=_TEST_USER_IDX,
                reference_file_idxs=(456,),
                query="첫 번째 참조문서를 해제한 뒤 검색해줘",
                top_k=10,
            )
        )

    # 첫 요청은 전송 시점에 선택된 123번 문서만 검색한다.
    assert initial_response.result_count == 1
    assert _returned_file_idxs(initial_response) == frozenset({123})

    # 456번 문서를 추가한 다음 요청부터 두 문서가 모두 검색 범위가 된다.
    assert added_response.result_count == 2
    assert _returned_file_idxs(added_response) == frozenset(
        {
            123,
            456,
        }
    )

    # 123번 문서를 해제한 다음 요청부터 456번 문서만 검색 범위에 남는다.
    assert removed_response.result_count == 1
    assert _returned_file_idxs(removed_response) == frozenset({456})
