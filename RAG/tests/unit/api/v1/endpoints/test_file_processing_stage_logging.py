"""파일 처리 함수가 단계별 요약 로그만 남기는지 실제 호출 흐름으로 검증한다."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from jipsa_rag.api.v1.endpoints import file_processing as file_processing_module
from jipsa_rag.core.logging import RequestContextFilter
from jipsa_rag.core.request_context import reset_request_id, set_request_id
from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    ChunkingContext,
    TextChunk,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)
from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata
from jipsa_rag.schemas.file_processing import FileProcessingRequest
from jipsa_rag.services.file_indexing import FileIndexingResult

_REQUEST_ID = "11111111-1111-4111-8111-111111111111"
_FILE_BYTES = b"test-document-bytes"
_FILE_HASH = hashlib.sha256(_FILE_BYTES).hexdigest()
_PRIVATE_FILE_NAME = "private-file-name-that-must-not-be-logged.pdf"
_PRIVATE_URL = (
    "https://example-bucket.s3.ap-northeast-2.amazonaws.com/private/document.pdf?"
    "X-Amz-Signature=private-signature-that-must-not-be-logged"
)

_EXPECTED_STAGE_EVENTS = (
    "file_download_completed",
    "document_parsing_ocr_completed",
    "document_chunking_completed",
    "document_embedding_completed",
    "file_indexing_completed",
    "file_processing_completed",
)

_PROHIBITED_FIELDS = frozenset(
    {
        "content",
        "chunk_content",
        "chunks",
        "question",
        "prompt",
        "ocr_text",
        "answer",
        "embedding",
        "embeddings",
        "embedding_vector",
        "vectors",
        "payload",
        "request_body",
        "response_body",
    }
)


def _read_log_field(
    record: logging.LogRecord,
    field_name: str,
) -> object:
    """구조화 ``extra``로 추가된 로그 필드를 누락 여부까지 검증하며 읽는다.

    ``logging.LogRecord``의 정적 타입에는 애플리케이션이 ``extra``로 주입한
    ``event``, ``request_id`` 등의 필드가 선언되어 있지 않다. 테스트에서 상수 이름을
    ``getattr()``로 읽으면 Ruff B009 규칙을 위반하고, 직접 속성 접근은
    Mypy strict에서
    정의되지 않은 속성으로 판단될 수 있다. 따라서 실제 저장 위치인 ``__dict__``를
    사용하고, 필드가 누락되면 명확한 AssertionError를 발생시킨다.
    """

    try:
        return record.__dict__[field_name]
    except KeyError as error:
        raise AssertionError(f"로그 필드가 누락되었습니다: {field_name}") from error


class _RecordHandler(logging.Handler):
    """Formatter 적용 전 단계 로그의 구조화 필드를 보관한다."""

    def __init__(self) -> None:
        """빈 레코드 목록으로 Handler를 초기화한다."""

        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """원문을 별도 문자열로 복사하지 않고 LogRecord를 저장한다."""

        self.records.append(record)


@dataclass(frozen=True, slots=True)
class _DownloadedFileStub:
    """다운로드 완료 후 파서에 필요한 최소 파일 정보."""

    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ProcessingSettingsStub:
    """파일 처리 함수가 사용하는 문서 설정의 최소 대역."""

    index_version: int = 2
    ocr_enabled: bool = True


@dataclass(frozen=True, slots=True)
class _LoggingSettingsStub:
    """모든 정상 테스트 단계를 INFO로 유지하는 로그 설정 대역."""

    slow_stage_threshold_ms: float = 1_000_000.0


class _FileDownloaderStub:
    """네트워크 없이 지정된 임시 파일을 반환하는 다운로더 대역."""

    def __init__(self, file_path: Path) -> None:
        """파서가 읽을 임시 파일 경로를 저장한다."""

        self._file_path = file_path

    @asynccontextmanager
    async def download_and_validate(
        self,
        *,
        file_url: str,
        users_idx: int,
        file_idx: int,
    ) -> AsyncIterator[_DownloadedFileStub]:
        """입력 식별자를 검증하고 고정된 다운로드 결과를 반환한다."""

        assert file_url == _PRIVATE_URL
        assert users_idx == 45
        assert file_idx == 123
        yield _DownloadedFileStub(
            path=self._file_path,
            size_bytes=len(_FILE_BYTES),
            sha256=_FILE_HASH,
        )


class _DocumentParserStub:
    """두 개 구조 단위 중 한 개에 텍스트가 있는 PDF 파서 대역."""

    @property
    def parser_type(self) -> str:
        """로그와 색인 메타데이터에 사용할 파서 종류를 반환한다."""

        return "PDF_HYBRID_OCR"

    @property
    def parser_version(self) -> str:
        """결정적 Chunk ID와 색인 메타데이터에 사용할 버전을 반환한다."""

        return "2.0.0"

    async def parse(self, file_path: Path) -> ParsedDocument:
        """고정된 구조 단위와 텍스트 단위 수를 가진 문서를 반환한다."""

        assert file_path.exists()
        return ParsedDocument(
            file_type=DocumentType.PDF,
            units=(
                ParsedDocumentUnit(
                    text="searchable test text",
                    source_metadata={
                        "page_number": 1,
                    },
                ),
                ParsedDocumentUnit(
                    text="",
                    source_metadata={
                        "page_number": 2,
                    },
                ),
            ),
            document_metadata={
                "page_count": 2,
            },
        )


class _ParserFactoryStub:
    """PDF 요청에 고정 파서를 반환하는 Factory 대역."""

    def __init__(self, parser: _DocumentParserStub) -> None:
        """반환할 파서 객체를 저장한다."""

        self._parser = parser

    def get_parser(
        self,
        file_type: DocumentType | str,
    ) -> _DocumentParserStub:
        """PDF 형식만 허용하고 테스트 파서를 반환한다."""

        assert str(file_type).strip().lower() == "pdf"
        return self._parser


class _DocumentChunkerStub:
    """요청된 개수만큼 결정적 테스트 청크를 생성한다."""

    def __init__(self, chunk_count: int) -> None:
        """생성할 청크 수를 저장한다."""

        self._chunk_count = chunk_count

    async def chunk(
        self,
        *,
        document: ParsedDocument,
        context: ChunkingContext,
    ) -> ChunkedDocument:
        """파일 처리량 확장 테스트에 사용할 청크 집합을 반환한다."""

        assert context.users_idx == 45
        assert context.file_idx == 123
        assert context.file_hash == _FILE_HASH

        chunks = tuple(self._build_chunk(index) for index in range(self._chunk_count))
        return ChunkedDocument(
            file_type=document.file_type,
            chunks=chunks,
            source_unit_count=document.unit_count,
            text_unit_count=document.text_unit_count,
        )

    @staticmethod
    def _build_chunk(index: int) -> TextChunk:
        """순번별 고유 UUID와 해시를 가진 단일 청크를 생성한다."""

        content = f"searchable test chunk {index}"
        return TextChunk(
            chunk_id=str(UUID(int=index + 1)),
            chunk_index=index,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            start_offset=0,
            end_offset=len(content),
            source_metadata={
                "page_number": 1,
                "source_unit_index": 0,
                "unit_start_offset": 0,
                "unit_end_offset": len(content),
            },
        )


class _ChunkEmbedderStub:
    """청크별 고정 차원 벡터를 반환하는 CUDA TEI 대역."""

    embedding_batch_size = 3

    @property
    def embedding_model(self) -> str:
        """청킹 컨텍스트와 응답에 사용할 모델 식별자를 반환한다."""

        return "test/embedding-model"

    async def embed(
        self,
        *,
        document: ChunkedDocument,
    ) -> EmbeddedDocument:
        """벡터 원문을 로그로 전달하지 않고 임베딩 결과만 반환한다."""

        return EmbeddedDocument(
            embedding_model=self.embedding_model,
            embedding_dim=3,
            chunks=tuple(
                EmbeddedChunk(
                    chunk=chunk,
                    embedding=(0.1, 0.2, 0.3),
                )
                for chunk in document.chunks
            ),
        )


class _FileIndexingServiceStub:
    """Local RAG DB와 Qdrant 식별자만 반환하는 색인 대역."""

    async def index(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> FileIndexingResult:
        """메타데이터 범위를 확인하고 고정된 색인 결과를 반환한다."""

        assert metadata.users_idx == 45
        assert metadata.file_idx == 123
        assert metadata.file_name == _PRIVATE_FILE_NAME
        return FileIndexingResult(
            rag_document_idx=100,
            rag_index_run_idx=200,
            chunk_count=embedded_document.chunk_count,
        )


@contextmanager
def _capture_stage_records() -> Iterator[list[logging.LogRecord]]:
    """파일 처리 Logger를 INFO로 격리하고 Request ID Filter를 적용한다."""

    target_logger = file_processing_module.logger
    previous_handlers = tuple(target_logger.handlers)
    previous_level = target_logger.level
    previous_propagate = target_logger.propagate
    previous_disabled = target_logger.disabled

    handler = _RecordHandler()
    handler.addFilter(RequestContextFilter())

    target_logger.handlers.clear()
    target_logger.addHandler(handler)
    target_logger.setLevel(logging.INFO)
    target_logger.propagate = False
    target_logger.disabled = False

    try:
        yield handler.records
    finally:
        target_logger.handlers.clear()
        target_logger.handlers.extend(previous_handlers)
        target_logger.setLevel(previous_level)
        target_logger.propagate = previous_propagate
        target_logger.disabled = previous_disabled
        handler.close()


def _build_request() -> FileProcessingRequest:
    """원문과 URL 비노출 검증에 사용할 파일 처리 요청을 생성한다."""

    return FileProcessingRequest.model_validate(
        {
            "file_idx": 123,
            "user_idx": 45,
            "folder_idx": 9,
            "file_name": _PRIVATE_FILE_NAME,
            "file_type": "pdf",
            "download_url": _PRIVATE_URL,
            "url_expires_in": 900,
        }
    )


async def _run_file_processing(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunk_count: int,
) -> list[logging.LogRecord]:
    """외부 네트워크와 저장소 없이 실제 파일 처리 조정 함수를 실행한다."""

    file_path = tmp_path / "downloaded.document"
    file_path.write_bytes(_FILE_BYTES)

    processing_settings = _ProcessingSettingsStub()
    logging_settings = _LoggingSettingsStub()

    def get_processing_settings() -> _ProcessingSettingsStub:
        return processing_settings

    def get_logging_settings() -> _LoggingSettingsStub:
        return logging_settings

    monkeypatch.setattr(
        file_processing_module,
        "get_document_processing_settings",
        get_processing_settings,
    )
    monkeypatch.setattr(
        file_processing_module,
        "get_logging_settings",
        get_logging_settings,
    )

    request_context_token = set_request_id(_REQUEST_ID)
    try:
        with _capture_stage_records() as records:
            response = await file_processing_module.process_file_processing_request(
                request=_build_request(),
                file_downloader=cast(Any, _FileDownloaderStub(file_path)),
                document_parser_factory=cast(
                    Any,
                    _ParserFactoryStub(_DocumentParserStub()),
                ),
                document_chunker=cast(Any, _DocumentChunkerStub(chunk_count)),
                chunk_embedder=cast(Any, _ChunkEmbedderStub()),
                file_indexing_service=cast(Any, _FileIndexingServiceStub()),
            )
    finally:
        reset_request_id(request_context_token)

    assert response.success is True
    assert response.data is not None
    assert response.data.chunk_count == chunk_count
    return records


@pytest.mark.asyncio
async def test_file_processing_emits_complete_stage_summary_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """다운로드부터 전체 완료까지 필요한 지표가 단계별 한 줄로 기록되어야 한다."""

    records = await _run_file_processing(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        chunk_count=7,
    )

    events = tuple(_read_log_field(record, "event") for record in records)
    assert events == _EXPECTED_STAGE_EVENTS
    assert all(record.levelno == logging.INFO for record in records)
    assert all(_read_log_field(record, "request_id") == _REQUEST_ID for record in records)

    records_by_event = {_read_log_field(record, "event"): record for record in records}

    download = records_by_event["file_download_completed"]
    assert _read_log_field(download, "file_type") == "pdf"
    assert _read_log_field(download, "size_bytes") == len(_FILE_BYTES)
    assert isinstance(_read_log_field(download, "duration_ms"), float)

    parsing = records_by_event["document_parsing_ocr_completed"]
    assert _read_log_field(parsing, "structure_unit_count") == 2
    assert _read_log_field(parsing, "text_unit_count") == 1
    assert _read_log_field(parsing, "parser_type") == "PDF_HYBRID_OCR"
    assert _read_log_field(parsing, "ocr_enabled") is True

    chunking = records_by_event["document_chunking_completed"]
    assert _read_log_field(chunking, "chunk_count") == 7

    embedding = records_by_event["document_embedding_completed"]
    assert _read_log_field(embedding, "chunk_count") == 7
    assert _read_log_field(embedding, "embedding_dim") == 3
    assert _read_log_field(embedding, "batch_count") == 3

    indexing = records_by_event["file_indexing_completed"]
    assert _read_log_field(indexing, "rag_document_idx") == 100
    assert _read_log_field(indexing, "rag_index_run_idx") == 200
    assert _read_log_field(indexing, "chunk_count") == 7

    completed = records_by_event["file_processing_completed"]
    assert _read_log_field(completed, "success") is True
    assert _read_log_field(completed, "rag_document_idx") == 100
    assert _read_log_field(completed, "rag_index_run_idx") == 200
    assert isinstance(_read_log_field(completed, "total_duration_ms"), float)

    for record in records:
        assert _PROHIBITED_FIELDS.isdisjoint(record.__dict__)
        assert _PRIVATE_FILE_NAME not in record.getMessage()
        assert _PRIVATE_URL not in record.getMessage()
        assert "private-signature-that-must-not-be-logged" not in record.getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_count", [1, 100])
async def test_info_stage_log_count_does_not_scale_with_chunk_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunk_count: int,
) -> None:
    """청크 수가 증가해도 INFO 단계 로그는 고정된 여섯 줄만 생성해야 한다."""

    records = await _run_file_processing(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        chunk_count=chunk_count,
    )

    assert tuple(_read_log_field(record, "event") for record in records) == _EXPECTED_STAGE_EVENTS
    assert len(records) == len(_EXPECTED_STAGE_EVENTS)
    chunking_log_count = sum(
        _read_log_field(record, "event") == "document_chunking_completed" for record in records
    )
    assert chunking_log_count == 1
    embedding_log_count = sum(
        _read_log_field(record, "event") == "document_embedding_completed" for record in records
    )
    assert embedding_log_count == 1
