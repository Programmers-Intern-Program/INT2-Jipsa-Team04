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
from jipsa_rag.infrastructure.chunking.models import (
    ChunkingContext,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)

FILE_HASH = hashlib.sha256(b"test-pdf-file").hexdigest()
PARSER_VERSION = "1.0.0"
EMBEDDING_MODEL = "test/embedding-model"


def _create_context(
    *,
    parser_version: str = PARSER_VERSION,
    embedding_model: str = EMBEDDING_MODEL,
    index_version: int = 1,
) -> ChunkingContext:
    """테스트용 문서·파서·임베딩 식별 컨텍스트를 생성한다."""

    return ChunkingContext(
        users_idx=1,
        file_idx=10,
        file_hash=FILE_HASH,
        parser_version=parser_version,
        embedding_model=embedding_model,
        index_version=index_version,
    )


def _create_single_unit_document(
    text: str,
) -> ParsedDocument:
    """Chunk ID 식별자 테스트에 사용할 단일 페이지 문서를 생성한다."""

    return ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text=text,
                source_metadata={
                    "page_number": 1,
                },
            ),
        ),
    )


@pytest.mark.asyncio
async def test_chunker_generates_deterministic_uuid_chunk_ids() -> None:
    """같은 문서·파서·임베딩 입력은 같은 UUID Chunk ID를 생성한다."""

    parsed_document = _create_single_unit_document("A deterministic chunk identifier test.")
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
async def test_chunker_changes_chunk_id_when_parser_version_changes() -> None:
    """파서 버전이 변경되면 기존 청크와 다른 UUID를 생성한다."""

    parsed_document = _create_single_unit_document("Parser version changes the chunk identity.")
    chunker = CharacterTextChunker(
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )

    first_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(
            parser_version="1.0.0",
        ),
    )
    second_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(
            parser_version="1.1.0",
        ),
    )

    assert first_result.chunks[0].chunk_id != second_result.chunks[0].chunk_id


@pytest.mark.asyncio
async def test_chunker_changes_chunk_id_when_embedding_model_changes() -> None:
    """임베딩 모델 식별자가 변경되면 기존 청크와 다른 UUID를 생성한다."""

    parsed_document = _create_single_unit_document("Embedding model changes the chunk identity.")
    chunker = CharacterTextChunker(
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )

    first_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(
            embedding_model="test/model-v1",
        ),
    )
    second_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(
            embedding_model="test/model-v2",
        ),
    )

    assert first_result.chunks[0].chunk_id != second_result.chunks[0].chunk_id


@pytest.mark.asyncio
async def test_chunker_changes_chunk_id_when_index_version_changes() -> None:
    """색인 버전이 변경되면 새로운 UUID Chunk ID를 생성한다."""

    parsed_document = _create_single_unit_document("Index version changes the chunk identity.")
    chunker = CharacterTextChunker(
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )

    first_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(
            index_version=1,
        ),
    )
    second_result = await chunker.chunk(
        document=parsed_document,
        context=_create_context(
            index_version=2,
        ),
    )

    assert first_result.chunks[0].chunk_id != second_result.chunks[0].chunk_id


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

        assert parsed_document.text[chunk.start_offset : chunk.end_offset] == chunk.content


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
            chunk_overlap_chars=(chunk_overlap_chars),
        )
