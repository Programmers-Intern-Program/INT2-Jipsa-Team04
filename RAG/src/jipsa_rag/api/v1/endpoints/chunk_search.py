"""내부 애플리케이션 서버가 사용하는 관련 청크 검색 API를 제공한다."""

from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.api.v1.error_mapping import (
    convert_embedding_error,
    convert_vector_search_error,
)
from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.embedding.exceptions import EmbeddingError
from jipsa_rag.infrastructure.embedding.query import TeiQueryEmbedder
from jipsa_rag.infrastructure.indexing.exceptions import VectorDatabaseError
from jipsa_rag.infrastructure.indexing.qdrant_search import (
    QdrantChunkSearchRepository,
)
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
)
from jipsa_rag.services.chunk_search import ChunkSearchService

router = APIRouter(
    prefix="/chunks",
    tags=["Chunk Search"],
)


# 현재 실행 환경의 설정을 검색 의존성에 주입한다.
#
# get_settings()는 프로세스 안에서 캐시되므로 요청마다 dotenv 파일을
# 다시 읽지 않으며 SecretStr 값도 일반 문자열 표현으로 노출하지 않는다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


def get_query_embedder(
    settings: SettingsDependency,
) -> TeiQueryEmbedder:
    """현재 환경의 TEI 설정이 적용된 질의 임베딩 생성기를 반환한다."""

    return TeiQueryEmbedder(settings)


# API 단위 테스트에서는 이 의존성을 Stub으로 교체하여 실제 TEI 서버나
# CUDA GPU 컨테이너를 실행하지 않고 엔드포인트 계약만 검증할 수 있다.
QueryEmbedderDependency = Annotated[
    TeiQueryEmbedder,
    Depends(get_query_embedder),
]


async def get_chunk_search_repository(
    settings: SettingsDependency,
) -> AsyncIterator[QdrantChunkSearchRepository]:
    """요청 범위 Qdrant 검색 저장소를 생성하고 연결을 안전하게 종료한다.

    색인용 Qdrant 저장소와 검색용 저장소는 책임이 다르므로
    객체를 공유하지 않는다. 검색 요청마다 생성한 클라이언트는
    응답 완료 또는 예외 발생과 관계없이 finally 블록에서 종료하여
    연결 누수를 방지한다.
    """

    repository = QdrantChunkSearchRepository(settings)

    try:
        yield repository
    finally:
        await repository.close()


# API 단위 테스트에서는 이 의존성을 교체하여 실제 Qdrant 서버 없이
# 인증, 요청 검증, 성공 응답과 예외 변환을 검증할 수 있다.
ChunkSearchRepositoryDependency = Annotated[
    QdrantChunkSearchRepository,
    Depends(get_chunk_search_repository),
]


def get_chunk_search_service(
    query_embedder: QueryEmbedderDependency,
    repository: ChunkSearchRepositoryDependency,
) -> ChunkSearchService:
    """질의 임베딩과 Qdrant 검색을 연결한 서비스를 반환한다."""

    return ChunkSearchService(
        query_embedder=query_embedder,
        repository=repository,
    )


ChunkSearchServiceDependency = Annotated[
    ChunkSearchService,
    Depends(get_chunk_search_service),
]


@router.post(
    "/search",
    status_code=HTTPStatus.OK,
    response_model=ApiResponse[ChunkSearchResponse],
    summary="관련 청크 검색",
    description=(
        "애플리케이션 서버가 전달한 사용자 질의를 TEI에서 임베딩한 뒤, "
        "Qdrant에서 동일 user_idx, is_active=true, "
        "file_idx IN reference_file_idxs 조건을 모두 만족하는 청크만 검색한다. "
        "top_k는 최대 반환 개수를 제한하고 score_threshold는 "
        "Cosine 관련도 최소 점수로 적용한다."
    ),
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": ApiResponse[None],
            "description": "내부 토큰 누락 또는 불일치",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": (
                "user_idx, reference_file_idxs, query, top_k 또는 score_threshold 요청값 검증 실패"
            ),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": (
                "TEI 요청 거부·응답 계약 오류, Qdrant 검색 거부 또는 검색 결과 payload 계약 오류"
            ),
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": ("내부 토큰 미설정, TEI 또는 Qdrant 연결 실패 및 일시적 사용 불가"),
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "TEI 질의 임베딩 요청 시간 초과",
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": "분류되지 않은 내부 검색 처리 실패",
        },
    },
)
async def search_chunks(
    request: ChunkSearchRequest,
    chunk_search_service: ChunkSearchServiceDependency,
) -> ApiResponse[ChunkSearchResponse]:
    """내부 인증된 사용자 및 참조문서 범위의 청크를 반환한다."""

    try:
        response_data = await chunk_search_service.search(request)

    except EmbeddingError as error:
        raise convert_embedding_error(
            error,
            user_idx=request.user_idx,
        ) from error

    except VectorDatabaseError as error:
        raise convert_vector_search_error(
            error,
            user_idx=request.user_idx,
        ) from error

    # 외부 응답에는 질의 임베딩 벡터, Qdrant 내부 객체, file_hash,
    # Presigned URL 또는 내부 인증 토큰을 포함하지 않는다.
    return ApiResponse[ChunkSearchResponse](
        success=True,
        code="CHUNK_SEARCH_COMPLETED",
        message="Relevant document chunks were retrieved.",
        data=response_data,
    )
