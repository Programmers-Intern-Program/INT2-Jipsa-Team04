"""고정 다중 형식 문서를 실제 OCR·TEI·DB·Qdrant 파이프라인으로 검증한다.

Issue #123의 두 번째 작업 묶음은 첫 번째 묶음에서 저장소에 고정한 실제 문서를
운영 Local RAG 처리 흐름에 통과시킨다. AWS Backend와 Presigned GET URL의 HTTP
경계만 결정적인 ``MockTransport``로 교체하고 다음 구성요소는 실제 구현을 사용한다.

- PDF, DOCX, PPTX, XLSX, TXT 운영 파서와 구조화 청커
- CUDA EasyOCR과 문서별 이미지 위치 연결
- CUDA TEI 문서·질의 임베딩
- Local RAG MySQL 또는 MariaDB 문서·청크·색인 실행 이력
- Qdrant Point, vector, payload, 활성 상태와 사용자·문서 범위

실제 GPU 추론과 로컬 인프라 데이터 변경을 동반하므로
``JIPSA_RAG_RUN_E2E=1``을 명시한 경우에만 실행한다. 테스트 데이터는 전용 사용자와
파일 ID 범위에 한정하여 시작 전과 종료 후 정리한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit

import httpx2
import pytest
import torch
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jipsa_rag.api.ingest import get_application_server_ingest_client
from jipsa_rag.api.v1.endpoints.file_processing import (
    get_document_parser_factory,
    get_file_downloader,
)
from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.document_processing import (
    DocumentProcessingSettings,
    get_document_processing_settings,
)
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.infrastructure.document.images.models import ExtractedDocumentImage
from jipsa_rag.infrastructure.document.images.pdf import PdfImageExtractor
from jipsa_rag.infrastructure.document.media_aware import OcrAwarePdfDocumentParser
from jipsa_rag.infrastructure.document.parser_factory import DocumentParserFactory
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader
from jipsa_rag.infrastructure.ocr import (
    EasyOcrEngine,
    OcrDocumentEnricher,
    OcrRecognitionResult,
)
from jipsa_rag.infrastructure.ocr.exceptions import OcrRecognitionError
from jipsa_rag.main import app

# ============================================================
# 실행 제어와 고정 Fixture 경로
# ============================================================

_RUN_ENV: Final[str] = "JIPSA_RAG_RUN_E2E"
_FIXTURE_ROOT: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures/e2e_documents"
_DOCUMENT_MANIFEST_PATH: Final[Path] = _FIXTURE_ROOT / "manifest.json"
_PIPELINE_EXPECTATIONS_PATH: Final[Path] = _FIXTURE_ROOT / "pipeline_expectations.json"

_BACKEND_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/internal/files/(?P<file_idx>[1-9][0-9]*)/"
    r"(?P<operation>manifest|ingest-complete)$"
)
_DOWNLOAD_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/fixed-pipeline/(?P<file_idx>[1-9][0-9]*)/(?P<file_name>[^/]+)$"
)
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_NORMALIZATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^A-Z0-9]+")
_INDEX_VERSION: Final[int] = 2

_TEXT_CASE_IDS: Final[tuple[str, ...]] = (
    "pdf-text-table",
    "docx-structure",
    "pptx-structure",
    "xlsx-structure",
    "txt-lines-utf8",
)
_OCR_CASE_IDS: Final[tuple[str, ...]] = (
    "pdf-with-image",
    "docx-with-image",
    "pptx-with-image",
    "xlsx-with-image",
    "scanned-document",
    "hybrid-image-only-page",
)
_PARTIAL_CASE_ID: Final[str] = "pdf-partial-ocr"
_MAIN_CASE_IDS: Final[tuple[str, ...]] = (*_TEXT_CASE_IDS, *_OCR_CASE_IDS)


# ============================================================
# JSON 계약 모델과 동적 값 좁히기
# ============================================================


def _object(value: object, label: str) -> dict[str, object]:
    """동적 JSON 값을 문자열 key를 가진 객체로 좁힌다."""

    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be a JSON object.")
    if any(not isinstance(key, str) for key in value):
        raise AssertionError(f"{label} must contain only string keys.")
    return cast(dict[str, object], value)


def _objects(mapping: Mapping[str, object], key: str) -> list[dict[str, object]]:
    """매핑에서 JSON 객체 배열을 읽는다."""

    value = mapping.get(key)
    if not isinstance(value, list):
        raise AssertionError(f"{key} must be a JSON array.")
    return [_object(item, f"{key} item") for item in cast(list[object], value)]


def _str(mapping: Mapping[str, object], key: str) -> str:
    """매핑에서 비어 있지 않은 문자열을 읽는다."""

    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise AssertionError(f"{key} must be a non-empty string.")
    return value


def _int(mapping: Mapping[str, object], key: str) -> int:
    """매핑에서 bool이 아닌 정수를 읽는다."""

    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"{key} must be an integer.")
    return value


def _optional_int(mapping: Mapping[str, object], key: str) -> int | None:
    """매핑에서 null 또는 bool이 아닌 정수를 읽는다."""

    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"{key} must be an integer or null.")
    return value


def _bool(mapping: Mapping[str, object], key: str) -> bool:
    """매핑에서 JSON boolean을 읽는다."""

    value = mapping.get(key)
    if not isinstance(value, bool):
        raise AssertionError(f"{key} must be a boolean.")
    return value


def _db_bool(mapping: Mapping[str, object], key: str) -> bool:
    """DB의 bool 또는 0·1 값을 Python bool로 정규화한다."""

    value = mapping.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise AssertionError(f"{key} must be a database boolean.")


def _equivalent(actual: object, expected: object) -> bool:
    """JSON list와 런타임 tuple 차이를 허용하여 메타데이터 의미를 비교한다."""

    if isinstance(expected, list):
        if not isinstance(actual, Sequence) or isinstance(actual, str | bytes | bytearray):
            return False
        actual_values = list(cast(Sequence[object], actual))
        return len(actual_values) == len(expected) and all(
            _equivalent(actual_item, expected_item)
            for actual_item, expected_item in zip(actual_values, expected, strict=True)
        )

    if isinstance(expected, dict):
        if not isinstance(actual, Mapping):
            return False
        actual_mapping = cast(Mapping[object, object], actual)
        expected_mapping = cast(dict[object, object], expected)
        return all(
            key in actual_mapping and _equivalent(actual_mapping[key], expected_value)
            for key, expected_value in expected_mapping.items()
        )

    return actual == expected


def _assert_metadata_contains(
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    label: str,
) -> None:
    """예상 위치 메타데이터가 실제 값에 동일한 의미로 포함되는지 확인한다."""

    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        assert _equivalent(actual_value, expected_value), (
            f"{label}[{key!r}] mismatch: "
            f"expected={expected_value!r}, actual={actual_value!r}"
        )


def _source_metadata(mapping: Mapping[str, object]) -> dict[str, object]:
    """DB 또는 Qdrant의 source_metadata를 일반 JSON 객체로 변환한다."""

    value = mapping.get("source_metadata")
    if isinstance(value, dict):
        return _object(value, "source_metadata")
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return _object(json.loads(value), "source_metadata")
    raise AssertionError("source_metadata must be a JSON object or JSON string.")


def _load_json(path: Path, label: str) -> dict[str, object]:
    """UTF-8 JSON 파일을 읽고 최상위 객체 계약을 확인한다."""

    return _object(json.loads(path.read_text(encoding="utf-8")), label)


_DOCUMENT_MANIFEST: Final[dict[str, object]] = _load_json(
    _DOCUMENT_MANIFEST_PATH,
    "document manifest",
)
_PIPELINE_EXPECTATIONS: Final[dict[str, object]] = _load_json(
    _PIPELINE_EXPECTATIONS_PATH,
    "pipeline expectations",
)
_DOCUMENT_MANIFEST_BY_ID: Final[dict[str, dict[str, object]]] = {
    _str(document, "id"): document for document in _objects(_DOCUMENT_MANIFEST, "documents")
}

_TEST_USER_IDX: Final[int] = _int(_PIPELINE_EXPECTATIONS, "user_idx")
_TEST_FOLDER_IDX: Final[int] = _int(_PIPELINE_EXPECTATIONS, "folder_idx")
_OTHER_USER_IDX: Final[int] = _TEST_USER_IDX + 1


@dataclass(frozen=True, slots=True)
class ExpectedAssertion:
    """청크에서 확인할 고유 토큰과 원본 위치 계약."""

    token: str
    source_metadata: Mapping[str, object]
    is_ocr: bool


@dataclass(frozen=True, slots=True)
class FixtureCase:
    """고정 원본 문서와 실제 파이프라인 예상 결과."""

    case_id: str
    file_idx: int
    relative_path: Path
    file_type: str
    content_type: str
    sha256: str
    size_bytes: int
    parser_type: str
    parser_version: str
    assertions: tuple[ExpectedAssertion, ...]
    forced_failure_image_index: int | None = None
    success_token: str | None = None
    failure_token: str | None = None

    @property
    def path(self) -> Path:
        """저장소에 고정된 실제 문서 경로를 반환한다."""

        if self.relative_path.is_absolute() or ".." in self.relative_path.parts:
            raise AssertionError("Fixture paths must remain below the fixture root.")
        return _FIXTURE_ROOT / self.relative_path

    @property
    def file_name(self) -> str:
        """Backend manifest와 Qdrant payload에 사용할 표시 파일명."""

        return self.relative_path.name

    @property
    def download_url(self) -> str:
        """테스트 전용 Presigned GET URL 형태의 HTTPS URL."""

        return (
            f"https://files.e2e.invalid/fixed-pipeline/{self.file_idx}/{self.file_name}"
            f"?X-Amz-Signature=issue-123-{self.file_idx}"
        )

    @property
    def manifest(self) -> dict[str, object]:
        """Backend manifest와 POST /ingest가 공유할 요청 본문."""

        return {
            "file_idx": self.file_idx,
            "user_idx": _TEST_USER_IDX,
            "folder_idx": _TEST_FOLDER_IDX,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "download_url": self.download_url,
            "url_expires_in": 900,
        }


def _build_case(expectation: Mapping[str, object]) -> FixtureCase:
    """파이프라인 기대값과 기존 문서 manifest를 하나의 Case로 결합한다."""

    case_id = _str(expectation, "id")
    base = _DOCUMENT_MANIFEST_BY_ID.get(case_id, {})

    path_value = expectation.get("path", base.get("path"))
    file_type_value = expectation.get("file_type", base.get("file_type"))
    content_type_value = expectation.get("content_type", base.get("content_type"))
    sha256_value = expectation.get("sha256", base.get("sha256"))
    size_value = expectation.get("size_bytes", base.get("size_bytes"))

    if not isinstance(path_value, str) or not path_value:
        raise AssertionError(f"{case_id} requires a fixture path.")
    if not isinstance(file_type_value, str) or not file_type_value:
        raise AssertionError(f"{case_id} requires file_type.")
    if not isinstance(content_type_value, str) or not content_type_value:
        raise AssertionError(f"{case_id} requires content_type.")
    if not isinstance(sha256_value, str) or not _SHA256_PATTERN.fullmatch(sha256_value):
        raise AssertionError(f"{case_id} requires a SHA-256 value.")
    if isinstance(size_value, bool) or not isinstance(size_value, int):
        raise AssertionError(f"{case_id} requires size_bytes.")

    assertions: list[ExpectedAssertion] = []
    for assertion in _objects(expectation, "assertions"):
        metadata = _object(assertion.get("metadata"), "assertion metadata")
        is_ocr_value = assertion.get("ocr", False)
        if not isinstance(is_ocr_value, bool):
            raise AssertionError("assertion.ocr must be a boolean.")
        assertions.append(
            ExpectedAssertion(
                token=_str(assertion, "token"),
                source_metadata=metadata,
                is_ocr=is_ocr_value,
            )
        )

    forced_failure = expectation.get("forced_failure_image_index")
    if forced_failure is not None and (
        isinstance(forced_failure, bool) or not isinstance(forced_failure, int)
    ):
        raise AssertionError("forced_failure_image_index must be an integer or null.")

    success_token = expectation.get("success_token")
    failure_token = expectation.get("failure_token")
    if success_token is not None and not isinstance(success_token, str):
        raise AssertionError("success_token must be a string or null.")
    if failure_token is not None and not isinstance(failure_token, str):
        raise AssertionError("failure_token must be a string or null.")

    return FixtureCase(
        case_id=case_id,
        file_idx=_int(expectation, "file_idx"),
        relative_path=Path(path_value),
        file_type=file_type_value,
        content_type=content_type_value,
        sha256=sha256_value,
        size_bytes=size_value,
        parser_type=_str(expectation, "expected_parser_type"),
        parser_version=_str(expectation, "expected_parser_version"),
        assertions=tuple(assertions),
        forced_failure_image_index=cast(int | None, forced_failure),
        success_token=cast(str | None, success_token),
        failure_token=cast(str | None, failure_token),
    )


_ALL_CASES: Final[tuple[FixtureCase, ...]] = tuple(
    _build_case(value) for value in _objects(_PIPELINE_EXPECTATIONS, "cases")
)
_CASES_BY_ID: Final[dict[str, FixtureCase]] = {case.case_id: case for case in _ALL_CASES}
_MAIN_CASES: Final[tuple[FixtureCase, ...]] = tuple(_CASES_BY_ID[value] for value in _MAIN_CASE_IDS)
_TEXT_CASES: Final[tuple[FixtureCase, ...]] = tuple(_CASES_BY_ID[value] for value in _TEXT_CASE_IDS)
_OCR_CASES: Final[tuple[FixtureCase, ...]] = tuple(_CASES_BY_ID[value] for value in _OCR_CASE_IDS)
_PARTIAL_CASE: Final[FixtureCase] = _CASES_BY_ID[_PARTIAL_CASE_ID]
_MAIN_FILE_IDXS: Final[tuple[int, ...]] = tuple(case.file_idx for case in _MAIN_CASES)
_ALL_FILE_IDXS: Final[tuple[int, ...]] = (*_MAIN_FILE_IDXS, _PARTIAL_CASE.file_idx)


def _canonical_token(value: str) -> str:
    """OCR 공백·구두점 차이를 제거한 대문자 영숫자 토큰을 반환한다."""

    return _TOKEN_NORMALIZATION_PATTERN.sub("", value.upper())


def _contains_token(content: str, token: str) -> bool:
    """텍스트 파서와 OCR 결과 모두에 사용할 안정적인 토큰 포함 판정."""

    return _canonical_token(token) in _canonical_token(content)


def _matching_chunks(
    chunks: Sequence[Mapping[str, object]],
    assertion: ExpectedAssertion,
) -> tuple[Mapping[str, object], ...]:
    """고유 토큰이 포함된 청크를 원본 순서대로 반환한다."""

    return tuple(
        chunk
        for chunk in chunks
        if _contains_token(_str(chunk, "content"), assertion.token)
    )


# ============================================================
# Backend·다운로드 HTTP 경계
# ============================================================


@dataclass(slots=True)
class BackendRecorder:
    """고정 manifest를 반환하고 실제 ingest-complete callback을 기록한다."""

    settings: Settings
    cases: Mapping[int, FixtureCase]
    manifest_requests: list[int] = field(default_factory=list)
    callbacks: dict[int, list[dict[str, object]]] = field(default_factory=dict)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        """허용된 Backend 내부 API 두 경로만 처리한다."""

        matched = _BACKEND_PATH_PATTERN.fullmatch(request.url.path)
        if matched is None:
            return httpx2.Response(status_code=404)

        file_idx = int(matched.group("file_idx"))
        operation = matched.group("operation")
        case = self.cases.get(file_idx)
        if case is None:
            return httpx2.Response(status_code=404)

        internal_token = self.settings.internal_token
        if internal_token is None:
            raise AssertionError("INTERNAL_TOKEN must be configured for E2E.")
        assert request.headers["X-Internal-Token"] == internal_token.get_secret_value()

        if operation == "manifest":
            assert request.method == "GET"
            self.manifest_requests.append(file_idx)
            return httpx2.Response(status_code=200, json=case.manifest)

        assert operation == "ingest-complete"
        assert request.method == "POST"
        payload = _object(
            json.loads(request.content.decode("utf-8")),
            "ingest-complete payload",
        )
        self.callbacks.setdefault(file_idx, []).append(payload)
        return httpx2.Response(status_code=204)

    def callback(self, file_idx: int) -> dict[str, object]:
        """파일별 성공 callback이 정확히 한 번 전송되었는지 확인한다."""

        payloads = self.callbacks.get(file_idx, [])
        assert len(payloads) == 1, (
            f"file_idx={file_idx} expected one callback, received {len(payloads)}"
        )
        payload = payloads[0]
        assert _bool(payload, "success") is True
        return payload


@dataclass(frozen=True, slots=True)
class DownloadContract:
    """실제 고정 파일 바이트를 HttpFileDownloader에 스트리밍한다."""

    cases: Mapping[int, FixtureCase]

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        """요청 파일 ID·이름을 검증하고 실제 원본 ByteStream을 반환한다."""

        matched = _DOWNLOAD_PATH_PATTERN.fullmatch(request.url.path)
        if matched is None:
            return httpx2.Response(status_code=404)

        file_idx = int(matched.group("file_idx"))
        file_name = matched.group("file_name")
        case = self.cases.get(file_idx)
        if case is None or file_name != case.file_name:
            return httpx2.Response(status_code=404)

        assert request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"
        payload = case.path.read_bytes()
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": case.content_type,
                "Content-Length": str(len(payload)),
            },
            stream=httpx2.ByteStream(payload),
        )


# ============================================================
# Local RAG DB 조회와 정리
# ============================================================


@dataclass(frozen=True, slots=True)
class DatabaseState:
    """활성 문서, 원본 청크와 해당 문서의 전체 색인 실행 이력."""

    document: Mapping[str, object]
    chunks: tuple[Mapping[str, object], ...]
    runs: tuple[Mapping[str, object], ...]


def _db_engine(settings: Settings) -> AsyncEngine:
    """검증과 E2E 정리에만 사용하는 독립 비동기 DB 엔진."""

    return create_async_engine(settings.database_url, pool_pre_ping=True)


async def _database_state(settings: Settings, case: FixtureCase) -> DatabaseState:
    """한 고정 파일의 활성 문서·청크·전체 실행 이력을 조회한다."""

    engine = _db_engine(settings)
    try:
        async with engine.connect() as connection:
            document_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `RAG_Document_IDX` AS `rag_document_idx`,
                                `File_IDX` AS `file_idx`,
                                `Users_IDX` AS `users_idx`,
                                `Folder_IDX` AS `folder_idx`,
                                `File_Name` AS `file_name`,
                                `File_Type` AS `file_type`,
                                `File_Hash` AS `file_hash`,
                                `Index_Version` AS `index_version`,
                                `Parse_Status` AS `parse_status`,
                                `Index_Status` AS `index_status`,
                                `Chunk_Count` AS `chunk_count`,
                                `Parser_Type` AS `parser_type`,
                                `Parser_Version` AS `parser_version`,
                                `Embedding_Model` AS `embedding_model`,
                                `Created_At` AS `created_at`,
                                `Updated_At` AS `updated_at`,
                                (`Deleted_At` IS NOT NULL) AS `is_deleted`
                            FROM `RAG_Document`
                            WHERE `Users_IDX` = :users_idx
                              AND `File_IDX` = :file_idx
                              AND `Deleted_At` IS NULL
                            ORDER BY `RAG_Document_IDX`
                            """
                        ),
                        {
                            "users_idx": _TEST_USER_IDX,
                            "file_idx": case.file_idx,
                        },
                    )
                )
                .mappings()
                .all()
            )
            assert len(document_rows) == 1
            document = cast(Mapping[str, object], document_rows[0])
            rag_document_idx = _int(document, "rag_document_idx")

            chunk_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `Chunk_ID` AS `chunk_id`,
                                `RAG_Document_IDX` AS `rag_document_idx`,
                                `File_IDX` AS `file_idx`,
                                `Users_IDX` AS `users_idx`,
                                `Folder_IDX` AS `folder_idx`,
                                `Chunk_Index` AS `chunk_index`,
                                `Content` AS `content`,
                                `Token_Count` AS `token_count`,
                                `Page` AS `page`,
                                `Slide_No` AS `slide_no`,
                                `Sheet_Name` AS `sheet_name`,
                                `Section_Title` AS `section_title`,
                                `Start_Offset` AS `start_offset`,
                                `End_Offset` AS `end_offset`,
                                `Source_Metadata` AS `source_metadata`,
                                `Content_Hash` AS `content_hash`,
                                `Embedding_Model` AS `embedding_model`,
                                `Index_Version` AS `index_version`,
                                `Created_At` AS `created_at`
                            FROM `RAG_Chunk`
                            WHERE `RAG_Document_IDX` = :rag_document_idx
                            ORDER BY `Chunk_Index`
                            """
                        ),
                        {"rag_document_idx": rag_document_idx},
                    )
                )
                .mappings()
                .all()
            )

            run_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `RAG_Index_Run_IDX` AS `rag_index_run_idx`,
                                `RAG_Document_IDX` AS `rag_document_idx`,
                                `File_IDX` AS `file_idx`,
                                `Users_IDX` AS `users_idx`,
                                `Run_Type` AS `run_type`,
                                `Status` AS `status`,
                                `Parser_Type` AS `parser_type`,
                                `Parser_Version` AS `parser_version`,
                                `Embedding_Model` AS `embedding_model`,
                                `Chunk_Count` AS `chunk_count`,
                                `Error_Message` AS `error_message`,
                                `Started_At` AS `started_at`,
                                `Finished_At` AS `finished_at`,
                                `Created_At` AS `created_at`
                            FROM `RAG_Index_Run`
                            WHERE `RAG_Document_IDX` = :rag_document_idx
                            ORDER BY `RAG_Index_Run_IDX`
                            """
                        ),
                        {"rag_document_idx": rag_document_idx},
                    )
                )
                .mappings()
                .all()
            )

            return DatabaseState(
                document=document,
                chunks=tuple(cast(Mapping[str, object], row) for row in chunk_rows),
                runs=tuple(cast(Mapping[str, object], row) for row in run_rows),
            )
    finally:
        await engine.dispose()


async def _cleanup_database(settings: Settings, file_idxs: Sequence[int]) -> None:
    """지정 E2E 파일의 실행·청크·문서를 FK 역순으로 삭제한다."""

    engine = _db_engine(settings)
    parameters = {
        "users_idx": _TEST_USER_IDX,
        "file_idx_min": min(file_idxs),
        "file_idx_max": max(file_idxs),
    }
    try:
        async with engine.begin() as connection:
            for statement in (
                text(
                    """
                    DELETE FROM `RAG_Index_Run`
                    WHERE `Users_IDX` = :users_idx
                      AND `File_IDX` BETWEEN :file_idx_min AND :file_idx_max
                    """
                ),
                text(
                    """
                    DELETE FROM `RAG_Chunk`
                    WHERE `Users_IDX` = :users_idx
                      AND `File_IDX` BETWEEN :file_idx_min AND :file_idx_max
                    """
                ),
                text(
                    """
                    DELETE FROM `RAG_Document`
                    WHERE `Users_IDX` = :users_idx
                      AND `File_IDX` BETWEEN :file_idx_min AND :file_idx_max
                    """
                ),
            ):
                await connection.execute(statement, parameters)
    finally:
        await engine.dispose()


# ============================================================
# Qdrant 조회와 정리
# ============================================================


def _qdrant_client(settings: Settings) -> AsyncQdrantClient:
    """현재 실제 E2E 환경의 Qdrant 비동기 클라이언트."""

    api_key = (
        settings.qdrant_api_key.get_secret_value()
        if settings.qdrant_api_key is not None
        else None
    )
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        grpc_port=settings.qdrant_grpc_port,
        prefer_grpc=settings.qdrant_prefer_grpc,
        api_key=api_key,
        timeout=max(1, ceil(settings.qdrant_timeout_seconds)),
    )


def _scope_filter(
    *,
    users_idx: int,
    file_idxs: Sequence[int],
    active_only: bool,
) -> models.Filter:
    """사용자·파일 범위와 선택적 활성 조건을 Qdrant filter로 만든다."""

    conditions: list[models.Condition] = [
        models.FieldCondition(
            key="users_idx",
            match=models.MatchValue(value=users_idx),
        ),
        models.FieldCondition(
            key="file_idx",
            match=models.MatchAny(any=list(file_idxs)),
        ),
    ]
    if active_only:
        conditions.append(
            models.FieldCondition(
                key="is_active",
                match=models.MatchValue(value=True),
            )
        )
    return models.Filter(must=conditions)


async def _qdrant_points(
    settings: Settings,
    *,
    users_idx: int,
    file_idxs: Sequence[int],
    active_only: bool = True,
) -> tuple[models.Record, ...]:
    """지정 범위의 Point, payload와 vector를 실제 Qdrant에서 읽는다."""

    client = _qdrant_client(settings)
    try:
        points, next_offset = await client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=_scope_filter(
                users_idx=users_idx,
                file_idxs=file_idxs,
                active_only=active_only,
            ),
            limit=512,
            with_payload=True,
            with_vectors=True,
        )
        assert next_offset is None
        return tuple(points)
    finally:
        await client.close()


async def _cleanup_qdrant(settings: Settings, file_idxs: Sequence[int]) -> None:
    """지정 E2E 사용자·파일의 활성 및 비활성 Point를 모두 삭제한다."""

    client = _qdrant_client(settings)
    try:
        if not await client.collection_exists(settings.qdrant_collection):
            return
        await client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=models.FilterSelector(
                filter=_scope_filter(
                    users_idx=_TEST_USER_IDX,
                    file_idxs=file_idxs,
                    active_only=False,
                )
            ),
            wait=True,
        )
    finally:
        await client.close()


async def _cleanup(settings: Settings, file_idxs: Sequence[int]) -> None:
    """Qdrant 복제 데이터 후 Local RAG 원본 데이터를 정리한다."""

    await _cleanup_qdrant(settings, file_idxs)
    await _cleanup_database(settings, file_idxs)


# ============================================================
# 실제 GPU·Local 인프라 사전 검증
# ============================================================


def _uses_test_only_hostname(url: str) -> bool:
    """URL이 ``*.test`` Mock 전용 예약 호스트인지 확인한다."""

    hostname = urlsplit(url).hostname
    if hostname is None:
        return True
    normalized = hostname.rstrip(".").lower()
    return normalized == "test" or normalized.endswith(".test")


def _validate_real_runtime(
    settings: Settings,
    processing_settings: DocumentProcessingSettings,
) -> None:
    """실제 DB·Qdrant·CUDA TEI·CUDA OCR 설정이 준비됐는지 조기 검증한다."""

    if settings.app_env != "test":
        pytest.fail(
            "Issue #123 E2E cleanup is allowed only with JIPSA_RAG_APP_ENV=test.",
            pytrace=False,
        )

    invalid_urls: list[str] = []
    if _uses_test_only_hostname(str(settings.qdrant_url)):
        invalid_urls.append("JIPSA_RAG_QDRANT_URL")
    if _uses_test_only_hostname(str(settings.embedding_base_url)):
        invalid_urls.append("JIPSA_RAG_EMBEDDING_BASE_URL")
    if invalid_urls:
        pytest.fail(
            "Issue #123 full pipeline E2E received mock-only infrastructure URLs: "
            f"{', '.join(invalid_urls)}. Load .env.local into the current process.",
            pytrace=False,
        )

    if settings.rag_ingest_token is None:
        pytest.fail("RAG_INGEST_TOKEN is required for real E2E.", pytrace=False)
    if settings.internal_token is None:
        pytest.fail("INTERNAL_TOKEN is required for real E2E.", pytrace=False)

    required_ocr_flags = (
        processing_settings.image_extraction_enabled,
        processing_settings.ocr_enabled,
        processing_settings.ocr_gpu,
        processing_settings.ocr_gpu_required,
    )
    if not all(required_ocr_flags):
        pytest.fail(
            "Image extraction, OCR, OCR GPU and OCR GPU required settings must all be enabled.",
            pytrace=False,
        )
    if not processing_settings.ocr_device.lower().startswith("cuda"):
        pytest.fail("JIPSA_RAG_OCR_DEVICE must select a CUDA device.", pytrace=False)
    if not torch.cuda.is_available():
        pytest.fail("torch.cuda.is_available() is false for the real OCR E2E.", pytrace=False)


# ============================================================
# 실제 E2E 공통 Runtime과 선택 실패 OCR 엔진
# ============================================================


@dataclass(frozen=True, slots=True)
class E2eRuntime:
    """실제 인제스트 완료 후 여러 검증이 공유하는 상태."""

    client: TestClient
    settings: Settings
    processing_settings: DocumentProcessingSettings
    recorder: BackendRecorder
    responses: Mapping[int, Mapping[str, object]]
    partial_engine: _SelectiveFailureOcrEngine


class _SelectiveFailureOcrEngine:
    """지정 이미지 하나만 실패시키고 나머지는 실제 CUDA EasyOCR에 위임한다."""

    def __init__(
        self,
        *,
        delegate: EasyOcrEngine,
        failed_image_index: int,
    ) -> None:
        self._delegate = delegate
        self._failed_image_index = failed_image_index
        self.delegated_count = 0
        self.forced_failure_count = 0

    @property
    def engine_name(self) -> str:
        """성공 OCR 단위에는 실제 EasyOCR 엔진 식별자를 유지한다."""

        return self._delegate.engine_name

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        """두 번째 고정 이미지만 도메인 OCR 오류로 전환한다."""

        image_index = image.source_metadata.get("image_index")
        if image_index == self._failed_image_index:
            self.forced_failure_count += 1
            raise OcrRecognitionError("Forced E2E OCR failure for one fixed image.")

        self.delegated_count += 1
        return await self._delegate.recognize(image)


def _http_settings(settings: Settings) -> Settings:
    """Backend와 다운로드 HTTP 경계만 E2E 전용 호스트로 교체한다."""

    return settings.model_copy(
        update={
            "app_server_base_url": "https://backend.e2e.invalid",
            "app_server_max_attempts": 1,
            "app_server_retry_initial_delay_seconds": 0.0,
            "app_server_retry_max_delay_seconds": 0.0,
            "file_download_allowed_host_suffixes": ".e2e.invalid",
        }
    )


def _ingest_cases(
    *,
    client: TestClient,
    cases: Sequence[FixtureCase],
) -> dict[int, Mapping[str, object]]:
    """고정 Case를 순서대로 인제스트하고 성공 응답 데이터를 반환한다."""

    responses: dict[int, Mapping[str, object]] = {}
    for case in cases:
        response = client.post("/ingest", json=case.manifest)
        assert response.status_code == 200, (
            f"{case.case_id} ingest failed: "
            f"status={response.status_code}, body={response.text}"
        )
        body = _object(response.json(), "POST /ingest response")
        assert _bool(body, "success") is True
        assert _str(body, "code") == "FILE_INDEXING_COMPLETED"
        responses[case.file_idx] = _object(
            body.get("data"),
            "POST /ingest response data",
        )
    return responses


@pytest.fixture(scope="module", autouse=True)
def require_e2e_opt_in() -> None:
    """일반 Pytest 실행에서는 실제 GPU·Local 인프라 호출을 건너뛴다."""

    if os.getenv(_RUN_ENV) != "1":
        pytest.skip(f"Set {_RUN_ENV}=1 to run Issue #123 full pipeline E2E tests.")


@pytest.fixture(scope="module")
def e2e_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[E2eRuntime]:
    """열두 고정 문서를 실제 OCR·TEI·DB·Qdrant 흐름으로 인제스트한다."""

    settings = get_settings()
    processing_settings = get_document_processing_settings()
    _validate_real_runtime(settings, processing_settings)

    failed_image_index = _PARTIAL_CASE.forced_failure_image_index
    if failed_image_index is None:
        raise AssertionError("The partial OCR case requires a failed image index.")

    # 일반 문서는 운영 기본 Factory를 공유하여 한 번 로드한 EasyOCR Reader를
    # 재사용한다. 부분 실패 문서만 별도 Factory로 잠시 교체한다.
    parser_factory = DocumentParserFactory(settings=processing_settings)
    selective_engine = _SelectiveFailureOcrEngine(
        delegate=EasyOcrEngine(processing_settings),
        failed_image_index=failed_image_index,
    )
    partial_parser_factory = DocumentParserFactory(
        parsers=(
            OcrAwarePdfDocumentParser(
                image_extractor=PdfImageExtractor(processing_settings),
                ocr_enricher=OcrDocumentEnricher(
                    engine=selective_engine,
                    settings=processing_settings,
                ),
            ),
        )
    )

    http_settings = _http_settings(settings)
    cases_by_idx = {case.file_idx: case for case in _ALL_CASES}
    recorder = BackendRecorder(settings=http_settings, cases=cases_by_idx)
    backend_client = ApplicationServerIngestClient(
        http_settings,
        transport=httpx2.MockTransport(recorder.handle),
    )
    downloader = HttpFileDownloader(
        http_settings,
        transport=httpx2.MockTransport(DownloadContract(cases_by_idx).handle),
        temp_directory=Path(tmp_path_factory.mktemp("issue-123-full-pipeline")),
    )

    app.dependency_overrides[get_application_server_ingest_client] = lambda: backend_client
    app.dependency_overrides[get_file_downloader] = lambda: downloader
    app.dependency_overrides[get_document_parser_factory] = lambda: parser_factory

    initial_cleanup_completed = False
    try:
        asyncio.run(_cleanup(settings, _ALL_FILE_IDXS))
        initial_cleanup_completed = True

        ingest_token = settings.rag_ingest_token
        if ingest_token is None:
            raise AssertionError("RAG_INGEST_TOKEN disappeared after preflight validation.")

        with TestClient(app) as client:
            client.headers["X-Internal-Token"] = ingest_token.get_secret_value()
            responses = _ingest_cases(client=client, cases=_MAIN_CASES)

            # 같은 실제 API·DB·Qdrant·TEI 경로를 유지하면서 OCR 엔진만
            # 두 번째 이미지에서 도메인 오류를 내도록 교체한다. 인제스트가 끝난
            # 즉시 운영 기본 Factory를 복원하여 이후 검색 API에 영향을 주지 않는다.
            app.dependency_overrides[get_document_parser_factory] = (
                lambda: partial_parser_factory
            )
            responses.update(_ingest_cases(client=client, cases=(_PARTIAL_CASE,)))
            app.dependency_overrides[get_document_parser_factory] = lambda: parser_factory

            yield E2eRuntime(
                client=client,
                settings=settings,
                processing_settings=processing_settings,
                recorder=recorder,
                responses=responses,
                partial_engine=selective_engine,
            )
    finally:
        try:
            if initial_cleanup_completed:
                asyncio.run(_cleanup(settings, _ALL_FILE_IDXS))
        finally:
            app.dependency_overrides.pop(get_application_server_ingest_client, None)
            app.dependency_overrides.pop(get_file_downloader, None)
            app.dependency_overrides.pop(get_document_parser_factory, None)


# ============================================================
# 1. 고정 파일과 파이프라인 기대값 자체 검증
# ============================================================


@pytest.mark.parametrize("case", _ALL_CASES, ids=lambda case: case.case_id)
def test_fixed_pipeline_fixture_checksum_and_case_contract(case: FixtureCase) -> None:
    """실제 문서 바이트와 파서·토큰 기대값이 누락 없이 고정됐는지 확인한다."""

    assert case.path.is_file()
    assert case.path.stat().st_size == case.size_bytes
    assert hashlib.sha256(case.path.read_bytes()).hexdigest() == case.sha256
    assert _SHA256_PATTERN.fullmatch(case.sha256)
    assert case.assertions
    assert case.parser_type
    assert case.parser_version


# ============================================================
# 2. PDF·DOCX·PPTX·XLSX·TXT 구조 파싱 결과
# ============================================================


@pytest.mark.parametrize("case", _TEXT_CASES, ids=lambda case: case.case_id)
def test_structured_text_table_slide_sheet_and_line_results(
    e2e_runtime: E2eRuntime,
    case: FixtureCase,
) -> None:
    """형식별 문단·표·슬라이드·시트·줄 위치가 callback 청크까지 보존되는지 검증한다."""

    response_data = e2e_runtime.responses[case.file_idx]
    callback = e2e_runtime.recorder.callback(case.file_idx)
    callback_chunks = tuple(_objects(callback, "chunks"))

    assert e2e_runtime.recorder.manifest_requests.count(case.file_idx) == 1
    assert _str(response_data, "processing_status") == "INDEXED"
    assert _int(response_data, "chunk_count") == len(callback_chunks)
    assert _int(callback, "index_version") == _INDEX_VERSION

    for assertion in case.assertions:
        matches = _matching_chunks(callback_chunks, assertion)
        assert matches, f"{case.case_id} did not preserve {assertion.token}."
        assert any(
            all(
                _equivalent(_source_metadata(chunk).get(key), value)
                for key, value in assertion.source_metadata.items()
            )
            for chunk in matches
        ), f"{case.case_id} did not preserve the expected source location."


# ============================================================
# 3. 실제 CUDA OCR 결과와 원본 이미지 위치
# ============================================================


@pytest.mark.parametrize("case", _OCR_CASES, ids=lambda case: case.case_id)
def test_actual_cuda_ocr_result_and_original_image_location(
    e2e_runtime: E2eRuntime,
    case: FixtureCase,
) -> None:
    """EasyOCR 결과가 이미지 종류와 문서별 원본 위치를 가진 청크로 저장되는지 검증한다."""

    callback = e2e_runtime.recorder.callback(case.file_idx)
    callback_chunks = tuple(_objects(callback, "chunks"))

    for assertion in case.assertions:
        assert assertion.is_ocr is True
        matches = _matching_chunks(callback_chunks, assertion)
        assert matches, f"Actual OCR did not recognize {assertion.token}."

        matched_metadata: list[dict[str, object]] = []
        for chunk in matches:
            metadata = _source_metadata(chunk)
            try:
                _assert_metadata_contains(
                    metadata,
                    assertion.source_metadata,
                    label=f"{case.case_id}.ocr_source_metadata",
                )
            except AssertionError:
                continue
            matched_metadata.append(metadata)

        assert matched_metadata, (
            f"{case.case_id} OCR text was found without its expected original location."
        )
        for metadata in matched_metadata:
            assert _str(metadata, "image_sha256")
            assert _SHA256_PATTERN.fullmatch(_str(metadata, "image_sha256"))
            assert _int(metadata, "ocr_line_count") > 0
            confidence = metadata.get("ocr_mean_confidence")
            assert isinstance(confidence, int | float) and not isinstance(confidence, bool)
            assert 0.0 < float(confidence) <= 1.0


# ============================================================
# 4. OCR 일부 실패 시 부분 성공
# ============================================================


def test_ocr_partial_failure_keeps_successful_image_indexed(
    e2e_runtime: E2eRuntime,
) -> None:
    """두 이미지 중 하나의 OCR 실패가 성공 이미지의 DB·Qdrant 색인을 롤백하지 않는지 검증한다."""

    runtime = e2e_runtime
    engine = e2e_runtime.partial_engine
    callback = runtime.recorder.callback(_PARTIAL_CASE.file_idx)
    callback_chunks = tuple(_objects(callback, "chunks"))

    assert engine.delegated_count == 1
    assert engine.forced_failure_count == 1
    assert _PARTIAL_CASE.success_token is not None
    assert _PARTIAL_CASE.failure_token is not None

    callback_content = "\n".join(_str(chunk, "content") for chunk in callback_chunks)
    assert _contains_token(callback_content, _PARTIAL_CASE.success_token)
    assert not _contains_token(callback_content, _PARTIAL_CASE.failure_token)

    state = asyncio.run(_database_state(runtime.settings, _PARTIAL_CASE))
    assert _str(state.document, "parse_status") == "PARSED"
    assert _str(state.document, "index_status") == "INDEXED"
    assert len(state.runs) == 1
    assert _str(state.runs[0], "status") == "SUCCESS"

    database_content = "\n".join(_str(chunk, "content") for chunk in state.chunks)
    assert _contains_token(database_content, _PARTIAL_CASE.success_token)
    assert not _contains_token(database_content, _PARTIAL_CASE.failure_token)

    points = asyncio.run(
        _qdrant_points(
            runtime.settings,
            users_idx=_TEST_USER_IDX,
            file_idxs=(_PARTIAL_CASE.file_idx,),
        )
    )
    point_content = "\n".join(
        _str(cast(Mapping[str, object], point.payload), "content")
        for point in points
        if point.payload is not None
    )
    assert _contains_token(point_content, _PARTIAL_CASE.success_token)
    assert not _contains_token(point_content, _PARTIAL_CASE.failure_token)


# ============================================================
# 5. Local RAG DB 문서·청크·색인 실행 이력
# ============================================================


@pytest.mark.parametrize("case", _MAIN_CASES, ids=lambda case: case.case_id)
def test_local_rag_document_chunk_and_index_run_history(
    e2e_runtime: E2eRuntime,
    case: FixtureCase,
) -> None:
    """실제 DB의 문서 상태, 원본 청크와 FULL 실행 이력을 상호 검증한다."""

    state = asyncio.run(_database_state(e2e_runtime.settings, case))
    document = state.document

    assert _int(document, "file_idx") == case.file_idx
    assert _int(document, "users_idx") == _TEST_USER_IDX
    assert _optional_int(document, "folder_idx") == _TEST_FOLDER_IDX
    assert _str(document, "file_name") == case.file_name
    assert _str(document, "file_type") == case.file_type.upper()
    assert _str(document, "file_hash") == case.sha256
    assert _int(document, "index_version") == _INDEX_VERSION
    assert _str(document, "parse_status") == "PARSED"
    assert _str(document, "index_status") == "INDEXED"
    assert _str(document, "parser_type") == case.parser_type
    assert _str(document, "parser_version") == case.parser_version
    assert _str(document, "embedding_model") == e2e_runtime.settings.embedding_model
    assert _db_bool(document, "is_deleted") is False
    assert document.get("created_at") is not None
    assert document.get("updated_at") is not None

    assert state.chunks
    assert _int(document, "chunk_count") == len(state.chunks)
    assert tuple(_int(chunk, "chunk_index") for chunk in state.chunks) == tuple(
        range(len(state.chunks))
    )

    for chunk in state.chunks:
        assert _int(chunk, "rag_document_idx") == _int(document, "rag_document_idx")
        assert _int(chunk, "file_idx") == case.file_idx
        assert _int(chunk, "users_idx") == _TEST_USER_IDX
        assert _optional_int(chunk, "folder_idx") == _TEST_FOLDER_IDX
        assert _int(chunk, "start_offset") >= 0
        assert _int(chunk, "end_offset") > _int(chunk, "start_offset")
        assert _SHA256_PATTERN.fullmatch(_str(chunk, "content_hash"))
        assert _str(chunk, "embedding_model") == e2e_runtime.settings.embedding_model
        assert _int(chunk, "index_version") == _INDEX_VERSION
        assert chunk.get("created_at") is not None

    for assertion in case.assertions:
        matches = _matching_chunks(state.chunks, assertion)
        assert matches, f"Local RAG DB did not preserve {assertion.token}."
        assert any(
            all(
                _equivalent(_source_metadata(chunk).get(key), value)
                for key, value in assertion.source_metadata.items()
            )
            for chunk in matches
        )

    # 시작 전 전용 범위를 정리하므로 현재 문서에는 정확히 한 번의 FULL 성공
    # 이력만 존재해야 한다. 재실행 이력이 우연히 누적되면 격리 실패로 판단한다.
    assert len(state.runs) == 1
    run = state.runs[0]
    assert _int(run, "rag_document_idx") == _int(document, "rag_document_idx")
    assert _int(run, "file_idx") == case.file_idx
    assert _int(run, "users_idx") == _TEST_USER_IDX
    assert _str(run, "run_type") == "FULL"
    assert _str(run, "status") == "SUCCESS"
    assert _str(run, "parser_type") == case.parser_type
    assert _str(run, "parser_version") == case.parser_version
    assert _str(run, "embedding_model") == e2e_runtime.settings.embedding_model
    assert _int(run, "chunk_count") == len(state.chunks)
    assert run.get("error_message") is None
    assert run.get("started_at") is not None
    assert run.get("finished_at") is not None
    assert run.get("created_at") is not None


# ============================================================
# 6. Qdrant Point·payload·활성 상태·문서 범위
# ============================================================


@pytest.mark.parametrize("case", _MAIN_CASES, ids=lambda case: case.case_id)
def test_qdrant_point_payload_active_state_and_document_scope(
    e2e_runtime: E2eRuntime,
    case: FixtureCase,
) -> None:
    """Qdrant 복제 데이터가 Local RAG 청크와 1:1이며 다른 사용자 범위에서 보이지 않는지 검증한다."""

    state = asyncio.run(_database_state(e2e_runtime.settings, case))
    points = asyncio.run(
        _qdrant_points(
            e2e_runtime.settings,
            users_idx=_TEST_USER_IDX,
            file_idxs=(case.file_idx,),
        )
    )
    chunks_by_id = {_str(chunk, "chunk_id"): chunk for chunk in state.chunks}

    assert len(points) == len(chunks_by_id)
    assert {str(point.id) for point in points} == set(chunks_by_id)

    for point in points:
        assert point.payload is not None
        payload = cast(Mapping[str, object], point.payload)
        point_id = str(point.id)
        local_chunk = chunks_by_id[point_id]

        assert _str(payload, "chunk_id") == point_id
        assert _int(payload, "file_idx") == case.file_idx
        assert _int(payload, "users_idx") == _TEST_USER_IDX
        assert _optional_int(payload, "folder_idx") == _TEST_FOLDER_IDX
        assert _str(payload, "file_name") == case.file_name
        assert _str(payload, "file_type") == case.file_type.upper()
        assert _str(payload, "file_hash") == case.sha256
        assert _str(payload, "parser_type") == case.parser_type
        assert _str(payload, "parser_version") == case.parser_version
        assert _str(payload, "content") == _str(local_chunk, "content")
        assert _str(payload, "content_hash") == _str(local_chunk, "content_hash")
        assert _str(payload, "embedding_model") == e2e_runtime.settings.embedding_model
        assert _int(payload, "embedding_dim") == e2e_runtime.settings.embedding_dim
        assert _int(payload, "index_version") == _INDEX_VERSION
        assert _bool(payload, "is_active") is True
        assert _str(payload, "created_at")

    other_user_points = asyncio.run(
        _qdrant_points(
            e2e_runtime.settings,
            users_idx=_OTHER_USER_IDX,
            file_idxs=(case.file_idx,),
        )
    )
    assert other_user_points == ()


# ============================================================
# 7. CUDA TEI 임베딩과 벡터 차원
# ============================================================


def test_cuda_tei_document_embeddings_and_vector_dimension(e2e_runtime: E2eRuntime) -> None:
    """실제 TEI가 생성한 모든 문서 벡터의 모델·차원·유한값을 검증한다."""

    assert not _uses_test_only_hostname(str(e2e_runtime.settings.embedding_base_url))
    assert e2e_runtime.settings.embedding_dim > 0

    all_points = asyncio.run(
        _qdrant_points(
            e2e_runtime.settings,
            users_idx=_TEST_USER_IDX,
            file_idxs=_MAIN_FILE_IDXS,
        )
    )
    assert all_points

    for case in _MAIN_CASES:
        response_data = e2e_runtime.responses[case.file_idx]
        assert _str(response_data, "embedding_model") == e2e_runtime.settings.embedding_model
        assert _int(response_data, "embedding_dim") == e2e_runtime.settings.embedding_dim
        assert _int(response_data, "chunk_count") > 0

    for point in all_points:
        assert isinstance(point.vector, list)
        vector = point.vector
        assert len(vector) == e2e_runtime.settings.embedding_dim
        assert all(isinstance(value, int | float) for value in vector)
        assert all(math.isfinite(float(value)) for value in vector)
        assert any(float(value) != 0.0 for value in vector)


@pytest.mark.parametrize("case", _MAIN_CASES, ids=lambda case: case.case_id)
def test_real_tei_query_embedding_respects_reference_document_scope(
    e2e_runtime: E2eRuntime,
    case: FixtureCase,
) -> None:
    """실제 질의 임베딩 검색이 선택한 한 문서의 활성 Point만 반환하는지 검증한다."""

    expected_token = case.assertions[0].token
    response = e2e_runtime.client.post(
        "/api/v1/chunks/search",
        json={
            "user_idx": _TEST_USER_IDX,
            "reference_file_idxs": [case.file_idx],
            "query": f"문서에서 {expected_token} 값을 찾아 주세요.",
            "top_k": 20,
            "score_threshold": None,
        },
    )
    assert response.status_code == 200, (
        f"{case.case_id} search failed: status={response.status_code}, body={response.text}"
    )

    body = _object(response.json(), "chunk search response")
    assert _bool(body, "success") is True
    assert _str(body, "code") == "CHUNK_SEARCH_COMPLETED"
    data = _object(body.get("data"), "chunk search response data")
    results = _objects(data, "results")

    assert results
    assert {_int(result, "file_idx") for result in results} == {case.file_idx}
    assert any(_contains_token(_str(result, "content"), expected_token) for result in results)
