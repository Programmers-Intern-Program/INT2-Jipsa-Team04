"""파일 처리 요청 API의 다운로드, 파싱 및 오류 응답을 테스트한다."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.file_processing import (
    get_document_parser_factory,
    get_file_downloader,
)
from jipsa_rag.core.config import get_settings
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
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)
from jipsa_rag.infrastructure.document.parser_factory import (
    DocumentParserFactory,
)
from jipsa_rag.infrastructure.file.downloader import (
    HttpFileDownloader,
)

# API 계층 테스트에서는 실제 pypdf 파싱을 수행하지 않는다.
#
# 다운로더의 PDF Magic Byte와 해시 검증에 필요한 최소 바이트를 사용하고,
# 문서 파싱 결과는 StubPdfDocumentParser가 반환한다.
#
# 실제 PDF 구조, 암호화, 페이지 추출 및 텍스트 정규화 동작은
# PdfDocumentParser 단위 테스트에서 별도로 검증한다.
PDF_CONTENT = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"

PDF_SHA256 = hashlib.sha256(PDF_CONTENT).hexdigest()

VALID_FILE_PROCESSING_REQUEST: dict[str, object] = {
    "file_url": (
        "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
        "files/example-file.pdf?X-Amz-Signature=example"
    ),
    "users_idx": 1,
    "file_idx": 10,
    "folder_idx": 3,
    "file_name": "project-guide.pdf",
    "file_type": "pdf",
    # 대문자 해시도 요청 스키마에서 소문자로 정규화되어야 한다.
    "file_hash": PDF_SHA256.upper(),
}


class StubPdfDocumentParser:
    """파일 처리 API 테스트에서 사용하는 PDF 파서 대역."""

    def __init__(self) -> None:
        """호출 경로와 선택적으로 발생시킬 예외를 초기화한다."""

        self.error: DocumentParserError | None = None
        self.received_file_paths: list[Path] = []

    @property
    def file_type(self) -> DocumentType:
        """Factory에 등록할 문서 형식으로 PDF를 반환한다."""

        return DocumentType.PDF

    async def parse(
        self,
        file_path: Path,
    ) -> ParsedDocument:
        """임시 파일 생명주기를 확인하고 고정된 파싱 결과를 반환한다."""

        self.received_file_paths.append(file_path)

        # 파싱은 HttpFileDownloader의 async with 내부에서 실행되어야 한다.
        #
        # 이 시점에 파일이 존재하지 않으면 엔드포인트가 context 밖에서
        # 파서를 호출한 것이므로 테스트를 즉시 실패시킨다.
        assert file_path.exists()
        assert file_path.is_file()

        if self.error is not None:
            raise self.error

        return ParsedDocument(
            file_type=self.file_type,
            units=(
                ParsedDocumentUnit(
                    text="First page text.",
                    source_metadata={
                        "page_number": 1,
                    },
                ),
                # 빈 페이지도 원본 위치 보존을 위해 단위로 유지한다.
                ParsedDocumentUnit(
                    text="",
                    source_metadata={
                        "page_number": 2,
                    },
                ),
            ),
            document_metadata={
                "page_count": 2,
            },
        )


@pytest.fixture
def pdf_document_parser() -> StubPdfDocumentParser:
    """파일 처리 API에 주입할 테스트 전용 PDF 파서를 반환한다."""

    return StubPdfDocumentParser()


@pytest.fixture
def file_processing_client(
    client: TestClient,
    tmp_path: Path,
    pdf_document_parser: StubPdfDocumentParser,
) -> Iterator[TestClient]:
    """외부 네트워크와 실제 PDF 파싱 없이 파일 처리 API를 테스트한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        get_settings(),
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    parser_factory = DocumentParserFactory(
        parsers=(pdf_document_parser,),
    )

    # conftest의 client fixture에서 사용하는 실제 애플리케이션과
    # 동일한 객체에 다운로드 및 문서 파서 의존성 override를 적용한다.
    from jipsa_rag.main import app

    app.dependency_overrides[get_file_downloader] = lambda: downloader
    app.dependency_overrides[get_document_parser_factory] = lambda: parser_factory

    try:
        yield client
    finally:
        # 다른 API 테스트에 테스트용 인프라 객체가 남지 않도록
        # fixture 종료 시 모든 override를 제거한다.
        app.dependency_overrides.pop(
            get_file_downloader,
            None,
        )
        app.dependency_overrides.pop(
            get_document_parser_factory,
            None,
        )


def test_file_processing_request_returns_parsed_response(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
) -> None:
    """유효한 PDF 요청이 문서 파싱 완료 응답을 반환한다."""

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == 202
    assert response.json() == {
        "success": True,
        "code": "FILE_PARSING_COMPLETED",
        "message": "File download, validation, and parsing completed.",
        "data": {
            "users_idx": 1,
            "file_idx": 10,
            "folder_idx": 3,
            "file_name": "project-guide.pdf",
            "file_type": "PDF",
            "file_size_bytes": len(PDF_CONTENT),
            "file_hash_verified": True,
            "page_count": 2,
            "text_unit_count": 1,
            "processing_status": "PARSED",
        },
    }

    # 파서는 다운로드된 임시 파일 경로를 정확히 한 번 전달받아야 한다.
    assert len(pdf_document_parser.received_file_paths) == 1

    parsed_file_path = pdf_document_parser.received_file_paths[0]

    # 응답이 반환된 시점에는 download_and_validate() context가 종료되어
    # 임시 파일이 삭제되어야 한다.
    assert not parsed_file_path.exists()


def test_file_processing_request_allows_null_folder_idx(
    file_processing_client: TestClient,
) -> None:
    """루트 경로 파일은 Folder_IDX 없이 요청할 수 있다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["folder_idx"] = None

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    assert response.status_code == 202
    assert response.json()["data"]["folder_idx"] is None
    assert response.json()["data"]["processing_status"] == "PARSED"


def test_file_processing_request_rejects_hash_mismatch_before_parsing(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
) -> None:
    """파일 해시가 다르면 문서 파서를 호출하기 전에 오류를 반환한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_hash"] = "0" * 64

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "code": "FILE_HASH_MISMATCH",
        "message": "The downloaded file hash does not match.",
        "data": None,
    }

    # 다운로드 검증이 완료되지 않았으므로 파서를 실행하면 안 된다.
    assert pdf_document_parser.received_file_paths == []


@pytest.mark.parametrize(
    (
        "parser_error",
        "expected_status_code",
        "expected_code",
        "expected_message",
    ),
    [
        (
            UnsupportedDocumentTypeError(DocumentType.DOCX),
            415,
            "UNSUPPORTED_DOCUMENT_TYPE",
            "The document type is not supported.",
        ),
        (
            InvalidDocumentError(DocumentType.PDF),
            422,
            "INVALID_DOCUMENT",
            "The document structure is invalid.",
        ),
        (
            EncryptedDocumentError(DocumentType.PDF),
            422,
            "ENCRYPTED_DOCUMENT",
            "Encrypted documents are not supported.",
        ),
        (
            DocumentTextExtractionError(
                file_type=DocumentType.PDF,
                source_metadata={
                    "page_number": 2,
                },
            ),
            422,
            "DOCUMENT_TEXT_EXTRACTION_FAILED",
            "Text could not be extracted from the document.",
        ),
        (
            DocumentTextNotFoundError(DocumentType.PDF),
            422,
            "DOCUMENT_TEXT_NOT_FOUND",
            "No extractable text was found in the document.",
        ),
        (
            DocumentFileNotFoundError(Path("missing-document.pdf")),
            500,
            "DOCUMENT_READ_FAILED",
            "The document could not be read.",
        ),
        (
            DocumentReadError(Path("unreadable-document.pdf")),
            500,
            "DOCUMENT_READ_FAILED",
            "The document could not be read.",
        ),
    ],
    ids=[
        "unsupported-document-type",
        "invalid-document",
        "encrypted-document",
        "text-extraction-failed",
        "text-not-found",
        "document-file-not-found",
        "document-read-failed",
    ],
)
def test_file_processing_request_maps_document_parser_error(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
    parser_error: DocumentParserError,
    expected_status_code: int,
    expected_code: str,
    expected_message: str,
) -> None:
    """문서 파서 예외를 공통 API 오류 응답으로 변환한다."""

    pdf_document_parser.error = parser_error

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == expected_status_code
    assert response.json() == {
        "success": False,
        "code": expected_code,
        "message": expected_message,
        "data": None,
    }

    # 파싱 실패 시에도 파서는 async with 내부에서 실행되어야 한다.
    assert len(pdf_document_parser.received_file_paths) == 1

    parsed_file_path = pdf_document_parser.received_file_paths[0]

    # 파서가 예외를 발생시켜도 다운로더의 finally가 실행되어
    # 다운로드한 임시 파일이 남지 않아야 한다.
    assert not parsed_file_path.exists()


def test_file_processing_request_rejects_invalid_file_hash(
    file_processing_client: TestClient,
) -> None:
    """64자리 SHA-256 형식이 아닌 파일 해시를 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_hash"] = "invalid-hash"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["message"] == "Request validation failed."
    assert body["data"]["errors"][0]["field"] == "body.file_hash"


def test_file_processing_request_rejects_non_https_url(
    file_processing_client: TestClient,
) -> None:
    """HTTPS가 아닌 파일 URL을 요청 검증 단계에서 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_url"] = "http://example-bucket.s3.ap-northeast-2.amazonaws.com/file.pdf"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["data"]["errors"][0]["field"] == "body.file_url"


def test_file_processing_request_rejects_unsupported_file_type(
    file_processing_client: TestClient,
) -> None:
    """현재 요청 스키마에서 허용하지 않는 파일 타입을 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_name"] = "project-guide.docx"
    request_body["file_type"] = "DOCX"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"


def test_file_processing_request_rejects_pdf_without_pdf_extension(
    file_processing_client: TestClient,
) -> None:
    """PDF 타입과 파일명 확장자가 일치하지 않으면 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_name"] = "project-guide.txt"
    request_body["file_type"] = "PDF"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"


def test_file_processing_request_rejects_file_name_with_path(
    file_processing_client: TestClient,
) -> None:
    """디렉터리 경로가 포함된 파일명을 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_name"] = "../project-guide.pdf"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["data"]["errors"][0]["field"] == "body.file_name"


def test_file_processing_request_rejects_non_positive_identifiers(
    file_processing_client: TestClient,
) -> None:
    """0 이하의 사용자 및 파일 식별자를 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["users_idx"] = 0
    request_body["file_idx"] = -1

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()
    invalid_fields = {error["field"] for error in body["data"]["errors"]}

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert "body.users_idx" in invalid_fields
    assert "body.file_idx" in invalid_fields


def test_file_processing_request_rejects_unknown_field(
    file_processing_client: TestClient,
) -> None:
    """API 계약에 정의되지 않은 요청 필드를 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["unknown_field"] = "unexpected-value"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["data"]["errors"][0]["field"] == "body.unknown_field"
