"""내부 애플리케이션 서버가 사용하는 근거 기반 RAG 답변 API를 제공한다."""

from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from jipsa_rag.api.v1.endpoints.chunk_search import ChunkSearchServiceDependency
from jipsa_rag.api.v1.error_mapping import (
    convert_embedding_error,
    convert_generation_error,
    convert_vector_search_error,
)
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.core.generation_config import (
    GenerationSettings,
    get_generation_settings,
)
from jipsa_rag.infrastructure.embedding.exceptions import EmbeddingError
from jipsa_rag.infrastructure.generation.claude import ClaudeGenerationClient
from jipsa_rag.infrastructure.generation.exceptions import GenerationError
from jipsa_rag.infrastructure.indexing.exceptions import (
    IndexStorageError,
    VectorDatabaseError,
)
from jipsa_rag.schemas.common import ApiResponse, ValidationErrorData
from jipsa_rag.schemas.rag_answer import RagAnswerRequest, RagAnswerResponse
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.rag_answer import (
    RagAnswerService,
    RagAnswerServiceError,
)

router = APIRouter(
    prefix="/rag",
    tags=["RAG Answer"],
)


def get_rag_generation_settings() -> GenerationSettings:
    """현재 환경의 Claude 생성 설정을 안전한 API 설정 오류로 변환한다.

    API Key 원문이나 Pydantic 입력값이 전역 예외 로그에 포함되지 않도록
    설정 검증 오류를 공통 서비스 사용 불가 오류로 정규화한다.
    """

    try:
        return get_generation_settings()
    except ValidationError:
        raise AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            public_message="The generation service is temporarily unavailable.",
            log_context={
                "generation_configuration_status": "invalid",
            },
        ) from None


GenerationSettingsDependency = Annotated[
    GenerationSettings,
    Depends(get_rag_generation_settings),
]


async def get_generation_client(
    settings: GenerationSettingsDependency,
) -> AsyncIterator[ClaudeGenerationClient]:
    """요청 범위 Claude 클라이언트를 생성하고 연결을 정리한다."""

    client = ClaudeGenerationClient(settings)

    try:
        yield client
    finally:
        # 정상 응답, 검증 오류 또는 외부 공급자 예외와 관계없이
        # 요청 범위 Anthropic 비동기 클라이언트의 연결 자원을 종료한다.
        await client.close()


GenerationClientDependency = Annotated[
    ClaudeGenerationClient,
    Depends(get_generation_client),
]


def get_prompt_builder() -> RagPromptBuilder:
    """기본 문맥 예산을 사용하는 프롬프트 구성기를 반환한다."""

    return RagPromptBuilder()


PromptBuilderDependency = Annotated[
    RagPromptBuilder,
    Depends(get_prompt_builder),
]


def get_rag_answer_service(
    chunk_search_service: ChunkSearchServiceDependency,
    prompt_builder: PromptBuilderDependency,
    generation_client: GenerationClientDependency,
) -> RagAnswerService:
    """검색과 Claude 생성을 연결한 RAG 답변 서비스를 반환한다."""

    return RagAnswerService(
        chunk_searcher=chunk_search_service,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )


RagAnswerServiceDependency = Annotated[
    RagAnswerService,
    Depends(get_rag_answer_service),
]


@router.post(
    "/answers",
    status_code=HTTPStatus.OK,
    response_model=ApiResponse[RagAnswerResponse],
    summary="참조문서 기반 RAG 답변 생성",
    description=(
        "질문 전송 시점에 전달된 reference_file_idxs를 현재 요청의 독립적인 "
        "검색 범위로 고정한다. 사용자, 활성 상태 및 참조문서 조건을 모두 "
        "만족하는 청크만 검색한 뒤 Claude를 통해 근거 기반 답변과 "
        "출처를 반환한다. "
        "reference_file_idxs가 비어 있거나 생략된 요청은 전체 문서 검색으로 "
        "변환하지 않고 요청 검증 단계에서 거부한다."
    ),
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": ApiResponse[None],
            "description": "X-Internal-Token 누락 또는 불일치",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": (
                "user_idx, reference_file_idxs, query, top_k 또는 score_threshold 요청값 검증 실패"
            ),
        },
        HTTPStatus.TOO_MANY_REQUESTS: {
            "model": ApiResponse[None],
            "description": "Claude 생성 공급자 요청 제한",
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": (
                "TEI 또는 Qdrant 요청 거부, 임베딩 응답 계약 오류, 검색 결과 계약 오류"
            ),
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": (
                "내부 토큰·Claude 설정 누락, TEI·Qdrant·Claude 일시적 사용 불가 "
                "또는 Claude 요청 시간 초과"
            ),
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": "답변 오케스트레이션 또는 생성 응답 변환 실패",
        },
    },
)
async def create_rag_answer(
    request: RagAnswerRequest,
    rag_answer_service: RagAnswerServiceDependency,
) -> ApiResponse[RagAnswerResponse]:
    """내부 인증된 요청의 고정된 참조문서 범위에서 RAG 답변을 생성한다."""

    try:
        response_data = await rag_answer_service.answer(request)

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

    except IndexStorageError as error:
        # 현재 검색 구현은 VectorDatabaseError를 반환하지만, 향후 다른
        # IndexStorageError 구현체가 주입되어도 내부 세부 정보를
        # 노출하지 않는다.
        raise AppException(
            ErrorCode.INTERNAL_SERVER_ERROR,
            log_context={
                "user_idx": request.user_idx,
                "rag_answer_error_type": type(error).__name__,
                "rag_answer_operation": "chunk_search",
            },
        ) from error

    except GenerationError as error:
        raise convert_generation_error(
            error,
            user_idx=request.user_idx,
        ) from error

    except RagAnswerServiceError as error:
        # operation은 서비스가 정의한 안전한 작업 식별자이며 질문, 청크,
        # 프롬프트 또는 생성 응답 원문을 포함하지 않는다.
        raise AppException(
            ErrorCode.INTERNAL_SERVER_ERROR,
            log_context={
                "user_idx": request.user_idx,
                "rag_answer_error_type": type(error).__name__,
                "rag_answer_operation": error.operation,
            },
        ) from error

    # 응답에는 답변, 제한된 출처 발췌문과 생성 메타데이터만 포함한다.
    # 내부 인증 토큰, 질의 임베딩, Qdrant 객체, 전체 청크 원문 및 API Key는
    # RagAnswerResponse 계약에 존재하지 않으므로 외부로 직렬화되지 않는다.
    return ApiResponse[RagAnswerResponse](
        success=True,
        code="RAG_ANSWER_COMPLETED",
        message="The RAG answer was generated.",
        data=response_data,
    )
