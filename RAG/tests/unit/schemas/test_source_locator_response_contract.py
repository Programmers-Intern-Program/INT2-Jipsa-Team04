"""형식별 원본 위치가 최종 출처 응답까지 보존되는지 검증한다."""

import pytest

from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.source_locator import (
    SourceContentOrigin,
    SourceLocatorKind,
    build_source_locator,
)


def test_pdf_locator_preserves_page_response() -> None:
    """기존 PDF 페이지 출처는 공통 모델 도입 후에도 유지되어야 한다."""

    locator = build_source_locator(
        file_type=SupportedFileType.PDF,
        source_metadata={
            "location_kind": "pdf_page",
            "page_number": 8,
        },
    )

    assert locator.kind is SourceLocatorKind.PDF_PAGE
    assert locator.page == 8
    assert locator.structure_path == "page:8"


def test_docx_locator_preserves_section_heading_paragraph_and_table() -> None:
    """DOCX 구조화 청커가 만든 섹션·제목·문단·표 위치를 모두 반환한다."""

    locator = build_source_locator(
        file_type=SupportedFileType.DOCX,
        source_metadata={
            "location_kind": "docx_block",
            "unit_type": "table",
            "section_index": 2,
            "block_index": 9,
            "paragraph_index": 7,
            "table_index": 1,
            "section_title": "운영 절차",
            "section_heading_level": 2,
            "row_count": 4,
            "column_count": 3,
        },
    )

    assert locator.kind is SourceLocatorKind.DOCX_BLOCK
    assert locator.section_index == 2
    assert locator.block_index == 9
    assert locator.paragraph_index == 7
    assert locator.table_index == 1
    assert locator.section_title == "운영 절차"
    assert locator.heading_level == 2
    assert locator.row_count == 4
    assert locator.column_count == 3


def test_pptx_locator_preserves_slide_shape_and_geometry() -> None:
    """PPTX 슬라이드와 중첩 도형 경로 및 EMU 좌표를 반환한다."""

    locator = build_source_locator(
        file_type=SupportedFileType.PPTX,
        source_metadata={
            "location_kind": "pptx_shape",
            "slide_number": 4,
            "shape_index": 3,
            "shape_id": 27,
            "shape_path": "3.2",
            "shape_name": "성과 차트",
            "shape_type_name": "CHART",
            "coordinate_space": "group",
            "shape_left_emu": 100,
            "shape_top_emu": 200,
            "shape_width_emu": 300,
            "shape_height_emu": 400,
        },
    )

    assert locator.kind is SourceLocatorKind.PPTX_SHAPE
    assert locator.slide_no == 4
    assert locator.shape_index == 3
    assert locator.shape_id == 27
    assert locator.shape_path == "3.2"
    assert locator.shape_name == "성과 차트"
    assert locator.shape_type_name == "CHART"
    assert locator.coordinate_space == "group"
    assert locator.shape_left_emu == 100
    assert locator.shape_top_emu == 200
    assert locator.shape_width_emu == 300
    assert locator.shape_height_emu == 400


def test_xlsx_locator_preserves_sheet_and_exact_cell_range() -> None:
    """XLSX 시트, 행·열 및 셀 범위가 응답용 위치에 그대로 남아야 한다."""

    locator = build_source_locator(
        file_type=SupportedFileType.XLSX,
        source_metadata={
            "location_kind": "xlsx_cell_range",
            "sheet_number": 2,
            "sheet_name": "성과",
            "row_number": 12,
            "start_row": 12,
            "end_row": 12,
            "start_column": 2,
            "end_column": 5,
            "start_cell": "B12",
            "end_cell": "E12",
            "cell_range": "B12:E12",
            "cell_coordinates": ("B12", "D12", "E12"),
            "merged_ranges": ("B12:C12",),
        },
    )

    assert locator.kind is SourceLocatorKind.XLSX_CELL_RANGE
    assert locator.sheet_number == 2
    assert locator.sheet_name == "성과"
    assert locator.row_number == 12
    assert locator.start_row == 12
    assert locator.end_row == 12
    assert locator.start_column == 2
    assert locator.end_column == 5
    assert locator.start_cell == "B12"
    assert locator.end_cell == "E12"
    assert locator.cell_range == "B12:E12"
    assert locator.cell_coordinates == ("B12", "D12", "E12")
    assert locator.merged_cell_ranges == ("B12:C12",)


def test_txt_locator_preserves_line_and_character_ranges() -> None:
    """TXT 줄 범위와 exclusive 문자 범위를 동시에 반환한다."""

    locator = build_source_locator(
        file_type=SupportedFileType.TXT,
        source_metadata={
            "location_kind": "txt_line",
            "line_start_number": 14,
            "line_end_number": 16,
            "source_char_start": 120,
            "source_char_end": 184,
        },
    )

    assert locator.kind is SourceLocatorKind.TXT_LINE
    assert locator.line_start == 14
    assert locator.line_end == 16
    assert locator.char_start == 120
    assert locator.char_end == 184
    assert locator.structure_path == "line:14-16"


@pytest.mark.parametrize(
    ("file_type", "metadata", "expected_primary_location"),
    [
        (
            SupportedFileType.PDF,
            {"page_number": 3},
            ("page", 3),
        ),
        (
            SupportedFileType.DOCX,
            {"section_index": 1, "block_index": 5},
            ("block_index", 5),
        ),
        (
            SupportedFileType.PPTX,
            {"slide_number": 6, "shape_path": "2"},
            ("slide_no", 6),
        ),
        (
            SupportedFileType.XLSX,
            {"sheet_name": "요약", "cell_range": "A1:C3"},
            ("sheet_name", "요약"),
        ),
    ],
)
def test_ocr_locator_keeps_original_location_and_image_ordinal(
    file_type: SupportedFileType,
    metadata: dict[str, object],
    expected_primary_location: tuple[str, object],
) -> None:
    """OCR 출처에는 원본 위치와 사용자 표시용 이미지 순번이 함께 있어야 한다."""

    locator = build_source_locator(
        file_type=file_type,
        source_metadata={
            **metadata,
            "content_origin": "ocr",
            "unit_type": "ocr_image",
            "image_index": 2,
            "image_id": "image-2",
            "image_kind": "rendered_image",
            "ocr_engine": "easyocr",
            "ocr_mean_confidence": 0.93,
        },
    )

    field_name, expected_value = expected_primary_location
    assert getattr(locator, field_name) == expected_value
    assert locator.content_origin is SourceContentOrigin.OCR
    assert locator.image_ordinal == 2
    assert locator.image_index == 2
    assert locator.image_id == "image-2"


def test_ocr_locator_rejects_image_id_without_image_ordinal() -> None:
    """이미지 ID만으로는 사용자가 확인할 순번이 없으므로 OCR 출처를 거부한다."""

    with pytest.raises(ValueError, match="image_ordinal"):
        build_source_locator(
            file_type=SupportedFileType.PDF,
            source_metadata={
                "page_number": 1,
                "content_origin": "ocr",
                "unit_type": "ocr_image",
                "image_id": "pdf-image-without-order",
            },
        )


def test_ocr_locator_rejects_image_ordinal_without_original_location() -> None:
    """이미지 순번이 있어도 원본 슬라이드 위치가 없으면 출처로 반환하지 않는다."""

    with pytest.raises(ValueError, match="slide_no"):
        build_source_locator(
            file_type=SupportedFileType.PPTX,
            source_metadata={
                "content_origin": "ocr",
                "unit_type": "ocr_image",
                "image_index": 1,
                "image_id": "pptx-image-1",
            },
        )
