"""문자 기반 문서 청킹과 메타데이터 생성을 테스트한다."""

import hashlib

import pytest

from jipsa_rag.infrastructure.chunking.character import (
    CharacterTextChunker,
)
from jipsa_rag.infrastructure.chunking.exceptions import (
    InvalidChunkingConfigurationError,
    NoDocumentChunksError,
)
from jipsa_rag.infrastructure.chunking.models import ChunkingContext
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)

FILE_HASH = hashlib.sha256(b"test-pdf-file").hexdigest()


def _create_context(
    *,
    index_version: int = 1,
) -> ChunkingContext:
    """테스트용 문서 식별 컨텍스트를 생성한다."""

    return ChunkingContext(
        users_idx=1,
        file_idx=10,
        file_hash=FILE_HASH,
        index_version=index_version,
    )


@pytest.mark.asyncio
async def test_chunker_preserves_page_boundaries_and_offsets() -> None:
    """청크가 PDF 페이지 경계를 넘지 않고 원문 오프셋을 유지한다."""

    first_page_text = "First page contains enough text to create several separate chunks."
    second_page_text = "Second page contains its own text and must remain independent."

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text=first_page_text,
                source_metadata={
                    "page_number": 1,
                },
            ),
            ParsedDocumentUnit(
                text=second_page_text,
                source_metadata={
                    "page_number": 2,
                },
            ),
        ),
        document_metadata={
            "page_count": 2,
        },
    )

    chunker = CharacterTextChunker(
        chunk_size_chars=24,
        chunk_overlap_chars=5,
    )

    result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(),
    )

    assert result.file_type is DocumentType.PDF
    assert result.source_unit_count == 2
    assert result.text_unit_count == 2
    assert result.chunk_count > 2

    for expected_chunk_index, chunk in enumerate(result.chunks):
        assert chunk.chunk_index == expected_chunk_index
        assert len(chunk.content) <= 24

        # 전역 Offset은 ParsedDocument.text와 직접 일치해야 한다.
        assert parsed_document.text[chunk.start_offset : chunk.end_offset] == chunk.content

        page_number = chunk.source_metadata["page_number"]
        source_unit_index = chunk.source_metadata["source_unit_index"]
        unit_start_offset = chunk.source_metadata["unit_start_offset"]
        unit_end_offset = chunk.source_metadata["unit_end_offset"]

        assert isinstance(page_number, int)
        assert isinstance(source_unit_index, int)
        assert isinstance(unit_start_offset, int)
        assert isinstance(unit_end_offset, int)

        if page_number == 1:
            assert source_unit_index == 0
            assert first_page_text[unit_start_offset:unit_end_offset] == chunk.content
        else:
            assert page_number == 2
            assert source_unit_index == 1
            assert second_page_text[unit_start_offset:unit_end_offset] == chunk.content


@pytest.mark.asyncio
async def test_chunker_skips_empty_source_units() -> None:
    """텍스트가 없는 빈 페이지는 청크를 생성하지 않는다."""

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="",
                source_metadata={
                    "page_number": 1,
                },
            ),
            ParsedDocumentUnit(
                text="Text from the second page.",
                source_metadata={
                    "page_number": 2,
                },
            ),
        ),
        document_metadata={
            "page_count": 2,
        },
    )

    chunker = CharacterTextChunker(
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )

    result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(),
    )

    assert result.chunk_count == 1
    assert result.chunks[0].content == "Text from the second page."
    assert result.chunks[0].source_metadata["page_number"] == 2
    assert result.chunks[0].source_metadata["source_unit_index"] == 1
    assert result.chunks[0].start_offset == 0


@pytest.mark.asyncio
async def test_chunker_uses_configured_overlap() -> None:
    """적절한 구분자가 없으면 고정 문자 위치와 중첩으로 분할한다."""

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="abcdefghij",
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )

    chunker = CharacterTextChunker(
        chunk_size_chars=6,
        chunk_overlap_chars=2,
    )

    result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(),
    )

    assert [chunk.content for chunk in result.chunks] == [
        "abcdef",
        "efghij",
    ]
    assert [(chunk.start_offset, chunk.end_offset) for chunk in result.chunks] == [
        (0, 6),
        (4, 10),
    ]


@pytest.mark.asyncio
async def test_chunker_prefers_paragraph_boundary() -> None:
    """최대 크기 안에 문단 경계가 있으면 해당 위치에서 분할한다."""

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="First paragraph.\n\nSecond paragraph.",
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )

    chunker = CharacterTextChunker(
        chunk_size_chars=20,
        chunk_overlap_chars=0,
    )

    result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(),
    )

    assert [chunk.content for chunk in result.chunks] == [
        "First paragraph.",
        "Second paragraph.",
    ]


@pytest.mark.asyncio
async def test_chunker_generates_deterministic_uuid_chunk_ids() -> None:
    """같은 입력은 같은 UUID Chunk ID를 생성한다."""

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="A deterministic chunk identifier test.",
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )

    chunker = CharacterTextChunker(
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )

    first_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(),
    )
    second_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(),
    )

    assert first_result.chunks[0].chunk_id == second_result.chunks[0].chunk_id


@pytest.mark.asyncio
async def test_chunker_changes_chunk_id_when_index_version_changes() -> None:
    """색인 버전이 변경되면 새로운 UUID Chunk ID를 생성한다."""

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="Index version changes the chunk identity.",
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )

    chunker = CharacterTextChunker(
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )

    first_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(index_version=1),
    )
    second_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(index_version=2),
    )

    assert first_result.chunks[0].chunk_id != second_result.chunks[0].chunk_id


@pytest.mark.asyncio
async def test_chunker_generates_content_hash() -> None:
    """청크 원문 기준 SHA-256 Content Hash를 생성한다."""

    content = "Content hash test."

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text=content,
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )

    chunker = CharacterTextChunker(
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )

    result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(),
    )

    assert result.chunks[0].content_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_chunker_rejects_document_without_text_chunks() -> None:
    """모든 원본 단위가 비어 있으면 명확한 청킹 예외를 발생시킨다."""

    parsed_document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="",
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )

    chunker = CharacterTextChunker()

    with pytest.raises(NoDocumentChunksError):
        await chunker.chunk(
            document=parsed_document,
            context=_create_context(),
        )


@pytest.mark.parametrize(
    (
        "chunk_size_chars",
        "chunk_overlap_chars",
    ),
    [
        (0, 0),
        (-1, 0),
        (100, -1),
        (100, 100),
        (100, 101),
    ],
)
def test_chunker_rejects_invalid_configuration(
    chunk_size_chars: int,
    chunk_overlap_chars: int,
) -> None:
    """잘못된 청크 크기 또는 중첩 크기를 거부한다."""

    with pytest.raises(InvalidChunkingConfigurationError):
        CharacterTextChunker(
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )
  