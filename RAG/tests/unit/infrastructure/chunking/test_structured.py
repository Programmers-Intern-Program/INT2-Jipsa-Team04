"""형식별 구조 청킹과 기존 결정적 ID/Hash 하위 호환성을 테스트한다."""

import hashlib
from typing import cast

import pytest

from jipsa_rag.infrastructure.chunking.character import CharacterTextChunker
from jipsa_rag.infrastructure.chunking.models import ChunkingContext
from jipsa_rag.infrastructure.chunking.structured import StructuredDocumentChunker
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)

FILE_HASH = hashlib.sha256(b"same source file").hexdigest()


def _context(*, parser_version: str = "1.0.0") -> ChunkingContext:
    """모든 비교 테스트가 공유하는 결정적 청킹 식별 정보를 반환한다."""

    return ChunkingContext(
        users_idx=7,
        file_idx=11,
        file_hash=FILE_HASH,
        parser_version=parser_version,
        embedding_model="test/embedding-model",
        index_version=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_type", "metadata"),
    (
        (DocumentType.PDF, {"page_number": 1}),
        (
            DocumentType.DOCX,
            {"unit_type": "paragraph", "section_index": 1, "block_index": 1},
        ),
        (
            DocumentType.PPTX,
            {"unit_type": "shape_text", "slide_number": 1, "shape_path": "2"},
        ),
        (
            DocumentType.XLSX,
            {"unit_type": "row", "sheet_name": "Sheet1", "cell_range": "A1:C1"},
        ),
        (
            DocumentType.TXT,
            {"unit_type": "line", "line_number": 1, "text_char_start": 0},
        ),
    ),
)
async def test_structured_chunker_preserves_existing_content_hash_and_chunk_id(
    file_type: DocumentType,
    metadata: dict[str, str | int],
) -> None:
    """구조 메타데이터 보강은 기존 문자 청커의 ID와 Hash를 변경하지 않는다."""

    document = ParsedDocument(
        file_type=file_type,
        units=(
            ParsedDocumentUnit(
                text="동일한 원문은 동일한 청크 식별자를 가져야 한다.",
                source_metadata=metadata,
            ),
        ),
    )
    context = _context()

    legacy = await CharacterTextChunker(
        chunk_size_chars=20,
        chunk_overlap_chars=5,
    ).chunk(document=document, context=context)
    structured = await StructuredDocumentChunker(
        chunk_size_chars=20,
        chunk_overlap_chars=5,
    ).chunk(document=document, context=context)

    assert [chunk.content for chunk in structured.chunks] == [
        chunk.content for chunk in legacy.chunks
    ]
    assert [chunk.content_hash for chunk in structured.chunks] == [
        chunk.content_hash for chunk in legacy.chunks
    ]
    assert [chunk.chunk_id for chunk in structured.chunks] == [
        chunk.chunk_id for chunk in legacy.chunks
    ]
    assert [chunk.start_offset for chunk in structured.chunks] == [
        chunk.start_offset for chunk in legacy.chunks
    ]
    assert all(
        chunk.source_metadata["chunking_strategy"] == "STRUCTURED_DOCUMENT"
        for chunk in structured.chunks
    )


@pytest.mark.asyncio
async def test_docx_strategy_propagates_heading_context_to_following_blocks() -> None:
    """DOCX 제목은 같은 섹션의 후속 문단과 표 청크에 전달된다."""

    document = ParsedDocument(
        file_type=DocumentType.DOCX,
        units=(
            ParsedDocumentUnit(
                text="설치 방법",
                source_metadata={
                    "unit_type": "heading",
                    "heading_level": 1,
                    "section_index": 1,
                    "block_index": 1,
                },
            ),
            ParsedDocumentUnit(
                text="패키지를 설치하고 환경 변수를 설정한다.",
                source_metadata={
                    "unit_type": "paragraph",
                    "section_index": 1,
                    "block_index": 2,
                },
            ),
            ParsedDocumentUnit(
                text="변수\t설명",
                source_metadata={
                    "unit_type": "table",
                    "section_index": 1,
                    "block_index": 3,
                },
            ),
        ),
    )

    result = await StructuredDocumentChunker().chunk(document=document, context=_context())

    assert result.chunks[0].source_metadata["section_title"] == "설치 방법"
    assert result.chunks[1].source_metadata["section_title"] == "설치 방법"
    assert result.chunks[2].source_metadata["section_title"] == "설치 방법"
    assert result.chunks[1].source_metadata["structure_path"] == "section:1/block:2"


@pytest.mark.asyncio
async def test_docx_strategy_resets_heading_context_when_section_changes() -> None:
    """DOCX 섹션이 바뀌면 이전 섹션의 제목과 제목 레벨을 전달하지 않는다."""

    document = ParsedDocument(
        file_type=DocumentType.DOCX,
        units=(
            ParsedDocumentUnit(
                text="제1장",
                source_metadata={
                    "unit_type": "heading",
                    "heading_level": 1,
                    "section_index": 1,
                    "block_index": 1,
                },
            ),
            ParsedDocumentUnit(
                text="첫 번째 섹션의 일반 문단",
                source_metadata={
                    "unit_type": "paragraph",
                    "section_index": 1,
                    "block_index": 2,
                },
            ),
            ParsedDocumentUnit(
                text="두 번째 섹션의 제목 없는 문단",
                source_metadata={
                    "unit_type": "paragraph",
                    "section_index": 2,
                    "block_index": 3,
                },
            ),
            ParsedDocumentUnit(
                text="제2장",
                source_metadata={
                    "unit_type": "heading",
                    "heading_level": 2,
                    "section_index": 2,
                    "block_index": 4,
                },
            ),
            ParsedDocumentUnit(
                text="항목\t값",
                source_metadata={
                    "unit_type": "table",
                    "section_index": 2,
                    "block_index": 5,
                },
            ),
            ParsedDocumentUnit(
                text="세 번째 섹션의 제목 없는 문단",
                source_metadata={
                    "unit_type": "paragraph",
                    "section_index": 3,
                    "block_index": 6,
                },
            ),
        ),
    )

    result = await StructuredDocumentChunker().chunk(document=document, context=_context())

    first_heading = result.chunks[0].source_metadata
    first_paragraph = result.chunks[1].source_metadata
    second_section_without_heading = result.chunks[2].source_metadata
    second_heading = result.chunks[3].source_metadata
    second_table = result.chunks[4].source_metadata
    third_section_without_heading = result.chunks[5].source_metadata

    assert first_heading["section_title"] == "제1장"
    assert first_heading["section_heading_level"] == 1
    assert first_paragraph["section_title"] == "제1장"
    assert first_paragraph["section_heading_level"] == 1

    # 섹션 2의 첫 문단에는 아직 제목이 없으므로 섹션 1의 제목이 남아서는 안 된다.
    assert "section_title" not in second_section_without_heading
    assert "section_heading_level" not in second_section_without_heading
    assert second_section_without_heading["structure_path"] == "section:2/block:3"

    # 섹션 2에서 새 제목이 나타난 이후에는 그 제목만 같은 섹션에 전달되어야 한다.
    assert second_heading["section_title"] == "제2장"
    assert second_heading["section_heading_level"] == 2
    assert second_table["section_title"] == "제2장"
    assert second_table["section_heading_level"] == 2

    # 다음 섹션으로 다시 전환되면 섹션 2의 제목도 동일하게 제거되어야 한다.
    assert "section_title" not in third_section_without_heading
    assert "section_heading_level" not in third_section_without_heading
    assert third_section_without_heading["structure_path"] == "section:3/block:6"


@pytest.mark.asyncio
async def test_txt_strategy_derives_global_character_range_for_split_line() -> None:
    """긴 TXT 한 줄의 각 청크가 원문 기준 exclusive 문자 범위를 가진다."""

    text = "ABCDEFGHIJKLMNO"
    document = ParsedDocument(
        file_type=DocumentType.TXT,
        units=(
            ParsedDocumentUnit(
                text=text,
                source_metadata={
                    "unit_type": "line",
                    "line_number": 3,
                    "text_char_start": 20,
                    "text_char_end": 35,
                },
            ),
        ),
    )

    result = await StructuredDocumentChunker(
        chunk_size_chars=6,
        chunk_overlap_chars=2,
    ).chunk(document=document, context=_context())

    assert len(result.chunks) > 1
    for chunk in result.chunks:
        unit_start = cast(int, chunk.source_metadata["unit_start_offset"])
        unit_end = cast(int, chunk.source_metadata["unit_end_offset"])
        assert chunk.source_metadata["chunk_source_char_start"] == 20 + unit_start
        assert chunk.source_metadata["chunk_source_char_end"] == 20 + unit_end
        assert chunk.source_metadata["structure_path"] == "line:3"


@pytest.mark.asyncio
async def test_parser_version_change_generates_new_deterministic_chunk_id() -> None:
    """원문이 같아도 파서 버전이 바뀌면 재색인용 Point ID가 달라진다."""

    document = ParsedDocument(
        file_type=DocumentType.PPTX,
        units=(
            ParsedDocumentUnit(
                text="동일한 도형 텍스트",
                source_metadata={"slide_number": 1, "shape_path": "1"},
            ),
        ),
    )
    chunker = StructuredDocumentChunker()

    old = await chunker.chunk(document=document, context=_context(parser_version="1.0.0"))
    new = await chunker.chunk(document=document, context=_context(parser_version="1.1.0"))
    repeated = await chunker.chunk(document=document, context=_context(parser_version="1.1.0"))

    assert old.chunks[0].content_hash == new.chunks[0].content_hash
    assert old.chunks[0].chunk_id != new.chunks[0].chunk_id
    assert new.chunks[0].chunk_id == repeated.chunks[0].chunk_id


@pytest.mark.asyncio
async def test_explicit_empty_strategy_list_does_not_restore_defaults() -> None:
    """빈 전략 목록은 운영 기본 전략으로 조용히 대체되지 않는다."""

    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="전략 미등록 확인",
                source_metadata={"page_number": 1},
            ),
        ),
    )
    chunker = StructuredDocumentChunker(strategies=())

    with pytest.raises(ValueError, match="No structure-preserving chunking strategy"):
        await chunker.chunk(document=document, context=_context())
