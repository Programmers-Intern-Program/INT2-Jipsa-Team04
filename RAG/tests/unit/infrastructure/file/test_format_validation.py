"""지원 형식별 MIME Type과 Magic Byte 검증을 테스트한다."""

import pytest

from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.file.format_validation import validate_content_type_and_magic


@pytest.mark.parametrize(
    ("content_type", "payload", "expected_family"),
    [
        ("application/pdf", b"%PDF-1.7\n", "PDF"),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\x03\x04rest",
            "OOXML",
        ),
        ("text/plain; charset=utf-8", "안녕하세요".encode(), "TEXT"),
        ("text/plain", "첫 줄\n둘째 줄".encode("utf-16-le"), "TEXT"),
        ("application/octet-stream", b"%PDF-1.7\n", "PDF"),
    ],
)
def test_accepts_supported_mime_and_magic(
    content_type: str,
    payload: bytes,
    expected_family: str,
) -> None:
    assert (
        validate_content_type_and_magic(
            content_type=content_type,
            leading_bytes=payload,
            users_idx=1,
            file_idx=2,
        )
        == expected_family
    )


def test_rejects_mime_magic_mismatch() -> None:
    with pytest.raises(AppException) as exception_info:
        validate_content_type_and_magic(
            content_type="application/pdf",
            leading_bytes=b"PK\x03\x04rest",
            users_idx=1,
            file_idx=2,
        )

    assert exception_info.value.code == "INVALID_FILE"
