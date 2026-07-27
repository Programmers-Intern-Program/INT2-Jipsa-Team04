"""이미지 추출 전 OOXML ZIP 안전 검증을 테스트한다."""

import zipfile
from pathlib import Path

import pytest

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.exceptions import InvalidDocumentError
from jipsa_rag.infrastructure.document.images.common import validate_ooxml_image_package
from jipsa_rag.infrastructure.document.models import DocumentType

_REQUIRED = frozenset({"[Content_Types].xml", "word/document.xml"})


def test_validator_accepts_required_members_within_limits(tmp_path: Path) -> None:
    """정상적인 작은 OOXML 중앙 디렉터리는 통과한다."""

    file_path = tmp_path / "valid.docx"
    with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("word/document.xml", "<document/>")

    validate_ooxml_image_package(
        file_path,
        file_type=DocumentType.DOCX,
        required_members=_REQUIRED,
        settings=DocumentProcessingSettings(_env_file=None),
    )


def test_validator_rejects_path_traversal_member(tmp_path: Path) -> None:
    """ZIP 내부 상위 경로를 포함하는 엔트리는 이미지 read 이전에 거부한다."""

    file_path = tmp_path / "traversal.docx"
    with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("word/document.xml", "<document/>")
        package.writestr("../outside.bin", b"payload")

    with pytest.raises(InvalidDocumentError):
        validate_ooxml_image_package(
            file_path,
            file_type=DocumentType.DOCX,
            required_members=_REQUIRED,
            settings=DocumentProcessingSettings(_env_file=None),
        )


def test_validator_rejects_member_larger_than_configured_limit(tmp_path: Path) -> None:
    """단일 해제 크기 한계를 넘는 이미지 파트는 압축 해제 전에 거부한다."""

    file_path = tmp_path / "oversized.docx"
    with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("word/document.xml", "<document/>")
        package.writestr("word/media/image1.png", b"x" * 2048)

    settings = DocumentProcessingSettings(
        ooxml_max_member_uncompressed_bytes=1024,
        ooxml_max_total_uncompressed_bytes=1024 * 1024,
        _env_file=None,
    )

    with pytest.raises(InvalidDocumentError):
        validate_ooxml_image_package(
            file_path,
            file_type=DocumentType.DOCX,
            required_members=_REQUIRED,
            settings=settings,
        )
