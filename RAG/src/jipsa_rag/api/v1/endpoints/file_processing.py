"""애플리케이션 서버에서 전달한 파일 처리 요청을 접수한다."""

from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.chunking.character import CharacterTextChunker
from jipsa_rag.infrastructure.chunking.exceptions import (
    DocumentChunkingError,
    InvalidChunkingConfigurationError,
    NoDocumentChunksError,
)
from jipsa_rag.infrastructure.chunking.models import ChunkingContext
from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentParserError,
    DocumentReadError,
    DocumentTextExtractionError,
    DocumentTextNotFoundError,
    EncryptedDocumentError,
    InvalidDocumentError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.parser_factory import (
    DocumentParserFactory,
)
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingError,
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.embedding.tei import TeiChunkEmbedder
from jipsa_rag.infrastructure.file.downloader import (
    HttpFileDownloader,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
)
from jipsa_rag.schemas.file_processing import (
    FileProcessingAcceptedResponse,
    FileProcessingRequest,
)

router = APIRouter(
    prefix="/files",
    tags=["File Processing"],
)

# 동일한 파일과 동일한 청킹 정책을 다시 처리할 때
# 결정적인 Chunk ID를 생성하기 위한 초기 색인 버전이다.
#
# 청킹 정책이나 Chunk ID 생성 규칙을 변경할 경우 기존 벡터와
# 충돌하지 않도록 이 버전도 함께 증가시켜야 한다.
_DEFAULT_INDEX_VERSION: Final[int] = 1


# 파일 처리 엔드포인트에서 사용할 Settings 의존성이다.
#
# get_settings()는 환경 설정 객체를 캐싱하므로
# 요청마다 dotenv 파일을 다시 읽지 않는다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


def get_file_downloader(
    settings: SettingsDependency,
) -> HttpFileDownloader:
    """현재 환경 설정이 적용된 파일 다운로더를 생성한다."""

    return HttpFileDownloader(settings)


# 테스트에서는 get_file_downloader 의존성을 교체하여
# 실제 외부 네트워크 요청 없이 API 동작을 검증한다.
FileDownloaderDependency = Annotated[
    HttpFileDownloader,
    Depends(get_file_downloader),
]


def get_document_parser_factory() -> DocumentParserFactory:
    """현재 구현이 완료된 문서 파서가 등록된 Factory를 생성한다.

    DocumentParserFactory의 기본 생성자는 현재 PdfDocumentParser만
    등록한다.

    DOCX, XLSX 및 PPTX 파서 구현이 완료되면 Factory의 기본 등록
    목록만 확장하면 되며 파일 처리 API는 변경하지 않아도 된다.

    테스트에서는 이 의존성을 교체하여 실제 pypdf 실행 없이
    파서 선택, 호출 및 예외 변환 동작을 검증할 수 있다.
    """

    return DocumentParserFactory()


# 구체적인 PdfDocumentParser를 엔드포인트에 직접 주입하지 않고
# Factory를 주입하여 요청의 file_type에 따라 파서를 선택한다.
DocumentParserFactoryDependency = Annotated[
    DocumentParserFactory,
    Depends(get_document_parser_factory),
]


def get_document_chunker() -> CharacterTextChunker:
    """현재 문서 청킹 정책이 적용된 문자 기반 청커를 생성한다.

    CharacterTextChunker의 기본 정책은 청크당 최대 1,000자와
    최대 200자 중첩이다.

    청커를 의존성으로 분리하여 테스트에서는 실제 문자 분할 로직과
    관계없이 API의 단계 연결과 예외 변환을 검증할 수 있다.
    """

    return CharacterTextChunker()


DocumentChunkerDependency = Annotated[
    CharacterTextChunker,
    Depends(get_document_chunker),
]


def get_chunk_embedder(
    settings: SettingsDependency,
) -> TeiChunkEmbedder:
    """현재 환경 설정이 적용된 TEI 청크 임베딩 생성기를 생성한다."""

    return TeiChunkEmbedder(settings)


# 실제 TEI 서버 통신은 임베딩 인프라 단위 테스트에서 검증한다.
#
# API 테스트에서는 이 의존성을 Stub으로 교체하여 다운로드, 파싱,
# 청킹 및 임베딩 단계의 호출 순서와 오류 응답만 검증한다.
ChunkEmbedderDependency = Annotated[
    TeiChunkEmbedder,
    Depends(get_chunk_embedder),
]


def _convert_document_parser_error(
    error: DocumentParserError,
    *,
    users_idx: int,
    file_idx: int,
) -> AppException:
    """문서 파서 계층의 예외를 공통 애플리케이션 예외로 변환한다.

    문서 파서는 FastAPI 및 HTTP 응답 구조에 의존하지 않는다.
    API 경계에서 파서 예외를 AppException으로 변환하여
    인프라 계층과 API 계층의 책임을 분리한다.

    임시 파일 전체 경로, Presigned URL 및 파일 해시는
    내부 로그 컨텍스트에도 포함하지 않는다.
    """

    log_context: dict[str, str | int] = {
        "users_idx": users_idx,
        "file_idx": file_idx,
        "document_error_type": type(error).__name__,
    }

    if isinstance(error, UnsupportedDocumentTypeError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.UNSUPPORTED_DOCUMENT_TYPE

    elif isinstance(error, EncryptedDocumentError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.ENCRYPTED_DOCUMENT

    elif isinstance(error, InvalidDocumentError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.INVALID_DOCUMENT

    elif isinstance(error, DocumentTextExtractionError):
        log_context["file_type"] = str(error.file_type)

        # 현재 PDF 파서는 텍스트 추출 실패 위치로 page_number를 전달한다.
        #
        # 향후 DOCX, XLSX 및 PPTX가 추가되면 paragraph_index,
        # sheet_name, slide_number 등의 안전한 위치 정보도
        # 필요한 범위에서 별도로 매핑한다.
        page_number = error.source_metadata.get("page_number")

        if isinstance(page_number, int):
            log_context["page_number"] = page_number

        error_code = ErrorCode.DOCUMENT_TEXT_EXTRACTION_FAILED

    elif isinstance(error, DocumentTextNotFoundError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.DOCUMENT_TEXT_NOT_FOUND

    elif isinstance(
        error,
        (
            DocumentFileNotFoundError,
            DocumentReadError,
        ),
    ):
        # 다운로드 검증이 끝난 임시 파일은 async with 블록 안에서
        # 존재해야 한다.
        #
        # 해당 시점에 파일이 사라졌거나 읽을 수 없다면 사용자 입력보다
        # 서버 내부 파일 생명주기 또는 파일 시스템 문제에 해당한다.
        error_code = ErrorCode.DOCUMENT_READ_FAILED

    else:
        # 새로운 DocumentParserError 하위 예외가 추가되었지만
        # 아직 명시적인 변환 규칙이 없는 경우 내부 구현 정보를
        # 노출하지 않고 공통 서버 오류로 처리한다.
        error_code = ErrorCode.INTERNAL_SERVER_ERROR

    return AppException(
        error_code,
        log_context=log_context,
    )


def _convert_document_chunking_error(
    error: DocumentChunkingError,
    *,
    users_idx: int,
    file_idx: int,
) -> AppException:
    """문서 청킹 계층의 예외를 공통 애플리케이션 예외로 변환한다."""

    log_context: dict[str, str | int] = {
        "users_idx": users_idx,
        "file_idx": file_idx,
        "chunking_error_type": type(error).__name__,
    }

    if isinstance(error, NoDocumentChunksError):
        # 파일 내용이나 청크 원문은 기록하지 않고
        # 문서 형식만 안전한 진단 정보로 사용한다.
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.DOCUMENT_CHUNKS_NOT_FOUND

    elif isinstance(error, InvalidChunkingConfigurationError):
        # 청크 크기와 중첩 크기는 비밀값이나 문서 내용이 아니므로
        # 서버 설정 오류를 진단하기 위한 로그 컨텍스트로 사용할 수 있다.
        log_context["chunk_size_chars"] = error.chunk_size_chars
        log_context["chunk_overlap_chars"] = error.chunk_overlap_chars
        error_code = ErrorCode.DOCUMENT_CHUNKING_FAILED

    else:
        # 새 청킹 예외가 추가되었지만 명시적인 변환 규칙이 없으면
        # 내부 구현 정보를 노출하지 않는 공통 청킹 실패로 변환한다.
        error_code = ErrorCode.DOCUMENT_CHUNKING_FAILED

    return AppException(
        error_code,
        log_context=log_context,
    )


def _convert_embedding_error(
    error: EmbeddingError,
    *,
    users_idx: int,
    file_idx: int,
) -> AppException:
    """임베딩 계층의 예외를 공통 애플리케이션 예외로 변환한다.

    청크 원문, TEI 응답 본문 및 임베딩 벡터는
    외부 응답이나 내부 로그 컨텍스트에 포함하지 않는다.
    """

    log_context: dict[str, str | int] = {
        "users_idx": users_idx,
        "file_idx": file_idx,
        "embedding_error_type": type(error).__name__,
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
        # reason에는 벡터 개수, 차원 또는 값 타입처럼
        # TeiChunkEmbedder가 생성한 안전한 검증 정보만 포함된다.
        log_context["embedding_response_reason"] = error.reason
        log_context["embedding_batch_start_index"] = error.batch_start_index
        error_code = ErrorCode.INVALID_EMBEDDING_RESPONSE

    else:
        # 새 EmbeddingError 하위 예외가 추가되었지만 변환 규칙이 없으면
        # 구체적인 내부 내용을 공개하지 않는 일반 생성 실패로 처리한다.
        error_code = ErrorCode.EMBEDDING_GENERATION_FAILED

    return AppException(
        error_code,
        log_context=log_context,
    )


@router.post(
    "/process",
    status_code=HTTPStatus.ACCEPTED,
    response_model=ApiResponse[FileProcessingAcceptedResponse],
    summary="RAG 파일 처리 요청 접수",
    description=(
        "애플리케이션 서버에서 파일 URL과 파일 정보를 전달받아 "
        "원본 PDF 파일을 다운로드하고 파일 형식과 SHA-256 해시를 "
        "검증한 뒤 페이지별 텍스트 추출, 텍스트 청킹 및 "
        "청크별 임베딩 생성을 수행한다."
    ),
    responses={
        HTTPStatus.BAD_REQUEST: {
            "model": ApiResponse[None],
            "description": "다운로드 URL 검증 실패",
        },
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE: {
            "model": ApiResponse[None],
            "description": "최대 허용 파일 크기 초과",
        },
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: {
            "model": ApiResponse[None],
            "description": "지원하지 않는 MIME 유형 또는 문서 파서",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": (
                "요청값, PDF 형식, 파일 해시, 문서 텍스트 또는 검색 가능 청크 검증 실패"
            ),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 또는 임베딩 서비스 응답 실패",
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": "임베딩 서비스 연결 실패 또는 일시적 사용 불가",
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 또는 임베딩 요청 시간 초과",
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": "문서 읽기, 청킹, 임베딩 또는 내부 처리 실패",
        },
    },
)
async def accept_file_processing_request(
    request: FileProcessingRequest,
    file_downloader: FileDownloaderDependency,
    document_parser_factory: DocumentParserFactoryDependency,
    document_chunker: DocumentChunkerDependency,
    chunk_embedder: ChunkEmbedderDependency,
) -> ApiResponse[FileProcessingAcceptedResponse]:
    """원본 파일을 다운로드한 뒤 파싱, 청킹 및 임베딩을 수행한다."""

    try:
        # API는 PdfDocumentParser와 같은 구체 구현체를 직접 선택하지 않는다.
        #
        # 요청 파일 형식을 Factory에 전달하면 현재 등록된
        # DocumentParser 구현체가 반환된다.
        #
        # 지원하지 않는 형식이면 네트워크 다운로드 전에 실패하므로
        # 불필요한 외부 요청과 임시 파일 생성을 방지할 수 있다.
        document_parser = document_parser_factory.get_parser(
            request.file_type,
        )

        async with file_downloader.download_and_validate(
            # Presigned URL은 변환하거나 로그에 남기지 않고
            # 애플리케이션 서버가 전달한 원문을 그대로 사용한다.
            file_url=request.file_url,
            expected_sha256=request.file_hash,
            users_idx=request.users_idx,
            file_idx=request.file_idx,
        ) as downloaded_file:
            # HttpFileDownloader의 context가 종료되면 임시 파일은
            # 성공 여부와 관계없이 삭제된다.
            #
            # 따라서 파일 경로가 필요한 파싱 단계만 async with 블록에서
            # 수행하고 파싱 결과는 메모리 모델로 반환받는다.
            parsed_document = await document_parser.parse(
                downloaded_file.path,
            )

            # async with 블록 종료 이후에도 응답에 사용할 수 있도록
            # 임시 파일과 무관한 파일 크기만 별도 값으로 보관한다.
            file_size_bytes = downloaded_file.size_bytes

        # 파싱이 완료되면 원본 임시 파일은 더 이상 필요하지 않다.
        #
        # TEI 요청이 지연되더라도 임시 파일을 불필요하게 유지하지 않도록
        # 청킹과 임베딩은 다운로드 context가 종료된 뒤 수행한다.
        chunked_document = await document_chunker.chunk(
            document=parsed_document,
            context=ChunkingContext(
                users_idx=request.users_idx,
                file_idx=request.file_idx,
                file_hash=request.file_hash,
                index_version=_DEFAULT_INDEX_VERSION,
            ),
        )

        embedded_document = await chunk_embedder.embed(
            document=chunked_document,
        )

        # 현재 단계에서는 임베딩 결과를 DB 또는 Qdrant에 저장하지 않는다.
        #
        # 외부 응답에는 청크 원문과 벡터를 노출하지 않고
        # 처리 결과를 확인할 수 있는 개수와 모델 메타데이터만 포함한다.
        response_data = FileProcessingAcceptedResponse(
            users_idx=request.users_idx,
            file_idx=request.file_idx,
            folder_idx=request.folder_idx,
            file_name=request.file_name,
            file_type=request.file_type,
            file_size_bytes=file_size_bytes,
            page_count=parsed_document.unit_count,
            text_unit_count=parsed_document.text_unit_count,
            chunk_count=embedded_document.chunk_count,
            embedding_model=embedded_document.embedding_model,
            embedding_dim=embedded_document.embedding_dim,
        )

    except DocumentParserError as error:
        # pypdf 예외나 파일 시스템 예외를 API 응답에 직접 노출하지 않고
        # 프로젝트 공통 AppException과 ErrorCode로 변환한다.
        raise _convert_document_parser_error(
            error,
            users_idx=request.users_idx,
            file_idx=request.file_idx,
        ) from error

    except DocumentChunkingError as error:
        # 청크 원문이나 파일 해시를 노출하지 않고
        # 청킹 계층 예외를 공통 API 오류로 변환한다.
        raise _convert_document_chunking_error(
            error,
            users_idx=request.users_idx,
            file_idx=request.file_idx,
        ) from error

    except EmbeddingError as error:
        # TEI 응답 본문과 벡터를 노출하지 않고
        # 임베딩 계층 예외를 공통 API 오류로 변환한다.
        raise _convert_embedding_error(
            error,
            users_idx=request.users_idx,
            file_idx=request.file_idx,
        ) from error

    # Presigned URL, 실제 파일 해시, 추출 텍스트, 청크 원문,
    # 임베딩 벡터 및 임시 파일 경로는 외부 응답에 포함하지 않는다.
    return ApiResponse[FileProcessingAcceptedResponse](
        success=True,
        code="FILE_EMBEDDING_COMPLETED",
        message="File download, parsing, chunking, and embedding completed.",
        data=response_data,
    )
