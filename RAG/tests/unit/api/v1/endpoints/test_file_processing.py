"""파일 처리 요청 API의 입력 검증과 다운로드 결과를 테스트한다."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.file_processing import (
    get_file_downloader,
)
from jipsa_rag.core.config import get_settings
from jipsa_rag.infrastructure.file.downloader import (
    HttpFileDownloader,
)

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


@pytest.fixture
def file_processing_client(
    client: TestClient,
    tmp_path: Path,
) -> Iterator[TestClient]:
    """외부 네트워크 요청 없이 파일 처리 API를 테스트한다."""

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

    # conftest의 client fixture에서 사용하는 실제 애플리케이션과
    # 동일한 객체에 다운로드 의존성 override를 적용한다.
    from jipsa_rag.main import app

    app.dependency_overrides[get_file_downloader] = lambda: downloader

    try:
        yield client
    finally:
        # 다른 API 테스트에 Mock Downloader가 남지 않도록
        # fixture 종료 시 override를 제거한다.
        app.dependency_overrides.pop(
            get_file_downloader,
            None,
        )


def test_file_processing_request_returns_validated_response(
    file_processing_client: TestClient,
) -> None:
    """유효한 PDF 요청이 다운로드 및 검증 완료 응답을 반환한다."""

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == 202
    assert response.json() == {
        "success": True,
        "code": "FILE_VALIDATION_COMPLETED",
        "message": ("File download and validation completed."),
        "data": {
            "users_idx": 1,
            "file_idx": 10,
            "folder_idx": 3,
            "file_name": "project-guide.pdf",
            "file_type": "PDF",
            "file_size_bytes": len(PDF_CONTENT),
            "file_hash_verified": True,
            "processing_status": "VALIDATED",
        },
    }


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


def test_file_processing_request_rejects_hash_mismatch(
    file_processing_client: TestClient,
) -> None:
    """실제 다운로드 파일과 요청 해시가 다르면 오류를 반환한다."""

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
        "message": ("The downloaded file hash does not match."),
        "data": None,
    }


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
    """현재 처리 대상이 아닌 파일 타입을 거부한다."""

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
