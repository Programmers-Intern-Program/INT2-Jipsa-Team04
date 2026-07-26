"""Local RAG DB와 Qdrant의 다중 형식 위치 저장 계약을 테스트한다."""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.chunking.models import TextChunk
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.embedding.models import EmbeddedChunk, EmbeddedDocument
from jipsa_rag.infrastructure.indexing.local_repository import (
    LocalRagIndexRepository,
    _build_chunk_parameters,
)
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata
from jipsa_rag.infrastructure.indexing.qdrant_store import (
    _PAYLOAD_INDEXES,
    _build_payload,
)


def _metadata(file_type: DocumentType = DocumentType.PPTX) -> DocumentIndexMetadata:
    """저장소 단위 테스트에 사용할 문서 메타데이터를 생성한다."""

    return DocumentIndexMetadata(
        users_idx=10,
        file_idx=20,
        folder_idx=30,
        file_name=f"sample.{file_type.value.lower()}",
        file_type=file_type,
        file_hash=hashlib.sha256(b"sample file").hexdigest(),
        index_version=2,
        parser_type=f"{file_type.value}_TEXT",
        parser_version="1.1.0",
    )


def _embedded_document() -> EmbeddedDocument:
    """도형 위치 메타데이터가 포함된 단일 청크 임베딩 결과를 생성한다."""

    content = "슬라이드 도형 텍스트"
    chunk = TextChunk(
        chunk_id=str(uuid4()),
        chunk_index=0,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        start_offset=0,
        end_offset=len(content),
        source_metadata={
            "unit_type": "shape_text",
            "location_kind": "pptx_shape",
            "slide_number": 2,
            "shape_path": "3.1",
            "shape_left_emu": 914_400,
            "shape_top_emu": 1_828_800,
            "shape_left_ratio": 0.1,
            "chunking_strategy": "STRUCTURED_DOCUMENT",
            "chunking_strategy_version": "1.0.0",
        },
    )
    return EmbeddedDocument(
        embedding_model="test/embedding-model",
        embedding_dim=3,
        chunks=(EmbeddedChunk(chunk=chunk, embedding=(0.1, 0.2, 0.3)),),
    )


def test_local_rag_chunk_parameters_store_full_source_metadata_json() -> None:
    """기존 위치 컬럼과 함께 전체 source_metadata를 JSON 컬럼에 저장한다."""

    embedded_document = _embedded_document()
    parameters = _build_chunk_parameters(
        rag_document_idx=100,
        metadata=_metadata(),
        embedding_model=embedded_document.embedding_model,
        embedded_chunk=embedded_document.chunks[0],
    )

    assert parameters["slide_no"] == 2
    assert parameters["page"] is None
    assert parameters["sheet_name"] is None
    assert parameters["start_offset"] == 0
    assert parameters["end_offset"] == len("슬라이드 도형 텍스트")

    source_metadata_json = cast(str, parameters["source_metadata"])
    source_metadata = cast(dict[str, object], json.loads(source_metadata_json))
    assert source_metadata["shape_path"] == "3.1"
    assert source_metadata["shape_left_emu"] == 914_400
    assert source_metadata["chunking_strategy"] == "STRUCTURED_DOCUMENT"


def test_qdrant_payload_preserves_full_and_flattened_position_contract() -> None:
    """검색 payload에 전체 메타데이터와 자주 쓰는 위치 필드를 함께 저장한다."""

    embedded_document = _embedded_document()
    payload = _build_payload(
        rag_document_idx=100,
        metadata=_metadata(),
        embedded_document=embedded_document,
        embedded_chunk_index=0,
        chunk=embedded_document.chunks[0].chunk,
        is_active=False,
        created_at="2026-07-27T00:00:00+00:00",
    )

    assert payload["parser_type"] == "PPTX_TEXT"
    assert payload["parser_version"] == "1.1.0"
    assert payload["slide_no"] == 2
    assert payload["location_kind"] == "pptx_shape"
    assert payload["shape_path"] == "3.1"
    assert payload["shape_left_emu"] == 914_400
    assert payload["shape_left_ratio"] == 0.1
    source_metadata = cast(Mapping[str, object], payload["source_metadata"])
    assert source_metadata["shape_top_emu"] == 1_828_800
    assert payload["is_active"] is False


def test_qdrant_payload_indexes_keep_existing_filter_contract() -> None:
    """위치 payload를 확장해도 기존 Collection의 필터 인덱스 집합을 유지한다."""

    indexed_fields = {field_name for field_name, _ in _PAYLOAD_INDEXES}

    assert indexed_fields == {
        "users_idx",
        "file_idx",
        "folder_idx",
        "rag_document_idx",
        "is_active",
        "index_version",
        "embedding_model",
        "file_type",
        "parser_version",
    }


class _Transaction:
    """prepare_indexing 테스트용 비동기 트랜잭션 context 대역."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback


class _Session:
    """실제 DB 없이 SQL 실행 호출만 수용하는 AsyncSession 대역."""

    def __init__(self) -> None:
        self.executions: list[tuple[object, object | None]] = []

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> None:
        self.executions.append((statement, parameters))


class _ReindexRecordingRepository(LocalRagIndexRepository):
    """실제 DB 조회 없이 REINDEX 선택만 기록하는 저장소 테스트 대역."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.captured_run_types: list[str] = []

    async def _read_exact_document(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedding_model: str,
    ) -> tuple[int, str] | None:
        del metadata, embedding_model
        return None

    async def _read_last_insert_id(self, *, operation: str) -> int:
        del operation
        return 101

    async def _read_previous_indexed_document_ids(
        self,
        *,
        users_idx: int,
        file_idx: int,
        current_rag_document_idx: int,
    ) -> tuple[int, ...]:
        del users_idx, file_idx, current_rag_document_idx
        return (77,)

    async def _insert_index_run(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
        started_at: datetime,
        run_type: str,
    ) -> int:
        del rag_document_idx, metadata, embedded_document, started_at
        self.captured_run_types.append(run_type)
        return 202


@pytest.mark.asyncio
async def test_existing_index_causes_reindex_run_type() -> None:
    """같은 파일의 이전 정상 문서가 있으면 실행 이력을 REINDEX로 기록한다."""

    session = _Session()
    repository = _ReindexRecordingRepository(cast(AsyncSession, session))

    result = await repository.prepare_indexing(
        metadata=_metadata(),
        embedded_document=_embedded_document(),
    )

    assert repository.captured_run_types == ["REINDEX"]
    assert result.previous_rag_document_idxs == (77,)
    assert result.reuses_existing_index is False
