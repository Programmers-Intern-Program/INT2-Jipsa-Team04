"""PPTX 슬라이드의 제목, 도형 텍스트, 표와 발표자 노트를 추출한다.

PPTX도 DOCX/XLSX와 같은 OOXML ZIP 패키지지만 본문 구조는 슬라이드와 도형
트리로 구성된다. 이 파서는 슬라이드 순서와 도형 순서를 보존하면서 다음 요소를
``ParsedDocumentUnit``으로 변환한다.

- 슬라이드 제목 placeholder
- 일반 텍스트 프레임을 가진 도형
- 표(GraphicFrame)
- 그룹 도형 안의 중첩 도형
- 발표자 노트 placeholder

차트의 데이터 계열, SmartArt의 의미 구조, 이미지 OCR, 비디오, 오디오와 도형의
시각적 위치 해석은 현재 범위에 포함하지 않는다. 다만 텍스트 프레임이 있는 일반
도형과 표는 원본 위치를 추적할 수 있도록 슬라이드 번호, 도형 순번과 중첩 경로를
메타데이터로 남긴다.
"""

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Final, Protocol, cast

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.presentation import Presentation as PptxPresentation
from pptx.shapes.base import BaseShape
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.group import GroupShape
from pptx.slide import Slide

from jipsa_rag.infrastructure.document.exceptions import (
    DocumentParserError,
    DocumentReadError,
    DocumentTextExtractionError,
    DocumentTextNotFoundError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
    SourceMetadataValue,
)
from jipsa_rag.infrastructure.document.parsers._common import (
    normalize_inline_text,
    normalize_text,
    validate_ooxml_package,
)

# Local RAG DB에 기록할 텍스트 기반 PPTX 파서 종류다.
_PPTX_PARSER_TYPE: Final[str] = "PPTX_TEXT"

# 슬라이드/도형 unit 경계 또는 source_metadata 계약이 바뀌면 증가시킨다.
_PPTX_PARSER_VERSION: Final[str] = "1.0.0"

# PPTX 패키지 여부를 판별하기 위한 최소 필수 ZIP 멤버다.
_PPTX_REQUIRED_MEMBERS: Final[frozenset[str]] = frozenset(
    {
        "[Content_Types].xml",
        "ppt/presentation.xml",
    }
)


class _ShapeWithText(Protocol):
    """``has_text_frame``이 참인 도형에서 필요한 최소 텍스트 계약.

    ``BaseShape``의 정적 타입에는 모든 하위 구현의 ``text`` 속성이 선언되어 있지
    않다. 런타임에서 ``shape.has_text_frame``을 확인한 뒤 이 Protocol로 cast하면
    구체 도형 클래스에 강하게 결합하지 않으면서 타입 검사도 통과할 수 있다.
    """

    @property
    def text(self) -> str:
        """도형 텍스트 프레임의 전체 문자열을 반환한다."""

        ...


class PptxDocumentParser:
    """``python-pptx``를 이용하여 PPTX의 슬라이드별 텍스트 위치를 보존한다."""

    @property
    def file_type(self) -> DocumentType:
        """이 구현체가 처리하는 공통 문서 형식인 PPTX를 반환한다."""

        return DocumentType.PPTX

    @property
    def parser_type(self) -> str:
        """Local RAG DB에 저장할 PPTX 파서 종류를 반환한다."""

        return _PPTX_PARSER_TYPE

    @property
    def parser_version(self) -> str:
        """재파싱과 Chunk ID 생성에 사용할 파서 호환 버전을 반환한다."""

        return _PPTX_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        """동기식 PPTX 파싱을 작업 스레드에서 실행한다.

        ``python-pptx``는 ZIP, XML 관계와 슬라이드 객체를 동기 방식으로 읽는다.
        큰 발표 자료가 FastAPI 이벤트 루프를 직접 점유하지 않도록
        ``asyncio.to_thread()``로 이동한다.
        """

        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: Path) -> ParsedDocument:
        """PPTX 패키지를 열고 슬라이드 순서대로 공통 unit을 생성한다."""

        # 다운로드 경로 외의 직접 호출도 안전하도록 파서 경계에서 PPTX 내부 루트를
        # 다시 검증한다. 확장자만 PPTX인 DOCX/XLSX 위장 파일을 거부한다.
        validate_ooxml_package(
            file_path,
            file_type=self.file_type,
            required_members=_PPTX_REQUIRED_MEMBERS,
        )

        try:
            presentation = Presentation(file_path)
            units = self._parse_presentation(presentation)
        except DocumentParserError:
            raise
        except OSError as error:
            raise DocumentReadError(file_path) from error
        except Exception as error:
            # 손상된 슬라이드 관계, XML 또는 패키지 오류를 라이브러리별 타입으로
            # 노출하지 않고 공통 손상 문서 예외로 변환한다.
            raise InvalidDocumentError(self.file_type) from error

        parsed_document = ParsedDocument(
            file_type=self.file_type,
            units=tuple(units),
            document_metadata={
                "slide_count": len(presentation.slides),
                # 한 슬라이드에서 여러 도형과 노트 unit이 생성될 수 있으므로
                # slide_count와 실제 source unit 수를 별도로 기록한다.
                "source_unit_count": len(units),
            },
        )

        if parsed_document.text_unit_count == 0:
            raise DocumentTextNotFoundError(self.file_type)

        return parsed_document

    def _parse_presentation(
        self,
        presentation: PptxPresentation,
    ) -> list[ParsedDocumentUnit]:
        """모든 슬라이드를 1부터 시작하는 번호 순서로 파싱한다."""

        units: list[ParsedDocumentUnit] = []

        for slide_number, slide in enumerate(presentation.slides, start=1):
            # ``slide.shapes.title`` 접근 시 동일 XML 요소를 감싼 새로운 프록시 객체가
            # 반환될 수 있다. Python 객체 동일성(`is`)으로 비교하면 제목을 놓칠 수
            # 있으므로 슬라이드 안에서 안정적인 ``shape_id``를 사용한다.
            title_shape = slide.shapes.title
            title_shape_id = (
                title_shape.shape_id
                if title_shape is not None
                else None
            )

            for shape_index, shape in enumerate(slide.shapes, start=1):
                try:
                    units.extend(
                        self._parse_shape(
                            shape,
                            slide_number=slide_number,
                            shape_index=shape_index,
                            # 최상위 도형의 경로는 화면에 표시되는 1-based 순번이다.
                            # 그룹 내부 도형은 2.1, 2.2처럼 점으로 중첩 경로를 확장한다.
                            shape_path=str(shape_index),
                            is_title=(
                                title_shape_id is not None
                                and shape.shape_id == title_shape_id
                            ),
                        )
                    )
                except DocumentParserError:
                    raise
                except Exception as error:
                    # 특정 도형 추출 실패는 슬라이드 전체 손상으로 뭉개지 않고
                    # 슬라이드 번호와 최상위 도형 순번을 포함한 위치 오류로 변환한다.
                    raise DocumentTextExtractionError(
                        file_type=self.file_type,
                        source_metadata={
                            "slide_number": slide_number,
                            "shape_index": shape_index,
                        },
                    ) from error

            # 발표자 노트는 도형 목록과 별도의 notesSlide 관계에 저장된다. 슬라이드의
            # 모든 시각 도형 뒤에 노트 unit을 추가하여 동일 슬라이드 안의 읽기 순서를
            # "슬라이드 본문 → 발표자 노트"로 결정적으로 유지한다.
            notes_text = self._extract_notes_text(slide)
            if notes_text:
                units.append(
                    ParsedDocumentUnit(
                        text=notes_text,
                        source_metadata={
                            "unit_type": "speaker_notes",
                            "slide_number": slide_number,
                            # 현재 슬라이드당 실제 발표자 노트 본문은 하나의 unit으로
                            # 합치므로 notes_index는 항상 1이다. 향후 여러 placeholder를
                            # 분리할 경우 파서 버전과 함께 계약을 변경해야 한다.
                            "notes_index": 1,
                        },
                    )
                )

        return units

    def _parse_shape(
        self,
        shape: BaseShape,
        *,
        slide_number: int,
        shape_index: int,
        shape_path: str,
        is_title: bool,
    ) -> Iterable[ParsedDocumentUnit]:
        """단일 도형 또는 그룹 도형을 공통 unit Iterable로 변환한다.

        일반 도형은 최대 하나의 unit을 반환하지만, 그룹 도형은 자식 도형 수에 따라
        여러 unit을 반환할 수 있으므로 반환 타입을 ``Iterable``로 통일한다.
        """

        metadata: dict[str, SourceMetadataValue] = {
            "slide_number": slide_number,
            # shape_index는 슬라이드의 최상위 도형 순번이다. 그룹 내부 도형도 부모의
            # shape_index를 공유하고, 실제 중첩 위치는 shape_path로 구분한다.
            "shape_index": shape_index,
            "shape_path": shape_path,
            "shape_name": shape.name,
            # 정수 값은 저장·검색 payload에서 안정적으로 비교하기 위한 원본 코드다.
            "shape_type": int(shape.shape_type),
            # 사람이 로그나 source metadata를 읽기 쉽도록 enum 이름도 함께 저장한다.
            "shape_type_name": getattr(
                shape.shape_type,
                "name",
                str(shape.shape_type),
            ),
        }

        if (
            shape.shape_type == MSO_SHAPE_TYPE.GROUP
            and isinstance(shape, GroupShape)
        ):
            grouped_units: list[ParsedDocumentUnit] = []

            for child_index, child_shape in enumerate(shape.shapes, start=1):
                grouped_units.extend(
                    self._parse_shape(
                        child_shape,
                        slide_number=slide_number,
                        # 그룹 내부 자식은 최상위 도형 목록의 별도 항목이 아니므로
                        # 부모의 shape_index를 유지한다.
                        shape_index=shape_index,
                        shape_path=f"{shape_path}.{child_index}",
                        # 제목 placeholder가 그룹 내부에 존재하는 일반적인 PPTX 구조는
                        # 아니며, 제목 판별은 최상위 title shape_id 기준으로만 수행한다.
                        is_title=False,
                    )
                )

            return grouped_units

        if shape.has_table:
            # ``BaseShape`` 정적 타입에는 table 속성이 없다. python-pptx에서
            # has_table=True인 실제 구현은 GraphicFrame이므로 런타임 조건 확인 후
            # cast하여 표 API에 접근한다.
            table = cast(GraphicFrame, shape).table
            rows: list[str] = []

            for row in table.rows:
                # 셀 내부 여러 문단은 공백으로 합치고, 셀 사이는 탭으로 구분한다.
                # 중간 빈 셀은 열 위치를 나타내므로 유지하고 오른쪽 빈 탭만 제거한다.
                row_text = "\t".join(
                    normalize_inline_text(cell.text)
                    for cell in row.cells
                ).rstrip()
                rows.append(row_text)

            return (
                ParsedDocumentUnit(
                    text=normalize_text("\n".join(rows)),
                    source_metadata={
                        **metadata,
                        "unit_type": "table",
                        "row_count": len(table.rows),
                        "column_count": len(table.columns),
                    },
                ),
            )

        if shape.has_text_frame:
            # text_frame이 있는 도형만 text 속성에 안전하게 접근한다.
            text = normalize_text(cast(_ShapeWithText, shape).text)

            return (
                ParsedDocumentUnit(
                    text=text,
                    source_metadata={
                        **metadata,
                        "unit_type": (
                            "title"
                            if is_title
                            else "shape_text"
                        ),
                        "is_title": is_title,
                    },
                ),
            )

        # 이미지, 차트, 미디어 등 텍스트 프레임이나 표가 없는 도형은 현재 검색 텍스트를
        # 만들 수 없으므로 unit을 생성하지 않는다. 이미지 OCR은 별도 기능 범위다.
        return ()

    @staticmethod
    def _extract_notes_text(slide: Slide) -> str:
        """슬라이드의 실제 발표자 노트 본문을 정규화하여 반환한다.

        ``notes_text_frame``은 일반적으로 슬라이드 이미지, 날짜와 슬라이드 번호
        placeholder를 제외하고 발표자가 입력한 노트 placeholder의 텍스트를 제공한다.
        노트 관계가 없거나 일부 비표준 PPTX에서 notes API를 읽을 수 없으면 빈 문자열을
        반환하여 슬라이드 본문 파싱은 계속 진행한다.
        """

        if not slide.has_notes_slide:
            return ""

        try:
            notes_text_frame = slide.notes_slide.notes_text_frame
        except (AttributeError, KeyError):
            # 발표자 노트 관계가 부분적으로 손상되었거나 라이브러리가 지원하지 않는
            # 비표준 구조는 노트 없음으로 처리한다. 슬라이드 도형 텍스트까지 실패시키지 않는다.
            return ""

        return normalize_text(notes_text_frame.text)
