"""PDF 전용 파일 처리 요청 스키마의 지원 형식 계약을 테스트한다."""

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from jipsa_rag.schemas.file_processing import (
    FileProcessingRequest,
    SupportedFileType,
)

_VALID_FILE_PROCESSING_REQUEST: Mapping[str, object] = {
    "file_idx": 123,
    "user_idx": 45,
    "folder_idx": 9,
    "file_name": "document.pdf",
    "file_type": "pdf",
    "download_url": (
        "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
        "files/document.pdf?X-Amz-Signature=test-signature"
    ),
    "url_expires_in": 900,
}


def test_file_processing_request_accepts_pdf() -> None:
    """텍스트 레이어 PDF 요청은 정상적인 요청 모델로 변환해야 한다."""

    request = FileProcessingRequest.model_validate(
        dict(_VALID_FILE_PROCESSING_REQUEST),
    )

    assert request.file_type is SupportedFileType.PDF
    assert request.file_name == "document.pdf"


def test_file_processing_request_normalizes_pdf_type() -> None:
    """PDF 파일 타입의 대소문자와 앞뒤 공백을 정규화해야 한다."""

    request_data = dict(_VALID_FILE_PROCESSING_REQUEST)
    request_data["file_type"] = " PDF "

    request = FileProcessingRequest.model_validate(request_data)

    assert request.file_type is SupportedFileType.PDF


@pytest.mark.parametrize(
    (
        "file_type",
        "file_name",
    ),
    [
        pytest.param(
            "txt",
            "document.txt",
            id="txt",
        ),
        pytest.param(
            "docx",
            "document.docx",
            id="docx",
        ),
        pytest.param(
            "xlsx",
            "document.xlsx",
            id="xlsx",
        ),
        pytest.param(
            "pptx",
            "document.pptx",
            id="pptx",
        ),
    ],
)
def test_file_processing_request_rejects_unsupported_file_types(
    file_type: str,
    file_name: str,
) -> None:
    """TXT, DOCX, XLSX 및 PPTX 요청을 스키마 단계에서 거부한다."""

    request_data = dict(_VALID_FILE_PROCESSING_REQUEST)
    request_data["file_type"] = file_type
    request_data["file_name"] = file_name

    with pytest.raises(ValidationError) as exception_info:
        FileProcessingRequest.model_validate(request_data)

    validation_errors = exception_info.value.errors()

    # file_type Enum 검증에서 실패해야 한다.
    #
    # 파일 다운로드나 파서 선택 단계까지 진입한 뒤 실패하는 것이 아니라
    # FastAPI 요청 본문 검증 시점에 422 응답으로 차단하기 위한 계약이다.
    assert any(
        error["loc"] == ("file_type",) and error["type"] == "enum" for error in validation_errors
    )


@pytest.mark.parametrize(
    "file_name",
    [
        pytest.param(
            "document.txt",
            id="txt-extension-disguised-as-pdf",
        ),
        pytest.param(
            "document.docx",
            id="docx-extension-disguised-as-pdf",
        ),
        pytest.param(
            "document.xlsx",
            id="xlsx-extension-disguised-as-pdf",
        ),
        pytest.param(
            "document.pptx",
            id="pptx-extension-disguised-as-pdf",
        ),
    ],
)
def test_file_processing_request_rejects_non_pdf_extension(
    file_name: str,
) -> None:
    """file_type을 pdf로 위장한 비PDF 파일명도 거부한다."""

    request_data = dict(_VALID_FILE_PROCESSING_REQUEST)
    request_data["file_type"] = "pdf"
    request_data["file_name"] = file_name

    with pytest.raises(ValidationError) as exception_info:
        FileProcessingRequest.model_validate(request_data)

    validation_errors = exception_info.value.errors()

    assert any(error["loc"] == () and error["type"] == "value_error" for error in validation_errors)
