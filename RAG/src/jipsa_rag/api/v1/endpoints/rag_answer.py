"""선택한 참조문서만 근거로 사용하는 내부 RAG 답변 API를 제공한다."""

from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.api.v1.endpoints.chunk_search import (
    ChunkSearchServiceDependency,
)
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.core.generation_config import (
    GenerationSettings,
    get_generation_settings,
)
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingError,
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.generation.claude import (
    ClaudeGenerationClient,
)
from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationAuthenticationError,
    GenerationError,
    GenerationProviderError,
    GenerationRateLimitError,
    GenerationServerError,
    GenerationTimeoutError,
    InvalidGenerationResponseError,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    IndexStorageError,
    InvalidVectorSearchResultError,
    VectorCollectionConfigurationError,
    VectorDatabaseError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
)
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
)
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.rag_answer import (
    RagAnswerService,
    RagAnswerServiceError,
)

router = APIRouter(
    prefix="/rag",
    tags=["RAG Answer"],
)


# Claude 생성 설정은 별도의 BaseSettings 모델에서 관리한다.
#
# API Key는 SecretStr로 유지되며 Claude SDK 클라이언트를 생성하는 시점에만
# 원문을 꺼낸다. 엔드포인트, 로그 컨텍스트 또는 외부 오류 응답에는
# GenerationSettings 객체나 API Key를 전달하지 않는다.
GenerationSettingsDependency = Annotated[
    GenerationSettings,
    Depends(get_generation_settings),
]


def get_rag_prompt_builder() -> RagPromptBuilder:
    """검색된 청크를 Claude용 근거 프롬프트와 공개 출처로 변환한다."""

    return RagPromptBuilder()


RagPromptBuilderDependency = Annotated[
    RagPromptBuilder,
    Depends(get_rag_prompt_builder),
]


async def get_generation_client(
    settings: GenerationSettingsDependency,
) -> AsyncIterator[ClaudeGenerationClient]:
    """요청 단위 Claude 클라이언트를 생성하고 연결 자원을 정리한다.

    API 단위 테스트에서는 이 의존성이 아니라 최종 ``RagAnswerService``
    의존성을 Stub으로 교체하므로 실제 Claude API와 네트워크 통신이
    발생하지 않는다.
    """

    client = ClaudeGenerationClient(
        settings,
    )

    try:
        yield client
    finally:
        await client.close()


ClaudeGenerationClientDependency = Annotated[
    ClaudeGenerationClient,
    Depends(get_generation_client),
]


def get_rag_answer_service(
    chunk_search_service: ChunkSearchServiceDependency,
    prompt_builder: RagPromptBuilderDependency,
    generation_client: ClaudeGenerationClientDependency,
) -> RagAnswerService:
    """검색, 프롬프트 구성 및 Claude 생성을 연결한 답변 서비스를 반환한다."""

    return RagAnswerService(
        chunk_searcher=chunk_search_service,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )


RagAnswerServiceDependency = Annotated[
    RagAnswerService,
    Depends(get_rag_answer_service),
]


def _convert_embedding_error(
    error: EmbeddingError,
    *,
    user_idx: int,
) -> AppException:
    """질의 임베딩 오류를 질문 원문 없이 공통 API 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "embedding_error_type": type(error).__name__,
        "embedding_operation": "embed_search_query",
    }

    if isinstance(
        error,
        EmbeddingServiceTimeoutError,
    ):
        error_code = ErrorCode.EMBEDDING_SERVICE_TIMEOUT

    elif isinstance(
        error,
        EmbeddingServiceUnavailableError,
    ):
        if error.status_code is not None:
            log_context["embedding_status_code"] = error.status_code

        error_code = ErrorCode.EMBEDDING_SERVICE_UNAVAILABLE

    elif isinstance(
        error,
        EmbeddingServiceRejectedError,
    ):
        log_context["embedding_status_code"] = error.status_code
        error_code = ErrorCode.EMBEDDING_REQUEST_REJECTED

    elif isinstance(
        error,
        InvalidEmbeddingResponseError,
    ):
        # reason은 벡터 개수·차원·값 형식처럼 임베딩 계층이 만든
        # 안전한 계약 검증 정보만 포함한다.
        log_context["embedding_response_reason"] = error.reason
        log_context["embedding_batch_start_index"] = error.batch_start_index
        error_code = ErrorCode.INVALID_EMBEDDING_RESPONSE

    else:
        error_code = ErrorCode.EMBEDDING_GENERATION_FAILED

    return AppException(
        error_code,
        log_context=log_context,
    )


def _convert_index_error(
    error: IndexStorageError,
    *,
    user_idx: int,
) -> AppException:
    """Qdrant 검색 오류를 청크 원문과 payload 없이 공통 API 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "index_error_type": type(error).__name__,
    }

    if isinstance(
        error,
        VectorDatabaseError,
    ):
        log_context["vector_operation"] = error.operation

        if error.status_code is not None:
            log_context["vector_status_code"] = error.status_code

        if isinstance(
            error,
            VectorDatabaseUnavailableError,
        ):
            error_code = ErrorCode.VECTOR_DATABASE_UNAVAILABLE

        elif isinstance(
            error,
            InvalidVectorSearchResultError,
        ):
            error_code = ErrorCode.INVALID_VECTOR_SEARCH_RESULT

        elif isinstance(
            error,
            (
                VectorDatabaseRejectedError,
                VectorCollectionConfigurationError,
            ),
        ):
            error_code = ErrorCode.VECTOR_SEARCH_FAILED

        else:
            error_code = ErrorCode.VECTOR_SEARCH_FAILED

    else:
        # 답변 경로의 IndexStorageError는 청크 검색 중 발생한 내부 저장소
        # 계층 오류다. SQL, 원문 또는 하위 예외 메시지를 외부에 노출하지 않는다.
        error_code = ErrorCode.VECTOR_SEARCH_FAILED

    return AppException(
        error_code,
        log_context=log_context,
    )


def _convert_generation_error(
    error: GenerationError,
    *,
    user_idx: int,
) -> AppException:
    """Claude 생성 오류를 프롬프트와 API Key 없이 공통 API 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "generation_error_type": type(error).__name__,
    }

    if isinstance(
        error,
        GenerationProviderError,
    ):
        # provider, HTTP 상태 코드 및 공급자 요청 ID는 원문 프롬프트나
        # API Key를 포함하지 않는 진단용 메타데이터다.
        log_context["generation_provider"] = error.provider

        if error.status_code is not None:
            log_context["generation_status_code"] = error.status_code

        if error.request_id is not None:
            log_context["generation_request_id"] = error.request_id

    if isinstance(
        error,
        GenerationTimeoutError,
    ):
        error_code = ErrorCode.GENERATION_SERVICE_TIMEOUT

    elif isinstance(
        error,
        (
            GenerationAuthenticationError,
            GenerationRateLimitError,
            GenerationServerError,
        ),
    ):
        # API Key 오류, 공급자 요청 제한 및 공급자 서버 장애는
        # 사용자가 요청 본문을 수정해 해결할 수 없는 일시적 서비스 문제다.
        error_code = ErrorCode.GENERATION_SERVICE_UNAVAILABLE

    elif isinstance(
        error,
        InvalidGenerationResponseError,
    ):
        error_code = ErrorCode.INVALID_GENERATION_RESPONSE

    elif isinstance(
        error,
        GenerationProviderError,
    ):
        error_code = ErrorCode.GENERATION_REQUEST_FAILED

    else:
        error_code = ErrorCode.GENERATION_FAILED

    return AppException(
        error_code,
        log_context=log_context,
    )


@router.post(
    "/answers",
    status_code=HTTPStatus.OK,
    response_model=ApiResponse[RagAnswerResponse],
    summary="선택 참조문서 기반 RAG 답변 생성",
    description=(
        "질문 전송 시점에 전달된 reference_file_idxs 범위에서만 "
        "관련 청크를 검색한다. 검색 결과가 없으면 Claude API를 "
        "호출하지 않고 근거 부족 응답을 반환한다. 정상 답변에는 "
        "파일명, 원본 위치, 섹션 및 길이가 제한된 청크 발췌문 "
        "출처가 포함된다."
    ),
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": ApiResponse[None],
            "description": "X-Internal-Token 누락 또는 불일치",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": (
                "참조문서 미선택 시 REFERENCE_DOCUMENT_REQUIRED, "
                "그 밖의 요청값 오류 시 REQUEST_VALIDATION_FAILED"
            ),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": ("TEI·Qdrant·Claude 요청 거부 또는 외부 서비스 응답 계약 오류"),
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": ("TEI, Qdrant 또는 Claude 생성 공급자의 일시적 사용 불가"),
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "TEI 또는 Claude 요청 시간 초과",
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": "분류되지 않은 RAG 답변 처리 실패",
        },
    },
)
async def answer_question(
    request: RagAnswerRequest,
    rag_answer_service: RagAnswerServiceDependency,
) -> ApiResponse[RagAnswerResponse]:
    """내부 인증된 요청에 대해 선택 문서 근거 기반 답변을 반환한다.

    질문, 검색 청크, 생성 프롬프트, 답변 원문 및 API Key는 이 함수의
    로그 컨텍스트나 예외 메시지에 기록하지 않는다. 하위 계층 오류는
    오류 타입과 안전한 작업 메타데이터만 남기는 ``AppException``으로
    변환한다.
    """

    try:
        response_data = await rag_answer_service.answer(
            request,
        )

    except EmbeddingError as error:
        raise _convert_embedding_error(
            error,
            user_idx=request.user_idx,
        ) from None

    except IndexStorageError as error:
        raise _convert_index_error(
            error,
            user_idx=request.user_idx,
        ) from None

    except GenerationError as error:
        raise _convert_generation_error(
            error,
            user_idx=request.user_idx,
        ) from None

    except RagAnswerServiceError as error:
        # operation은 서비스가 미리 정의한 안전한 식별자다.
        #
        # 원본 질문, 청크, 프롬프트 또는 하위 예외 메시지는
        # AppException의 원인 체인과 로그 컨텍스트에 연결하지 않는다.
        raise AppException(
            ErrorCode.INTERNAL_SERVER_ERROR,
            log_context={
                "user_idx": request.user_idx,
                "rag_answer_operation": error.operation,
            },
        ) from None

    return ApiResponse[RagAnswerResponse](
        success=True,
        code="RAG_ANSWER_COMPLETED",
        message="The RAG answer request was processed.",
        data=response_data,
    )
