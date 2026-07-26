"""선택한 참조문서만 근거로 사용하는 내부 RAG 답변 API를 제공한다."""

from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Annotated, Final

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
from jipsa_rag.infrastructure.generation.client import GenerationClient
from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationAuthenticationError,
    GenerationBudgetExceededError,
    GenerationError,
    GenerationProviderError,
    GenerationRateLimitError,
    GenerationServerError,
    GenerationTimeoutError,
    InvalidGenerationResponseError,
)
from jipsa_rag.infrastructure.generation.limited import (
    GenerationLimitPolicy,
    LimitedGenerationClient,
    get_shared_generation_concurrency_limiter,
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
from jipsa_rag.services.query_routing import RoutedRagAnswerService
from jipsa_rag.services.rag_answer import (
    RagAnswerService,
    RagAnswerServiceError,
)

router = APIRouter(
    prefix="/rag",
    tags=["RAG Answer"],
)

# Claude 답변의 인용 검증 실패를 식별하는 서비스 작업 코드다.
#
# 이 값은 Claude가 정상 HTTP 응답을 반환했더라도 답변에 인용이 없거나,
# 현재 프롬프트에 존재하지 않는 SOURCE-N을 인용한 경우 사용된다.
# 이러한 결과는 일반적인 내부 서버 오류가 아니라 생성 공급자 응답 계약 위반이므로
# API 계층에서 INVALID_GENERATION_RESPONSE로 변환한다.
_CITATION_VALIDATION_OPERATION: Final[str] = "answer_citation_validation_failed"


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
) -> AsyncIterator[GenerationClient]:
    """요청 범위 Claude 예산 제한 클라이언트를 생성하고 정리한다.

    실제 Anthropic SDK 클라이언트는 요청마다 생성하고 종료한다.

    ``LimitedGenerationClient``도 요청마다 새로 생성되므로 lookup 또는
    synthesis 한 건의 호출 횟수와 누적 토큰이 다른 사용자 요청과 섞이지
    않는다.

    동시성 제한기는 프로세스 범위에서 공유되므로 서로 다른 HTTP 요청도
    설정된 Claude 최대 동시 호출 수를 함께 지킨다.
    """

    delegate = ClaudeGenerationClient(
        settings,
    )
    concurrency_limiter = get_shared_generation_concurrency_limiter(
        settings.anthropic_max_concurrent_requests,
    )
    client = LimitedGenerationClient(
        delegate=delegate,
        policy=GenerationLimitPolicy(
            max_calls=settings.anthropic_max_calls_per_answer,
            max_input_tokens=(settings.anthropic_max_input_tokens_per_answer),
            max_output_tokens=(settings.anthropic_max_output_tokens_per_answer),
            max_output_tokens_per_call=(settings.anthropic_max_output_tokens),
        ),
        concurrency_limiter=concurrency_limiter,
    )

    try:
        yield client
    finally:
        await delegate.close()


GenerationClientDependency = Annotated[
    GenerationClient,
    Depends(get_generation_client),
]


def get_rag_answer_service(
    chunk_search_service: ChunkSearchServiceDependency,
    prompt_builder: RagPromptBuilderDependency,
    generation_client: GenerationClientDependency,
) -> RagAnswerService:
    """질의 라우팅과 요청 범위 Claude 제한이 적용된 서비스를 반환한다.

    lookup은 기존 단일 검색·단일 생성 흐름을 그대로 사용한다.

    synthesis는 PDF별 검색, 부분 생성 및 최종 종합을 수행하지만 모든
    Claude 호출이 동일한 요청 범위 ``LimitedGenerationClient``를
    통과하므로 호출 횟수와 토큰 예산이 하나의 답변 단위로 누적된다.
    """

    return RoutedRagAnswerService(
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
    """생성 오류를 질문·프롬프트·API Key 없이 공통 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "generation_error_type": type(error).__name__,
    }

    if isinstance(
        error,
        GenerationProviderError,
    ):
        # provider, 상태 코드 및 요청 ID는 프롬프트 원문이나 API Key를
        # 포함하지 않는 안전한 진단 메타데이터다.
        log_context["generation_provider"] = error.provider

        if error.status_code is not None:
            log_context["generation_status_code"] = error.status_code

        if error.request_id is not None:
            log_context["generation_request_id"] = error.request_id

    if isinstance(
        error,
        GenerationBudgetExceededError,
    ):
        # 실제 호출 수나 실제 토큰 수는 사용자별 사용 패턴 정보가 될 수
        # 있으므로 기록하지 않고 초과한 제한 종류만 남긴다.
        log_context["generation_budget_limit_type"] = error.limit_type
        error_code = ErrorCode.GENERATION_BUDGET_EXCEEDED

    elif isinstance(
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


def _convert_rag_answer_service_error(
    error: RagAnswerServiceError,
    *,
    user_idx: int,
) -> AppException:
    """답변 서비스 계약 오류를 민감 정보 없는 공통 API 오류로 변환한다.

    Claude 답변의 인용 검증 실패는 생성 요청 자체가 실패한 것이 아니라
    공급자가 반환한 답변이 Local RAG의 SOURCE-N 계약을 만족하지 못한 상태다.
    따라서 AWS Backend가 정상 답변으로 처리하지 않도록 502
    ``INVALID_GENERATION_RESPONSE``로 변환한다.

    그 밖의 서비스 작업 오류는 현재 공개 오류 코드로 더 세분화할 수 없는
    내부 오케스트레이션 실패이므로 기존과 같이 500으로 처리한다.

    Args:
        error:
            답변 서비스가 원문 대신 안전한 ``operation``만 보관한 예외다.
        user_idx:
            질문 원문 없이 요청 범위를 추적하기 위한 사용자 식별자다.

    Returns:
        공통 예외 처리기가 외부 오류 응답으로 변환할 ``AppException``이다.
    """

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "rag_answer_error_type": type(error).__name__,
        "rag_answer_operation": error.operation,
    }

    if error.operation == _CITATION_VALIDATION_OPERATION:
        error_code = ErrorCode.INVALID_GENERATION_RESPONSE
    else:
        error_code = ErrorCode.INTERNAL_SERVER_ERROR

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
        "관련 청크를 검색한다. lookup 질문은 기존 단일 생성 흐름을 "
        "사용하며, synthesis 질문은 PDF별 부분 답변을 생성한 뒤 "
        "검증된 부분 결과만 최종 종합한다. 일부 PDF의 근거가 부족하면 "
        "해당 PDF를 최종 후보에서 제외하고, 모든 PDF의 근거가 부족하면 "
        "최종 Claude 호출 없이 insufficient_evidence를 반환한다. "
        "정상 답변에는 유효한 [SOURCE-N] 인용이 하나 이상 필요하며 "
        "sources에는 최종 답변이 실제 인용한 출처만 포함된다. "
        "답변별 Claude 호출 횟수 또는 누적 토큰 예산을 초과하면 "
        "GENERATION_BUDGET_EXCEEDED 오류를 반환한다."
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
        HTTPStatus.TOO_MANY_REQUESTS: {
            "model": ApiResponse[None],
            "description": ("답변별 Claude 호출 횟수 또는 누적 입력·출력 토큰 예산 초과"),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": (
                "TEI·Qdrant·Claude 요청 거부, 외부 서비스 응답 계약 오류 또는 "
                "Claude SOURCE-N 인용 계약 위반"
            ),
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

    질문, 검색 청크, 생성 프롬프트, Claude 답변 원문 및 API Key는 이 함수의
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
        # 서비스 예외는 질문, 청크, 프롬프트 또는 Claude 응답 원문 대신
        # 안전한 operation만 보관한다. 변환된 AppException도 원본 서비스
        # 예외를 원인 체인으로 연결하지 않아 외부 로그 수집기가 예외 객체를
        # 직렬화하더라도 민감한 생성 데이터에 접근하지 못하게 한다.
        raise _convert_rag_answer_service_error(
            error,
            user_idx=request.user_idx,
        ) from None

    return ApiResponse[RagAnswerResponse](
        success=True,
        code="RAG_ANSWER_COMPLETED",
        message="The RAG answer request was processed.",
        data=response_data,
    )
