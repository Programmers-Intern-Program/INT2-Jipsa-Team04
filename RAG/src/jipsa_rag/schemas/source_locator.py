"""문서 형식과 OCR 여부에 독립적인 공통 출처 위치 모델을 정의한다.

검색 결과와 최종 답변 출처는 더 이상 PDF 페이지 필드만으로 위치를 표현하지
않는다. 이 모듈은 PDF, DOCX, PPTX, TXT, XLSX의 원본 구조 위치와 이미지 OCR
위치를 하나의 안정적인 API 모델로 변환한다.

중요한 설계 원칙은 다음과 같다.

1. ``file_type``은 원본 파일 형식을 나타낸다.
2. ``content_origin``은 일반 파서 텍스트인지 OCR 텍스트인지 나타낸다.
3. OCR 출처도 원본 페이지·문단·슬라이드·시트 위치를 그대로 유지한다.
4. 형식별 세부 위치는 선택 필드로 보존하되, 외부 API는 항상 같은
   ``SourceLocator`` 객체를 사용한다.
5. 이전 색인처럼 일부 위치 필드만 존재하는 payload도 안전하게 읽을 수 있도록
   결정적인 fallback 규칙을 제공한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jipsa_rag.schemas.file_processing import SupportedFileType


class SourceContentOrigin(StrEnum):
    """검색 청크 본문의 생성 경로를 구분한다."""

    TEXT = "text"
    OCR = "ocr"


class SourceLocatorKind(StrEnum):
    """원본 문서에서 사용자가 확인할 수 있는 대표 위치 종류다."""

    DOCUMENT = "document"
    PDF_PAGE = "pdf_page"
    DOCX_BLOCK = "docx_block"
    PPTX_SLIDE = "pptx_slide"
    PPTX_SHAPE = "pptx_shape"
    XLSX_CELL_RANGE = "xlsx_cell_range"
    TXT_LINE = "txt_line"


class SourceLocator(BaseModel):
    """모든 지원 문서와 OCR 청크가 공유하는 출처 위치 모델.

    형식별 필드는 필요한 경우에만 채운다. 예를 들어 PDF는 ``page``를,
    PPTX는 ``slide_no``와 선택적인 ``shape_path``를, XLSX는 ``sheet_name``과
    ``cell_range``를 사용한다. OCR 청크는 같은 위치 필드에 더해
    ``image_index``·``image_id``·``image_kind``를 함께 제공한다.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    file_type: SupportedFileType = Field(
        description="원본 문서 형식",
        examples=["pptx"],
    )
    kind: SourceLocatorKind = Field(
        description="원본 문서에서의 대표 위치 종류",
        examples=["pptx_shape"],
    )
    content_origin: SourceContentOrigin = Field(
        default=SourceContentOrigin.TEXT,
        description="일반 파서 텍스트 또는 OCR 텍스트 여부",
        examples=["ocr"],
    )
    unit_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="paragraph, table, ocr_image 등 파서 단위 종류",
    )
    structure_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        description="원본 문서 구조를 나타내는 결정적 경로",
        examples=["slide:2/shape:3.1"],
    )

    # PDF 위치
    page: int | None = Field(default=None, gt=0, description="PDF 페이지 번호")

    # DOCX 위치
    section_index: int | None = Field(default=None, ge=0)
    block_index: int | None = Field(default=None, ge=0)
    paragraph_index: int | None = Field(default=None, ge=0)
    table_index: int | None = Field(default=None, ge=0)
    row_number: int | None = Field(default=None, gt=0)
    column_number: int | None = Field(default=None, gt=0)
    section_title: str | None = Field(default=None, min_length=1, max_length=500)

    # PPTX 위치
    slide_no: int | None = Field(default=None, gt=0)
    shape_index: int | None = Field(default=None, ge=0)
    shape_id: int | None = Field(default=None, ge=0)
    shape_path: str | None = Field(default=None, min_length=1, max_length=500)
    shape_left_emu: int | None = Field(default=None, ge=0)
    shape_top_emu: int | None = Field(default=None, ge=0)
    shape_width_emu: int | None = Field(default=None, ge=0)
    shape_height_emu: int | None = Field(default=None, ge=0)

    # XLSX 위치
    sheet_number: int | None = Field(default=None, gt=0)
    sheet_name: str | None = Field(default=None, min_length=1, max_length=255)
    cell_range: str | None = Field(default=None, min_length=1, max_length=255)
    cell_coordinates: tuple[str, ...] = Field(default_factory=tuple)
    merged_cell_ranges: tuple[str, ...] = Field(default_factory=tuple)

    # TXT 위치
    line_number: int | None = Field(default=None, gt=0)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

    # OCR 이미지 위치 및 검증 정보
    image_index: int | None = Field(default=None, gt=0)
    image_id: str | None = Field(default=None, min_length=1, max_length=255)
    image_kind: str | None = Field(default=None, min_length=1, max_length=100)
    ocr_engine: str | None = Field(default=None, min_length=1, max_length=100)
    ocr_mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_location_contract(self) -> Self:
        """서로 충돌하는 형식별 위치와 잘못된 문자 범위를 거부한다."""

        if self.char_start is not None and self.char_end is not None:
            if self.char_end < self.char_start:
                raise ValueError("char_end must be greater than or equal to char_start.")

        if self.content_origin is SourceContentOrigin.OCR:
            # OCR 출처는 원본 이미지 식별자나 순번 중 하나는 반드시 보존해야 한다.
            # 이전 색인과의 호환성을 위해 둘 중 하나만 있어도 허용한다.
            if self.image_id is None and self.image_index is None:
                raise ValueError("OCR source locators must contain image_id or image_index.")

        # 한 파일 형식의 locator에 다른 형식의 핵심 위치가 섞이면 최종 응답이
        # 잘못된 UI 링크를 만들 수 있으므로 명시적으로 차단한다.
        if self.file_type is SupportedFileType.PDF:
            if any(
                value is not None
                for value in (
                    self.slide_no,
                    self.sheet_name,
                    self.sheet_number,
                    self.line_number,
                )
            ):
                raise ValueError("PDF locators must not contain non-PDF primary locations.")

        if self.file_type is SupportedFileType.PPTX and self.page is not None:
            raise ValueError("PPTX locators must not contain a PDF page.")

        if self.file_type is SupportedFileType.XLSX and any(
            value is not None for value in (self.page, self.slide_no)
        ):
            raise ValueError("XLSX locators must not contain PDF or PPTX locations.")

        if self.file_type is SupportedFileType.TXT and any(
            value is not None for value in (self.page, self.slide_no, self.sheet_name)
        ):
            raise ValueError("TXT locators must not contain document-structure locations.")

        return self


def build_source_locator(
    *,
    file_type: SupportedFileType | str,
    source_metadata: Mapping[str, object] | None = None,
    legacy_page: int | None = None,
    legacy_slide_no: int | None = None,
    legacy_sheet_name: str | None = None,
    legacy_section_title: str | None = None,
) -> SourceLocator:
    """Qdrant source metadata와 기존 전용 필드에서 공통 locator를 생성한다.

    현재 색인은 전체 ``source_metadata`` 객체를 저장하지만 과거 point에는
    page·slide_no·sheet_name 같은 top-level 필드만 있을 수 있다. 이 함수는
    전체 메타데이터를 우선 사용하고, 값이 없을 때만 legacy 필드를 사용한다.
    """

    normalized_file_type = (
        file_type
        if isinstance(file_type, SupportedFileType)
        else SupportedFileType(str(file_type).strip().lower())
    )
    metadata = source_metadata or {}

    unit_type = _optional_text(metadata, "unit_type")
    raw_content_origin = _optional_text(metadata, "content_origin")
    content_origin = (
        SourceContentOrigin.OCR
        if raw_content_origin == SourceContentOrigin.OCR.value or unit_type == "ocr_image"
        else SourceContentOrigin.TEXT
    )

    page = _first_int(metadata, ("page_number", "page"), minimum=1) or legacy_page
    slide_no = (
        _first_int(metadata, ("slide_number", "slide_no"), minimum=1)
        or legacy_slide_no
    )
    sheet_number = _first_int(
        metadata,
        ("sheet_number", "sheet_index"),
        minimum=1,
    )
    sheet_name = _optional_text(metadata, "sheet_name") or legacy_sheet_name
    section_title = _optional_text(metadata, "section_title") or legacy_section_title

    kind = _resolve_locator_kind(
        file_type=normalized_file_type,
        location_kind=_optional_text(metadata, "location_kind"),
        shape_path=_optional_text(metadata, "shape_path"),
    )

    structure_path = _optional_text(metadata, "structure_path")
    shape_path = _optional_text(metadata, "shape_path")
    if structure_path is None:
        structure_path = _build_fallback_structure_path(
            file_type=normalized_file_type,
            page=page,
            section_index=_first_int(metadata, ("section_index",), minimum=0),
            block_index=_first_int(metadata, ("block_index",), minimum=0),
            slide_no=slide_no,
            shape_path=shape_path,
            sheet_name=sheet_name,
            cell_range=_optional_text(metadata, "cell_range"),
            line_number=_first_int(metadata, ("line_number",), minimum=1),
        )

    char_start = _first_int(
        metadata,
        (
            "chunk_source_char_start",
            "source_char_start",
            "text_char_start",
        ),
        minimum=0,
    )
    char_end = _first_int(
        metadata,
        (
            "chunk_source_char_end",
            "source_char_end",
            "text_char_end",
        ),
        minimum=0,
    )

    return SourceLocator(
        file_type=normalized_file_type,
        kind=kind,
        content_origin=content_origin,
        unit_type=unit_type,
        structure_path=structure_path,
        page=page,
        section_index=_first_int(metadata, ("section_index",), minimum=0),
        block_index=_first_int(metadata, ("block_index",), minimum=0),
        paragraph_index=_first_int(metadata, ("paragraph_index",), minimum=0),
        table_index=_first_int(metadata, ("table_index",), minimum=0),
        row_number=_first_int(metadata, ("row_number",), minimum=1),
        column_number=_first_int(metadata, ("column_number",), minimum=1),
        section_title=section_title,
        slide_no=slide_no,
        shape_index=_first_int(metadata, ("shape_index",), minimum=0),
        shape_id=_first_int(metadata, ("shape_id",), minimum=0),
        shape_path=shape_path,
        shape_left_emu=_first_int(metadata, ("shape_left_emu",), minimum=0),
        shape_top_emu=_first_int(metadata, ("shape_top_emu",), minimum=0),
        shape_width_emu=_first_int(metadata, ("shape_width_emu",), minimum=0),
        shape_height_emu=_first_int(metadata, ("shape_height_emu",), minimum=0),
        sheet_number=sheet_number,
        sheet_name=sheet_name,
        cell_range=_optional_text(metadata, "cell_range"),
        cell_coordinates=_text_tuple(metadata, "cell_coordinates"),
        merged_cell_ranges=_text_tuple(metadata, "merged_cell_ranges"),
        line_number=_first_int(metadata, ("line_number",), minimum=1),
        char_start=char_start,
        char_end=char_end,
        image_index=_first_int(
            metadata,
            ("image_index", "page_image_index", "image_order"),
            minimum=1,
        ),
        image_id=_optional_text(metadata, "image_id"),
        image_kind=_optional_text(metadata, "image_kind"),
        ocr_engine=_optional_text(metadata, "ocr_engine"),
        ocr_mean_confidence=_optional_float(metadata, "ocr_mean_confidence"),
    )


def _resolve_locator_kind(
    *,
    file_type: SupportedFileType,
    location_kind: str | None,
    shape_path: str | None,
) -> SourceLocatorKind:
    """파서 location_kind를 외부의 안정적인 enum으로 정규화한다."""

    if location_kind is not None:
        try:
            return SourceLocatorKind(location_kind)
        except ValueError:
            # 파서가 더 세분화된 내부 location_kind를 추가하더라도 API가
            # 즉시 깨지지 않도록 원본 형식의 대표 위치로 축약한다.
            pass

    if file_type is SupportedFileType.PDF:
        return SourceLocatorKind.PDF_PAGE
    if file_type is SupportedFileType.DOCX:
        return SourceLocatorKind.DOCX_BLOCK
    if file_type is SupportedFileType.PPTX:
        return (
            SourceLocatorKind.PPTX_SHAPE
            if shape_path is not None
            else SourceLocatorKind.PPTX_SLIDE
        )
    if file_type is SupportedFileType.XLSX:
        return SourceLocatorKind.XLSX_CELL_RANGE
    if file_type is SupportedFileType.TXT:
        return SourceLocatorKind.TXT_LINE
    return SourceLocatorKind.DOCUMENT


def _build_fallback_structure_path(
    *,
    file_type: SupportedFileType,
    page: int | None,
    section_index: int | None,
    block_index: int | None,
    slide_no: int | None,
    shape_path: str | None,
    sheet_name: str | None,
    cell_range: str | None,
    line_number: int | None,
) -> str | None:
    """과거 payload에서도 사람이 확인 가능한 구조 경로를 복원한다."""

    if file_type is SupportedFileType.PDF and page is not None:
        return f"page:{page}"
    if file_type is SupportedFileType.DOCX:
        parts: list[str] = []
        if section_index is not None:
            parts.append(f"section:{section_index}")
        if block_index is not None:
            parts.append(f"block:{block_index}")
        return "/".join(parts) or None
    if file_type is SupportedFileType.PPTX and slide_no is not None:
        return f"slide:{slide_no}" + (f"/{shape_path}" if shape_path else "")
    if file_type is SupportedFileType.XLSX and sheet_name is not None:
        return f"sheet:{sheet_name}" + (f"/range:{cell_range}" if cell_range else "")
    if file_type is SupportedFileType.TXT and line_number is not None:
        return f"line:{line_number}"
    return None


def _first_int(
    metadata: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    minimum: int,
) -> int | None:
    """후보 키 중 첫 번째 유효 정수를 읽는다."""

    for key in keys:
        value = metadata.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value >= minimum:
            return value
    return None


def _optional_float(metadata: Mapping[str, object], key: str) -> float | None:
    """0~1 범위의 유한 confidence 값을 읽는다."""

    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        return None
    return normalized


def _optional_text(metadata: Mapping[str, object], key: str) -> str | None:
    """공백이 아닌 문자열을 정규화하여 읽는다."""

    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _text_tuple(metadata: Mapping[str, object], key: str) -> tuple[str, ...]:
    """JSON 배열 형태의 위치 문자열을 불변 tuple로 변환한다."""

    value = metadata.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            normalized.append(text)
    return tuple(normalized)
