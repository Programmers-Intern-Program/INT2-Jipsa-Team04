"""고정 다중 형식 문서를 실제 OCR·TEI·DB·Qdrant 파이프라인으로 검증한다.

Issue #123의 두 번째·세 번째 작업 묶음은 첫 번째 묶음에서 저장소에 고정한 실제 문서를
운영 Local RAG 처리 흐름에 통과시킨다. AWS Backend와 Presigned GET URL의 HTTP
경계만 결정적인 ``MockTransport``로 교체하고 다음 구성요소는 실제 구현을 사용한다.

- PDF, DOCX, PPTX, XLSX, TXT 운영 파서와 구조화 청커
- CUDA EasyOCR과 문서별 이미지 위치 연결
- CUDA TEI 문서·질의 임베딩
- Local RAG MySQL 또는 MariaDB 문서·청크·색인 실행 이력
- Qdrant Point, vector, payload, 활성 상태와 사용자·문서 범위
- 실제 Claude lookup·synthesis 답변과 형식별 source_locator
- 답변 본문 [SOURCE-N], cited_source_ids 및 sources 순서 일치
- 사용자·선택 문서 출처 격리와 근거 부족 Claude 미호출
- 부분 실패, 재인제스트·재색인·보상, 동시 인제스트 복구력
- 임시 파일·추출 이미지 정리와 질문·청크·프롬프트 로그 비노출

실제 GPU 추론과 로컬 인프라 데이터 변경을 동반하므로
``JIPSA_RAG_RUN_E2E=1``을 명시한 경우에만 실행한다. 테스트 데이터는 전용 사용자와
파일 ID 범위에 한정하여 시작 전과 종료 후 정리한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from math import ceil
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx2
import pytest
import torch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jipsa_rag.api.ingest import get_application_server_ingest_client
from jipsa_rag.api.v1.endpoints.file_processing import (
    DatabaseSessionDependency,
    FileIndexLockDependency,
    QdrantVectorStoreDependency,
    get_document_parser_factory,
    get_file_downloader,
    get_file_indexing_service,
)
from jipsa_rag.api.v1.endpoints.rag_answer import (
    get_generation_client,
    get_rag_answer_service,
)
from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.document_processing import (
    DocumentProcessingSettings,
    get_document_processing_settings,
)
from jipsa_rag.core.generation_config import (
    GenerationSettings,
    get_generation_settings,
)
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.infrastructure.document.images.models import ExtractedDocumentImage
from jipsa_rag.infrastructure.document.images.pdf import PdfImageExtractor
from jipsa_rag.infrastructure.document.media_aware import OcrAwarePdfDocumentParser
from jipsa_rag.infrastructure.document.models import DocumentType, ParsedDocument
from jipsa_rag.infrastructure.document.parser import DocumentParser
from jipsa_rag.infrastructure.document.parser_factory import DocumentParserFactory
from jipsa_rag.infrastructure.embedding.models import EmbeddedDocument
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader
from jipsa_rag.infrastructure.generation.exceptions import GenerationServerError
from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
)
from jipsa_rag.infrastructure.indexing.concurrent_repository import (
    ConcurrentSafeLocalRagIndexRepository,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    VectorDatabaseUnavailableError,
)
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata
from jipsa_rag.infrastructure.indexing.qdrant_store import QdrantChunkVectorStore
from jipsa_rag.infrastructure.ocr import (
    EasyOcrEngine,
    OcrDocumentEnricher,
    OcrRecognitionResult,
)
from jipsa_rag.infrastructure.ocr.exceptions import OcrRecognitionError
from jipsa_rag.main import app
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.source_locator import build_source_locator
from jipsa_rag.services.file_indexing import FileIndexingService
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.query_routing import RoutedRagAnswerService

# ============================================================
# 실행 제어와 고정 Fixture 경로
# ============================================================

_RUN_ENV: Final[str] = "JIPSA_RAG_RUN_E2E"
_FIXTURE_ROOT: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures/e2e_documents"
_DOCUMENT_MANIFEST_PATH: Final[Path] = _FIXTURE_ROOT / "manifest.json"
_PIPELINE_EXPECTATIONS_PATH: Final[Path] = _FIXTURE_ROOT / "pipeline_expectations.json"
_ANSWER_EXPECTATIONS_PATH: Final[Path] = _FIXTURE_ROOT / "answer_expectations.json"

_BACKEND_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/internal/files/(?P<file_idx>[1-9][0-9]*)/"
    r"(?P<operation>manifest|ingest-complete)$"
)
_DOWNLOAD_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/fixed-pipeline/(?P<file_idx>[1-9][0-9]*)/(?P<file_name>[^/]+)$"
)
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_NORMALIZATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^A-Z0-9]+")
_SOURCE_CITATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[(?P<source_id>SOURCE-[1-9][0-9]*)\]"
)
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


def _strings(mapping: Mapping[str, object], key: str) -> tuple[str, ...]:
    """JSON 문자열 배열을 순서와 중복을 그대로 보존하여 읽는다."""

    value = mapping.get(key)
    if not isinstance(value, list):
        raise AssertionError(f"{key} must be a JSON array.")

    normalized: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item:
            raise AssertionError(f"{key} must contain only non-empty strings.")
        normalized.append(item)
    return tuple(normalized)


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
            f"{label}[{key!r}] mismatch: expected={expected_value!r}, actual={actual_value!r}"
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
_ANSWER_EXPECTATIONS: Final[dict[str, object]] = _load_json(
    _ANSWER_EXPECTATIONS_PATH,
    "answer expectations",
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


@dataclass(frozen=True, slots=True)
class ExpectedAnswerSource:
    """답변에 실제 인용돼야 하는 토큰·문서·공통 locator 계약."""

    token: str
    case_id: str
    locator: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AnswerScenario:
    """실제 Claude 답변 한 건의 선택 문서, 질문 및 예상 출처 계약."""

    scenario_id: str
    case_ids: tuple[str, ...]
    query: str
    expected_tokens: tuple[str, ...]
    expected_sources: tuple[ExpectedAnswerSource, ...]

    @property
    def cases(self) -> tuple[FixtureCase, ...]:
        """시나리오가 선택하는 고정 문서를 JSON 선언 순서대로 반환한다."""

        try:
            return tuple(_CASES_BY_ID[case_id] for case_id in self.case_ids)
        except KeyError as error:
            raise AssertionError(f"Unknown answer scenario fixture id: {error.args[0]}") from error

    @property
    def file_idxs(self) -> tuple[int, ...]:
        """답변 요청 reference_file_idxs에 전달할 실제 File_IDX 목록."""

        return tuple(case.file_idx for case in self.cases)


@dataclass(frozen=True, slots=True)
class AnswerScenarioResult:
    """한 번만 호출한 실제 Claude 답변을 여러 계약 테스트가 공유하는 결과."""

    scenario: AnswerScenario
    answer: str
    cited_source_ids: tuple[str, ...]
    sources: tuple[Mapping[str, object], ...]
    model: str


@dataclass(frozen=True, slots=True)
class AnswerRuntime:
    """lookup·synthesis·OCR 답변 호출 결과를 시나리오 ID로 제공한다."""

    results: Mapping[str, AnswerScenarioResult]

    def result(self, scenario_id: str) -> AnswerScenarioResult:
        """고정 시나리오 ID의 실제 답변 결과를 반환한다."""

        try:
            return self.results[scenario_id]
        except KeyError as error:
            raise AssertionError(f"Unknown answer result id: {scenario_id}") from error


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
        # 위의 명시적 런타임 타입 검증으로 각 값은 이미 목표 Union 타입으로
        # 축소됐다. 불필요한 cast를 제거하여 Mypy redundant-cast 오류를 막는다.
        forced_failure_image_index=forced_failure,
        success_token=success_token,
        failure_token=failure_token,
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

# 재인제스트·재색인·동시성·자원 정리 검증이 기존 답변 Fixture 상태를 변경하지 않도록
# 동일한 실제 파일 바이트를 별도 File_IDX로 격리한다. 원본 Fixture의 SHA-256, 경로,
# 파서 기대값과 토큰 계약은 그대로 재사용하므로 추가 바이너리 파일이 필요하지 않다.
_OPERATION_CASE: Final[FixtureCase] = replace(
    _CASES_BY_ID["pdf-text-table"],
    case_id="pdf-index-lifecycle-operations",
    file_idx=max(_ALL_FILE_IDXS) + 101,
)
_RESOURCE_CLEANUP_CASE: Final[FixtureCase] = replace(
    _CASES_BY_ID["xlsx-with-image"],
    case_id="xlsx-resource-cleanup-operations",
    file_idx=max(_ALL_FILE_IDXS) + 102,
)
_OPERATION_CASES: Final[tuple[FixtureCase, ...]] = (
    _OPERATION_CASE,
    _RESOURCE_CLEANUP_CASE,
)
_OPERATION_FILE_IDXS: Final[tuple[int, ...]] = tuple(case.file_idx for case in _OPERATION_CASES)
_ALL_E2E_FILE_IDXS: Final[tuple[int, ...]] = (*_ALL_FILE_IDXS, *_OPERATION_FILE_IDXS)
_NO_EVIDENCE_FILE_IDX: Final[int] = max(_ALL_E2E_FILE_IDXS) + 100


def _build_answer_scenario(value: Mapping[str, object]) -> AnswerScenario:
    """JSON 답변 기대값을 참조 범위가 검증된 불변 시나리오로 변환한다."""

    scenario_id = _str(value, "id")
    case_ids = _strings(value, "case_ids")
    expected_tokens = _strings(value, "expected_tokens")

    if not case_ids:
        raise AssertionError(f"{scenario_id} requires at least one fixture case.")
    if len(case_ids) != len(set(case_ids)):
        raise AssertionError(f"{scenario_id} case_ids must be unique.")
    if not expected_tokens:
        raise AssertionError(f"{scenario_id} requires at least one expected token.")

    unknown_case_ids = tuple(case_id for case_id in case_ids if case_id not in _CASES_BY_ID)
    if unknown_case_ids:
        raise AssertionError(f"{scenario_id} references unknown fixtures: {unknown_case_ids!r}.")

    expected_sources: list[ExpectedAnswerSource] = []
    for source in _objects(value, "expected_sources"):
        case_id = _str(source, "case_id")
        token = _str(source, "token")
        locator = _object(source.get("locator"), "answer source locator")

        if case_id not in case_ids:
            raise AssertionError(
                f"{scenario_id} source case {case_id!r} is outside reference scope."
            )
        if token not in expected_tokens:
            raise AssertionError(f"{scenario_id} source token {token!r} is not an expected token.")
        if not locator:
            raise AssertionError(f"{scenario_id} source locator must not be empty.")

        expected_sources.append(
            ExpectedAnswerSource(
                token=token,
                case_id=case_id,
                locator=locator,
            )
        )

    if not expected_sources:
        raise AssertionError(f"{scenario_id} requires expected sources.")

    return AnswerScenario(
        scenario_id=scenario_id,
        case_ids=case_ids,
        query=_str(value, "query"),
        expected_tokens=expected_tokens,
        expected_sources=tuple(expected_sources),
    )


def _single_answer_scenario(key: str) -> AnswerScenario:
    """최상위 JSON 객체 하나를 답변 시나리오로 읽는다."""

    return _build_answer_scenario(_object(_ANSWER_EXPECTATIONS.get(key), key))


_LOOKUP_ANSWER_SCENARIOS: Final[tuple[AnswerScenario, ...]] = tuple(
    _build_answer_scenario(value) for value in _objects(_ANSWER_EXPECTATIONS, "lookup_cases")
)
_OCR_LOOKUP_ANSWER_SCENARIOS: Final[tuple[AnswerScenario, ...]] = tuple(
    _build_answer_scenario(value) for value in _objects(_ANSWER_EXPECTATIONS, "ocr_lookup_cases")
)
_SYNTHESIS_ANSWER_SCENARIO: Final[AnswerScenario] = _single_answer_scenario("synthesis_case")
_MIXED_TEXT_OCR_ANSWER_SCENARIO: Final[AnswerScenario] = _single_answer_scenario(
    "mixed_text_ocr_case"
)
_ALL_ANSWER_SCENARIOS: Final[tuple[AnswerScenario, ...]] = (
    *_LOOKUP_ANSWER_SCENARIOS,
    *_OCR_LOOKUP_ANSWER_SCENARIOS,
    _SYNTHESIS_ANSWER_SCENARIO,
    _MIXED_TEXT_OCR_ANSWER_SCENARIO,
)
_ANSWER_SCENARIOS_BY_ID: Final[dict[str, AnswerScenario]] = {
    scenario.scenario_id: scenario for scenario in _ALL_ANSWER_SCENARIOS
}

if len(_ANSWER_SCENARIOS_BY_ID) != len(_ALL_ANSWER_SCENARIOS):
    raise AssertionError("Answer scenario IDs must be unique.")


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
        chunk for chunk in chunks if _contains_token(_str(chunk, "content"), assertion.token)
    )


# ============================================================
# Backend·다운로드 HTTP 경계
# ============================================================


@dataclass(slots=True)
class BackendRecorder:
    """고정 manifest를 반환하고 실제 ingest-complete callback을 기록한다.

    일반 E2E는 순차적으로 호출하지만 중복·동시 인제스트 검증은 동일한 MockTransport를
    여러 요청 스레드가 공유한다. list와 dict 갱신을 Lock으로 보호하여 테스트 대역의
    데이터 경합이 운영 advisory lock 검증 결과로 오인되지 않게 한다.
    """

    settings: Settings
    cases: Mapping[int, FixtureCase]
    manifest_requests: list[int] = field(default_factory=list)
    callbacks: dict[int, list[dict[str, object]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

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
            with self._lock:
                self.manifest_requests.append(file_idx)
            return httpx2.Response(status_code=200, json=case.manifest)

        assert operation == "ingest-complete"
        assert request.method == "POST"
        payload = _object(
            json.loads(request.content.decode("utf-8")),
            "ingest-complete payload",
        )
        with self._lock:
            self.callbacks.setdefault(file_idx, []).append(payload)
        return httpx2.Response(status_code=204)

    def callback(self, file_idx: int) -> dict[str, object]:
        """파일별 성공 callback이 정확히 한 번 전송되었는지 확인한다."""

        payloads = self.payloads(file_idx)
        assert len(payloads) == 1, (
            f"file_idx={file_idx} expected one callback, received {len(payloads)}"
        )
        payload = payloads[0]
        assert _bool(payload, "success") is True
        return payload

    def payloads(self, file_idx: int) -> tuple[dict[str, object], ...]:
        """동시 요청 중에도 안전한 callback 스냅샷을 반환한다."""

        with self._lock:
            return tuple(dict(payload) for payload in self.callbacks.get(file_idx, []))

    def manifest_request_count(self, file_idx: int) -> int:
        """특정 파일 manifest 재조회 횟수를 원자적으로 반환한다."""

        with self._lock:
            return self.manifest_requests.count(file_idx)

    def clear_file_history(self, file_idx: int) -> None:
        """격리된 작업 Case를 재사용하기 전에 HTTP 기록만 제거한다."""

        with self._lock:
            self.manifest_requests = [
                value for value in self.manifest_requests if value != file_idx
            ]
            self.callbacks.pop(file_idx, None)


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


@dataclass(frozen=True, slots=True)
class FileHistoryState:
    """한 사용자·파일의 활성·삭제·실패 문서와 모든 청크·실행 이력."""

    documents: tuple[Mapping[str, object], ...]
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


async def _file_history_state(
    settings: Settings,
    *,
    users_idx: int,
    file_idx: int,
) -> FileHistoryState:
    """soft delete 여부와 관계없이 파일 색인 생명주기 전체를 조회한다."""

    engine = _db_engine(settings)
    try:
        async with engine.connect() as connection:
            documents = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `RAG_Document_IDX` AS `rag_document_idx`,
                                `File_IDX` AS `file_idx`,
                                `Users_IDX` AS `users_idx`,
                                `File_Hash` AS `file_hash`,
                                `Index_Version` AS `index_version`,
                                `Index_Status` AS `index_status`,
                                `Parser_Type` AS `parser_type`,
                                `Parser_Version` AS `parser_version`,
                                `Chunk_Count` AS `chunk_count`,
                                (`Deleted_At` IS NOT NULL) AS `is_deleted`,
                                `Created_At` AS `created_at`,
                                `Updated_At` AS `updated_at`
                            FROM `RAG_Document`
                            WHERE `Users_IDX` = :users_idx
                              AND `File_IDX` = :file_idx
                            ORDER BY `RAG_Document_IDX`
                            """
                        ),
                        {"users_idx": users_idx, "file_idx": file_idx},
                    )
                )
                .mappings()
                .all()
            )
            chunks = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `Chunk_ID` AS `chunk_id`,
                                `RAG_Document_IDX` AS `rag_document_idx`,
                                `File_IDX` AS `file_idx`,
                                `Users_IDX` AS `users_idx`,
                                `Chunk_Index` AS `chunk_index`,
                                `Content_Hash` AS `content_hash`
                            FROM `RAG_Chunk`
                            WHERE `Users_IDX` = :users_idx
                              AND `File_IDX` = :file_idx
                            ORDER BY `RAG_Document_IDX`, `Chunk_Index`
                            """
                        ),
                        {"users_idx": users_idx, "file_idx": file_idx},
                    )
                )
                .mappings()
                .all()
            )
            runs = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `RAG_Index_Run_IDX` AS `rag_index_run_idx`,
                                `RAG_Document_IDX` AS `rag_document_idx`,
                                `Run_Type` AS `run_type`,
                                `Status` AS `status`,
                                `Error_Message` AS `error_message`,
                                `Started_At` AS `started_at`,
                                `Finished_At` AS `finished_at`
                            FROM `RAG_Index_Run`
                            WHERE `Users_IDX` = :users_idx
                              AND `File_IDX` = :file_idx
                            ORDER BY `RAG_Index_Run_IDX`
                            """
                        ),
                        {"users_idx": users_idx, "file_idx": file_idx},
                    )
                )
                .mappings()
                .all()
            )
            return FileHistoryState(
                documents=tuple(cast(Mapping[str, object], row) for row in documents),
                chunks=tuple(cast(Mapping[str, object], row) for row in chunks),
                runs=tuple(cast(Mapping[str, object], row) for row in runs),
            )
    finally:
        await engine.dispose()


async def _cleanup_database(settings: Settings, file_idxs: Sequence[int]) -> None:
    """지정한 정확한 E2E File_IDX만 FK 역순으로 삭제한다.

    최소·최대 범위 삭제는 전용 ID 사이에 존재하는 다른 테스트 데이터를 함께
    지울 수 있다. expanding IN parameter를 사용하여 현재 모듈이 소유한 File_IDX만
    삭제하고, 사용자 범위도 동시에 고정한다.
    """

    normalized_file_idxs = tuple(dict.fromkeys(file_idxs))
    if not normalized_file_idxs:
        return

    engine = _db_engine(settings)
    parameters = {
        "users_idx": _TEST_USER_IDX,
        "file_idxs": normalized_file_idxs,
    }
    try:
        async with engine.begin() as connection:
            for table_name in (
                "RAG_Index_Run",
                "RAG_Chunk",
                "RAG_Document",
            ):
                statement = text(
                    f"""
                    DELETE FROM `{table_name}`
                    WHERE `Users_IDX` = :users_idx
                      AND `File_IDX` IN :file_idxs
                    """
                ).bindparams(bindparam("file_idxs", expanding=True))
                await connection.execute(statement, parameters)
    finally:
        await engine.dispose()


# ============================================================
# Qdrant 조회와 정리
# ============================================================


def _qdrant_client(settings: Settings) -> AsyncQdrantClient:
    """현재 실제 E2E 환경의 Qdrant 비동기 클라이언트."""

    api_key = (
        settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key is not None else None
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


async def _upsert_qdrant_probe_point(
    settings: Settings,
    *,
    source_point: models.Record,
    point_id: str,
    users_idx: int,
) -> None:
    """기존 실제 Point를 복제해 다른 사용자 경계 검증용 Point를 저장한다."""

    if source_point.payload is None or not isinstance(source_point.vector, list):
        raise AssertionError("A source Qdrant point with payload and vector is required.")

    payload = dict(cast(Mapping[str, object], source_point.payload))
    payload["users_idx"] = users_idx
    payload["chunk_id"] = point_id
    payload["content"] = "OTHER-USER-SECURITY-PROBE-ONLY"

    client = _qdrant_client(settings)
    try:
        await client.upsert(
            collection_name=settings.qdrant_collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=cast(list[float], source_point.vector),
                    payload=payload,
                )
            ],
            wait=True,
        )
    finally:
        await client.close()


async def _delete_qdrant_probe_point(settings: Settings, *, point_id: str) -> None:
    """보안 경계 검증용 단일 Point를 ID 기준으로 정리한다."""

    client = _qdrant_client(settings)
    try:
        await client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=models.PointIdsList(points=[point_id]),
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
    generation_settings: GenerationSettings,
) -> None:
    """실제 DB·Qdrant·CUDA TEI·CUDA OCR·Claude 설정을 조기 검증한다."""

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

    # synthesis는 선택 문서별 부분 생성 후 최종 종합을 한 번 더 호출한다.
    # 환경의 답변별 호출 상한이 이 고정 시나리오보다 작으면 테스트 도중 비용을
    # 사용한 뒤 실패하므로 실제 Claude 호출 전에 명확하게 차단한다.
    required_synthesis_calls = len(_SYNTHESIS_ANSWER_SCENARIO.case_ids) + 1
    if generation_settings.anthropic_max_calls_per_answer < required_synthesis_calls:
        pytest.fail(
            "JIPSA_RAG_ANTHROPIC_MAX_CALLS_PER_ANSWER must allow at least "
            f"{required_synthesis_calls} calls for the fixed synthesis E2E.",
            pytrace=False,
        )

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
    generation_settings: GenerationSettings
    recorder: BackendRecorder
    responses: Mapping[int, Mapping[str, object]]
    partial_engine: _SelectiveFailureOcrEngine
    parser_factory: DocumentParserFactory
    download_temp_directory: Path


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
            f"{case.case_id} ingest failed: status={response.status_code}, body={response.text}"
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

    # 일반 단위 테스트의 잘못된 캐시가 실제 E2E 프로세스 환경을 덮지 않도록
    # 현재 .env.local 주입 상태에서 Claude 설정을 다시 생성한다. ValidationError
    # 문자열에는 입력 API Key가 숨겨지지만, 테스트 출력에도 원문을 전달하지 않는다.
    get_generation_settings.cache_clear()
    try:
        generation_settings = get_generation_settings()
    except ValidationError:
        pytest.fail(
            "A valid Anthropic API key and Claude model are required for answer E2E.",
            pytrace=False,
        )

    _validate_real_runtime(settings, processing_settings, generation_settings)

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
    cases_by_idx = {case.file_idx: case for case in (*_ALL_CASES, *_OPERATION_CASES)}
    recorder = BackendRecorder(settings=http_settings, cases=cases_by_idx)
    backend_client = ApplicationServerIngestClient(
        http_settings,
        transport=httpx2.MockTransport(recorder.handle),
    )
    download_temp_directory = Path(tmp_path_factory.mktemp("issue-123-full-pipeline"))
    downloader = HttpFileDownloader(
        http_settings,
        transport=httpx2.MockTransport(DownloadContract(cases_by_idx).handle),
        temp_directory=download_temp_directory,
    )

    app.dependency_overrides[get_application_server_ingest_client] = lambda: backend_client
    app.dependency_overrides[get_file_downloader] = lambda: downloader
    app.dependency_overrides[get_document_parser_factory] = lambda: parser_factory

    initial_cleanup_completed = False
    try:
        asyncio.run(_cleanup(settings, _ALL_E2E_FILE_IDXS))
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
            app.dependency_overrides[get_document_parser_factory] = lambda: partial_parser_factory
            responses.update(_ingest_cases(client=client, cases=(_PARTIAL_CASE,)))
            app.dependency_overrides[get_document_parser_factory] = lambda: parser_factory

            yield E2eRuntime(
                client=client,
                settings=settings,
                processing_settings=processing_settings,
                generation_settings=generation_settings,
                recorder=recorder,
                responses=responses,
                partial_engine=selective_engine,
                parser_factory=parser_factory,
                download_temp_directory=download_temp_directory,
            )
    finally:
        try:
            if initial_cleanup_completed:
                asyncio.run(_cleanup(settings, _ALL_E2E_FILE_IDXS))
        finally:
            app.dependency_overrides.pop(get_application_server_ingest_client, None)
            app.dependency_overrides.pop(get_file_downloader, None)
            app.dependency_overrides.pop(get_document_parser_factory, None)
            get_generation_settings.cache_clear()


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

        # Qdrant SDK 타입은 일반 dense vector와 multi-vector를 같은 list Union으로
        # 표현한다. all(isinstance(...))만 사용하면 Mypy는 각 원소가 숫자라고 후속
        # 줄까지 축소하지 못하므로, 원소별 검증과 정규화를 한 번에 수행한다.
        numeric_vector: list[float] = []
        for raw_value in point.vector:
            if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
                raise AssertionError("Expected a one-dimensional numeric embedding vector.")
            numeric_vector.append(float(raw_value))

        assert len(numeric_vector) == e2e_runtime.settings.embedding_dim
        assert all(math.isfinite(value) for value in numeric_vector)
        assert any(value != 0.0 for value in numeric_vector)


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


# ============================================================
# 8. 실제 Claude 답변 호출과 공통 응답 계약
# ============================================================


def _execute_answer_scenario(
    runtime: E2eRuntime,
    scenario: AnswerScenario,
) -> AnswerScenarioResult:
    """한 시나리오를 실제 lookup 또는 synthesis 경로로 한 번만 실행한다.

    Claude 응답 원문 전체를 Assertion 메시지에 포함하면 질문과 문서 청크가 CI
    로그에 노출될 수 있다. HTTP 오류 메시지는 상태 코드와 공개 API 오류 code만
    남기고 body·질문·프롬프트·원문 답변은 출력하지 않는다.
    """

    response = runtime.client.post(
        "/api/v1/rag/answers",
        json={
            "user_idx": _TEST_USER_IDX,
            "reference_file_idxs": list(scenario.file_idxs),
            "query": scenario.query,
            "top_k": 20,
            "score_threshold": None,
        },
    )

    if response.status_code != 200:
        error_code = "unknown"
        try:
            response_body = _object(response.json(), "RAG answer error response")
            code_value = response_body.get("code")
            if isinstance(code_value, str) and code_value:
                error_code = code_value
        except (AssertionError, ValueError, TypeError):
            pass
        raise AssertionError(
            f"{scenario.scenario_id} answer failed: "
            f"status={response.status_code}, code={error_code}"
        )

    body = _object(response.json(), "RAG answer response")
    assert _bool(body, "success") is True
    assert _str(body, "code") == "RAG_ANSWER_COMPLETED"
    data = _object(body.get("data"), "RAG answer response data")

    assert _str(data, "status") == "answered"
    model = _str(data, "model")
    # Claude API는 설정의 별칭을 공급자 정식 모델 ID로 정규화하여 반환할 수 있다.
    # 특정 별칭 문자열의 완전 일치보다 Claude 모델 식별 계약을 확인하여 모델의
    # 정상적인 정규화 때문에 E2E가 실패하지 않게 한다.
    assert model.startswith("claude-")
    assert runtime.generation_settings.anthropic_model.startswith("claude-")

    usage = _object(data.get("usage"), "RAG answer usage")
    assert _int(usage, "input_tokens") > 0
    assert _int(usage, "output_tokens") > 0

    sources = tuple(_objects(data, "sources"))
    assert sources

    return AnswerScenarioResult(
        scenario=scenario,
        answer=_str(data, "answer"),
        cited_source_ids=_strings(data, "cited_source_ids"),
        sources=tuple(cast(Mapping[str, object], source) for source in sources),
        model=model,
    )


@pytest.fixture(scope="module")
def answer_runtime(e2e_runtime: E2eRuntime) -> AnswerRuntime:
    """모든 답변 시나리오를 한 번씩 호출하고 결과를 후속 검증에 재사용한다.

    동일 답변을 SOURCE 계약별 테스트에서 반복 호출하면 실제 Claude 비용과 실행
    시간이 검증 항목 수만큼 증가한다. 이 Fixture는 각 lookup, synthesis, OCR 혼합
    질문을 정확히 한 번 실행하고 불변 결과를 여러 테스트가 읽도록 한다.
    """

    results = {
        scenario.scenario_id: _execute_answer_scenario(e2e_runtime, scenario)
        for scenario in _ALL_ANSWER_SCENARIOS
    }
    return AnswerRuntime(results=results)


def _body_source_ids(answer: str) -> tuple[str, ...]:
    """답변 본문의 SOURCE-N을 중복 없이 최초 등장 순서로 반환한다."""

    return tuple(dict.fromkeys(_SOURCE_CITATION_PATTERN.findall(answer)))


def _locator(source: Mapping[str, object]) -> dict[str, object]:
    """최종 sources 항목에서 필수 공통 source_locator를 읽는다."""

    return _object(source.get("source_locator"), "RAG answer source_locator")


def _matching_answer_sources(
    result: AnswerScenarioResult,
    expected: ExpectedAnswerSource,
) -> tuple[Mapping[str, object], ...]:
    """예상 문서와 토큰을 실제 발췌문에 함께 포함하는 인용 출처를 찾는다."""

    case = _CASES_BY_ID[expected.case_id]
    return tuple(
        source
        for source in result.sources
        if _int(source, "file_idx") == case.file_idx
        and _contains_token(_str(source, "excerpt"), expected.token)
    )


def _assert_expected_answer_tokens(result: AnswerScenarioResult) -> None:
    """질문이 요구한 모든 고정 토큰이 실제 Claude 답변에 보존됐는지 확인한다."""

    for token in result.scenario.expected_tokens:
        assert _contains_token(result.answer, token), (
            f"{result.scenario.scenario_id} omitted expected token {token!r}."
        )


def _assert_expected_answer_sources(result: AnswerScenarioResult) -> None:
    """모든 예상 토큰이 올바른 문서·원본 위치 출처에 실제로 연결되는지 확인한다."""

    for expected in result.scenario.expected_sources:
        matching_sources = _matching_answer_sources(result, expected)
        assert matching_sources, (
            f"{result.scenario.scenario_id} did not cite the source containing {expected.token!r}."
        )
        assert any(
            all(
                _equivalent(_locator(source).get(key), value)
                for key, value in expected.locator.items()
            )
            for source in matching_sources
        ), (
            f"{result.scenario.scenario_id} cited {expected.token!r} without "
            "the expected source locator."
        )


def _assert_source_storage_links(
    runtime: E2eRuntime,
    result: AnswerScenarioResult,
) -> None:
    """외부 sources의 Chunk_ID가 선택 문서의 실제 Local RAG 원본 청크인지 확인한다."""

    states = {
        case.file_idx: asyncio.run(_database_state(runtime.settings, case))
        for case in result.scenario.cases
    }
    chunk_ids_by_file = {
        file_idx: {_str(chunk, "chunk_id") for chunk in state.chunks}
        for file_idx, state in states.items()
    }

    selected_file_idxs = frozenset(result.scenario.file_idxs)
    assert frozenset(_int(source, "file_idx") for source in result.sources) <= (selected_file_idxs)

    for source in result.sources:
        file_idx = _int(source, "file_idx")
        case = next(case for case in result.scenario.cases if case.file_idx == file_idx)
        assert _str(source, "file_name") == case.file_name
        assert _str(source, "file_type") == case.file_type
        assert _str(source, "chunk_id") in chunk_ids_by_file[file_idx]
        assert _str(source, "excerpt")

        locator = _locator(source)
        assert _str(locator, "file_type") == case.file_type

        # 하위 호환 대표 위치 필드가 존재하면 공통 locator와 반드시 같은 값이어야 한다.
        for legacy_key, locator_key in (
            ("page", "page"),
            ("slide_no", "slide_no"),
            ("sheet_name", "sheet_name"),
            ("section_title", "section_title"),
        ):
            legacy_value = source.get(legacy_key)
            locator_value = locator.get(locator_key)
            if legacy_value is not None and locator_value is not None:
                assert legacy_value == locator_value


# ============================================================
# 9. 형식별 단일 문서 lookup 답변
# ============================================================


@pytest.mark.parametrize(
    "scenario",
    _LOOKUP_ANSWER_SCENARIOS,
    ids=lambda scenario: scenario.scenario_id,
)
def test_format_specific_single_document_lookup_answer(
    e2e_runtime: E2eRuntime,
    answer_runtime: AnswerRuntime,
    scenario: AnswerScenario,
) -> None:
    """PDF·DOCX·PPTX·XLSX·TXT 단일 문서 질문이 선택 범위 안에서 답변되는지 검증한다."""

    assert len(scenario.case_ids) == 1
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_tokens(result)
    _assert_expected_answer_sources(result)
    _assert_source_storage_links(e2e_runtime, result)
    assert {_int(source, "file_idx") for source in result.sources} == {scenario.file_idxs[0]}


# ============================================================
# 10. 여러 형식 synthesis 답변
# ============================================================


def test_multiformat_synthesis_answer_uses_every_selected_format(
    e2e_runtime: E2eRuntime,
    answer_runtime: AnswerRuntime,
) -> None:
    """다섯 형식의 문서별 부분 답변이 최종 synthesis 답변과 출처에 모두 남는지 검증한다."""

    scenario = _SYNTHESIS_ANSWER_SCENARIO
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_tokens(result)
    _assert_expected_answer_sources(result)
    _assert_source_storage_links(e2e_runtime, result)

    assert len(scenario.case_ids) == len(_TEXT_CASE_IDS)
    assert {_int(source, "file_idx") for source in result.sources} == set(scenario.file_idxs)
    assert {_str(source, "file_type") for source in result.sources} == {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "txt",
    }


# ============================================================
# 11. 텍스트 청크와 OCR 청크 혼합 답변
# ============================================================


def test_text_and_ocr_chunks_are_used_in_one_answer(
    e2e_runtime: E2eRuntime,
    answer_runtime: AnswerRuntime,
) -> None:
    """한 혼합 PDF의 텍스트 1페이지와 OCR 2페이지가 같은 lookup 답변에 사용되는지 검증한다."""

    scenario = _MIXED_TEXT_OCR_ANSWER_SCENARIO
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_tokens(result)
    _assert_expected_answer_sources(result)
    _assert_source_storage_links(e2e_runtime, result)

    origins = {_str(_locator(source), "content_origin") for source in result.sources}
    assert origins == {"text", "ocr"}
    assert {_int(_locator(source), "page") for source in result.sources} == {1, 2}
    assert {_int(source, "file_idx") for source in result.sources} == {scenario.file_idxs[0]}


# ============================================================
# 12. SOURCE-N·cited_source_ids·sources 일치
# ============================================================


@pytest.mark.parametrize(
    "scenario",
    _ALL_ANSWER_SCENARIOS,
    ids=lambda scenario: scenario.scenario_id,
)
def test_answer_body_cited_source_ids_and_sources_are_identical(
    answer_runtime: AnswerRuntime,
    scenario: AnswerScenario,
) -> None:
    """본문 최초 인용 순서와 선언·외부 sources 순서가 모든 답변에서 정확히 같은지 검증한다."""

    result = answer_runtime.result(scenario.scenario_id)
    body_source_ids = _body_source_ids(result.answer)
    response_source_ids = tuple(_str(source, "source_id") for source in result.sources)
    chunk_ids = tuple(_str(source, "chunk_id") for source in result.sources)

    assert body_source_ids
    assert body_source_ids == result.cited_source_ids
    assert body_source_ids == response_source_ids
    assert len(response_source_ids) == len(set(response_source_ids))
    assert len(chunk_ids) == len(set(chunk_ids))


# ============================================================
# 13. PDF 페이지 출처 위치
# ============================================================


def test_pdf_answer_sources_preserve_page_location(answer_runtime: AnswerRuntime) -> None:
    """PDF 텍스트·표 답변 출처가 원본 1페이지 locator로 반환되는지 검증한다."""

    scenario = _ANSWER_SCENARIOS_BY_ID["lookup-pdf-text-table"]
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_sources(result)

    for source in result.sources:
        locator = _locator(source)
        assert _str(locator, "file_type") == "pdf"
        assert _str(locator, "kind") == "pdf_page"
        assert _str(locator, "content_origin") == "text"
        assert _int(locator, "page") == 1


# ============================================================
# 14. DOCX 문단·표 출처 위치
# ============================================================


def test_docx_answer_sources_distinguish_paragraph_and_table(
    answer_runtime: AnswerRuntime,
) -> None:
    """DOCX 본문과 표 인용이 각자의 block·paragraph 또는 table 위치를 보존하는지 검증한다."""

    scenario = _ANSWER_SCENARIOS_BY_ID["lookup-docx-paragraph-table"]
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_sources(result)

    expected_unit_types = {"paragraph", "table"}
    cited_unit_types = {_str(_locator(source), "unit_type") for source in result.sources}
    assert expected_unit_types <= cited_unit_types


# ============================================================
# 15. PPTX 슬라이드 출처 위치
# ============================================================


def test_pptx_answer_sources_preserve_slide_and_shape_location(
    answer_runtime: AnswerRuntime,
) -> None:
    """PPTX 두 슬라이드의 인용이 slide_no와 shape_path를 구분해 반환하는지 검증한다."""

    scenario = _ANSWER_SCENARIOS_BY_ID["lookup-pptx-slide-shapes"]
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_sources(result)

    slide_numbers = {_int(_locator(source), "slide_no") for source in result.sources}
    assert {1, 2} <= slide_numbers
    assert all(_str(_locator(source), "shape_path") for source in result.sources)


# ============================================================
# 16. XLSX 시트·셀 범위 출처 위치
# ============================================================


def test_xlsx_answer_sources_preserve_sheet_and_cell_range(
    answer_runtime: AnswerRuntime,
) -> None:
    """XLSX 인용이 Overview·Details 시트와 실제 A2:B2 범위를 함께 반환하는지 검증한다."""

    scenario = _ANSWER_SCENARIOS_BY_ID["lookup-xlsx-sheet-cell-ranges"]
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_sources(result)

    sheet_names = {_str(_locator(source), "sheet_name") for source in result.sources}
    assert {"Overview", "Details"} <= sheet_names
    assert all(_str(_locator(source), "cell_range") for source in result.sources)


# ============================================================
# 17. TXT 줄 범위 출처 위치
# ============================================================


def test_txt_answer_sources_preserve_line_and_character_ranges(
    answer_runtime: AnswerRuntime,
) -> None:
    """TXT 2·3번째 줄 인용이 줄 범위와 원본 문자 범위를 정확히 반환하는지 검증한다."""

    scenario = _ANSWER_SCENARIOS_BY_ID["lookup-txt-line-ranges"]
    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_sources(result)

    line_numbers = {_int(_locator(source), "line_number") for source in result.sources}
    assert {2, 3} <= line_numbers
    for source in result.sources:
        locator = _locator(source)
        assert _int(locator, "line_start") == _int(locator, "line_end")
        assert _int(locator, "char_end") >= _int(locator, "char_start")


# ============================================================
# 18. OCR 이미지 순번과 원본 위치 출처
# ============================================================


@pytest.mark.parametrize(
    "scenario",
    (*_OCR_LOOKUP_ANSWER_SCENARIOS, _MIXED_TEXT_OCR_ANSWER_SCENARIO),
    ids=lambda scenario: scenario.scenario_id,
)
def test_ocr_answer_sources_preserve_image_ordinal_and_original_location(
    answer_runtime: AnswerRuntime,
    scenario: AnswerScenario,
) -> None:
    """OCR 인용이 이미지 순번과 PDF·DOCX·PPTX·XLSX 원본 위치를 함께 반환하는지 검증한다."""

    result = answer_runtime.result(scenario.scenario_id)
    _assert_expected_answer_sources(result)

    ocr_sources = tuple(
        source for source in result.sources if _str(_locator(source), "content_origin") == "ocr"
    )
    assert ocr_sources

    for source in ocr_sources:
        locator = _locator(source)
        image_ordinal = _int(locator, "image_ordinal")
        assert image_ordinal == _int(locator, "image_index")
        assert image_ordinal > 0
        assert _str(locator, "image_id")
        assert _str(locator, "image_kind")
        assert _str(locator, "ocr_engine") == "EASYOCR_CUDA"

        confidence = locator.get("ocr_mean_confidence")
        assert isinstance(confidence, int | float) and not isinstance(confidence, bool)
        assert 0.0 < float(confidence) <= 1.0

        file_type = _str(locator, "file_type")
        if file_type == "pdf":
            assert _int(locator, "page") > 0
        elif file_type == "docx":
            assert any(
                locator.get(key) is not None
                for key in ("block_index", "paragraph_index", "table_index")
            )
        elif file_type == "pptx":
            assert _int(locator, "slide_no") > 0
        elif file_type == "xlsx":
            assert _str(locator, "sheet_name")
            assert _str(locator, "cell_range")
        else:
            raise AssertionError(f"Unexpected OCR answer file type: {file_type}")


# ============================================================
# 19. 범위·근거 부족·부분 실패·색인 생명주기 공통 Test Double
# ============================================================


_MISSING_OVERRIDE: Final[object] = object()
_SECURITY_PROBE_TOKEN: Final[str] = "OTHER-USER-SECURITY-PROBE-ONLY"
_SURVIVING_EVIDENCE_TOKEN: Final[str] = "SURVIVING-DOCUMENT-EVIDENCE"
_FAILED_DOCUMENT_TOKEN: Final[str] = "FAILED-DOCUMENT-EVIDENCE"


@contextmanager
def _temporary_dependency_override(
    dependency: Callable[..., object],
    replacement: Callable[..., object],
) -> Iterator[None]:
    """FastAPI dependency override를 예외와 Assertion 실패에도 원래 상태로 복구한다."""

    previous = app.dependency_overrides.get(dependency, _MISSING_OVERRIDE)
    app.dependency_overrides[dependency] = replacement
    try:
        yield
    finally:
        if previous is _MISSING_OVERRIDE:
            app.dependency_overrides.pop(dependency, None)
        else:
            app.dependency_overrides[dependency] = cast(
                Callable[..., object],
                previous,
            )


@dataclass(frozen=True, slots=True)
class _VersionedDocumentParser:
    """실제 파싱 결과는 유지하면서 재색인 정체성의 parser_version만 변경한다."""

    delegate: DocumentParser
    version: str

    @property
    def file_type(self) -> DocumentType:
        """원본 파서가 담당하는 DocumentType을 그대로 반환한다."""

        return self.delegate.file_type

    @property
    def parser_type(self) -> str:
        """텍스트·OCR 추출 방식 식별자는 원본과 동일하게 유지한다."""

        return self.delegate.parser_type

    @property
    def parser_version(self) -> str:
        """새 문서 버전을 강제하는 E2E 전용 호환 버전을 반환한다."""

        return self.version

    async def parse(self, file_path: Path) -> ParsedDocument:
        """운영 파서의 실제 다중 형식 파싱과 OCR 처리를 그대로 위임한다."""

        return await self.delegate.parse(file_path)


class _FailIfCalledGenerationClient:
    """근거가 없을 때 Claude 호출이 발생하면 즉시 실패시키는 생성 대역."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        del request
        self.call_count += 1
        raise AssertionError("Claude must not be called without document evidence.")


class _ControlledChunkSearcher:
    """문서별 검색 성공·실패를 결정적으로 재현하는 synthesis 검색 대역."""

    def __init__(
        self,
        *,
        responses: Mapping[int, ChunkSearchResponse],
        failed_file_idxs: frozenset[int] = frozenset(),
    ) -> None:
        self._responses = dict(responses)
        self._failed_file_idxs = failed_file_idxs
        self.requests: list[ChunkSearchRequest] = []

    async def search(self, request: ChunkSearchRequest) -> ChunkSearchResponse:
        """단일 문서 synthesis 검색 요청을 기록하고 지정 실패만 발생시킨다."""

        self.requests.append(request)
        if len(request.reference_file_idxs) != 1:
            raise AssertionError("Controlled synthesis search requires one file per call.")

        file_idx = request.reference_file_idxs[0]
        if file_idx in self._failed_file_idxs:
            raise VectorDatabaseUnavailableError(
                "search_chunks",
                status_code=503,
            )
        try:
            return self._responses[file_idx]
        except KeyError as error:
            raise AssertionError(f"Missing controlled search response for {file_idx}.") from error


class _DeterministicStructuredGenerationClient:
    """부분·최종 synthesis에서 실제 SOURCE 계약만 결정적으로 응답하는 대역."""

    def __init__(self, *, answer_token: str) -> None:
        self.answer_token = answer_token
        self.requests: list[GenerationRequest] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """현재 프롬프트에 존재하는 첫 SOURCE를 인용한 구조화 응답을 반환한다."""

        self.requests.append(request)
        source_ids = tuple(dict.fromkeys(re.findall(r"SOURCE-[1-9][0-9]*", request.user_prompt)))
        if not source_ids:
            raise AssertionError("A deterministic generation prompt requires SOURCE-N.")

        cited_source_id = source_ids[0]
        structured_output: dict[str, object] = {
            "status": "answered",
            "answer": f"{self.answer_token} [{cited_source_id}]",
            "cited_source_ids": [cited_source_id],
        }
        return GenerationResult(
            text=json.dumps(structured_output, ensure_ascii=False),
            model="claude-e2e-deterministic",
            usage=GenerationUsage(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
            structured_output=structured_output,
        )


class _SensitiveFailureGenerationClient:
    """프롬프트 수신 후 안전한 공급자 오류만 발생시켜 로그 비노출을 검증한다."""

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        raise GenerationServerError(
            "Generation provider request failed.",
            provider="anthropic",
            status_code=503,
            request_id="safe-e2e-request-id",
        )


class _FailPreviousDeactivationVectorStore:
    """신규 Point 활성화 뒤 이전 문서 비활성화만 한 번 실패시키는 Qdrant 대역."""

    def __init__(self, delegate: QdrantChunkVectorStore) -> None:
        self._delegate = delegate
        self.failure_count = 0

    async def upsert_document(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
        is_active: bool,
    ) -> None:
        await self._delegate.upsert_document(
            rag_document_idx=rag_document_idx,
            metadata=metadata,
            embedded_document=embedded_document,
            is_active=is_active,
        )

    async def set_documents_active(
        self,
        *,
        rag_document_idxs: tuple[int, ...],
        is_active: bool,
    ) -> None:
        # 신규 문서 활성화는 성공시키고, 기존 정상 문서를 비활성화하는 첫 호출만
        # 실패시킨다. 이후 보상 단계의 이전 문서 재활성화와 신규 문서 비활성화는
        # 실제 Qdrant에 위임하여 최종 검색 가능 상태까지 검증한다.
        if not is_active and rag_document_idxs and self.failure_count == 0:
            self.failure_count += 1
            raise VectorDatabaseUnavailableError(
                "set_documents_active",
                status_code=503,
            )
        await self._delegate.set_documents_active(
            rag_document_idxs=rag_document_idxs,
            is_active=is_active,
        )

    async def delete_chunks(self, *, chunk_ids: tuple[str, ...]) -> None:
        await self._delegate.delete_chunks(chunk_ids=chunk_ids)


class _StaticRagAnswerServiceProvider:
    """FastAPI override가 동일한 RAG 답변 서비스 인스턴스를 반환하도록 한다."""

    def __init__(self, service: RoutedRagAnswerService) -> None:
        self._service = service

    def __call__(self) -> RoutedRagAnswerService:
        return self._service


def _controlled_search_result(
    *,
    file_idx: int,
    content: str,
    content_origin: str = "text",
) -> ChunkSearchResult:
    """보안·부분 실패 테스트에 사용할 형식 유효한 단일 PDF 검색 결과를 만든다."""

    source_metadata: dict[str, object] = {
        "page_number": 1,
        "unit_type": "ocr_image" if content_origin == "ocr" else "paragraph",
        "content_origin": content_origin,
    }
    if content_origin == "ocr":
        source_metadata.update(
            {
                "image_index": 1,
                "image_id": "controlled-ocr-image-1",
                "image_kind": "pdf_embedded",
                "ocr_engine": "EASYOCR_CUDA",
                "ocr_mean_confidence": 0.95,
            }
        )

    return ChunkSearchResult(
        chunk_id=str(uuid5(NAMESPACE_URL, f"controlled-chunk-{file_idx}-{content}")),
        score=1.0,
        rag_document_idx=file_idx + 10_000,
        file_idx=file_idx,
        folder_idx=_TEST_FOLDER_IDX,
        file_name=f"controlled-{file_idx}.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        content=content,
        token_count=None,
        source_locator=build_source_locator(
            file_type=SupportedFileType.PDF,
            source_metadata=source_metadata,
        ),
        parser_version="controlled-e2e-v1",
        embedding_model="controlled-e2e-embedding",
        index_version=_INDEX_VERSION,
    )


def _response_code(response: object) -> str:
    """오류 본문 전체를 출력하지 않고 공개 code만 안전하게 읽는다."""

    json_method = getattr(response, "json", None)
    if not callable(json_method):
        return "unknown"
    try:
        body = _object(json_method(), "API response")
    except (AssertionError, TypeError, ValueError):
        return "unknown"
    value = body.get("code")
    return value if isinstance(value, str) and value else "unknown"


def _active_documents(history: FileHistoryState) -> tuple[Mapping[str, object], ...]:
    """검색 가능한 INDEXED·비삭제 문서만 반환한다."""

    return tuple(
        document
        for document in history.documents
        if _str(document, "index_status") == "INDEXED" and not _db_bool(document, "is_deleted")
    )


def _assert_directory_empty(path: Path) -> None:
    """다운로드·추출 작업 후 임시 디렉터리에 파일이나 하위 폴더가 없는지 확인한다."""

    assert path.is_dir()
    remaining = tuple(path.rglob("*"))
    assert remaining == (), (
        "Temporary document or extracted image artifacts remained: "
        f"{tuple(item.name for item in remaining)!r}"
    )


# ============================================================
# 20. 선택하지 않은 문서와 다른 사용자의 출처 차단
# ============================================================


def test_unselected_document_and_other_user_sources_are_blocked(
    e2e_runtime: E2eRuntime,
    answer_runtime: AnswerRuntime,
) -> None:
    """실제 Qdrant 필터와 최종 답변이 사용자·선택 문서 경계를 모두 지키는지 검증한다."""

    selected_case = _CASES_BY_ID["pdf-text-table"]
    unselected_case = _CASES_BY_ID["docx-structure"]
    selected_points = asyncio.run(
        _qdrant_points(
            e2e_runtime.settings,
            users_idx=_TEST_USER_IDX,
            file_idxs=(selected_case.file_idx,),
        )
    )
    assert selected_points

    probe_id = str(uuid5(NAMESPACE_URL, "issue-123-other-user-security-probe"))
    asyncio.run(
        _upsert_qdrant_probe_point(
            e2e_runtime.settings,
            source_point=selected_points[0],
            point_id=probe_id,
            users_idx=_OTHER_USER_IDX,
        )
    )
    try:
        # 같은 File_IDX와 같은 벡터를 가진 다른 사용자 Point가 실제로 존재하는 상태에서
        # 원래 사용자의 검색 결과에 probe가 섞이지 않아야 사용자 필터 검증이 유효하다.
        other_user_points = asyncio.run(
            _qdrant_points(
                e2e_runtime.settings,
                users_idx=_OTHER_USER_IDX,
                file_idxs=(selected_case.file_idx,),
            )
        )
        assert any(str(point.id) == probe_id for point in other_user_points)

        response = e2e_runtime.client.post(
            "/api/v1/chunks/search",
            json={
                "user_idx": _TEST_USER_IDX,
                "reference_file_idxs": [selected_case.file_idx],
                "query": (
                    "선택 문서의 PDF-PARAGRAPH-TOKEN-101을 찾고 "
                    "선택하지 않은 DOCX-PARAGRAPH-TOKEN-201은 제외해 주세요."
                ),
                "top_k": 20,
                "score_threshold": None,
            },
        )
        assert response.status_code == 200
        body = _object(response.json(), "scope search response")
        data = _object(body.get("data"), "scope search response data")
        results = _objects(data, "results")
        assert results
        assert {_int(result, "file_idx") for result in results} == {selected_case.file_idx}
        assert all(_str(result, "chunk_id") != probe_id for result in results)
        assert all(
            not _contains_token(_str(result, "content"), _SECURITY_PROBE_TOKEN)
            for result in results
        )
        assert all(
            not _contains_token(
                _str(result, "content"),
                unselected_case.assertions[0].token,
            )
            for result in results
        )

        # 최종 답변 계층도 검색 후보 전체가 아니라 질문에서 선택한 파일의 실제 인용
        # sources만 반환해야 한다. 기존 실제 Claude lookup 결과를 보안 회귀 기준으로 쓴다.
        scenario = _ANSWER_SCENARIOS_BY_ID["lookup-pdf-text-table"]
        answer_result = answer_runtime.result(scenario.scenario_id)
        assert {_int(source, "file_idx") for source in answer_result.sources} == {
            selected_case.file_idx
        }
        assert all(
            _int(source, "file_idx") != unselected_case.file_idx for source in answer_result.sources
        )
    finally:
        asyncio.run(
            _delete_qdrant_probe_point(
                e2e_runtime.settings,
                point_id=probe_id,
            )
        )


# ============================================================
# 21. 전체 근거 부족 시 Claude 미호출
# ============================================================


def test_no_evidence_skips_claude_generation(e2e_runtime: E2eRuntime) -> None:
    """실제 TEI·Qdrant 검색 결과가 비면 Claude dependency가 호출되지 않는지 검증한다."""

    generation_client = _FailIfCalledGenerationClient()

    def generation_dependency() -> _FailIfCalledGenerationClient:
        return generation_client

    with _temporary_dependency_override(
        get_generation_client,
        generation_dependency,
    ):
        response = e2e_runtime.client.post(
            "/api/v1/rag/answers",
            json={
                "user_idx": _TEST_USER_IDX,
                "reference_file_idxs": [_NO_EVIDENCE_FILE_IDX],
                "query": "색인되지 않은 문서에서 존재하지 않는 근거를 찾아 주세요.",
                "top_k": 5,
                "score_threshold": None,
            },
        )

    assert response.status_code == 200
    body = _object(response.json(), "no evidence answer response")
    data = _object(body.get("data"), "no evidence answer data")
    assert _str(data, "status") == "insufficient_evidence"
    assert _str(data, "answer") == "제공된 문서 근거만으로는 답변할 수 없습니다."
    assert _strings(data, "cited_source_ids") == ()
    assert _objects(data, "sources") == []
    assert data.get("model") is None
    assert data.get("usage") is None
    assert generation_client.call_count == 0


# ============================================================
# 22. 일부 문서 처리 실패 시 나머지 문서 답변 유지
# ============================================================


def test_synthesis_keeps_valid_document_when_one_document_search_fails(
    e2e_runtime: E2eRuntime,
) -> None:
    """한 문서의 VectorDB 실패가 다른 문서의 검증된 부분 답변을 제거하지 않는지 검증한다."""

    valid_file_idx = _NO_EVIDENCE_FILE_IDX + 1
    failed_file_idx = _NO_EVIDENCE_FILE_IDX + 2
    valid_result = _controlled_search_result(
        file_idx=valid_file_idx,
        content=_SURVIVING_EVIDENCE_TOKEN,
    )
    searcher = _ControlledChunkSearcher(
        responses={
            valid_file_idx: ChunkSearchResponse(
                user_idx=_TEST_USER_IDX,
                result_count=1,
                results=(valid_result,),
            )
        },
        failed_file_idxs=frozenset({failed_file_idx}),
    )
    generation_client = _DeterministicStructuredGenerationClient(
        answer_token=_SURVIVING_EVIDENCE_TOKEN,
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    with _temporary_dependency_override(
        get_rag_answer_service,
        _StaticRagAnswerServiceProvider(service),
    ):
        response = e2e_runtime.client.post(
            "/api/v1/rag/answers",
            json={
                "user_idx": _TEST_USER_IDX,
                "reference_file_idxs": [valid_file_idx, failed_file_idx],
                "query": "선택한 두 문서를 비교하고 확인 가능한 근거를 종합해 주세요.",
                "top_k": 5,
                "score_threshold": None,
            },
        )

    assert response.status_code == 200
    body = _object(response.json(), "partial success answer response")
    data = _object(body.get("data"), "partial success answer data")
    assert _str(data, "status") == "answered"
    assert _SURVIVING_EVIDENCE_TOKEN in _str(data, "answer")
    assert _FAILED_DOCUMENT_TOKEN not in _str(data, "answer")

    sources = _objects(data, "sources")
    assert sources
    assert {_int(source, "file_idx") for source in sources} == {valid_file_idx}
    assert all(_int(source, "file_idx") != failed_file_idx for source in sources)

    # 정상 문서 부분 답변 한 번과 최종 synthesis 한 번만 생성한다. 실패 문서는 검색
    # 단계에서 제외되므로 해당 문서용 Claude 호출은 발생하지 않는다.
    assert len(generation_client.requests) == 2
    assert tuple(request.reference_file_idxs for request in searcher.requests) == (
        (valid_file_idx,),
        (failed_file_idx,),
    )


# ============================================================
# 23. 재인제스트·재색인·삭제·보상 처리
# ============================================================


def test_reingest_reindex_soft_delete_and_compensation(
    e2e_runtime: E2eRuntime,
) -> None:
    """멱등 재인제스트, 새 버전 전환, 이전 삭제와 실패 보상을 실제 DB·Qdrant로 검증한다."""

    case = _OPERATION_CASE
    settings = e2e_runtime.settings
    asyncio.run(_cleanup(settings, (case.file_idx,)))
    e2e_runtime.recorder.clear_file_history(case.file_idx)

    base_parser = e2e_runtime.parser_factory.get_parser(case.file_type)
    reindex_version = f"{base_parser.parser_version}-issue123-reindex"
    failed_version = f"{base_parser.parser_version}-issue123-compensation"

    try:
        first_response = e2e_runtime.client.post("/ingest", json=case.manifest)
        second_response = e2e_runtime.client.post("/ingest", json=case.manifest)
        assert first_response.status_code == 200
        assert second_response.status_code == 200

        idempotent_history = asyncio.run(
            _file_history_state(
                settings,
                users_idx=_TEST_USER_IDX,
                file_idx=case.file_idx,
            )
        )
        assert len(idempotent_history.documents) == 1
        assert len(idempotent_history.runs) == 2
        assert all(_str(run, "status") == "SUCCESS" for run in idempotent_history.runs)
        reused_document_idx = _int(
            idempotent_history.documents[0],
            "rag_document_idx",
        )
        assert {_int(run, "rag_document_idx") for run in idempotent_history.runs} == {
            reused_document_idx
        }

        # 같은 실제 문서 바이트를 새 parser_version으로 처리하면 새 문서가 staging된
        # 뒤 활성화되고, 기존 정상 문서는 성공 확정 후에만 soft delete된다.
        reindex_factory = DocumentParserFactory(
            parsers=(
                _VersionedDocumentParser(
                    delegate=base_parser,
                    version=reindex_version,
                ),
            )
        )
        with _temporary_dependency_override(
            get_document_parser_factory,
            lambda: reindex_factory,
        ):
            reindex_response = e2e_runtime.client.post("/ingest", json=case.manifest)
        assert reindex_response.status_code == 200

        reindexed_history = asyncio.run(
            _file_history_state(
                settings,
                users_idx=_TEST_USER_IDX,
                file_idx=case.file_idx,
            )
        )
        assert len(reindexed_history.documents) == 2
        active_documents = _active_documents(reindexed_history)
        assert len(active_documents) == 1
        active_document = active_documents[0]
        assert _str(active_document, "parser_version") == reindex_version
        active_document_idx = _int(active_document, "rag_document_idx")

        deleted_documents = tuple(
            document for document in reindexed_history.documents if _db_bool(document, "is_deleted")
        )
        assert len(deleted_documents) == 1
        assert _int(deleted_documents[0], "rag_document_idx") == reused_document_idx

        all_points = asyncio.run(
            _qdrant_points(
                settings,
                users_idx=_TEST_USER_IDX,
                file_idxs=(case.file_idx,),
                active_only=False,
            )
        )
        active_point_document_ids = {
            _int(cast(Mapping[str, object], point.payload), "rag_document_idx")
            for point in all_points
            if point.payload is not None
            and _bool(cast(Mapping[str, object], point.payload), "is_active")
        }
        inactive_point_document_ids = {
            _int(cast(Mapping[str, object], point.payload), "rag_document_idx")
            for point in all_points
            if point.payload is not None
            and not _bool(cast(Mapping[str, object], point.payload), "is_active")
        }
        assert active_point_document_ids == {active_document_idx}
        assert reused_document_idx in inactive_point_document_ids

        # 다음 새 버전은 실제 Qdrant 신규 활성화까지 진행한 뒤 이전 문서 비활성화에서
        # 실패시킨다. 서비스는 이전 정상 Point를 다시 활성화하고 신규 Point를 삭제하며,
        # Local RAG에는 실패 문서와 FAILED 실행 이력만 남겨야 한다.
        compensation_factory = DocumentParserFactory(
            parsers=(
                _VersionedDocumentParser(
                    delegate=base_parser,
                    version=failed_version,
                ),
            )
        )
        failing_store_holder: list[_FailPreviousDeactivationVectorStore] = []

        def failing_indexing_service(
            database_session: DatabaseSessionDependency,
            vector_store: QdrantVectorStoreDependency,
            file_lock: FileIndexLockDependency,
        ) -> FileIndexingService:
            wrapped_store = _FailPreviousDeactivationVectorStore(vector_store)
            failing_store_holder.append(wrapped_store)
            return FileIndexingService(
                local_repository=ConcurrentSafeLocalRagIndexRepository(database_session),
                vector_store=wrapped_store,
                file_lock=file_lock,
            )

        with (
            _temporary_dependency_override(
                get_document_parser_factory,
                lambda: compensation_factory,
            ),
            _temporary_dependency_override(
                get_file_indexing_service,
                failing_indexing_service,
            ),
        ):
            failed_response = e2e_runtime.client.post("/ingest", json=case.manifest)

        assert failed_response.status_code == 503, (
            "Compensation fault returned an unexpected status: "
            f"code={_response_code(failed_response)}"
        )
        assert failing_store_holder
        assert failing_store_holder[-1].failure_count == 1

        compensated_history = asyncio.run(
            _file_history_state(
                settings,
                users_idx=_TEST_USER_IDX,
                file_idx=case.file_idx,
            )
        )
        compensated_active = _active_documents(compensated_history)
        assert len(compensated_active) == 1
        assert _int(compensated_active[0], "rag_document_idx") == active_document_idx
        assert _str(compensated_active[0], "parser_version") == reindex_version

        failed_documents = tuple(
            document
            for document in compensated_history.documents
            if _str(document, "index_status") == "FAILED"
        )
        assert len(failed_documents) == 1
        failed_document_idx = _int(failed_documents[0], "rag_document_idx")
        assert _str(failed_documents[0], "parser_version") == failed_version
        assert any(
            _str(run, "status") == "FAILED" and _int(run, "rag_document_idx") == failed_document_idx
            for run in compensated_history.runs
        )

        compensated_points = asyncio.run(
            _qdrant_points(
                settings,
                users_idx=_TEST_USER_IDX,
                file_idxs=(case.file_idx,),
                active_only=False,
            )
        )
        compensated_point_document_ids = {
            _int(cast(Mapping[str, object], point.payload), "rag_document_idx")
            for point in compensated_points
            if point.payload is not None
        }
        assert failed_document_idx not in compensated_point_document_ids
        assert active_document_idx in compensated_point_document_ids

        callbacks = e2e_runtime.recorder.payloads(case.file_idx)
        assert len(callbacks) == 4
        assert tuple(_bool(payload, "success") for payload in callbacks) == (
            True,
            True,
            True,
            False,
        )
        failure_callback = callbacks[-1]
        assert "chunks" not in failure_callback
        assert "chunk_count" not in failure_callback
        assert "index_version" not in failure_callback
    finally:
        asyncio.run(_cleanup(settings, (case.file_idx,)))
        e2e_runtime.recorder.clear_file_history(case.file_idx)


# ============================================================
# 24. 중복 요청과 동시 인제스트
# ============================================================


def test_duplicate_concurrent_ingest_is_serialized_and_idempotent(
    e2e_runtime: E2eRuntime,
) -> None:
    """같은 File_IDX 동시 요청이 하나의 문서·Point 집합과 두 성공 이력으로 수렴하는지 검증한다."""

    case = _OPERATION_CASE
    settings = e2e_runtime.settings
    asyncio.run(_cleanup(settings, (case.file_idx,)))
    e2e_runtime.recorder.clear_file_history(case.file_idx)
    start_barrier = threading.Barrier(3)

    def post_ingest() -> tuple[int, str]:
        """두 작업 스레드가 같은 시점에 POST /ingest를 시작하도록 대기한다."""

        start_barrier.wait(timeout=30)
        response = e2e_runtime.client.post("/ingest", json=case.manifest)
        return response.status_code, _response_code(response)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(post_ingest) for _ in range(2))
            start_barrier.wait(timeout=30)
            results = tuple(future.result(timeout=300) for future in futures)

        assert results == (
            (200, "FILE_INDEXING_COMPLETED"),
            (200, "FILE_INDEXING_COMPLETED"),
        )

        history = asyncio.run(
            _file_history_state(
                settings,
                users_idx=_TEST_USER_IDX,
                file_idx=case.file_idx,
            )
        )
        assert len(history.documents) == 1
        assert len(_active_documents(history)) == 1
        assert len(history.runs) == 2
        assert all(_str(run, "status") == "SUCCESS" for run in history.runs)
        assert len({_int(run, "rag_document_idx") for run in history.runs}) == 1

        chunk_ids = tuple(_str(chunk, "chunk_id") for chunk in history.chunks)
        assert chunk_ids
        assert len(chunk_ids) == len(set(chunk_ids))

        points = asyncio.run(
            _qdrant_points(
                settings,
                users_idx=_TEST_USER_IDX,
                file_idxs=(case.file_idx,),
            )
        )
        assert {str(point.id) for point in points} == set(chunk_ids)
        assert e2e_runtime.recorder.manifest_request_count(case.file_idx) == 2

        callbacks = e2e_runtime.recorder.payloads(case.file_idx)
        assert len(callbacks) == 2
        assert all(_bool(payload, "success") for payload in callbacks)
        callback_chunk_id_sets = tuple(
            tuple(_str(chunk, "chunk_id") for chunk in _objects(payload, "chunks"))
            for payload in callbacks
        )
        assert callback_chunk_id_sets[0] == callback_chunk_id_sets[1]
        assert set(callback_chunk_id_sets[0]) == set(chunk_ids)
    finally:
        asyncio.run(_cleanup(settings, (case.file_idx,)))
        e2e_runtime.recorder.clear_file_history(case.file_idx)


# ============================================================
# 25. 임시 파일과 추출 이미지 정리
# ============================================================


def test_temporary_download_and_extracted_image_resources_are_cleaned(
    e2e_runtime: E2eRuntime,
) -> None:
    """성공한 XLSX OCR 인제스트 뒤 다운로드 파일과 추출 이미지 임시물이 남지 않는지 검증한다."""

    case = _RESOURCE_CLEANUP_CASE
    settings = e2e_runtime.settings
    asyncio.run(_cleanup(settings, (case.file_idx,)))
    e2e_runtime.recorder.clear_file_history(case.file_idx)
    _assert_directory_empty(e2e_runtime.download_temp_directory)

    try:
        response = e2e_runtime.client.post("/ingest", json=case.manifest)
        assert response.status_code == 200
        callback = e2e_runtime.recorder.callback(case.file_idx)
        callback_chunks = _objects(callback, "chunks")
        assert callback_chunks

        # XLSX 인제스트 callback에는 일반 시트 텍스트 청크와 OCR 청크가 함께
        # 포함될 수 있다. 일반 텍스트 청크에는 content_origin이 존재하지 않는 것이
        # 정상 계약이므로 필수 문자열 판독기인 _str()로 모든 청크를 읽으면 안 된다.
        # 선택 필드 값을 안전하게 조회하여 실제 OCR 청크가 하나 이상 포함됐는지만
        # 검증한다.
        ocr_callback_chunks = tuple(
            chunk
            for chunk in callback_chunks
            if _source_metadata(chunk).get("content_origin") == "ocr"
        )
        assert ocr_callback_chunks, (
            "The successful XLSX image ingest must report at least one OCR chunk."
        )

        # 다운로더의 *.document와 형식 검증 임시 파일은 async context 종료 시 삭제된다.
        # XLSX 삽입 이미지는 바이트 기반 불변 모델로 전달되므로 callback·DB 어디에도
        # 임시 파일 경로나 추출 이미지 파일명이 저장되지 않아야 한다.
        _assert_directory_empty(e2e_runtime.download_temp_directory)
        serialized_callback = json.dumps(callback, ensure_ascii=False, default=str)
        assert str(e2e_runtime.download_temp_directory) not in serialized_callback
        assert ".document" not in serialized_callback
        assert "source.xlsx" not in serialized_callback
    finally:
        asyncio.run(_cleanup(settings, (case.file_idx,)))
        e2e_runtime.recorder.clear_file_history(case.file_idx)
        _assert_directory_empty(e2e_runtime.download_temp_directory)


# ============================================================
# 26. 질문·청크·OCR·프롬프트·인증정보 로그 비노출
# ============================================================


def test_question_chunk_ocr_and_prompt_are_not_exposed_in_failure_logs(
    e2e_runtime: E2eRuntime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """생성 실패 로그가 질문·근거 원문·전체 프롬프트를 포함하지 않는지 검증한다."""

    question_secret = "QUESTION-SECRET-ISSUE123-7A9F"
    chunk_secret = "CHUNK-SECRET-ISSUE123-8B0E"
    ocr_secret = "OCR-SECRET-ISSUE123-9C1D"
    prompt_secret = "PROMPT-SECRET-ISSUE123-0D2C"
    file_idx = _NO_EVIDENCE_FILE_IDX + 10

    controlled_result = _controlled_search_result(
        file_idx=file_idx,
        content=f"{chunk_secret} {ocr_secret} {prompt_secret}",
        content_origin="ocr",
    )
    searcher = _ControlledChunkSearcher(
        responses={
            file_idx: ChunkSearchResponse(
                user_idx=_TEST_USER_IDX,
                result_count=1,
                results=(controlled_result,),
            )
        }
    )
    generation_client = _SensitiveFailureGenerationClient()
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    with (
        caplog.at_level(logging.INFO),
        _temporary_dependency_override(
            get_rag_answer_service,
            _StaticRagAnswerServiceProvider(service),
        ),
    ):
        response = e2e_runtime.client.post(
            "/api/v1/rag/answers",
            json={
                "user_idx": _TEST_USER_IDX,
                "reference_file_idxs": [file_idx],
                "query": question_secret,
                "top_k": 5,
                "score_threshold": None,
            },
        )

    assert response.status_code == 503
    assert generation_client.requests
    generation_request = generation_client.requests[0]
    assert question_secret in generation_request.user_prompt
    assert chunk_secret in generation_request.user_prompt
    assert ocr_secret in generation_request.user_prompt
    assert prompt_secret in generation_request.user_prompt

    # caplog는 Formatter 적용 전 LogRecord를 보므로 message와 extra를 모두 문자열화해
    # 애플리케이션 코드가 원문을 필드에 직접 넣는 회귀까지 검사한다.
    log_text = "\n".join(
        (
            record.getMessage()
            + " "
            + repr(
                {
                    key: value
                    for key, value in record.__dict__.items()
                    if key not in {"args", "msg", "exc_info", "exc_text"}
                }
            )
        )
        for record in caplog.records
    )
    rag_ingest_token = e2e_runtime.settings.rag_ingest_token
    internal_token = e2e_runtime.settings.internal_token
    authentication_secrets = (
        rag_ingest_token.get_secret_value() if rag_ingest_token is not None else "",
        internal_token.get_secret_value() if internal_token is not None else "",
        e2e_runtime.generation_settings.anthropic_api_key.get_secret_value(),
    )

    for secret in (
        question_secret,
        chunk_secret,
        ocr_secret,
        prompt_secret,
        generation_request.user_prompt,
        generation_request.system_prompt or "",
        *authentication_secrets,
    ):
        if secret:
            assert secret not in log_text
