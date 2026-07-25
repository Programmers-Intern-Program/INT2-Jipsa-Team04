"""파일 처리 API가 PDF 이외 문서 요청을 거부하는지 테스트한다."""

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
        "files/document.pdf?X-Amz-Signature=test-signature"
    ),
    "url_expires_in": 900,
}


def test_supported_file_type_enum_contains_only_pdf() -> None:
    """외부 파일 처리 계약에는 PDF만 정식 지원 형식으로 노출한다."""

    assert tuple(
        SupportedFileType
    ) == (
        SupportedFileType.PDF,
    )


@pytest.mark.parametrize(
    "endpoint_path",
    [
        pytest.param(
            "/ingest",
            id="backend-ingest",
        ),
        pytest.param(
            "/api/v1/files/process",
            id="file-processing-api",
        ),
    ],
)
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
def test_non_pdf_requests_are_rejected_before_file_processing(
    client: TestClient,
    endpoint_path: str,
    file_type: str,
    file_name: str,
) -> None:
    """TXT·DOCX·XLSX·PPTX 요청은 다운로드·파싱 전에 422로 거부한다."""

    request_body = {
        **_VALID_REQUEST,
        "file_type": file_type,
        "file_name": file_name,
    }

    response = client.post(
        endpoint_path,
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["message"] == "Request validation failed."

    validation_errors = body[
        "data"
    ][
        "errors"
    ]

    invalid_fields = {
        error["field"]
        for error in validation_errors
    }

    # FileProcessingRequest.file_type Enum에서 거부되어 실제 파일 다운로드,
    # PDF 파싱, 청킹, 임베딩, DB·Qdrant 저장 및 완료 콜백으로 진행하지 않는다.
    assert "body.file_type" in invalid_fields


@pytest.mark.parametrize(
    "endpoint_path",
    [
        pytest.param(
            "/ingest",
            id="backend-ingest",
        ),
        pytest.param(
            "/api/v1/files/process",
            id="file-processing-api",
        ),
    ],
)
def test_pdf_type_with_non_pdf_extension_is_rejected(
    client: TestClient,
    endpoint_path: str,
) -> None:
    """file_type만 PDF로 위장하고 확장자가 다른 manifest를 거부한다."""

    request_body = {
        **_VALID_REQUEST,
        "file_type": "pdf",
        "file_name": "document.txt",
    }

    response = client.post(
        endpoint_path,
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"

    invalid_fields = {
        error["field"]
        for error in body[
            "data"
        ][
            "errors"
        ]
    }

    # 모델 전체 교차 검증 오류는 FastAPI 공통 검증 응답에서 body 위치로
    # 표현된다. 메시지 내용보다 위치와 상태 코드를 기준으로 계약을 검증한다.
    assert "body" in invalid_fields