"""선택한 혼합 참조문서만 근거로 사용하는 내부 RAG 답변 API를 제공한다."""

from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends

from jipsa_rag.api.v1.endpoints.chunk_search import ChunkSearchServiceDependency
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
from jipsa_rag.infrastructure.generation.claude import ClaudeGenerationClient
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
from jipsa_rag.schemas.common import ApiResponse, ValidationErrorData
from jipsa_rag.schemas.rag_answer import RagAnswerRequest, RagAnswerResponse
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.query_routing import RoutedRagAnswerService
from jipsa_rag.services.rag_answer import RagAnswerService, RagAnswerServiceError

router = APIRouter(
    prefix="/rag",
    tags=["RAG Answer"],
)

# Claude가 정상 HTTP 응답을 반환해도 구조화 출력, SOURCE-N 존재 여부,
# cited_source_ids, 본문 최초 인용 순서 또는 최종 응답 매핑이 계약과 다르면
# 생성 공급자 응답을 정상 사용자 답변으로 인정할 수 없다.
_INVALID_GENERATION_RESPONSE_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "answer_citation_validation_failed",
        "response_mapping_failed",
    }
)


GenerationSettingsDependency = Annotated[
    GenerationSettings,
    Depends(get_generation_settings),
]


def get_rag_prompt_builder() -> RagPromptBuilder:
    """혼합 문서와 OCR Source Locator를 포함하는 기본 프롬프트 구성기를 반환한다."""

    return RagPromptBuilder()


RagPromptBuilderDependency = Annotated[
    RagPromptBuilder,
    Depends(get_rag_prompt_builder),
]


async def get_generation_client(
    settings: GenerationSettingsDependency,
) -> AsyncIterator[GenerationClient]:
    """요청 범위 Claude 예산 제한 클라이언트를 생성하고 연결을 정리한다.

    lookup은 한 번의 생성 요청을 사용하고 synthesis는 문서별 부분 생성과 최종
    종합을 사용한다. 모든 호출은 같은 ``LimitedGenerationClient``를 통과하므로
    한 사용자 답변의 호출 횟수와 누적 토큰 예산을 하나의 요청 범위에서 계산한다.
    """

    delegate = ClaudeGenerationClient(settings)
    concurrency_limiter = get_shared_generation_concurrency_limiter(
        settings.anthropic_max_concurrent_requests
    )
    client = LimitedGenerationClient(
        delegate=delegate,
        policy=GenerationLimitPolicy(
            max_calls=settings.anthropic_max_calls_per_answer,
            max_input_tokens=settings.anthropic_max_input_tokens_per_answer,
            max_output_tokens=settings.anthropic_max_output_tokens_per_answer,
            max_output_tokens_per_call=settings.anthropic_max_output_tokens,
        ),
        concurrency_limiter=concurrency_limiter,
    )

    try:
        yield client
    finally:
        # 정상 응답, 부분 실패 또는 인용 검증 오류와 관계없이 요청 범위 SDK
        # 연결을 닫는다. API Key나 프롬프트는 종료 로그에 포함하지 않는다.
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
    """혼합 문서 질의 라우팅과 요청 범위 Claude 제한이 적용된 서비스를 반환한다.

    lookup은 PDF, DOCX, PPTX, TXT, XLSX와 OCR 청크를 한 번에 검색한다.
    synthesis는 선택 파일별 독립 검색과 부분 답변을 수행하며 한 문서의 안전한
    검색·생성 실패가 다른 문서의 유효 근거를 제거하지 않게 한다.
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

    if isinstance(error, EmbeddingServiceTimeoutError):
        error_code = ErrorCode.EMBEDDING_SERVICE_TIMEOUT
    elif isinstance(error, EmbeddingServiceUnavailableError):
        if error.status_code is not None:
            log_context["embedding_status_code"] = error.status_code
        error_code = ErrorCode.EMBEDDING_SERVICE_UNAVAILABLE
    elif isinstance(error, EmbeddingServiceRejectedError):
        log_context["embedding_status_code"] = error.status_code
        error_code = ErrorCode.EMBEDDING_REQUEST_REJECTED
    elif isinstance(error, InvalidEmbeddingResponseError):
        # reason은 벡터 개수·차원·값 형식처럼 임베딩 계층이 만든 안전한
        # 계약 진단 정보만 포함하며 사용자 질문이나 벡터 값은 포함하지 않는다.
        log_context["embedding_response_reason"] = error.reason
        log_context["embedding_batch_start_index"] = error.batch_start_index
        error_code = ErrorCode.INVALID_EMBEDDING_RESPONSE
    else:
        error_code = ErrorCode.EMBEDDING_GENERATION_FAILED

    return AppException(error_code, log_context=log_context)


def _convert_index_error(
    error: IndexStorageError,
    *,
    user_idx: int,
) -> AppException:
    """Qdrant 검색 오류를 청크 원문과 payload 없이 공통 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "index_error_type": type(error).__name__,
    }

    if isinstance(error, VectorDatabaseError):
        log_context["vector_operation"] = error.operation
        if error.status_code is not None:
            log_context["vector_status_code"] = error.status_code

        if isinstance(error, VectorDatabaseUnavailableError):
            error_code = ErrorCode.VECTOR_DATABASE_UNAVAILABLE
        elif isinstance(error, InvalidVectorSearchResultError):
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
        # SQL, Qdrant payload 또는 하위 예외 메시지는 외부 응답에 노출하지 않는다.
        error_code = ErrorCode.VECTOR_SEARCH_FAILED

    return AppException(error_code, log_context=log_context)


def _convert_generation_error(
    error: GenerationError,
    *,
    user_idx: int,
) -> AppException:
    """Claude 오류를 질문·프롬프트·API Key 없이 공통 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "generation_error_type": type(error).__name__,
    }

    if isinstance(error, GenerationProviderError):
        # 공급자명, 상태 코드, 요청 ID는 프롬프트와 응답 원문을 포함하지 않는
        # 안전한 진단 메타데이터다.
        log_context["generation_provider"] = error.provider
        if error.status_code is not None:
            log_context["generation_status_code"] = error.status_code
        if error.request_id is not None:
            log_context["generation_request_id"] = error.request_id

    if isinstance(error, GenerationBudgetExceededError):
        # 실제 사용자별 토큰 수는 기록하지 않고 초과한 제한 종류만 남긴다.
        log_context["generation_budget_limit_type"] = error.limit_type
        error_code = ErrorCode.GENERATION_BUDGET_EXCEEDED
    elif isinstance(error, GenerationTimeoutError):
        error_code = ErrorCode.GENERATION_SERVICE_TIMEOUT
    elif isinstance(
        error,
        (
            GenerationAuthenticationError,
            GenerationRateLimitError,
            GenerationServerError,
        ),
    ):
        error_code = ErrorCode.GENERATION_SERVICE_UNAVAILABLE
    elif isinstance(error, InvalidGenerationResponseError):
        error_code = ErrorCode.INVALID_GENERATION_RESPONSE
    elif isinstance(error, GenerationProviderError):
        error_code = ErrorCode.GENERATION_REQUEST_FAILED
    else:
        error_code = ErrorCode.GENERATION_FAILED

    return AppException(error_code, log_context=log_context)


def _convert_rag_answer_service_error(
    error: RagAnswerServiceError,
    *,
    user_idx: int,
) -> AppException:
    """RAG 응답 계약 오류를 민감 정보 없는 공통 API 오류로 변환한다.

    구조화 출력, 본문 SOURCE-N, ``cited_source_ids`` 또는 최종 ``sources``
    순서가 일치하지 않으면 Claude 성공 응답이라도 502
    ``INVALID_GENERATION_RESPONSE``로 처리한다. 사용자·문서 범위 위반을 포함한
    다른 오케스트레이션 계약 오류는 내부 서버 오류로 처리한다.
    """

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "rag_answer_error_type": type(error).__name__,
        "rag_answer_operation": error.operation,
    }
    error_code = (
        ErrorCode.INVALID_GENERATION_RESPONSE
        if error.operation in _INVALID_GENERATION_RESPONSE_OPERATIONS
        else ErrorCode.INTERNAL_SERVER_ERROR
    )
    return AppException(error_code, log_context=log_context)


@router.post(
    "/answers",
    status_code=HTTPStatus.OK,
    response_model=ApiResponse[RagAnswerResponse],
    summary="선택 혼합 참조문서 기반 RAG 답변 생성",
    description=(
        "질문 전송 시점의 reference_file_idxs를 고정 범위로 사용한다. "
        "PDF, DOCX, PPTX, TXT, XLSX의 일반 텍스트와 OCR 청크를 함께 검색한다. "
        "lookup은 단일 검색·생성 흐름을 사용하고 synthesis는 선택 파일별 독립 "
        "검색과 부분 답변 후 검증된 결과만 종합한다. 일부 문서가 미파싱·미색인되어 "
        "검색 결과가 없거나 안전한 검색·부분 생성 실패가 발생해도 유효한 나머지 "
        "문서로 계속한다. 유효한 부분 근거가 하나도 없으면 최종 Claude 호출을 "
        "생략한다. 정상 응답의 cited_source_ids와 sources는 본문 SOURCE-N 최초 "
        "등장 순서와 같으며 실제 인용된 출처만 포함한다. 각 출처의 source_locator는 "
        "페이지, 섹션·문단·표, 슬라이드·도형, 시트·셀, 줄·문자 범위와 OCR 이미지 "
        "순번을 형식별로 반환한다."
    ),
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": ApiResponse[None],
            "description": "X-Internal-Token 누락 또는 불일치",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": (
                "참조문서 미선택 시 REFERENCE_DOCUMENT_REQUIRED, 그 밖의 "
                "user_idx, query, top_k, score_threshold 오류 시 "
                "REQUEST_VALIDATION_FAILED"
            ),
        },
        HTTPStatus.TOO_MANY_REQUESTS: {
            "model": ApiResponse[None],
            "description": "답변별 Claude 호출 횟수 또는 누적 토큰 예산 초과",
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": (
                "TEI·Qdrant·Claude 요청 거부, 외부 서비스 응답 계약 오류, "
                "SOURCE-N·cited_source_ids·sources 인용 순서 계약 위반"
            ),
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": "TEI, Qdrant 또는 Claude 생성 공급자의 일시적 사용 불가",
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "TEI 또는 Claude 요청 시간 초과",
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": "분류되지 않은 RAG 답변 오케스트레이션 또는 범위 계약 실패",
        },
    },
)
async def answer_question(
    request: RagAnswerRequest,
    rag_answer_service: RagAnswerServiceDependency,
) -> ApiResponse[RagAnswerResponse]:
    """내부 인증된 요청에 대해 선택 문서 근거 기반 답변을 반환한다.

    질문, 검색 청크, 생성 프롬프트, Claude 답변 원문 및 API Key는 이 함수의
    로그 컨텍스트나 예외 메시지에 기록하지 않는다. 하위 계층 오류는 오류 타입과
    안전한 작업 메타데이터만 포함하는 ``AppException``으로 변환한다.
    """

    try:
        response_data = await rag_answer_service.answer(request)
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
