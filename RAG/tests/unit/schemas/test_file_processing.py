"""파일 처리 요청의 지원 형식과 확장자 교차 검증을 테스트한다."""

import pytest
from pydantic import ValidationError

from jipsa_rag.schemas.file_processing import FileProcessingRequest, SupportedFileType

_BASE: dict[str, object] = {
    "file_idx": 1,
    "user_idx": 2,
    "folder_idx": None,
    "download_url": "https://bucket.s3.ap-northeast-2.amazonaws.com/file?signature=test",
    "url_expires_in": 900,
}


@pytest.mark.parametrize("file_type", list(SupportedFileType))
def test_accepts_each_supported_extension(file_type: SupportedFileType) -> None:
    request = FileProcessingRequest.model_validate(
        {
            **_BASE,
            "file_name": f"document.{file_type.value.upper()}",
            "file_type": file_type.value.upper(),
        }
    )
    assert request.file_type is file_type


def test_rejects_declared_type_and_extension_mismatch() -> None:
    with pytest.raises(ValidationError):
        FileProcessingRequest.model_validate(
            {
                **_BASE,
                "file_name": "document.pdf",
                "file_type": "docx",
            }
        )
