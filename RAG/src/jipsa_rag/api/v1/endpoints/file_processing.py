"""애플리케이션 서버에서 전달한 파일 처리 요청을 접수한다."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
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


@router.post(
    "/process",
    status_code=HTTPStatus.ACCEPTED,
    response_model=ApiResponse[FileProcessingAcceptedResponse],
    summary="RAG 파일 처리 요청 접수",
    description=(
        "애플리케이션 서버에서 파일 URL과 파일 정보를 전달받아 "
        "원본 PDF 파일을 다운로드하고 파일 형식과 SHA-256 해시를 "
        "검증한 뒤 페이지별 텍스트를 추출한다."
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
            "description": ("요청값, PDF 형식, 파일 해시 또는 문서 텍스트 추출 검증 실패"),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 실패",
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 시간 초과",
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": "임시 문서 파일 읽기 또는 내부 처리 실패",
        },
    },
)
async def accept_file_processing_request(
    request: FileProcessingRequest,
    file_downloader: FileDownloaderDependency,
    document_parser_factory: DocumentParserFactoryDependency,
) -> ApiResponse[FileProcessingAcceptedResponse]:
    """원본 파일을 다운로드·검증하고 페이지별 텍스트를 추출한다."""

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
            # 따라서 파싱은 반드시 async with 블록이 유지되는 동안
            # 완료해야 하며 임시 파일 경로를 외부 계층으로 반환하면 안 된다.
            parsed_document = await document_parser.parse(
                downloaded_file.path,
            )

            # PDF 파서는 모든 원본 페이지를 ParsedDocumentUnit으로
            # 유지하므로 unit_count는 전체 페이지 수와 동일하다.
            #
            # 텍스트가 없는 빈 페이지도 page_count에는 포함되지만
            # text_unit_count에는 포함되지 않는다.
            response_data = FileProcessingAcceptedResponse(
                users_idx=request.users_idx,
                file_idx=request.file_idx,
                folder_idx=request.folder_idx,
                file_name=request.file_name,
                file_type=request.file_type,
                file_size_bytes=downloaded_file.size_bytes,
                page_count=parsed_document.unit_count,
                text_unit_count=parsed_document.text_unit_count,
            )

    except DocumentParserError as error:
        # pypdf 예외나 파일 시스템 예외를 API 응답에 직접 노출하지 않고
        # 프로젝트 공통 AppException과 ErrorCode로 변환한다.
        raise _convert_document_parser_error(
            error,
            users_idx=request.users_idx,
            file_idx=request.file_idx,
        ) from error

    # Presigned URL, 실제 파일 해시, 추출 텍스트 및 임시 파일 경로는
    # 현재 단계의 외부 응답에 포함하지 않는다.
    return ApiResponse[FileProcessingAcceptedResponse](
        success=True,
        code="FILE_PARSING_COMPLETED",
        message="File download, validation, and parsing completed.",
        data=response_data,
    )
