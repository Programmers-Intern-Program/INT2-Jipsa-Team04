"""공통 Source Locator의 문서 형식별 위치와 OCR 계약을 검증한다."""

import pytest
from pydantic import ValidationError

from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.source_locator import (
    SourceContentOrigin,
    SourceLocator,
    SourceLocatorKind,
    build_source_locator,
)


def test_build_pdf_ocr_locator_keeps_page_and_image_order() -> None:
    locator = build_source_locator(
        file_type=SupportedFileType.PDF,
        source_metadata={
            "page_number": 7,
            "unit_type": "ocr_image",
            "content_origin": "ocr",
            "image_index": 2,
            "image_id": "pdf-7-2",
            "image_kind": "pdf_embedded",
            "ocr_engine": "easyocr",
            "ocr_mean_confidence": 0.91,
        },
    )

    assert locator.kind is SourceLocatorKind.PDF_PAGE
    assert locator.content_origin is SourceContentOrigin.OCR
    assert locator.page == 7
    assert locator.image_index == 2
    assert locator.image_id == "pdf-7-2"
    assert locator.structure_path == "page:7"


@pytest.mark.parametrize(
    ("file_type", "metadata", "expected_kind"),
    [
        (
            SupportedFileType.DOCX,
            {
                "section_index": 0,
                "block_index": 5,
                "paragraph_index": 3,
                "section_title": "배포 절차",
            },
            SourceLocatorKind.DOCX_BLOCK,
        ),
        (
            SupportedFileType.PPTX,
            {"slide_number": 4, "shape_path": "shape:2.1"},
            SourceLocatorKind.PPTX_SHAPE,
        ),
        (
            SupportedFileType.XLSX,
            {"sheet_name": "요약", "cell_range": "B2:D8"},
            SourceLocatorKind.XLSX_CELL_RANGE,
        ),
        (
            SupportedFileType.TXT,
            {
                "line_number": 12,
                "source_char_start": 120,
                "source_char_end": 184,
            },
            SourceLocatorKind.TXT_LINE,
        ),
    ],
)
def test_build_source_locator_supports_every_non_pdf_format(
    file_type: SupportedFileType,
    metadata: dict[str, object],
    expected_kind: SourceLocatorKind,
) -> None:
    locator = build_source_locator(
        file_type=file_type,
        source_metadata=metadata,
    )

    assert locator.file_type is file_type
    assert locator.kind is expected_kind
    assert locator.content_origin is SourceContentOrigin.TEXT


def test_ocr_locator_requires_image_identity() -> None:
    with pytest.raises(ValidationError):
        SourceLocator(
            file_type=SupportedFileType.PDF,
            kind=SourceLocatorKind.PDF_PAGE,
            content_origin=SourceContentOrigin.OCR,
            page=1,
        )


def test_txt_locator_rejects_reversed_character_range() -> None:
    with pytest.raises(ValidationError):
        SourceLocator(
            file_type=SupportedFileType.TXT,
            kind=SourceLocatorKind.TXT_LINE,
            line_number=1,
            char_start=20,
            char_end=10,
        )
