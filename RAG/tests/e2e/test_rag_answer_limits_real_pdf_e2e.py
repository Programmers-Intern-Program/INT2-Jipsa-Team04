"""실제 PDF 인제스트와 검색을 사용해 종합 근거 부족·생성 제한을 검증한다.

AWS Backend와 S3 다운로드 HTTP 경계만 결정적인 MockTransport로 고정한다.
PDF 파싱, 청킹, CUDA TEI 임베딩, Local RAG DB 저장 및 Qdrant 검색은 실제
로컬 인프라를 사용한다.

Claude 결과가 달라져 회귀 테스트가 불안정해지는 것을 방지하기 위해 이 파일은
생성 클라이언트만 결정적인 테스트 대역으로 교체한다. 기존
``test_real_pdf_rag_e2e.py``는 실제 Claude 호출과 최종 응답을 별도로 검증한다.

실제 로컬 인프라에 데이터를 생성하고 삭제하므로 ``JIPSA_RAG_RUN_E2E=1``일
때만 실행한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import (
    AsyncIterator,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Final, cast

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jipsa_rag.api.ingest import get_application_server_ingest_client
from jipsa_rag.api.v1.endpoints.file_processing import get_file_downloader
from jipsa_rag.api.v1.endpoints.rag_answer import get_generation_client
from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.generation_config import get_generation_settings
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader
from jipsa_rag.infrastructure.generation.client import GenerationClient
from jipsa_rag.infrastructure.generation.limited import (
    GenerationConcurrencyLimiter,
    GenerationLimitPolicy,
    LimitedGenerationClient,
)
from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
)
from jipsa_rag.main import app

# ============================================================
# 실제 E2E 실행 제어 및 전용 식별자
# ============================================================

_RUN_ENV: Final[str] = "JIPSA_RAG_RUN_E2E"

# 기존 실제 Claude E2E의 94xxx 범위와 충돌하지 않도록 이슈 95 전용 범위를
# 사용한다. 테스트 시작 전과 종료 후 아래 사용자·파일 범위만 정리한다.
_TEST_USER_IDX: Final[int] = 95_001
_TEST_FOLDER_IDX: Final[int] = 95_010
_FIRST_FILE_IDX: Final[int] = 950_001
_SECOND_FILE_IDX: Final[int] = 950_002
_MISSING_FILE_IDX: Final[int] = 959_999

_FILE_IDXS: Final[tuple[int, int]] = (
    _FIRST_FILE_IDX,
    _SECOND_FILE_IDX,
)

_BACKEND_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/internal/files/(?P<file_idx>[1-9][0-9]*)/"
    r"(?P<operation>manifest|ingest-complete)$"
)
_DOWNLOAD_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/files/(?P<file_idx>[1-9][0-9]*)\.pdf$"
)

_INSUFFICIENT_EVIDENCE_ANSWER: Final[str] = "제공된 문서 근거만으로는 답변할 수 없습니다."


# ============================================================
# 실제 PDF Fixture
# ============================================================


@dataclass(frozen=True, slots=True)
class PdfCase:
    """인제스트할 실제 PDF의 고정 원문과 manifest를 정의한다."""

    file_idx: int
    file_name: str
    lines: tuple[str, ...]
    answer_token: str

    @property
    def pdf_bytes(self) -> bytes:
        """pypdf 운영 파서가 읽을 수 있는 실제 PDF 바이트를 반환한다."""

        return _build_text_pdf(self.lines)

    @property
    def download_url(self) -> str:
        """Presigned GET URL 역할의 고정 HTTPS URL을 반환한다."""

        return (
            f"https://files.e2e.invalid/files/{self.file_idx}.pdf"
            f"?X-Amz-Signature=e2e-{self.file_idx}"
        )

    @property
    def manifest(self) -> dict[str, object]:
        """Backend manifest와 POST /ingest 요청 본문을 반환한다."""

        return {
            "file_idx": self.file_idx,
            "user_idx": _TEST_USER_IDX,
            "folder_idx": _TEST_FOLDER_IDX,
            "file_name": self.file_name,
            "file_type": "pdf",
            "download_url": self.download_url,
            "url_expires_in": 900,
        }


_FIRST_PDF: Final[PdfCase] = PdfCase(
    file_idx=_FIRST_FILE_IDX,
    file_name="jipsa-e2e-routing-alpha.pdf",
    lines=(
        "JIPSA E2E ROUTING FIXTURE ALPHA",
        "ALPHA-RECOVERY-21 is the exact recovery code.",
        "The recovery window is exactly 21 minutes.",
        "The owning team is Platform Reliability.",
    ),
    answer_token="ALPHA-RECOVERY-21",
)

_SECOND_PDF: Final[PdfCase] = PdfCase(
    file_idx=_SECOND_FILE_IDX,
    file_name="jipsa-e2e-routing-beta.pdf",
    lines=(
        "JIPSA E2E ROUTING FIXTURE BETA",
        "BETA-VALIDATION-34 is the exact validation code.",
        "The validation interval is exactly 34 minutes.",
        "The owning team is Data Operations.",
    ),
    answer_token="BETA-VALIDATION-34",
)

_PDFS: Final[tuple[PdfCase, PdfCase]] = (
    _FIRST_PDF,
    _SECOND_PDF,
)


def _build_text_pdf(lines: Sequence[str]) -> bytes:
    """텍스트 레이어가 있는 결정적인 단일 페이지 PDF를 생성한다.

    별도 바이너리 Fixture 없이 운영 ``PdfDocumentParser``와 같은 pypdf
    경로를 실행하기 위해 Catalog, Pages, Page, Font, Content Stream,
    xref 및 Trailer를 모두 포함한 실제 PDF 구조를 만든다.
    """

    normalized_lines = tuple(lines)

    if not normalized_lines:
        raise ValueError("E2E PDF requires at least one text line.")

    if any(not line.strip() for line in normalized_lines):
        raise ValueError("E2E PDF text lines must not be empty.")

    # Built-in Helvetica에서 결정적으로 추출되도록 Fixture 원문은 ASCII만
    # 허용한다.
    for line in normalized_lines:
        line.encode("ascii")

    content_commands: list[bytes] = [
        b"BT",
        b"/F1 12 Tf",
        b"72 720 Td",
        b"16 TL",
    ]

    for line_index, line in enumerate(normalized_lines):
        if line_index > 0:
            content_commands.append(b"T*")

        escaped_line = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_commands.append(f"({escaped_line}) Tj".encode("ascii"))

    content_commands.append(b"ET")
    content_stream = b"\n".join(content_commands) + b"\n"

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(content_stream)).encode("ascii")
            + b" >>\nstream\n"
            + content_stream
            + b"endstream"
        ),
    ]

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    object_offsets: list[int] = [0]

    for object_number, object_body in enumerate(objects, start=1):
        object_offsets.append(len(pdf))
        pdf.extend(f"{object_number} 0 obj\n".encode("ascii"))
        pdf.extend(object_body)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")

    for object_offset in object_offsets[1:]:
        pdf.extend(f"{object_offset:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        (
            f"trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n"
            f"{xref_offset}\n"
            f"%%EOF\n"
        ).encode("ascii")
    )

    return bytes(pdf)


# ============================================================
# JSON 응답 검증 Helper
# ============================================================


def _object(
    value: object,
    label: str,
) -> dict[str, object]:
    """동적 JSON 값을 문자열 Key 객체로 좁힌다."""

    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be a JSON object.")

    if any(not isinstance(key, str) for key in value):
        raise AssertionError(f"{label} must contain only string keys.")

    # 런타임 검증으로 모든 Key가 str임을 확인했으므로 내부 테스트 타입으로
    # 안전하게 좁힌다.
    return cast(dict[str, object], value)


def _objects(
    mapping: Mapping[str, object],
    key: str,
) -> list[dict[str, object]]:
    """JSON 객체에서 객체 배열을 읽는다."""

    value = mapping.get(key)

    if not isinstance(value, list):
        raise AssertionError(f"{key} must be a JSON array.")

    return [
        _object(item, key)
        for item in cast(
            list[object],
            value,
        )
    ]


def _str(
    mapping: Mapping[str, object],
    key: str,
) -> str:
    """JSON 객체에서 비어 있지 않은 문자열을 읽는다."""

    value = mapping.get(key)

    if not isinstance(value, str) or not value:
        raise AssertionError(f"{key} must be a non-empty string.")

    return value


def _int(
    mapping: Mapping[str, object],
    key: str,
) -> int:
    """JSON 객체에서 bool이 아닌 정수를 읽는다."""

    value = mapping.get(key)

    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"{key} must be an integer.")

    return value


def _bool(
    mapping: Mapping[str, object],
    key: str,
) -> bool:
    """JSON 객체에서 boolean 값을 읽는다."""

    value = mapping.get(key)

    if not isinstance(value, bool):
        raise AssertionError(f"{key} must be a boolean.")

    return value


# ============================================================
# Backend manifest·완료 콜백 및 PDF 다운로드 MockTransport
# ============================================================


@dataclass(slots=True)
class BackendRecorder:
    """최신 manifest를 반환하고 ingest-complete 요청을 기록한다."""

    settings: Settings
    cases: Mapping[int, PdfCase]
    callbacks: dict[int, list[dict[str, object]]] = field(
        default_factory=dict,
    )

    async def handle(
        self,
        request: httpx2.Request,
    ) -> httpx2.Response:
        """허용된 Backend 내부 API 경로만 처리한다."""

        path_match = _BACKEND_PATH_PATTERN.fullmatch(request.url.path)

        if path_match is None:
            return httpx2.Response(status_code=404)

        file_idx = int(path_match.group("file_idx"))
        operation = path_match.group("operation")
        case = self.cases.get(file_idx)

        if case is None:
            return httpx2.Response(status_code=404)

        internal_token = self.settings.internal_token

        if internal_token is None:
            raise AssertionError("INTERNAL_TOKEN must be configured for E2E.")

        assert request.headers["X-Internal-Token"] == internal_token.get_secret_value()

        if operation == "manifest":
            assert request.method == "GET"

            return httpx2.Response(
                status_code=200,
                json=case.manifest,
            )

        assert operation == "ingest-complete"
        assert request.method == "POST"

        payload = _object(
            json.loads(request.content.decode("utf-8")),
            "ingest-complete payload",
        )
        self.callbacks.setdefault(file_idx, []).append(payload)

        return httpx2.Response(status_code=204)


@dataclass(frozen=True, slots=True)
class DownloadContract:
    """HttpFileDownloader에 실제 PDF ByteStream을 제공한다."""

    cases: Mapping[int, PdfCase]

    async def handle(
        self,
        request: httpx2.Request,
    ) -> httpx2.Response:
        """E2E Fixture PDF에 대한 GET 요청만 처리한다."""

        path_match = _DOWNLOAD_PATH_PATTERN.fullmatch(request.url.path)

        if path_match is None:
            return httpx2.Response(status_code=404)

        file_idx = int(path_match.group("file_idx"))
        case = self.cases.get(file_idx)

        if case is None:
            return httpx2.Response(status_code=404)

        assert request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"

        pdf_bytes = case.pdf_bytes

        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(len(pdf_bytes)),
            },
            stream=httpx2.ByteStream(pdf_bytes),
        )


# ============================================================
# Local RAG DB·Qdrant 정리
# ============================================================


def _db_engine(settings: Settings) -> AsyncEngine:
    """테스트 정리에 사용할 독립적인 비동기 DB 엔진을 생성한다."""

    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )


async def _cleanup_database(settings: Settings) -> None:
    """이 파일의 E2E 전용 DB 데이터를 FK 역순으로 삭제한다."""

    engine = _db_engine(settings)
    parameters = {
        "users_idx": _TEST_USER_IDX,
        "file_idx_a": _FIRST_FILE_IDX,
        "file_idx_b": _SECOND_FILE_IDX,
    }

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    DELETE FROM `RAG_Index_Run`
                    WHERE `Users_IDX` = :users_idx
                      AND `File_IDX`
                          IN (:file_idx_a, :file_idx_b)
                    """
                ),
                parameters,
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM `RAG_Chunk`
                    WHERE `Users_IDX` = :users_idx
                      AND `File_IDX`
                          IN (:file_idx_a, :file_idx_b)
                    """
                ),
                parameters,
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM `RAG_Document`
                    WHERE `Users_IDX` = :users_idx
                      AND `File_IDX`
                          IN (:file_idx_a, :file_idx_b)
                    """
                ),
                parameters,
            )
    finally:
        await engine.dispose()


def _qdrant_client(settings: Settings) -> AsyncQdrantClient:
    """현재 실제 E2E 설정의 Qdrant 클라이언트를 생성한다."""

    api_key = (
        settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key is not None else None
    )

    return AsyncQdrantClient(
        url=settings.qdrant_url,
        grpc_port=settings.qdrant_grpc_port,
        prefer_grpc=settings.qdrant_prefer_grpc,
        api_key=api_key,
        timeout=max(
            1,
            ceil(settings.qdrant_timeout_seconds),
        ),
    )


def _scope_filter() -> models.Filter:
    """이 파일의 E2E 사용자와 파일 범위만 선택하는 Filter를 반환한다."""

    return models.Filter(
        must=[
            models.FieldCondition(
                key="users_idx",
                match=models.MatchValue(
                    value=_TEST_USER_IDX,
                ),
            ),
            models.FieldCondition(
                key="file_idx",
                match=models.MatchAny(
                    any=list(_FILE_IDXS),
                ),
            ),
        ]
    )


async def _cleanup_qdrant(settings: Settings) -> None:
    """이 파일의 E2E 활성·비활성 Point를 모두 삭제한다."""

    client = _qdrant_client(settings)

    try:
        collection_exists = await client.collection_exists(
            settings.qdrant_collection,
        )

        if not collection_exists:
            return

        await client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=models.FilterSelector(
                filter=_scope_filter(),
            ),
            wait=True,
        )
    finally:
        await client.close()


async def _cleanup(settings: Settings) -> None:
    """Qdrant 복제 데이터와 Local RAG 원본 데이터를 정리한다."""

    await _cleanup_qdrant(settings)
    await _cleanup_database(settings)


# ============================================================
# 실제 PDF 인제스트 Fixture
# ============================================================


@dataclass(frozen=True, slots=True)
class E2eRuntime:
    """두 실제 PDF의 인제스트가 끝난 뒤 테스트가 공유할 상태."""

    client: TestClient
    settings: Settings
    recorder: BackendRecorder


@pytest.fixture(
    scope="module",
    autouse=True,
)
def require_e2e_opt_in() -> None:
    """일반 Pytest에서 실제 Local RAG 인프라 접근을 차단한다."""

    if os.getenv(_RUN_ENV) != "1":
        pytest.skip(f"Set {_RUN_ENV}=1 to run real PDF RAG E2E tests.")


@pytest.fixture(scope="module")
def e2e_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[E2eRuntime]:
    """실제 PDF 두 개를 인제스트하고 테스트 종료 후 정리한다."""

    settings = get_settings()

    # E2E Fixture는 테스트 전후로 DB 행과 Qdrant Point를 삭제한다.
    # 따라서 local 또는 development 프로필에서 실수로 실행하지 못하도록
    # 반드시 test 프로필인지 확인한다.
    if settings.app_env != "test":
        pytest.fail(
            "Real E2E cleanup is allowed only when JIPSA_RAG_APP_ENV=test.",
            pytrace=False,
        )

    # 앞선 테스트 또는 애플리케이션 import 과정에서 캐시된 생성 설정을
    # 제거하고 현재 프로세스 환경의 실제 설정을 다시 읽는다.
    get_generation_settings.cache_clear()

    try:
        get_generation_settings()
    except ValidationError as error:
        # ValidationError 입력에는 API Key 원문이 포함될 가능성이 있으므로
        # 전체 오류 문자열을 테스트 출력에 기록하지 않는다.
        pytest.fail(
            f"A valid generation configuration is required for real E2E: {type(error).__name__}",
            pytrace=False,
        )

    ingest_token = settings.rag_ingest_token

    if ingest_token is None:
        pytest.fail(
            "RAG_INGEST_TOKEN is required for real E2E.",
            pytrace=False,
        )

    if settings.internal_token is None:
        pytest.fail(
            "INTERNAL_TOKEN is required for real E2E.",
            pytrace=False,
        )

    # Backend manifest·callback 및 PDF 다운로드 경계만 Mock으로 고정한다.
    #
    # 다음 설정은 model_copy에서 변경하지 않으므로 실제 Local 인프라를
    # 사용한다.
    #
    # - database_url
    # - embedding_base_url
    # - embedding_model
    # - embedding_dim
    # - qdrant_url
    # - qdrant_collection
    http_settings = settings.model_copy(
        update={
            "app_server_base_url": "https://backend.e2e.invalid",
            "app_server_max_attempts": 1,
            "app_server_retry_initial_delay_seconds": 0.0,
            "app_server_retry_max_delay_seconds": 0.0,
            "file_download_allowed_host_suffixes": ".e2e.invalid",
        }
    )

    cases = {case.file_idx: case for case in _PDFS}
    recorder = BackendRecorder(
        settings=http_settings,
        cases=cases,
    )
    download_contract = DownloadContract(cases=cases)

    backend_client = ApplicationServerIngestClient(
        http_settings,
        transport=httpx2.MockTransport(recorder.handle),
    )
    downloader = HttpFileDownloader(
        http_settings,
        transport=httpx2.MockTransport(download_contract.handle),
        temp_directory=Path(
            tmp_path_factory.mktemp(
                "rag-answer-limits-real-pdf-e2e",
            )
        ),
    )

    def backend_dependency() -> ApplicationServerIngestClient:
        """POST /ingest에 Backend HTTP 계약 대역만 주입한다."""

        return backend_client

    def downloader_dependency() -> HttpFileDownloader:
        """실제 Downloader에 PDF ByteStream 대역만 주입한다."""

        return downloader

    app.dependency_overrides[get_application_server_ingest_client] = backend_dependency
    app.dependency_overrides[get_file_downloader] = downloader_dependency

    try:
        # 이전 실행이 중단되어 남은 전용 데이터를 제거한다.
        asyncio.run(_cleanup(settings))

        with TestClient(app) as client:
            client.headers["X-Internal-Token"] = ingest_token.get_secret_value()

            for case in _PDFS:
                response = client.post(
                    "/ingest",
                    json=case.manifest,
                )

                assert response.status_code == 200, (
                    f"file_idx={case.file_idx} ingest failed: "
                    f"status={response.status_code}, "
                    f"body={response.text}"
                )

                body = _object(
                    response.json(),
                    "POST /ingest response",
                )

                assert _bool(body, "success") is True
                assert _str(body, "code") == "FILE_INDEXING_COMPLETED"

            yield E2eRuntime(
                client=client,
                settings=settings,
                recorder=recorder,
            )
    finally:
        try:
            # Assertion 실패나 Fixture 구성 실패가 발생해도 E2E 전용 범위는
            # 반드시 정리한다.
            asyncio.run(_cleanup(settings))
        finally:
            app.dependency_overrides.pop(
                get_application_server_ingest_client,
                None,
            )
            app.dependency_overrides.pop(
                get_file_downloader,
                None,
            )
            app.dependency_overrides.pop(
                get_generation_client,
                None,
            )
            get_generation_settings.cache_clear()


# ============================================================
# 결정적 GenerationClient
# ============================================================


class ScriptedGenerationClient:
    """호출 순서에 따라 준비된 구조화 답변을 반환한다."""

    def __init__(
        self,
        results: tuple[GenerationResult, ...],
    ) -> None:
        """결과 시나리오와 호출 기록을 초기화한다."""

        self._results = results
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """요청을 기록하고 같은 순번의 결과를 반환한다."""

        call_index = len(self.calls)
        self.calls.append(request)

        if call_index >= len(self._results):
            raise AssertionError("Generation client received an unexpected extra call.")

        return self._results[call_index]


def _answered_result(
    *,
    answer: str,
    cited_source_ids: tuple[str, ...],
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> GenerationResult:
    """운영 Claude 구조화 출력과 같은 정상 답변 결과를 생성한다."""

    structured_output: dict[str, object] = {
        "status": "answered",
        "answer": answer,
        "cited_source_ids": list(cited_source_ids),
    }

    return GenerationResult(
        text=json.dumps(
            structured_output,
            ensure_ascii=False,
        ),
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        stop_reason="end_turn",
        structured_output=structured_output,
    )


def _insufficient_result() -> GenerationResult:
    """운영 Claude 구조화 출력과 같은 근거 부족 결과를 생성한다."""

    structured_output: dict[str, object] = {
        "status": "insufficient_evidence",
        "answer": _INSUFFICIENT_EVIDENCE_ANSWER,
        "cited_source_ids": [],
    }

    return GenerationResult(
        text=json.dumps(
            structured_output,
            ensure_ascii=False,
        ),
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=80,
            output_tokens=10,
        ),
        stop_reason="end_turn",
        structured_output=structured_output,
    )


def _scripted_generation_dependency(
    client: GenerationClient,
) -> Callable[[], AsyncIterator[GenerationClient]]:
    """FastAPI가 사용할 요청 범위 생성 클라이언트 의존성을 만든다."""

    async def dependency() -> AsyncIterator[GenerationClient]:
        """전달받은 생성 클라이언트를 현재 HTTP 요청에 주입한다."""

        yield client

    return dependency


def _post_synthesis_question(
    *,
    runtime: E2eRuntime,
    reference_file_idxs: Sequence[int],
    query: str,
) -> httpx2.Response:
    """실제 TEI·Qdrant 검색을 사용하는 synthesis 요청을 전송한다.

    FastAPI TestClient의 ``post`` 반환값은 현재 프로젝트의 타입 환경에서
    이미 ``httpx2.Response``으로 추론된다.

    불필요한 ``cast``를 사용하면 Mypy의 ``redundant-cast`` 검사가
    실패하므로 응답 객체를 그대로 반환한다.
    """

    return runtime.client.post(
        "/api/v1/rag/answers",
        json={
            "user_idx": _TEST_USER_IDX,
            "reference_file_idxs": list(reference_file_idxs),
            "query": query,
            "top_k": 10,
            "score_threshold": None,
        },
    )


# ============================================================
# 실제 PDF 기반 근거 부족·생성 제한 E2E
# ============================================================


def test_partial_evidence_uses_only_the_indexed_pdf(
    e2e_runtime: E2eRuntime,
) -> None:
    """한 PDF만 검색되면 해당 부분 근거만 최종 응답에 사용한다."""

    generation_client = ScriptedGenerationClient(
        (
            # 실제 첫 번째 PDF 검색 결과의 부분 답변이다.
            _answered_result(
                answer=(f"정확한 복구 코드는 {_FIRST_PDF.answer_token}입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
            # 검색 결과가 없는 두 번째 File_IDX에는 부분 Claude 호출이
            # 발생하지 않는다. 두 번째 호출은 바로 최종 종합이다.
            _answered_result(
                answer=(f"확인 가능한 복구 코드는 {_FIRST_PDF.answer_token}입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
        )
    )
    app.dependency_overrides[get_generation_client] = _scripted_generation_dependency(
        generation_client,
    )

    try:
        response = _post_synthesis_question(
            runtime=e2e_runtime,
            reference_file_idxs=(
                _FIRST_FILE_IDX,
                _MISSING_FILE_IDX,
            ),
            query=(
                "두 PDF를 비교하여 실제 문서에서 확인 가능한 exact recovery "
                "code만 원문 그대로 종합해 주세요."
            ),
        )
    finally:
        app.dependency_overrides.pop(
            get_generation_client,
            None,
        )

    assert response.status_code == 200, response.text

    body = _object(
        response.json(),
        "partial evidence response",
    )
    data = _object(
        body.get("data"),
        "partial evidence response data",
    )
    sources = _objects(
        data,
        "sources",
    )

    assert _bool(body, "success") is True
    assert _str(data, "status") == "answered"
    assert _FIRST_PDF.answer_token in _str(data, "answer")
    assert len(generation_client.calls) == 2

    assert {_int(source, "file_idx") for source in sources} == {
        _FIRST_FILE_IDX,
    }
    assert all(_int(source, "file_idx") != _MISSING_FILE_IDX for source in sources)


def test_all_partial_answers_insufficient_skip_final_generation(
    e2e_runtime: E2eRuntime,
) -> None:
    """두 실제 PDF 부분 답변이 모두 근거 부족이면 최종 호출을 생략한다."""

    generation_client = ScriptedGenerationClient(
        (
            _insufficient_result(),
            _insufficient_result(),
        )
    )
    app.dependency_overrides[get_generation_client] = _scripted_generation_dependency(
        generation_client,
    )

    try:
        response = _post_synthesis_question(
            runtime=e2e_runtime,
            reference_file_idxs=_FILE_IDXS,
            query=(
                "두 PDF를 비교하여 문서 근거만으로 확인할 수 없는 "
                "release signing secret을 종합해 주세요."
            ),
        )
    finally:
        app.dependency_overrides.pop(
            get_generation_client,
            None,
        )

    assert response.status_code == 200, response.text

    body = _object(
        response.json(),
        "all insufficient response",
    )
    data = _object(
        body.get("data"),
        "all insufficient response data",
    )

    # 실제 두 PDF 검색과 부분 생성 두 번까지만 실행한다. 세 번째 최종
    # 종합 호출이 발생하면 ScriptedGenerationClient가 테스트를 실패시킨다.
    assert len(generation_client.calls) == 2
    assert _str(data, "status") == "insufficient_evidence"
    assert _str(data, "answer") == _INSUFFICIENT_EVIDENCE_ANSWER
    assert _objects(data, "sources") == []
    assert data.get("model") is None
    assert data.get("usage") is None
    assert data.get("stop_reason") is None


def test_synthesis_call_budget_blocks_final_generation(
    e2e_runtime: E2eRuntime,
) -> None:
    """두 PDF 부분 호출 후 호출 예산이 소진되면 최종 종합을 429로 차단한다."""

    query_secret = "E2E-QUESTION-SECRET-DO-NOT-EXPOSE"

    delegate = ScriptedGenerationClient(
        (
            _answered_result(
                answer=(f"첫 PDF 코드는 {_FIRST_PDF.answer_token}입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
            _answered_result(
                answer=(f"두 번째 PDF 코드는 {_SECOND_PDF.answer_token}입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
            # max_calls=2이므로 이 결과는 절대 소비되면 안 된다.
            _answered_result(
                answer=(
                    f"{_FIRST_PDF.answer_token}과 "
                    f"{_SECOND_PDF.answer_token}입니다. "
                    "[SOURCE-1][SOURCE-2]"
                ),
                cited_source_ids=(
                    "SOURCE-1",
                    "SOURCE-2",
                ),
            ),
        )
    )

    limited_client = LimitedGenerationClient(
        delegate=delegate,
        policy=GenerationLimitPolicy(
            max_calls=2,
            max_input_tokens=1_000_000,
            max_output_tokens=100_000,
            max_output_tokens_per_call=4_096,
        ),
        concurrency_limiter=GenerationConcurrencyLimiter(
            max_concurrency=1,
        ),
    )
    app.dependency_overrides[get_generation_client] = _scripted_generation_dependency(
        limited_client,
    )

    try:
        response = _post_synthesis_question(
            runtime=e2e_runtime,
            reference_file_idxs=_FILE_IDXS,
            query=(
                "두 PDF를 비교하여 exact recovery code와 exact validation "
                f"code를 종합해 주세요. {query_secret}"
            ),
        )
    finally:
        app.dependency_overrides.pop(
            get_generation_client,
            None,
        )

    assert response.status_code == 429

    body = _object(
        response.json(),
        "generation budget response",
    )

    assert _bool(body, "success") is False
    assert _str(body, "code") == "GENERATION_BUDGET_EXCEEDED"
    assert len(delegate.calls) == 2
    assert query_secret not in response.text
