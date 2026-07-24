"""외부 인프라 오류를 Jipsa RAG 공통 API 오류로 변환한다."""

from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingError,
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
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
    InvalidVectorSearchResultError,
    VectorCollectionConfigurationError,
    VectorDatabaseError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)


def convert_embedding_error(
    error: EmbeddingError,
    *,
    user_idx: int,
) -> AppException:
    """질의 임베딩 오류를 외부 공개 가능한 공통 API 오류로 변환한다.

    사용자 질의 원문, TEI 응답 본문과 임베딩 벡터는 로그 컨텍스트나
    외부 응답에 포함하지 않는다. 진단에는
    오류 유형과 안전한 상태 코드만 사용한다.
    """

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
        # reason은 벡터 개수, 차원 또는 값 타입과 같이
        # TeiQueryEmbedder가 생성한 안전한 검증 정보만 포함한다.
        log_context["embedding_response_reason"] = error.reason
        log_context["embedding_batch_start_index"] = error.batch_start_index
        error_code = ErrorCode.INVALID_EMBEDDING_RESPONSE

    else:
        error_code = ErrorCode.EMBEDDING_GENERATION_FAILED

    return AppException(
        error_code,
        log_context=log_context,
    )


def convert_vector_search_error(
    error: VectorDatabaseError,
    *,
    user_idx: int,
) -> AppException:
    """Qdrant 검색 오류를 재시도 가능성과 계약 위반 유형으로 분류한다.

    Qdrant 응답 본문, payload, 청크 원문과 질의 벡터는 외부 응답 또는
    구조화 로그에 복사하지 않는다. 저장소 예외가 보관한 작업명과 HTTP
    상태 코드만 안전한 진단 정보로 사용한다.
    """

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "vector_search_error_type": type(error).__name__,
        "vector_operation": error.operation,
    }

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

    return AppException(
        error_code,
        log_context=log_context,
    )


def convert_generation_error(
    error: GenerationError,
    *,
    user_idx: int,
) -> AppException:
    """Claude 생성 오류를 비밀값 없이 공통 API 오류로 변환한다.

    프롬프트, 생성 응답 원문, API Key 및 Anthropic 응답 본문은 외부 응답이나
    로그 컨텍스트에 포함하지 않는다. 공급자 이름과 안전한 상태 코드,
    내부에서 정의한 오류 분류만 진단 정보로 사용한다.
    """

    log_context: dict[str, str | int] = {
        "user_idx": user_idx,
        "generation_error_type": type(error).__name__,
    }

    if isinstance(error, GenerationProviderError):
        log_context["generation_provider"] = error.provider

        if error.status_code is not None:
            log_context["generation_status_code"] = error.status_code

        if error.request_id is not None:
            log_context["generation_request_id"] = error.request_id

    if isinstance(error, GenerationAuthenticationError):
        log_context["generation_failure_reason"] = "provider_authentication"

        return AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            public_message="The generation service is temporarily unavailable.",
            log_context=log_context,
        )

    if isinstance(error, GenerationRateLimitError):
        log_context["generation_failure_reason"] = "provider_rate_limit"

        return AppException(
            ErrorCode.TOO_MANY_REQUESTS,
            public_message="The generation service is temporarily rate limited.",
            log_context=log_context,
        )

    if isinstance(error, GenerationTimeoutError):
        log_context["generation_failure_reason"] = "provider_timeout"

        return AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            public_message="The generation service request timed out.",
            log_context=log_context,
        )

    if isinstance(error, GenerationServerError):
        log_context["generation_failure_reason"] = "provider_server_error"

        return AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            public_message="The generation service is temporarily unavailable.",
            log_context=log_context,
        )

    if isinstance(error, InvalidGenerationResponseError):
        log_context["generation_failure_reason"] = "invalid_provider_response"
        log_context["generation_response_reason"] = error.reason

        return AppException(
            ErrorCode.INTERNAL_SERVER_ERROR,
            public_message="The generation service returned an invalid response.",
            log_context=log_context,
        )

    if isinstance(error, GenerationProviderError):
        log_context["generation_failure_reason"] = "provider_request_failed"

        return AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            public_message="The generation request could not be completed.",
            log_context=log_context,
        )

    log_context["generation_failure_reason"] = "unclassified_generation_error"

    return AppException(
        ErrorCode.INTERNAL_SERVER_ERROR,
        log_context=log_context,
    )
