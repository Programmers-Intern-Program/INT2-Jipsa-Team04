"""Qdrant 클라이언트의 지연 생성과 소유권 생명주기를 테스트한다."""

from dataclasses import dataclass
from math import ceil
from typing import cast

import pytest
from qdrant_client import AsyncQdrantClient, models

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.embedding.query import QueryEmbedding
from jipsa_rag.infrastructure.indexing import (
    qdrant_search as qdrant_search_module,
)
from jipsa_rag.infrastructure.indexing import (
    qdrant_store as qdrant_store_module,
)
from jipsa_rag.infrastructure.indexing.qdrant_search import (
    QdrantChunkSearchRepository,
)
from jipsa_rag.infrastructure.indexing.qdrant_store import (
    QdrantChunkVectorStore,
)

_TEST_COLLECTION = "test_qdrant_lazy_client"
_TEST_EMBEDDING_MODEL = "test/qdrant-lazy-embedding"
_TEST_EMBEDDING_DIM = 3
_TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"


@dataclass(slots=True)
class _FakeQueryResponse:
    """query_points 테스트 응답에 필요한 points 필드만 제공한다."""

    points: list[models.ScoredPoint]


class _FakeAsyncQdrantClient:
    """실제 Qdrant 없이 지연 생성 이후의 호출만 기록하는 클라이언트 대역."""

    def __init__(self) -> None:
        """삭제·검색·종료 호출 기록과 기본 검색 응답을 초기화한다."""

        self.delete_calls: list[dict[str, object]] = []
        self.query_points_calls: list[dict[str, object]] = []
        self.query_points_result = _FakeQueryResponse(
            points=[],
        )
        self.close_call_count = 0

    async def delete(
        self,
        *,
        collection_name: str,
        points_selector: models.PointIdsList,
        wait: bool,
    ) -> models.UpdateResult:
        """Point 삭제 요청을 기록하고 완료 결과를 반환한다."""

        self.delete_calls.append(
            {
                "collection_name": collection_name,
                "points_selector": points_selector,
                "wait": wait,
            }
        )

        return models.UpdateResult(
            operation_id=1,
            status=models.UpdateStatus.COMPLETED,
        )

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
    ) -> _FakeQueryResponse:
        """청크 검색 요청을 기록하고 준비된 검색 결과를 반환한다."""

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
        """클라이언트 종료 호출 횟수를 기록한다."""

        self.close_call_count += 1


def _create_settings() -> Settings:
    """지연 생성 테스트용 Qdrant와 임베딩 설정을 생성한다."""

    return get_settings().model_copy(
        update={
            "embedding_model": _TEST_EMBEDDING_MODEL,
            "embedding_dim": _TEST_EMBEDDING_DIM,
            "qdrant_collection": _TEST_COLLECTION,
        }
    )


def _create_query_embedding() -> QueryEmbedding:
    """유효한 3차원 검색 질의 임베딩을 생성한다."""

    return QueryEmbedding(
        embedding_model=_TEST_EMBEDDING_MODEL,
        embedding_dim=_TEST_EMBEDDING_DIM,
        vector=(
            1.0,
            0.0,
            0.0,
        ),
    )


def _patch_vector_store_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    fake_client: _FakeAsyncQdrantClient,
) -> list[dict[str, object]]:
    """Vector Store의 AsyncQdrantClient 생성을 기록 가능한 Factory로 교체한다."""

    creation_calls: list[dict[str, object]] = []

    def create_client(
        **arguments: object,
    ) -> AsyncQdrantClient:
        """생성 인수를 기록하고 준비된 Fake Client를 반환한다."""

        creation_calls.append(
            dict(arguments),
        )

        return cast(
            AsyncQdrantClient,
            fake_client,
        )

    monkeypatch.setattr(
        qdrant_store_module,
        "AsyncQdrantClient",
        create_client,
    )

    return creation_calls


def _patch_search_repository_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    fake_client: _FakeAsyncQdrantClient,
) -> list[dict[str, object]]:
    """Search Repository의 클라이언트 생성을 기록 가능한 Factory로 교체한다."""

    creation_calls: list[dict[str, object]] = []

    def create_client(
        **arguments: object,
    ) -> AsyncQdrantClient:
        """생성 인수를 기록하고 준비된 Fake Client를 반환한다."""

        creation_calls.append(
            dict(arguments),
        )

        return cast(
            AsyncQdrantClient,
            fake_client,
        )

    monkeypatch.setattr(
        qdrant_search_module,
        "AsyncQdrantClient",
        create_client,
    )

    return creation_calls


def _assert_client_creation_settings(
    creation_arguments: dict[str, object],
    settings: Settings,
) -> None:
    """지연 생성된 클라이언트가 실제 Local RAG 설정을 사용하는지 검증한다."""

    expected_api_key = (
        settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key is not None else None
    )

    assert creation_arguments["url"] == settings.qdrant_url
    assert creation_arguments["grpc_port"] == settings.qdrant_grpc_port
    assert creation_arguments["prefer_grpc"] is settings.qdrant_prefer_grpc
    assert creation_arguments["api_key"] == expected_api_key
    assert creation_arguments["timeout"] == max(
        1,
        ceil(settings.qdrant_timeout_seconds),
    )

    # 테스트 경고를 제거하기 위해 실제 연결의 호환성 검사를 전역으로
    # 비활성화해서는 안 된다.
    #
    # check_compatibility를 전달하지 않으면 실제 Qdrant 연산에서는
    # qdrant-client의 기본 호환성 검사 정책이 그대로 적용된다.
    assert "check_compatibility" not in creation_arguments


@pytest.mark.asyncio
async def test_vector_store_constructor_empty_operation_and_close_do_not_create_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qdrant를 사용하지 않은 저장소 생명주기는 클라이언트를 만들지 않는다."""

    fake_client = _FakeAsyncQdrantClient()

    creation_calls = _patch_vector_store_client_creation(
        monkeypatch,
        fake_client,
    )

    vector_store = QdrantChunkVectorStore(
        _create_settings(),
    )

    # 객체가 FastAPI 의존성으로 생성되는 것만으로는 Qdrant 서버 버전 확인이
    # 발생하지 않아야 한다.
    assert creation_calls == []

    # 삭제 대상이 없는 보상 작업도 실제 Qdrant 연산이 아니므로
    # 클라이언트를 생성하지 않아야 한다.
    await vector_store.delete_chunks(
        chunk_ids=(),
    )

    assert creation_calls == []

    # 실제 연산이 한 번도 없었던 저장소를 닫는 과정에서도 종료를 위해
    # 클라이언트를 새로 만들면 안 된다.
    await vector_store.close()

    assert creation_calls == []
    assert fake_client.close_call_count == 0


@pytest.mark.asyncio
async def test_vector_store_first_real_operation_creates_one_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """최초 실제 저장 연산에서 한 번만 클라이언트를 만들고 종료해야 한다."""

    settings = _create_settings()
    fake_client = _FakeAsyncQdrantClient()

    creation_calls = _patch_vector_store_client_creation(
        monkeypatch,
        fake_client,
    )

    vector_store = QdrantChunkVectorStore(
        settings,
    )

    assert creation_calls == []

    # 비어 있지 않은 Chunk ID 삭제는 실제 Qdrant 연산이므로 이 시점에
    # 소유 클라이언트를 최초로 생성한다.
    await vector_store.delete_chunks(
        chunk_ids=(_TEST_CHUNK_ID,),
    )

    assert len(creation_calls) == 1
    assert len(fake_client.delete_calls) == 1

    _assert_client_creation_settings(
        creation_calls[0],
        settings,
    )

    # 같은 저장소의 다음 요청은 기존 클라이언트를 재사용해야 한다.
    await vector_store.delete_chunks(
        chunk_ids=(_TEST_CHUNK_ID,),
    )

    assert len(creation_calls) == 1
    assert len(fake_client.delete_calls) == 2

    await vector_store.close()

    assert fake_client.close_call_count == 1

    # close()를 반복해도 종료를 위해 새 클라이언트를 만들거나 같은
    # 클라이언트를 중복 종료하지 않아야 한다.
    await vector_store.close()

    assert len(creation_calls) == 1
    assert fake_client.close_call_count == 1


@pytest.mark.asyncio
async def test_search_validation_failure_does_not_create_qdrant_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """잘못된 검색 요청은 Qdrant 클라이언트 생성 전에 거부해야 한다."""

    fake_client = _FakeAsyncQdrantClient()

    creation_calls = _patch_search_repository_client_creation(
        monkeypatch,
        fake_client,
    )

    repository = QdrantChunkSearchRepository(
        _create_settings(),
    )

    assert creation_calls == []

    with pytest.raises(
        ValueError,
        match="reference_file_idxs must be a non-empty tuple",
    ):
        await repository.search(
            user_idx=1,
            reference_file_idxs=(),
            query_embedding=_create_query_embedding(),
            limit=3,
        )

    # 입력 검증 실패가 qdrant.test 등의 Mock 주소에 대한 서버 버전 확인으로
    # 이어지지 않아야 한다.
    assert creation_calls == []
    assert fake_client.query_points_calls == []

    await repository.close()

    assert creation_calls == []
    assert fake_client.close_call_count == 0


@pytest.mark.asyncio
async def test_search_first_valid_request_creates_one_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """최초 유효한 검색에서 한 번만 클라이언트를 만들고 재사용해야 한다."""

    settings = _create_settings()
    fake_client = _FakeAsyncQdrantClient()

    creation_calls = _patch_search_repository_client_creation(
        monkeypatch,
        fake_client,
    )

    repository = QdrantChunkSearchRepository(
        settings,
    )

    assert creation_calls == []

    first_result = await repository.search(
        user_idx=1,
        reference_file_idxs=(10,),
        query_embedding=_create_query_embedding(),
        limit=3,
        score_threshold=0.5,
    )

    assert first_result == ()
    assert len(creation_calls) == 1
    assert len(fake_client.query_points_calls) == 1

    _assert_client_creation_settings(
        creation_calls[0],
        settings,
    )

    # 같은 요청 범위에서 다음 검색을 수행해도 생성자는 다시 호출되지 않고
    # 첫 검색에서 만들어진 클라이언트를 재사용해야 한다.
    second_result = await repository.search(
        user_idx=1,
        reference_file_idxs=(10,),
        query_embedding=_create_query_embedding(),
        limit=3,
    )

    assert second_result == ()
    assert len(creation_calls) == 1
    assert len(fake_client.query_points_calls) == 2

    await repository.close()

    assert fake_client.close_call_count == 1

    # 이미 종료한 Repository를 다시 닫는 작업은 멱등적으로 처리한다.
    await repository.close()

    assert len(creation_calls) == 1
    assert fake_client.close_call_count == 1
