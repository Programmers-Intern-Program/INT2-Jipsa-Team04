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
6. 외부에 표시하는 이미지 순번은 ``image_ordinal``을 표준 필드로 사용하고,
   기존 ``image_index``는 하위 호환 필드로 함께 유지한다.
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
    ``image_ordinal``·``image_id``·``image_kind``를 함께 제공한다.
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

    # DOCX 위치. section_index와 block_index는 기존 파서 계약을 그대로 보존한다.
    section_index: int | None = Field(default=None, ge=0)
    block_index: int | None = Field(default=None, ge=0)
    paragraph_index: int | None = Field(default=None, ge=0)
    table_index: int | None = Field(default=None, ge=0)
    heading_level: int | None = Field(default=None, gt=0)
    column_number: int | None = Field(default=None, gt=0)
    row_count: int | None = Field(default=None, ge=0)
    column_count: int | None = Field(default=None, ge=0)
    section_title: str | None = Field(default=None, min_length=1, max_length=500)

    # PPTX 위치와 원본 도형 좌표. EMU는 OOXML의 정수 좌표 단위다.
    slide_no: int | None = Field(default=None, gt=0)
    shape_index: int | None = Field(default=None, ge=0)
    shape_id: int | None = Field(default=None, ge=0)
    shape_path: str | None = Field(default=None, min_length=1, max_length=500)
    shape_name: str | None = Field(default=None, min_length=1, max_length=500)
    shape_type_name: str | None = Field(default=None, min_length=1, max_length=100)
    coordinate_space: str | None = Field(default=None, min_length=1, max_length=100)
    shape_left_emu: int | None = Field(default=None, ge=0)
    shape_top_emu: int | None = Field(default=None, ge=0)
    shape_width_emu: int | None = Field(default=None, ge=0)
    shape_height_emu: int | None = Field(default=None, ge=0)

    # XLSX 위치. 행·열 번호와 셀 문자열을 함께 제공해 UI가 재계산하지 않게 한다.
    sheet_number: int | None = Field(default=None, gt=0)
    sheet_name: str | None = Field(default=None, min_length=1, max_length=255)
    row_number: int | None = Field(default=None, gt=0)
    start_row: int | None = Field(default=None, gt=0)
    end_row: int | None = Field(default=None, gt=0)
    start_column: int | None = Field(default=None, gt=0)
    end_column: int | None = Field(default=None, gt=0)
    start_cell: str | None = Field(default=None, min_length=1, max_length=100)
    end_cell: str | None = Field(default=None, min_length=1, max_length=100)
    cell_range: str | None = Field(default=None, min_length=1, max_length=255)
    cell_coordinates: tuple[str, ...] = Field(default_factory=tuple)
    merged_cell_ranges: tuple[str, ...] = Field(default_factory=tuple)

    # TXT 위치. line_start/line_end는 다중 줄 청크 확장을 고려한 표준 범위다.
    line_number: int | None = Field(default=None, gt=0)
    line_start: int | None = Field(default=None, gt=0)
    line_end: int | None = Field(default=None, gt=0)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

    # OCR 이미지 위치 및 검증 정보.
    image_ordinal: int | None = Field(
        default=None,
        gt=0,
        description="원본 위치에서 사용자가 확인할 수 있는 1-based 이미지 순번",
    )
    image_index: int | None = Field(
        default=None,
        gt=0,
        description="기존 응답과의 하위 호환을 위한 이미지 순번 별칭",
    )
    image_id: str | None = Field(default=None, min_length=1, max_length=255)
    image_kind: str | None = Field(default=None, min_length=1, max_length=100)
    ocr_engine: str | None = Field(default=None, min_length=1, max_length=100)
    ocr_mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_location_contract(self) -> Self:
        """형식별 위치 범위와 OCR 이미지 순번 계약을 검증한다."""

        # 범위의 시작과 끝이 모두 존재할 때만 역전 여부를 검사한다.
        # Ruff SIM102 규칙에 맞춰 중첩 조건문을 하나의 명시적인 조건식으로
        # 결합하되 기존 검증 시점과 예외 메시지는 그대로 유지한다.
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("char_end must be greater than or equal to char_start.")

        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be greater than or equal to line_start.")

        if (
            self.start_row is not None
            and self.end_row is not None
            and self.end_row < self.start_row
        ):
            raise ValueError("end_row must be greater than or equal to start_row.")

        if (
            self.start_column is not None
            and self.end_column is not None
            and self.end_column < self.start_column
        ):
            raise ValueError("end_column must be greater than or equal to start_column.")

        # image_ordinal이 신규 표준 필드다. 기존 호출자가 image_index만 전달한
        # 경우에는 동일 값을 양쪽에 채워 직렬화 결과와 과거 소비자를 모두 보호한다.
        if self.image_ordinal is None and self.image_index is not None:
            object.__setattr__(self, "image_ordinal", self.image_index)
        elif self.image_index is None and self.image_ordinal is not None:
            object.__setattr__(self, "image_index", self.image_ordinal)
        elif (
            self.image_ordinal is not None
            and self.image_index is not None
            and self.image_ordinal != self.image_index
        ):
            raise ValueError("image_ordinal and image_index must match when both exist.")

        if self.content_origin is SourceContentOrigin.OCR:
            # OCR 출처는 사용자가 같은 원본 위치에서 이미지를 식별할 수 있도록
            # 1-based 순번을 반드시 포함한다. 현재 추출기는 모든 OCR 후보에
            # image_index를 기록하므로 builder가 이를 image_ordinal로 정규화한다.
            if self.image_ordinal is None:
                raise ValueError("OCR source locators must contain image_ordinal.")

            # 이미지 순번만 있고 원본 페이지·블록·슬라이드·시트 위치가 없으면
            # 사용자가 실제 문서에서 근거를 찾을 수 없다. 파일 형식에 맞는 대표
            # 원본 위치가 함께 있는지 검증한다.
            if self.file_type is SupportedFileType.PDF and self.page is None:
                raise ValueError("PDF OCR locators must contain page.")
            if self.file_type is SupportedFileType.DOCX and all(
                value is None
                for value in (
                    self.section_index,
                    self.block_index,
                    self.paragraph_index,
                    self.table_index,
                    self.structure_path,
                )
            ):
                raise ValueError("DOCX OCR locators must contain a document block.")
            if self.file_type is SupportedFileType.PPTX and self.slide_no is None:
                raise ValueError("PPTX OCR locators must contain slide_no.")
            if (
                self.file_type is SupportedFileType.XLSX
                and self.sheet_name is None
                and self.sheet_number is None
            ):
                raise ValueError("XLSX OCR locators must contain a sheet location.")
            if (
                self.file_type is SupportedFileType.TXT
                and self.line_number is None
                and self.line_start is None
            ):
                raise ValueError("TXT OCR locators must contain a line location.")

        # 한 파일 형식의 locator에 다른 형식의 핵심 위치가 섞이면 최종 응답이
        # 잘못된 UI 링크를 만들 수 있으므로 대표 위치 충돌을 명시적으로 차단한다.
        if self.file_type is SupportedFileType.PDF and any(
            value is not None
            for value in (
                self.slide_no,
                self.sheet_name,
                self.sheet_number,
                self.line_number,
                self.line_start,
            )
        ):
            raise ValueError("PDF locators must not contain non-PDF locations.")

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
    slide_no = _first_int(metadata, ("slide_number", "slide_no"), minimum=1) or legacy_slide_no
    sheet_number = _resolve_sheet_number(metadata)
    sheet_name = _optional_text(metadata, "sheet_name") or legacy_sheet_name
    section_title = _optional_text(metadata, "section_title") or legacy_section_title

    line_number = _first_int(metadata, ("line_number",), minimum=1)
    line_start = _first_int(
        metadata,
        ("line_start", "line_start_number", "line_number"),
        minimum=1,
    )
    line_end = _first_int(
        metadata,
        ("line_end", "line_end_number", "line_number"),
        minimum=1,
    )
    cell_range = _optional_text(metadata, "cell_range")
    shape_path = _optional_text(metadata, "shape_path")

    kind = _resolve_locator_kind(
        file_type=normalized_file_type,
        location_kind=_optional_text(metadata, "location_kind"),
        shape_path=shape_path,
    )

    structure_path = _optional_text(metadata, "structure_path")
    if structure_path is None:
        structure_path = _build_fallback_structure_path(
            file_type=normalized_file_type,
            page=page,
            section_index=_first_int(metadata, ("section_index",), minimum=0),
            block_index=_first_int(metadata, ("block_index",), minimum=0),
            slide_no=slide_no,
            shape_path=shape_path,
            sheet_name=sheet_name,
            cell_range=cell_range,
            line_start=line_start,
            line_end=line_end,
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
    image_ordinal = _first_int(
        metadata,
        (
            "image_ordinal",
            "image_index",
            "page_image_index",
            "image_order",
        ),
        minimum=1,
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
        heading_level=_first_int(
            metadata,
            ("heading_level", "section_heading_level"),
            minimum=1,
        ),
        row_number=_first_int(metadata, ("row_number",), minimum=1),
        column_number=_first_int(metadata, ("column_number",), minimum=1),
        row_count=_first_int(metadata, ("row_count",), minimum=0),
        column_count=_first_int(metadata, ("column_count",), minimum=0),
        section_title=section_title,
        slide_no=slide_no,
        shape_index=_first_int(metadata, ("shape_index",), minimum=0),
        shape_id=_first_int(metadata, ("shape_id",), minimum=0),
        shape_path=shape_path,
        shape_name=_optional_text(metadata, "shape_name"),
        shape_type_name=_optional_text(metadata, "shape_type_name"),
        coordinate_space=_optional_text(metadata, "coordinate_space"),
        shape_left_emu=_first_int(metadata, ("shape_left_emu",), minimum=0),
        shape_top_emu=_first_int(metadata, ("shape_top_emu",), minimum=0),
        shape_width_emu=_first_int(metadata, ("shape_width_emu",), minimum=0),
        shape_height_emu=_first_int(metadata, ("shape_height_emu",), minimum=0),
        sheet_number=sheet_number,
        sheet_name=sheet_name,
        start_row=_first_int(metadata, ("start_row",), minimum=1),
        end_row=_first_int(metadata, ("end_row",), minimum=1),
        start_column=_first_int(metadata, ("start_column",), minimum=1),
        end_column=_first_int(metadata, ("end_column",), minimum=1),
        start_cell=_optional_text(metadata, "start_cell"),
        end_cell=_optional_text(metadata, "end_cell"),
        cell_range=cell_range,
        cell_coordinates=_text_tuple(metadata, "cell_coordinates"),
        merged_cell_ranges=_first_text_tuple(
            metadata,
            ("merged_cell_ranges", "merged_ranges"),
        ),
        line_number=line_number,
        line_start=line_start,
        line_end=line_end,
        char_start=char_start,
        char_end=char_end,
        image_ordinal=image_ordinal,
        image_index=image_ordinal,
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
            # 파서가 pptx_speaker_notes처럼 더 세분화된 내부 값을 추가해도
            # 외부 API는 해당 파일 형식의 대표 위치로 안정적으로 축약한다.
            pass

    if file_type is SupportedFileType.PDF:
        return SourceLocatorKind.PDF_PAGE
    if file_type is SupportedFileType.DOCX:
        return SourceLocatorKind.DOCX_BLOCK
    if file_type is SupportedFileType.PPTX:
        return (
            SourceLocatorKind.PPTX_SHAPE if shape_path is not None else SourceLocatorKind.PPTX_SLIDE
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
    line_start: int | None,
    line_end: int | None,
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
        suffix = f"/shape:{shape_path}" if shape_path else ""
        return f"slide:{slide_no}{suffix}"
    if file_type is SupportedFileType.XLSX and sheet_name is not None:
        suffix = f"/range:{cell_range}" if cell_range else ""
        return f"sheet:{sheet_name}{suffix}"
    if file_type is SupportedFileType.TXT and line_start is not None:
        if line_end is not None and line_end != line_start:
            return f"line:{line_start}-{line_end}"
        return f"line:{line_start}"
    return None


def _resolve_sheet_number(metadata: Mapping[str, object]) -> int | None:
    """1-based sheet_number를 우선하고 0-based sheet_index를 안전하게 변환한다."""

    sheet_number = _first_int(metadata, ("sheet_number",), minimum=1)
    if sheet_number is not None:
        return sheet_number

    sheet_index = _first_int(metadata, ("sheet_index",), minimum=0)
    return None if sheet_index is None else sheet_index + 1


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


def _first_text_tuple(
    metadata: Mapping[str, object],
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    """후보 키 중 첫 번째 비어 있지 않은 문자열 배열을 읽는다."""

    for key in keys:
        normalized = _text_tuple(metadata, key)
        if normalized:
            return normalized
    return ()


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
