"""문서 청킹 공통 모델의 검증과 불변성을 테스트한다."""

import hashlib
from uuid import uuid4

import pytest

from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    ChunkingContext,
    TextChunk,
)
from jipsa_rag.infrastructure.document.models import DocumentType


def _sha256(value: str) -> str:
    """테스트 문자열의 SHA-256 hex 값을 반환한다."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_chunking_context_normalizes_file_hash() -> None:
    """ChunkingContext가 파일 해시를 소문자로 정규화한다."""

    file_hash = "A" * 64

    context = ChunkingContext(
        users_idx=1,
        file_idx=10,
        file_hash=file_hash,
        index_version=1,
    )

    assert context.file_hash == "a" * 64


@pytest.mark.parametrize(
    (
        "users_idx",
        "file_idx",
        "file_hash",
        "index_version",
    ),
    [
        (0, 10, "a" * 64, 1),
        (1, 0, "a" * 64, 1),
        (1, 10, "invalid-hash", 1),
        (1, 10, "a" * 64, 0),
    ],
)
def test_chunking_context_rejects_invalid_values(
    users_idx: int,
    file_idx: int,
    file_hash: str,
    index_version: int,
) -> None:
    """Chunk ID 생성에 사용할 수 없는 식별 정보를 거부한다."""

    with pytest.raises(ValueError):
        ChunkingContext(
            users_idx=users_idx,
            file_idx=file_idx,
            file_hash=file_hash,
            index_version=index_version,
        )


def test_text_chunk_normalizes_id_and_copies_metadata() -> None:
    """TextChunk가 UUID를 정규화하고 외부 메타데이터 변경을 차단한다."""

    content = "chunk text"
    source_metadata: dict[str, int] = {
        "page_number": 1,
    }

    chunk = TextChunk(
        chunk_id=str(uuid4()).upper(),
        chunk_index=0,
        content=content,
        content_hash=_sha256(content).upper(),
        start_offset=0,
        end_offset=len(content),
        source_metadata=source_metadata,
    )

    source_metadata["page_number"] = 2

    assert chunk.chunk_id == chunk.chunk_id.lower()
    assert chunk.content_hash == chunk.content_hash.lower()
    assert chunk.source_metadata["page_number"] == 1
    assert chunk.token_count is None


def test_chunked_document_returns_chunk_count() -> None:
    """ChunkedDocument가 전체 청크 개수를 반환한다."""

    first_content = "first"
    second_content = "second"

    chunks = (
        TextChunk(
            chunk_id=str(uuid4()),
            chunk_index=0,
            content=first_content,
            content_hash=_sha256(first_content),
            start_offset=0,
            end_offset=len(first_content),
            source_metadata={
                "page_number": 1,
            },
        ),
        TextChunk(
            chunk_id=str(uuid4()),
            chunk_index=1,
            content=second_content,
            content_hash=_sha256(second_content),
            start_offset=7,
            end_offset=7 + len(second_content),
            source_metadata={
                "page_number": 2,
            },
        ),
    )

    document = ChunkedDocument(
        file_type=DocumentType.PDF,
        chunks=chunks,
        source_unit_count=2,
        text_unit_count=2,
    )

    assert document.chunk_count == 2
    assert document.chunks == chunks


def test_chunked_document_rejects_non_contiguous_chunk_indexes() -> None:
    """0부터 연속되지 않는 Chunk Index 목록을 거부한다."""

    content = "chunk"

    chunk = TextChunk(
        chunk_id=str(uuid4()),
        chunk_index=1,
        content=content,
        content_hash=_sha256(content),
        start_offset=0,
        end_offset=len(content),
    )

    with pytest.raises(
        ValueError,
        match="chunk_index values must start at zero",
    ):
        ChunkedDocument(
            file_type=DocumentType.PDF,
            chunks=(chunk,),
            source_unit_count=1,
            text_unit_count=1,
        )
