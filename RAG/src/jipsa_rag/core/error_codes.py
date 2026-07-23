from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """외부 API 오류 응답을 구성하기 위한 고정 오류 정의."""

    status_code: int
    code: str
    message: str


class ErrorCode(Enum):
    """Jipsa RAG 서비스에서 공통으로 사용하는 오류 코드."""

    INVALID_REQUEST = ErrorDefinition(
        status_code=HTTPStatus.BAD_REQUEST,
        code="INVALID_REQUEST",
        message="The request is invalid.",
    )

    INVALID_FILE_URL = ErrorDefinition(
        status_code=HTTPStatus.BAD_REQUEST,
        code="INVALID_FILE_URL",
        message="The file URL is invalid.",
    )

    UNAUTHORIZED = ErrorDefinition(
        status_code=HTTPStatus.UNAUTHORIZED,
        code="UNAUTHORIZED",
        message="Authentication is required.",
    )

    FORBIDDEN = ErrorDefinition(
        status_code=HTTPStatus.FORBIDDEN,
        code="FORBIDDEN",
        message="You do not have permission to access this resource.",
    )

    RESOURCE_NOT_FOUND = ErrorDefinition(
        status_code=HTTPStatus.NOT_FOUND,
        code="RESOURCE_NOT_FOUND",
        message="The requested resource was not found.",
    )

    METHOD_NOT_ALLOWED = ErrorDefinition(
        status_code=HTTPStatus.METHOD_NOT_ALLOWED,
        code="METHOD_NOT_ALLOWED",
        message="The requested HTTP method is not allowed.",
    )

    CONFLICT = ErrorDefinition(
        status_code=HTTPStatus.CONFLICT,
        code="CONFLICT",
        message="The request conflicts with the current resource state.",
    )

    # 문서 파서가 등록되지 않은 파일 형식을 요청한 경우 사용한다.
    #
    # 현재 요청 스키마는 PDF만 허용하지만 DOCX, XLSX 및 PPTX가
    # 순차적으로 추가될 때 Factory 등록 여부를 명확하게 표현하기 위해
    # API 오류 코드도 문서 파서 기준으로 분리한다.
    UNSUPPORTED_DOCUMENT_TYPE = ErrorDefinition(
        status_code=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        code="UNSUPPORTED_DOCUMENT_TYPE",
        message="The document type is not supported.",
    )

    # 다운로드 형식 검증은 통과했지만 문서 내부 구조가 손상되어
    # 형식별 파서가 문서로 해석하지 못한 경우 사용한다.
    INVALID_DOCUMENT = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="INVALID_DOCUMENT",
        message="The document structure is invalid.",
    )

    # 현재 파일 처리 요청에는 문서 비밀번호가 포함되지 않으므로
    # 암호화 문서는 처리할 수 없는 입력으로 구분한다.
    ENCRYPTED_DOCUMENT = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="ENCRYPTED_DOCUMENT",
        message="Encrypted documents are not supported.",
    )

    # 문서 구조는 읽었지만 특정 페이지 등의 원본 위치에서
    # 텍스트 추출이 실패한 경우 사용한다.
    DOCUMENT_TEXT_EXTRACTION_FAILED = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="DOCUMENT_TEXT_EXTRACTION_FAILED",
        message="Text could not be extracted from the document.",
    )

    # 이미지 기반 스캔 PDF처럼 문서 전체에서 검색 가능한
    # 텍스트 레이어가 발견되지 않은 경우 사용한다.
    DOCUMENT_TEXT_NOT_FOUND = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="DOCUMENT_TEXT_NOT_FOUND",
        message="No extractable text was found in the document.",
    )

    # 검증이 끝난 임시 파일이 사라졌거나 파일 시스템 문제로
    # 문서 바이트를 읽지 못한 경우 사용한다.
    #
    # 정상적인 사용자 입력 오류가 아니라 서버 내부 파일 처리
    # 생명주기 또는 파일 시스템 문제이므로 500 응답으로 처리한다.
    DOCUMENT_READ_FAILED = ErrorDefinition(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="DOCUMENT_READ_FAILED",
        message="The document could not be read.",
    )

    # 파싱된 문서에는 텍스트가 존재하지만 청킹 정책을 적용한 뒤
    # 검색에 사용할 수 있는 청크를 하나도 생성하지 못한 경우 사용한다.
    DOCUMENT_CHUNKS_NOT_FOUND = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="DOCUMENT_CHUNKS_NOT_FOUND",
        message=("No searchable text chunks could be created from the document."),
    )

    # 청크 크기와 중첩 크기 같은 서버 내부 청킹 설정이 잘못되었거나
    # 예상하지 못한 청킹 처리 실패가 발생한 경우 사용한다.
    DOCUMENT_CHUNKING_FAILED = ErrorDefinition(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="DOCUMENT_CHUNKING_FAILED",
        message="The document could not be chunked.",
    )

    # TEI 서버가 설정된 제한 시간 안에 응답하지 않은 경우 사용한다.
    EMBEDDING_SERVICE_TIMEOUT = ErrorDefinition(
        status_code=HTTPStatus.GATEWAY_TIMEOUT,
        code="EMBEDDING_SERVICE_TIMEOUT",
        message="The embedding service request timed out.",
    )

    # TEI 서버에 연결할 수 없거나 과부하, 429 또는 5xx 응답으로
    # 현재 임베딩 서비스를 정상적으로 사용할 수 없는 경우 사용한다.
    EMBEDDING_SERVICE_UNAVAILABLE = ErrorDefinition(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        code="EMBEDDING_SERVICE_UNAVAILABLE",
        message="The embedding service is temporarily unavailable.",
    )

    # TEI 서버가 RAG 서버에서 생성한 요청을 4xx 응답으로 거부한 경우다.
    #
    # 사용자 요청 스키마가 아니라 내부 서비스 간 요청 문제이므로
    # 클라이언트의 4xx 오류가 아닌 502 Bad Gateway로 변환한다.
    EMBEDDING_REQUEST_REJECTED = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="EMBEDDING_REQUEST_REJECTED",
        message="The embedding service rejected the request.",
    )

    # TEI 서버가 성공 상태를 반환했지만 응답 JSON, 벡터 개수,
    # 벡터 차원 또는 벡터 값이 계약과 일치하지 않는 경우 사용한다.
    INVALID_EMBEDDING_RESPONSE = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="INVALID_EMBEDDING_RESPONSE",
        message="The embedding service returned an invalid response.",
    )

    # 명시적으로 분류하지 못한 임베딩 계층 오류가 발생한 경우 사용한다.
    EMBEDDING_GENERATION_FAILED = ErrorDefinition(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="EMBEDDING_GENERATION_FAILED",
        message="The document embeddings could not be generated.",
    )

    # RAG_Document, RAG_Chunk 또는 RAG_Index_Run 저장과
    # 최종 상태 변경에 실패한 경우 사용한다.
    #
    # DB 드라이버 오류나 SQL 문은 외부 응답에 직접 노출하지 않는다.
    LOCAL_RAG_STORAGE_FAILED = ErrorDefinition(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="LOCAL_RAG_STORAGE_FAILED",
        message=("The document index could not be stored in the Local RAG database."),
    )

    # Qdrant 연결 실패, 시간 초과, 429 또는 5xx처럼
    # 일시적으로 VectorDB를 사용할 수 없는 경우 사용한다.
    VECTOR_DATABASE_UNAVAILABLE = ErrorDefinition(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        code="VECTOR_DATABASE_UNAVAILABLE",
        message="The vector database is temporarily unavailable.",
    )

    # Qdrant가 요청을 거부했거나 임베딩 모델·차원 설정이
    # Collection 계약과 일치하지 않아 벡터를 저장하지 못한 경우 사용한다.
    VECTOR_STORAGE_FAILED = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="VECTOR_STORAGE_FAILED",
        message="The document vectors could not be stored.",
    )

    # Qdrant가 관련 청크 검색 요청을 거부했거나 질의 임베딩 모델·차원이
    # 현재 Collection 계약과 일치하지 않는 경우 사용한다.
    #
    # 저장 실패와 검색 실패를 같은 오류 코드로 합치면 애플리케이션 서버가
    # 재시도 또는 장애 분석 시 어느 단계에서 실패했는지 구분할 수 없으므로
    # 검색 전용 오류 코드를 별도로 정의한다.
    VECTOR_SEARCH_FAILED = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="VECTOR_SEARCH_FAILED",
        message="The vector search request could not be completed.",
    )

    # Qdrant 검색은 성공했지만 payload 필드, 사용자 범위, 활성 상태,
    # Point ID, 임베딩 모델 또는 결과 정렬 계약이 일치하지 않는 경우 사용한다.
    #
    # 청크 원문이나 잘못된 payload 값은 외부 메시지에 포함하지 않는다.
    INVALID_VECTOR_SEARCH_RESULT = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="INVALID_VECTOR_SEARCH_RESULT",
        message="The vector database returned an invalid search result.",
    )

    FILE_TOO_LARGE = ErrorDefinition(
        status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        code="FILE_TOO_LARGE",
        message="The file exceeds the maximum allowed size.",
    )

    REQUEST_VALIDATION_FAILED = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="REQUEST_VALIDATION_FAILED",
        message="Request validation failed.",
    )

    INVALID_FILE = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="INVALID_FILE",
        message="The downloaded file is invalid.",
    )

    FILE_HASH_MISMATCH = ErrorDefinition(
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="FILE_HASH_MISMATCH",
        message="The downloaded file hash does not match.",
    )

    UNSUPPORTED_FILE_MEDIA_TYPE = ErrorDefinition(
        status_code=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        code="UNSUPPORTED_FILE_MEDIA_TYPE",
        message="The downloaded file type is not supported.",
    )

    TOO_MANY_REQUESTS = ErrorDefinition(
        status_code=HTTPStatus.TOO_MANY_REQUESTS,
        code="TOO_MANY_REQUESTS",
        message="Too many requests have been received.",
    )

    FILE_DOWNLOAD_FAILED = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="FILE_DOWNLOAD_FAILED",
        message="The source file could not be downloaded.",
    )

    # 애플리케이션 서버 manifest 또는 완료 콜백 요청이
    # 설정된 제한 시간 안에 완료되지 않은 경우 사용한다.
    APPLICATION_SERVER_TIMEOUT = ErrorDefinition(
        status_code=HTTPStatus.GATEWAY_TIMEOUT,
        code="APPLICATION_SERVER_TIMEOUT",
        message="The application server request timed out.",
    )

    # 애플리케이션 서버 연결 실패, 429 또는 5xx 응답으로
    # 현재 내부 API를 정상적으로 사용할 수 없는 경우 사용한다.
    APPLICATION_SERVER_UNAVAILABLE = ErrorDefinition(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        code="APPLICATION_SERVER_UNAVAILABLE",
        message="The application server is temporarily unavailable.",
    )

    # 애플리케이션 서버가 내부 토큰, IP allowlist, 요청값 또는
    # 예상 상태 코드 문제로 RAG 요청을 거부한 경우 사용한다.
    APPLICATION_SERVER_REQUEST_REJECTED = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="APPLICATION_SERVER_REQUEST_REJECTED",
        message="The application server rejected the internal request.",
    )

    # manifest 응답이 JSON 또는 RAG 요청 스키마와 일치하지 않는 경우
    # 애플리케이션 서버와 RAG 사이의 계약 오류로 처리한다.
    INVALID_APPLICATION_SERVER_RESPONSE = ErrorDefinition(
        status_code=HTTPStatus.BAD_GATEWAY,
        code="INVALID_APPLICATION_SERVER_RESPONSE",
        message="The application server returned an invalid response.",
    )

    SERVICE_UNAVAILABLE = ErrorDefinition(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        code="SERVICE_UNAVAILABLE",
        message="The service is temporarily unavailable.",
    )

    FILE_DOWNLOAD_TIMEOUT = ErrorDefinition(
        status_code=HTTPStatus.GATEWAY_TIMEOUT,
        code="FILE_DOWNLOAD_TIMEOUT",
        message="The source file download timed out.",
    )

    INTERNAL_SERVER_ERROR = ErrorDefinition(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="INTERNAL_SERVER_ERROR",
        message="An internal server error occurred.",
    )

    @property
    def status_code(self) -> int:
        """예외에 대응하는 HTTP 상태 코드를 반환한다."""

        return int(self.value.status_code)

    @property
    def code(self) -> str:
        """외부 응답에서 사용할 오류 코드 문자열을 반환한다."""

        return self.value.code

    @property
    def message(self) -> str:
        """외부 응답에서 사용할 기본 오류 메시지를 반환한다."""

        return self.value.message
