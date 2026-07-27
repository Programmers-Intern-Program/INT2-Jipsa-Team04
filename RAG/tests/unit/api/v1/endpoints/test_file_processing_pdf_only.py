"""파일 처리 API 요청 스키마가 다섯 문서 형식을 허용하는지 테스트한다."""

import pytest
from fastapi.testclient import TestClient

from jipsa_rag.schemas.file_processing import SupportedFileType

_VALID_REQUEST: dict[str, object] = {
    "file_idx": 123,
    "user_idx": 45,
    "folder_idx": 9,
    "file_name": "document.pdf",
    "file_type": "pdf",
    "download_url": (
        "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
        "files/document?X-Amz-Signature=test-signature"
    ),
    "url_expires_in": 900,
}


def test_supported_file_type_enum_contains_all_document_formats() -> None:
    assert tuple(SupportedFileType) == (
        SupportedFileType.PDF,
        SupportedFileType.DOCX,
        SupportedFileType.PPTX,
        SupportedFileType.TXT,
        SupportedFileType.XLSX,
    )


@pytest.mark.parametrize(
    ("file_type", "file_name"),
    [
        pytest.param("pdf", "document.pdf", id="pdf"),
        pytest.param("docx", "document.docx", id="docx"),
        pytest.param("pptx", "document.pptx", id="pptx"),
        pytest.param("txt", "document.txt", id="txt"),
        pytest.param("xlsx", "document.xlsx", id="xlsx"),
    ],
)
def test_supported_requests_pass_request_validation(
    client: TestClient,
    file_type: str,
    file_name: str,
) -> None:
    request_body = {
        **_VALID_REQUEST,
        "file_type": file_type,
        "file_name": file_name,
    }

    # 의존 서비스가 테스트에서 stub 처리되지 않았다면 후속 단계에서 실패할 수
    # 있지만, 요청 스키마 단계의 422/REQUEST_VALIDATION_FAILED는 발생하면 안 된다.
    response = client.post("/api/v1/files/process", json=request_body)
    if response.status_code == 422:
        body = response.json()
        assert body.get("code") != "REQUEST_VALIDATION_FAILED"


@pytest.mark.parametrize(
    ("file_type", "file_name"),
    [
        pytest.param("pdf", "document.docx", id="pdf-docx"),
        pytest.param("docx", "document.pdf", id="docx-pdf"),
        pytest.param("pptx", "document.xlsx", id="pptx-xlsx"),
        pytest.param("txt", "document.pdf", id="txt-pdf"),
        pytest.param("xlsx", "document.txt", id="xlsx-txt"),
    ],
)
def test_type_and_extension_mismatch_is_rejected(
    client: TestClient,
    file_type: str,
    file_name: str,
) -> None:
    response = client.post(
        "/api/v1/files/process",
        json={**_VALID_REQUEST, "file_type": file_type, "file_name": file_name},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert "body" in {error["field"] for error in body["data"]["errors"]}
