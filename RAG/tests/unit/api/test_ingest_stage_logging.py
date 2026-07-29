"""Manifest 조회와 성공·실패 콜백의 단계 로그 계약을 검증한다."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import pytest

from jipsa_rag.api import ingest as ingest_module
from jipsa_rag.core.logging import RequestContextFilter
from jipsa_rag.core.request_context import reset_request_id, set_request_id
from jipsa_rag.infrastructure.indexing.chunk_snapshot_models import (
    IndexedChunkSnapshot,
    IndexedDocumentSnapshot,
)
from jipsa_rag.schemas.common import ApiResponse
from jipsa_rag.schemas.file_processing import (
    FileProcessingCompletedResponse,
    FileProcessingRequest,
)
from jipsa_rag.schemas.ingestion import ChunkSynchronizationRequest

_REQUEST_ID = "11111111-1111-4111-8111-111111111111"
_PRIVATE_CONTENT = "private chunk content that must not be logged"
_PRIVATE_ERROR = "private callback error detail that must not be logged"
_PRIVATE_URL = (
    "https://example-bucket.s3.ap-northeast-2.amazonaws.com/private/document.pdf?"
    "X-Amz-Signature=private-signature-that-must-not-be-logged"
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
    """인제스트 조정 Logger의 LogRecord를 포맷 이전 상태로 수집한다."""

    def __init__(self) -> None:
        """빈 레코드 목록으로 Handler를 초기화한다."""

        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """추가 직렬화 없이 레코드 참조를 저장한다."""

        self.records.append(record)


@dataclass(frozen=True, slots=True)
class _LoggingSettingsStub:
    """정상 단계가 WARNING으로 승격되지 않게 하는 로그 설정 대역."""

    slow_stage_threshold_ms: float = 1_000_000.0


class _ApplicationServerClientStub:
    """Manifest와 콜백을 메모리에서 처리하는 백엔드 클라이언트 대역."""

    def __init__(self, manifest: FileProcessingRequest) -> None:
        """반환할 Manifest와 콜백 기록 목록을 초기화한다."""

        self._manifest = manifest
        self.callback_calls: list[dict[str, object]] = []

    async def fetch_manifest(self, *, file_idx: int) -> FileProcessingRequest:
        """요청 File_IDX를 확인하고 최신 Manifest 대역을 반환한다."""

        assert file_idx == self._manifest.file_idx
        return self._manifest

    async def notify_ingest_complete(
        self,
        *,
        file_idx: int,
        success: bool,
        error_message: str | None = None,
        index_version: int | None = None,
        chunks: tuple[ChunkSynchronizationRequest, ...] | None = None,
    ) -> None:
        """콜백 payload를 전송하지 않고 호출 메타데이터만 보관한다."""

        self.callback_calls.append(
            {
                "file_idx": file_idx,
                "success": success,
                "error_message": error_message,
                "index_version": index_version,
                "chunks": chunks,
            }
        )


class _ActiveSnapshotServiceStub:
    """고정된 최신 활성 청크 스냅샷을 제공하는 서비스 대역."""

    def __init__(self, snapshot: IndexedDocumentSnapshot) -> None:
        """File_IDX lock 안에서 반환할 스냅샷을 저장한다."""

        self._snapshot = snapshot

    @asynccontextmanager
    async def hold_latest_active_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
    ) -> AsyncIterator[IndexedDocumentSnapshot]:
        """스냅샷 범위를 검증하고 읽기 전용 객체를 반환한다."""

        assert users_idx == self._snapshot.users_idx
        assert file_idx == self._snapshot.file_idx
        yield self._snapshot


@contextmanager
def _capture_ingest_records() -> Iterator[list[logging.LogRecord]]:
    """인제스트 Logger를 INFO로 격리하고 Request ID Filter를 적용한다."""

    target_logger = ingest_module.logger
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


def _build_manifest() -> FileProcessingRequest:
    """백엔드 재조회와 URL 비노출 검증에 사용할 Manifest를 생성한다."""

    return FileProcessingRequest.model_validate(
        {
            "file_idx": 123,
            "user_idx": 45,
            "folder_idx": 9,
            "file_name": "private-manifest-file.pdf",
            "file_type": "pdf",
            "download_url": _PRIVATE_URL,
            "url_expires_in": 900,
        }
    )


def _build_processing_response() -> ApiResponse[FileProcessingCompletedResponse]:
    """파일 처리 성공 후 인제스트 조정 계층이 받는 응답을 생성한다."""

    return ApiResponse[FileProcessingCompletedResponse](
        success=True,
        code="FILE_INDEXING_COMPLETED",
        message="File processing completed.",
        data=FileProcessingCompletedResponse(
            rag_document_idx=100,
            file_idx=123,
            user_idx=45,
            folder_idx=9,
            file_name="private-manifest-file.pdf",
            file_type="pdf",
            file_size_bytes=1024,
            page_count=2,
            text_unit_count=2,
            chunk_count=1,
            embedding_model="test/embedding-model",
            embedding_dim=3,
            processing_status="INDEXED",
        ),
    )


def _build_snapshot() -> IndexedDocumentSnapshot:
    """성공 콜백에 사용할 단일 활성 청크 스냅샷을 생성한다."""

    content_hash = hashlib.sha256(_PRIVATE_CONTENT.encode("utf-8")).hexdigest()
    chunk = IndexedChunkSnapshot(
        chunk_id=str(UUID(int=1)),
        chunk_index=0,
        content=_PRIVATE_CONTENT,
        content_hash=content_hash,
        token_count=6,
        source_metadata={
            "page_number": 1,
        },
    )
    return IndexedDocumentSnapshot(
        rag_document_idx=100,
        users_idx=45,
        file_idx=123,
        index_version=2,
        chunk_count=1,
        chunks=(chunk,),
    )


@pytest.mark.asyncio
async def test_manifest_and_success_callback_emit_safe_stage_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manifest와 성공 콜백은 시간·식별자·개수만 각각 한 줄로 기록해야 한다."""

    manifest = _build_manifest()
    client = _ApplicationServerClientStub(manifest)
    snapshot_service = _ActiveSnapshotServiceStub(_build_snapshot())
    processing_response = _build_processing_response()
    logging_settings = _LoggingSettingsStub()

    def get_logging_settings() -> _LoggingSettingsStub:
        return logging_settings

    async def process_file_processing_request(
        **_: Any,
    ) -> ApiResponse[FileProcessingCompletedResponse]:
        return processing_response

    monkeypatch.setattr(ingest_module, "get_logging_settings", get_logging_settings)
    monkeypatch.setattr(
        ingest_module,
        "process_file_processing_request",
        process_file_processing_request,
    )

    request_context_token = set_request_id(_REQUEST_ID)
    try:
        with _capture_ingest_records() as records:
            response = await ingest_module.ingest_file(
                request=manifest,
                file_downloader=cast(Any, object()),
                document_parser_factory=cast(Any, object()),
                document_chunker=cast(Any, object()),
                chunk_embedder=cast(Any, object()),
                file_indexing_service=cast(Any, object()),
                active_chunk_snapshot_service=cast(Any, snapshot_service),
                application_server_client=cast(Any, client),
            )
    finally:
        reset_request_id(request_context_token)

    assert response is processing_response
    assert [_read_log_field(record, "event") for record in records] == [
        "ingest_manifest_fetch_completed",
        "ingest_success_callback_completed",
    ]

    manifest_record, callback_record = records
    assert _read_log_field(manifest_record, "users_idx") == 45
    assert _read_log_field(manifest_record, "file_idx") == 123
    assert _read_log_field(manifest_record, "file_type") == "pdf"
    assert isinstance(_read_log_field(manifest_record, "duration_ms"), float)

    assert _read_log_field(callback_record, "callback_type") == "success"
    assert _read_log_field(callback_record, "success") is True
    assert _read_log_field(callback_record, "rag_document_idx") == 100
    assert _read_log_field(callback_record, "index_version") == 2
    assert _read_log_field(callback_record, "chunk_count") == 1

    assert client.callback_calls[0]["success"] is True
    callback_chunks = cast(
        tuple[ChunkSynchronizationRequest, ...],
        client.callback_calls[0]["chunks"],
    )
    assert len(callback_chunks) == 1

    for record in records:
        assert _PRIVATE_CONTENT not in record.getMessage()
        assert _PRIVATE_URL not in record.getMessage()
        assert "private-signature-that-must-not-be-logged" not in record.getMessage()
        assert "chunks" not in record.__dict__
        assert "payload" not in record.__dict__


@pytest.mark.asyncio
async def test_failure_callback_emits_completion_without_error_or_payload() -> None:
    """실패 콜백 성공 로그에는 원본 예외 메시지와 callback payload가 없어야 한다."""

    client = _ApplicationServerClientStub(_build_manifest())
    request_context_token = set_request_id(_REQUEST_ID)
    try:
        with _capture_ingest_records() as records:
            await ingest_module._notify_ingest_failure_safely(
                client=cast(Any, client),
                file_idx=123,
                processing_error=RuntimeError(_PRIVATE_ERROR),
                slow_stage_threshold_ms=1_000_000.0,
            )
    finally:
        reset_request_id(request_context_token)

    assert [_read_log_field(record, "event") for record in records] == [
        "ingest_failure_callback_completed"
    ]
    record = records[0]
    assert _read_log_field(record, "callback_type") == "failure"
    assert _read_log_field(record, "success") is False
    assert _read_log_field(record, "processing_error_type") == "RuntimeError"
    assert isinstance(_read_log_field(record, "duration_ms"), float)
    assert _PRIVATE_ERROR not in record.getMessage()
    assert "error_message" not in record.__dict__
    assert "payload" not in record.__dict__

    assert client.callback_calls[0]["success"] is False
    assert client.callback_calls[0]["index_version"] is None
    assert client.callback_calls[0]["chunks"] is None
