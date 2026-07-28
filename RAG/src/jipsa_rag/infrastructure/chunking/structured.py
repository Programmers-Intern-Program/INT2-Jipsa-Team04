"""문서 형식별 구조 메타데이터를 보존하는 청킹 진입점을 제공한다.

기존 ``CharacterTextChunker``의 분할 경계, Content Hash와 결정적 Chunk ID 생성
규칙은 운영 중인 Local RAG DB와 Qdrant Point ID 계약이다. 구조화 청킹을 추가하면서
이 규칙을 바꾸면 동일 원본이 불필요하게 전부 재색인되거나 기존 Point와 연결이
끊어진다. 따라서 이 모듈은 다음 원칙을 사용한다.

1. 기존 CharacterTextChunker가 만드는 content, chunk_index, content_hash, chunk_id를
   그대로 사용한다.
2. 형식별 전략은 ParsedDocumentUnit의 순서와 텍스트를 절대 변경하지 않는다.
3. 형식별 위치를 정규화한 source_metadata만 추가한다.
4. 긴 단일 unit의 내부 분할도 기존 문자 청커가 담당한다.

이 설계로 PDF의 기존 페이지 단위 청킹 결과와 ID가 완전히 유지되면서 DOCX,
PPTX, XLSX, TXT의 구조 위치 계약만 확장된다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from jipsa_rag.infrastructure.chunking.character import CharacterTextChunker
from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    ChunkingContext,
    TextChunk,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
    SourceMetadataValue,
)

STRUCTURED_CHUNKING_STRATEGY: Final[str] = "STRUCTURED_DOCUMENT"
STRUCTURED_CHUNKING_VERSION: Final[str] = "1.0.0"


class DocumentStructureStrategy(Protocol):
    """문서 텍스트를 바꾸지 않고 형식별 위치 메타데이터를 정규화하는 계약."""

    @property
    def file_type(self) -> DocumentType:
        """전략이 처리하는 문서 형식을 반환한다."""

        ...

    def prepare(self, document: ParsedDocument) -> ParsedDocument:
        """unit 순서와 텍스트가 동일한 메타데이터 보강 문서를 반환한다."""

        ...


@dataclass(frozen=True, slots=True)
class _BaseStructureStrategy:
    """형식별 전략이 공유하는 불변 메타데이터 보강 기반 클래스."""

    file_type: DocumentType

    def prepare(self, document: ParsedDocument) -> ParsedDocument:
        """문서 형식을 확인하고 각 unit의 위치 메타데이터를 보강한다."""

        if document.file_type is not self.file_type:
            raise ValueError(
                f"The {self.file_type.value} strategy cannot process "
                f"{document.file_type.value} documents."
            )

        units: list[ParsedDocumentUnit] = []
        state: dict[str, SourceMetadataValue] = {}

        for unit_index, unit in enumerate(document.units):
            additions = self._metadata_additions(
                unit,
                unit_index=unit_index,
                state=state,
            )
            units.append(
                ParsedDocumentUnit(
                    text=unit.text,
                    source_metadata={
                        **unit.source_metadata,
                        **additions,
                    },
                )
            )

        return ParsedDocument(
            file_type=document.file_type,
            units=tuple(units),
            document_metadata=document.document_metadata,
        )

    def _metadata_additions(
        self,
        unit: ParsedDocumentUnit,
        *,
        unit_index: int,
        state: dict[str, SourceMetadataValue],
    ) -> Mapping[str, SourceMetadataValue]:
        """하위 전략이 unit별로 추가할 위치 메타데이터를 반환한다."""

        del unit, state
        return {
            "source_unit_index": unit_index,
        }


class _PdfPageStructureStrategy(_BaseStructureStrategy):
    """PDF 페이지 경계를 보존하고 페이지 경로를 명시한다."""

    def _metadata_additions(
        self,
        unit: ParsedDocumentUnit,
        *,
        unit_index: int,
        state: dict[str, SourceMetadataValue],
    ) -> Mapping[str, SourceMetadataValue]:
        del state
        page_number = _positive_int(unit.source_metadata.get("page_number"))
        return {
            "source_unit_index": unit_index,
            "location_kind": "pdf_page",
            "structure_path": (
                f"page:{page_number}" if page_number is not None else f"page-unit:{unit_index + 1}"
            ),
        }


class _DocxStructureStrategy(_BaseStructureStrategy):
    """DOCX 제목 계층을 동일한 섹션의 이후 문단과 표에 전달한다."""

    def _metadata_additions(
        self,
        unit: ParsedDocumentUnit,
        *,
        unit_index: int,
        state: dict[str, SourceMetadataValue],
    ) -> Mapping[str, SourceMetadataValue]:
        metadata = unit.source_metadata
        section_index = _positive_int(metadata.get("section_index"))
        block_index = _positive_int(metadata.get("block_index"))
        unit_type = _text(metadata.get("unit_type"))

        previous_section_index = state.get("current_section_index")
        section_changed = (
            "current_section_index" not in state or previous_section_index != section_index
        )

        if section_changed:
            # DOCX 섹션 나누기는 이전 섹션의 제목 문맥이 끝나는 경계다.
            #
            # 첫 unit에서도 동일한 초기화 경로를 사용하면 별도의 최초 실행 분기를
            # 만들지 않아도 된다. 또한 유효한 section_index 뒤에 누락되거나 손상된
            # 값이 나타난 경우에도 이전 제목을 보수적으로 제거하여 다른 섹션의
            # 제목이 잘못 노출되는 것을 방지한다.
            state["current_section_index"] = section_index
            state.pop("section_title", None)
            state.pop("section_heading_level", None)

        if unit_type == "heading" and unit.text.strip():
            # 제목 unit 이후의 같은 섹션 문단과 표가 검색될 때 제목 컨텍스트를 함께
            # 표시할 수 있도록 가장 최근 제목과 레벨을 상태로 유지한다.
            #
            # 섹션 변경 초기화보다 뒤에서 실행해야 새 섹션의 첫 제목이 즉시 현재
            # 섹션 제목으로 등록되고, 이후 같은 섹션의 블록에만 전달된다.
            state["section_title"] = unit.text.strip()
            heading_level = _positive_int(metadata.get("heading_level"))
            state["section_heading_level"] = heading_level

        additions: dict[str, SourceMetadataValue] = {
            "source_unit_index": unit_index,
            "location_kind": "docx_block",
            "structure_path": (
                f"section:{section_index or 1}/block:{block_index or unit_index + 1}"
            ),
        }

        section_title = state.get("section_title")
        if isinstance(section_title, str) and section_title:
            additions["section_title"] = section_title

        stored_heading_level = state.get("section_heading_level")
        if isinstance(stored_heading_level, int) and not isinstance(
            stored_heading_level,
            bool,
        ):
            additions["section_heading_level"] = stored_heading_level

        return additions


class _PptxStructureStrategy(_BaseStructureStrategy):
    """PPTX 슬라이드와 도형 경로를 청크 위치 경로로 정규화한다."""

    def _metadata_additions(
        self,
        unit: ParsedDocumentUnit,
        *,
        unit_index: int,
        state: dict[str, SourceMetadataValue],
    ) -> Mapping[str, SourceMetadataValue]:
        del state
        metadata = unit.source_metadata
        slide_number = _positive_int(metadata.get("slide_number"))
        shape_path = _text(metadata.get("shape_path"))
        unit_type = _text(metadata.get("unit_type")) or "unit"

        if shape_path is not None:
            structure_path = f"slide:{slide_number or 1}/shape:{shape_path}"
        else:
            structure_path = f"slide:{slide_number or 1}/{unit_type}:{unit_index + 1}"

        return {
            "source_unit_index": unit_index,
            "location_kind": (_text(metadata.get("location_kind")) or "pptx_slide_unit"),
            "structure_path": structure_path,
        }


class _XlsxStructureStrategy(_BaseStructureStrategy):
    """XLSX 시트와 셀 범위를 하나의 구조 경로로 보존한다."""

    def _metadata_additions(
        self,
        unit: ParsedDocumentUnit,
        *,
        unit_index: int,
        state: dict[str, SourceMetadataValue],
    ) -> Mapping[str, SourceMetadataValue]:
        del state
        metadata = unit.source_metadata
        sheet_name = _text(metadata.get("sheet_name")) or "unknown"
        cell_range = _text(metadata.get("cell_range")) or f"unit-{unit_index + 1}"

        return {
            "source_unit_index": unit_index,
            "location_kind": "xlsx_cell_range",
            "structure_path": f"sheet:{sheet_name}/range:{cell_range}",
        }


class _TxtLineStructureStrategy(_BaseStructureStrategy):
    """TXT 줄 번호와 전역 문자 범위를 청크 위치 계약으로 유지한다."""

    def _metadata_additions(
        self,
        unit: ParsedDocumentUnit,
        *,
        unit_index: int,
        state: dict[str, SourceMetadataValue],
    ) -> Mapping[str, SourceMetadataValue]:
        del state
        line_number = _positive_int(unit.source_metadata.get("line_number"))
        return {
            "source_unit_index": unit_index,
            "location_kind": "txt_line",
            "structure_path": f"line:{line_number or unit_index + 1}",
        }


class StructuredDocumentChunker:
    """형식별 구조 전략과 기존 결정적 문자 청커를 결합한다.

    ``CharacterTextChunker``를 내부에 그대로 사용하므로 기존 PDF의 content,
    start/end offset, Content Hash와 UUIDv5 Chunk ID가 변경되지 않는다. 전략이
    추가하는 메타데이터는 ID 생성 입력에 포함되지 않는다.
    """

    def __init__(
        self,
        *,
        chunk_size_chars: int = 1_000,
        chunk_overlap_chars: int = 200,
        strategies: tuple[DocumentStructureStrategy, ...] | None = None,
    ) -> None:
        """문자 청커 설정과 선택적인 형식별 전략 목록을 초기화한다."""

        self._character_chunker = CharacterTextChunker(
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )

        if strategies is None:
            registered: tuple[DocumentStructureStrategy, ...] = (
                _PdfPageStructureStrategy(DocumentType.PDF),
                _DocxStructureStrategy(DocumentType.DOCX),
                _PptxStructureStrategy(DocumentType.PPTX),
                _XlsxStructureStrategy(DocumentType.XLSX),
                _TxtLineStructureStrategy(DocumentType.TXT),
            )
        else:
            # 빈 tuple도 "지원 전략 없음"이라는 호출자의 명시적 설정이다.
            # ``strategies or defaults``를 사용하면 테스트나 제한 실행에서 전달한
            # 빈 설정이 운영 기본값으로 조용히 바뀌므로 None과 빈 tuple을 구분한다.
            registered = tuple(strategies)
        self._strategies = {strategy.file_type: strategy for strategy in registered}

        if len(self._strategies) != len(registered):
            raise ValueError("Only one chunking strategy can be registered per document type.")

    @property
    def strategy_name(self) -> str:
        """Local RAG와 Qdrant payload에 기록할 청킹 전략 이름을 반환한다."""

        return STRUCTURED_CHUNKING_STRATEGY

    @property
    def strategy_version(self) -> str:
        """구조 메타데이터 계약 버전을 반환한다."""

        return STRUCTURED_CHUNKING_VERSION

    async def chunk(
        self,
        *,
        document: ParsedDocument,
        context: ChunkingContext,
    ) -> ChunkedDocument:
        """문서 형식 전략을 적용한 뒤 기존 결정적 문자 청킹을 수행한다."""

        try:
            strategy = self._strategies[document.file_type]
        except KeyError as error:
            raise ValueError(
                f"No structure-preserving chunking strategy for {document.file_type.value}."
            ) from error

        prepared_document = strategy.prepare(document)
        chunked_document = await self._character_chunker.chunk(
            document=prepared_document,
            context=context,
        )

        decorated_chunks = tuple(
            self._decorate_chunk(chunk, file_type=document.file_type)
            for chunk in chunked_document.chunks
        )

        return ChunkedDocument(
            file_type=chunked_document.file_type,
            chunks=decorated_chunks,
            source_unit_count=chunked_document.source_unit_count,
            text_unit_count=chunked_document.text_unit_count,
        )

    def _decorate_chunk(
        self,
        chunk: TextChunk,
        *,
        file_type: DocumentType,
    ) -> TextChunk:
        """ID와 Hash를 바꾸지 않고 청킹 전략·세부 위치 메타데이터를 추가한다."""

        metadata: dict[str, SourceMetadataValue] = {
            **chunk.source_metadata,
            "chunking_strategy": self.strategy_name,
            "chunking_strategy_version": self.strategy_version,
        }

        if file_type is DocumentType.TXT:
            _add_txt_chunk_character_range(metadata)

        return TextChunk(
            chunk_id=chunk.chunk_id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            content_hash=chunk.content_hash,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            source_metadata=metadata,
            token_count=chunk.token_count,
        )


def _add_txt_chunk_character_range(metadata: dict[str, SourceMetadataValue]) -> None:
    """긴 TXT 한 줄이 분할된 경우 청크의 실제 전역 문자 범위를 계산한다."""

    source_char_start = _non_negative_int(metadata.get("text_char_start"))
    unit_start_offset = _non_negative_int(metadata.get("unit_start_offset"))
    unit_end_offset = _non_negative_int(metadata.get("unit_end_offset"))

    if source_char_start is None or unit_start_offset is None or unit_end_offset is None:
        return

    metadata["chunk_source_char_start"] = source_char_start + unit_start_offset
    metadata["chunk_source_char_end"] = source_char_start + unit_end_offset


def _text(value: object) -> str | None:
    """비어 있지 않은 문자열 메타데이터만 반환한다."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _positive_int(value: object) -> int | None:
    """bool이 아닌 양의 정수 메타데이터만 반환한다."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _non_negative_int(value: object) -> int | None:
    """bool이 아닌 0 이상의 정수 메타데이터만 반환한다."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
