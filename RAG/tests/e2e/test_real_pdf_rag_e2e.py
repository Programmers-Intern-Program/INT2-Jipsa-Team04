"""실제 PDF부터 Claude 답변까지 Local RAG 전체 경로를 검증한다.

AWS Backend와 S3의 HTTP 계약만 결정적 MockTransport로 고정한다. PDF 파싱,
청킹, CUDA TEI 임베딩, Local RAG DB, Qdrant, Claude는 실제 구성요소를 사용한다.
실제 Claude 비용과 로컬 인프라 접근이 발생하므로 JIPSA_RAG_RUN_E2E=1일 때만
실행한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Final, cast

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfReader
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jipsa_rag.api.ingest import get_application_server_ingest_client
from jipsa_rag.api.v1.endpoints.file_processing import get_file_downloader
from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.generation_config import get_generation_settings
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader
from jipsa_rag.main import app

# ============================================================
# 실제 E2E 실행 제어 및 테스트 전용 외부 식별자
# ============================================================

# 실제 Claude API 호출과 Local 인프라 접근은 비용과 상태 변경을 동반한다.
#
# 일반 단위·통합 테스트 실행에 이 테스트가 포함되더라도 실제 호출이 발생하지
# 않도록 별도 환경 변수로 명시적으로 활성화한 경우에만 실행한다.
_RUN_ENV: Final[str] = "JIPSA_RAG_RUN_E2E"

# 실제 사용자 데이터와 충돌하지 않도록 일반 서비스 흐름에서 사용하지 않는
# 고정 식별자 범위를 사용한다.
#
# 테스트 시작 전과 종료 후 아래 사용자·파일 범위만 선택적으로 삭제한다.
_TEST_USER_IDX: Final[int] = 94_001
_TEST_FOLDER_IDX: Final[int] = 94_010
_ORCHID_FILE_IDX: Final[int] = 940_001
_COBALT_FILE_IDX: Final[int] = 940_002

_FILE_IDXS: Final[tuple[int, int]] = (
    _ORCHID_FILE_IDX,
    _COBALT_FILE_IDX,
)

# 현재 파일 처리 엔드포인트의 초기 색인 버전과 일치한다.
_INDEX_VERSION: Final[int] = 2

# 현재 텍스트 레이어 PDF 파서의 저장 계약과 일치한다.
_PARSER_TYPE: Final[str] = "PDF_TEXT"
_PARSER_VERSION: Final[str] = "1.0.0"

# Claude 답변 본문에 포함되는 SOURCE-N 형식의 인용을 추출한다.
_SOURCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[(SOURCE-[1-9][0-9]*)\]"
)

# 문서 및 청크 SHA-256 값의 저장 형식을 검증한다.
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{64}$"
)

# Mock Backend가 허용할 내부 API 경로를 정확히 제한한다.
_BACKEND_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/internal/files/(?P<file_idx>[1-9][0-9]*)/"
    r"(?P<operation>manifest|ingest-complete)$"
)

# Mock S3 역할의 다운로드 transport가 허용할 PDF 경로를 제한한다.
_DOWNLOAD_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/files/(?P<file_idx>[1-9][0-9]*)\.pdf$"
)


# ============================================================
# E2E 전용 PDF 및 질문 정의
# ============================================================


@dataclass(frozen=True, slots=True)
class PdfCase:
    """인제스트할 실제 PDF와 회귀 검증값을 정의한다."""

    # AWS Backend DB File.File_IDX 역할의 고정 식별자다.
    file_idx: int

    # manifest와 Local RAG DB, Qdrant payload에 저장할 파일명이다.
    file_name: str

    # 실제 PDF 텍스트 레이어에 기록할 줄 단위 고정 원문이다.
    lines: tuple[str, ...]

    # PDF 생성 규칙이나 원문이 의도치 않게 바뀌는 것을 감지할 기준 해시다.
    sha256: str

    # 실제 TEI 검색과 Claude 답변에서 반드시 확인할 고유 토큰이다.
    answer_token: str

    @property
    def pdf_bytes(self) -> bytes:
        """결정적 PDF 생성기를 이용하여 실제 PDF 바이트를 반환한다."""

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
        """Backend manifest 응답 및 POST /ingest 본문을 생성한다."""

        return {
            "file_idx": self.file_idx,
            "user_idx": _TEST_USER_IDX,
            "folder_idx": _TEST_FOLDER_IDX,
            "file_name": self.file_name,
            "file_type": "pdf",
            "download_url": self.download_url,
            "url_expires_in": 900,
        }


@dataclass(frozen=True, slots=True)
class QuestionCase:
    """Claude 질문과 반드시 확인할 답변 토큰·출처 범위를 정의한다."""

    # Pytest parameter ID로 사용할 식별 가능한 테스트 이름이다.
    name: str

    # 실제 Claude에 전달될 사용자 질문이다.
    query: str

    # 질문 전송 시점에 고정되는 선택 참조문서 File_IDX 목록이다.
    reference_file_idxs: tuple[int, ...]

    # 답변 본문에 반드시 존재해야 하는 PDF별 고유 토큰이다.
    answer_tokens: tuple[str, ...]

    # 최종 응답 sources에 정확히 포함되어야 하는 File_IDX 집합이다.
    expected_source_file_idxs: frozenset[int]


_ORCHID: Final[PdfCase] = PdfCase(
    file_idx=_ORCHID_FILE_IDX,
    file_name="jipsa-e2e-orchid.pdf",
    lines=(
        "JIPSA E2E FIXTURE ORCHID",
        "ORCHID-ALPHA-21 is the exact recovery code.",
        "The recovery window is exactly 21 minutes.",
        "The owning team is Platform Reliability.",
    ),
    sha256=(
        "c00ddc76c4f9a2ff3c9ff80bb309689"
        "c893186008cfb143c951ca2db7839a441"
    ),
    answer_token="ORCHID-ALPHA-21",
)

_COBALT: Final[PdfCase] = PdfCase(
    file_idx=_COBALT_FILE_IDX,
    file_name="jipsa-e2e-cobalt.pdf",
    lines=(
        "JIPSA E2E FIXTURE COBALT",
        "COBALT-BETA-34 is the exact validation code.",
        "The validation interval is exactly 34 minutes.",
        "The owning team is Data Operations.",
    ),
    sha256=(
        "014d79eb7b1b4997c61c07de8ff6c0"
        "aa437f735763d5da2ec88eaed9e2fead43"
    ),
    answer_token="COBALT-BETA-34",
)

_PDFS: Final[tuple[PdfCase, PdfCase]] = (
    _ORCHID,
    _COBALT,
)

_QUESTIONS: Final[tuple[QuestionCase, QuestionCase]] = (
    QuestionCase(
        name="single-reference",
        query=(
            "선택한 문서의 exact recovery code를 원문 그대로 답하고 "
            "해당 문서 출처를 인용해 주세요."
        ),
        reference_file_idxs=(
            _ORCHID_FILE_IDX,
        ),
        answer_tokens=(
            _ORCHID.answer_token,
        ),
        expected_source_file_idxs=frozenset(
            {
                _ORCHID_FILE_IDX,
            }
        ),
    ),
    QuestionCase(
        name="multiple-references",
        query=(
            "두 문서를 함께 사용하여 exact recovery code와 exact validation "
            "code를 각각 원문 그대로 답하고 각 문서 출처를 모두 "
            "인용해 주세요."
        ),
        reference_file_idxs=_FILE_IDXS,
        answer_tokens=(
            _ORCHID.answer_token,
            _COBALT.answer_token,
        ),
        expected_source_file_idxs=frozenset(
            _FILE_IDXS
        ),
    ),
)


# ============================================================
# 결정적 실제 PDF 생성
# ============================================================


def _build_text_pdf(
    lines: Sequence[str],
) -> bytes:
    """pypdf가 텍스트를 추출할 수 있는 실제 단일 페이지 PDF를 만든다.

    테스트 저장소에 별도 바이너리 파일을 추가하지 않고도 PDF 원문, 질문,
    예상 출처 및 SHA-256을 동일한 소스 코드 안에서 고정하기 위한 생성기다.

    생성되는 PDF는 다음 구조를 실제로 포함한다.

    - Catalog
    - Pages
    - Page
    - Helvetica Type1 Font
    - 텍스트 Content Stream
    - 각 Object의 실제 byte offset을 기록한 xref
    - Trailer와 startxref

    따라서 단순히 ``%PDF`` magic bytes만 가진 가짜 파일이 아니라
    ``PdfReader``와 운영 ``PdfDocumentParser``가 실제로 읽고
    ``extract_text()``를 수행할 수 있는 PDF다.
    """

    normalized = tuple(lines)

    if not normalized:
        raise ValueError(
            "E2E PDF requires at least one text line."
        )

    if any(not line.strip() for line in normalized):
        raise ValueError(
            "E2E PDF text lines must not be empty."
        )

    # Built-in Helvetica에서 결정적으로 추출되도록 fixture 원문은
    # ASCII 문자만 허용한다.
    for line in normalized:
        line.encode("ascii")

    content_commands: list[bytes] = [
        b"BT",
        b"/F1 12 Tf",
        b"72 720 Td",
        b"16 TL",
    ]

    for line_index, line in enumerate(normalized):
        if line_index > 0:
            # 다음 텍스트 줄로 이동한다.
            content_commands.append(
                b"T*"
            )

        # PDF literal string에서 역슬래시와 괄호는 문법 문자이므로
        # 원문 데이터로 유지되도록 이스케이프한다.
        escaped_line = (
            line.replace(
                "\\",
                "\\\\",
            )
            .replace(
                "(",
                "\\(",
            )
            .replace(
                ")",
                "\\)",
            )
        )

        content_commands.append(
            f"({escaped_line}) Tj".encode(
                "ascii"
            )
        )

    content_commands.append(
        b"ET"
    )

    content_stream = (
        b"\n".join(content_commands)
        + b"\n"
    )

    # Object 번호는 아래 순서로 고정한다.
    #
    # 1: Catalog
    # 2: Pages
    # 3: Page
    # 4: Font
    # 5: Content Stream
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        (
            b"<< /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica >>"
        ),
        (
            b"<< /Length "
            + str(len(content_stream)).encode(
                "ascii"
            )
            + b" >>\nstream\n"
            + content_stream
            + b"endstream"
        ),
    ]

    # 두 번째 줄의 binary comment는 PDF reader가 파일을 binary PDF로
    # 정상 식별할 수 있도록 한다.
    pdf = bytearray(
        b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    )

    # xref의 0번 Object는 free entry이므로 실제 Object offset 목록에서도
    # 0번 위치를 예약한다.
    object_offsets: list[int] = [
        0,
    ]

    for object_number, object_body in enumerate(
        objects,
        start=1,
    ):
        object_offsets.append(
            len(pdf)
        )

        pdf.extend(
            f"{object_number} 0 obj\n".encode(
                "ascii"
            )
        )
        pdf.extend(
            object_body
        )
        pdf.extend(
            b"\nendobj\n"
        )

    xref_offset = len(pdf)

    pdf.extend(
        f"xref\n0 {len(objects) + 1}\n".encode(
            "ascii"
        )
    )

    # PDF 표준에서 0번 Object는 항상 free entry다.
    pdf.extend(
        b"0000000000 65535 f \n"
    )

    for object_offset in object_offsets[1:]:
        pdf.extend(
            f"{object_offset:010d} 00000 n \n".encode(
                "ascii"
            )
        )

    pdf.extend(
        (
            f"trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n"
            f"{xref_offset}\n"
            f"%%EOF\n"
        ).encode(
            "ascii"
        )
    )

    return bytes(
        pdf
    )


# ============================================================
# 동적 JSON·DB·Qdrant 값 검증
# ============================================================


def _object(
    value: object,
    label: str,
) -> dict[str, object]:
    """동적 값을 문자열 key를 가진 JSON 객체로 좁힌다."""

    if not isinstance(
        value,
        dict,
    ):
        raise AssertionError(
            f"{label} must be a JSON object."
        )

    if any(
        not isinstance(key, str)
        for key in value
    ):
        raise AssertionError(
            f"{label} must contain only string keys."
        )

    return cast(
        dict[str, object],
        value,
    )


def _objects(
    mapping: Mapping[str, object],
    key: str,
) -> list[dict[str, object]]:
    """매핑에서 JSON 객체 배열을 읽는다."""

    value = mapping.get(
        key
    )

    if not isinstance(
        value,
        list,
    ):
        raise AssertionError(
            f"{key} must be a JSON array."
        )

    return [
        _object(
            item,
            key,
        )
        for item in cast(
            list[object],
            value,
        )
    ]


def _str(
    mapping: Mapping[str, object],
    key: str,
) -> str:
    """매핑에서 비어 있지 않은 문자열을 읽는다."""

    value = mapping.get(
        key
    )

    if not isinstance(
        value,
        str,
    ):
        raise AssertionError(
            f"{key} must be a string."
        )

    if not value:
        raise AssertionError(
            f"{key} must not be empty."
        )

    return value


def _int(
    mapping: Mapping[str, object],
    key: str,
) -> int:
    """매핑에서 bool이 아닌 정수를 읽는다."""

    value = mapping.get(
        key
    )

    # bool은 int의 하위 타입이므로 먼저 제외한다.
    if isinstance(
        value,
        bool,
    ) or not isinstance(
        value,
        int,
    ):
        raise AssertionError(
            f"{key} must be an integer."
        )

    return value


def _optional_int(
    mapping: Mapping[str, object],
    key: str,
) -> int | None:
    """매핑에서 선택적 정수를 읽는다."""

    value = mapping.get(
        key
    )

    if value is None:
        return None

    if isinstance(
        value,
        bool,
    ) or not isinstance(
        value,
        int,
    ):
        raise AssertionError(
            f"{key} must be an integer or null."
        )

    return value


def _bool(
    mapping: Mapping[str, object],
    key: str,
) -> bool:
    """매핑에서 JSON boolean 값을 읽는다."""

    value = mapping.get(
        key
    )

    if not isinstance(
        value,
        bool,
    ):
        raise AssertionError(
            f"{key} must be a boolean."
        )

    return value


def _db_bool(
    mapping: Mapping[str, object],
    key: str,
) -> bool:
    """MySQL/MariaDB의 bool 또는 0·1 값을 Python bool로 변환한다."""

    value = mapping.get(
        key
    )

    if isinstance(
        value,
        bool,
    ):
        return value

    if isinstance(
        value,
        int,
    ) and value in {
        0,
        1,
    }:
        return bool(
            value
        )

    raise AssertionError(
        f"{key} must be a database boolean."
    )


# ============================================================
# Backend manifest·완료 콜백 및 PDF 다운로드 계약
# ============================================================


@dataclass(slots=True)
class BackendRecorder:
    """최신 manifest를 반환하고 실제 완료 콜백 payload를 기록한다."""

    settings: Settings
    cases: Mapping[int, PdfCase]

    # RAG 서버가 실제로 manifest를 재조회한 File_IDX 순서를 보관한다.
    manifest_requests: list[int] = field(
        default_factory=list
    )

    # File_IDX별 ingest-complete 콜백 본문을 호출 순서대로 보관한다.
    callbacks: dict[int, list[dict[str, object]]] = field(
        default_factory=dict
    )

    async def handle(
        self,
        request: httpx2.Request,
    ) -> httpx2.Response:
        """Backend 내부 API 두 경로만 처리한다."""

        path_match = _BACKEND_PATH_PATTERN.fullmatch(
            request.url.path
        )

        if path_match is None:
            return httpx2.Response(
                status_code=404
            )

        file_idx = int(
            path_match.group(
                "file_idx"
            )
        )

        operation = path_match.group(
            "operation"
        )

        case = self.cases.get(
            file_idx
        )

        if case is None:
            return httpx2.Response(
                status_code=404
            )

        internal_token = self.settings.internal_token

        if internal_token is None:
            raise AssertionError(
                "INTERNAL_TOKEN must be configured for E2E."
            )

        # RAG -> Backend 요청에는 서비스 간 내부 인증 토큰이 반드시
        # 포함되어야 한다.
        assert request.headers["X-Internal-Token"] == (
            internal_token.get_secret_value()
        )

        if operation == "manifest":
            assert request.method == "GET"

            self.manifest_requests.append(
                file_idx
            )

            return httpx2.Response(
                status_code=200,
                json=case.manifest,
            )

        assert operation == "ingest-complete"
        assert request.method == "POST"

        payload = _object(
            json.loads(
                request.content.decode(
                    "utf-8"
                )
            ),
            "ingest-complete payload",
        )

        self.callbacks.setdefault(
            file_idx,
            [],
        ).append(
            payload
        )

        # ApplicationServerIngestClient의 정상 완료 계약과 동일하게
        # response body가 없는 204를 반환한다.
        return httpx2.Response(
            status_code=204
        )

    def callback(
        self,
        file_idx: int,
    ) -> dict[str, object]:
        """파일별 성공 콜백이 정확히 한 번 전송되었는지 확인한다."""

        payloads = self.callbacks.get(
            file_idx,
            [],
        )

        assert len(payloads) == 1, (
            f"file_idx={file_idx} expected one callback, "
            f"received {len(payloads)}"
        )

        payload = payloads[0]

        assert _bool(
            payload,
            "success",
        ) is True

        return payload


@dataclass(frozen=True, slots=True)
class DownloadContract:
    """HttpFileDownloader에 실제 PDF ByteStream을 제공한다."""

    cases: Mapping[int, PdfCase]

    async def handle(
        self,
        request: httpx2.Request,
    ) -> httpx2.Response:
        """허용된 E2E PDF GET 요청만 처리한다."""

        path_match = _DOWNLOAD_PATH_PATTERN.fullmatch(
            request.url.path
        )

        if path_match is None:
            return httpx2.Response(
                status_code=404
            )

        file_idx = int(
            path_match.group(
                "file_idx"
            )
        )

        case = self.cases.get(
            file_idx
        )

        if case is None:
            return httpx2.Response(
                status_code=404
            )

        assert request.method == "GET"

        # 압축이나 전송 중 변환 없이 원본 PDF 바이트를 받도록 downloader가
        # identity encoding을 요청했는지 검증한다.
        assert request.headers["accept-encoding"] == "identity"

        pdf_bytes = case.pdf_bytes

        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(
                    len(pdf_bytes)
                ),
            },
            # 실제 downloader의 streaming 경로가 실행되도록 일반 content가
            # 아니라 ByteStream으로 응답한다.
            stream=httpx2.ByteStream(
                pdf_bytes
            ),
        )


# ============================================================
# Local RAG DB 조회 및 정리
# ============================================================


@dataclass(frozen=True, slots=True)
class DatabaseState:
    """한 파일의 활성 문서, 청크, 최신 색인 실행 상태."""

    document: Mapping[str, object]
    chunks: tuple[Mapping[str, object], ...]
    latest_run: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class E2eRuntime:
    """두 PDF 인제스트 완료 후 테스트가 공유할 상태."""

    client: TestClient
    settings: Settings
    recorder: BackendRecorder
    responses: Mapping[int, Mapping[str, object]]


def _db_engine(
    settings: Settings,
) -> AsyncEngine:
    """검증과 정리에만 사용하는 독립적인 비동기 DB 엔진을 만든다.

    FastAPI TestClient는 별도 스레드의 이벤트 루프에서 애플리케이션을
    실행한다. 동일 AsyncEngine을 현재 테스트 스레드의 ``asyncio.run``과
    공유하면 연결이 다른 이벤트 루프에 귀속될 수 있으므로 매 검증 작업마다
    짧은 생명주기의 전용 엔진을 만들고 반드시 dispose한다.
    """

    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )


async def _database_state(
    settings: Settings,
    file_idx: int,
) -> DatabaseState:
    """지정 파일의 실제 Local RAG 저장 상태를 조회한다."""

    engine = _db_engine(
        settings
    )

    try:
        async with engine.connect() as connection:
            document_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `RAG_Document_IDX`
                                    AS `rag_document_idx`,
                                `File_IDX`
                                    AS `file_idx`,
                                `Users_IDX`
                                    AS `users_idx`,
                                `Folder_IDX`
                                    AS `folder_idx`,
                                `File_Name`
                                    AS `file_name`,
                                `File_Type`
                                    AS `file_type`,
                                `File_Hash`
                                    AS `file_hash`,
                                `Index_Version`
                                    AS `index_version`,
                                `Parse_Status`
                                    AS `parse_status`,
                                `Index_Status`
                                    AS `index_status`,
                                `Chunk_Count`
                                    AS `chunk_count`,
                                `Parser_Type`
                                    AS `parser_type`,
                                `Parser_Version`
                                    AS `parser_version`,
                                `Embedding_Model`
                                    AS `embedding_model`,
                                (`Deleted_At` IS NOT NULL)
                                    AS `is_deleted`
                            FROM `RAG_Document`
                            WHERE `Users_IDX` = :users_idx
                              AND `File_IDX` = :file_idx
                              AND `Deleted_At` IS NULL
                            ORDER BY `RAG_Document_IDX`
                            """
                        ),
                        {
                            "users_idx": _TEST_USER_IDX,
                            "file_idx": file_idx,
                        },
                    )
                )
                .mappings()
                .all()
            )

            # 테스트 시작 전에 전용 범위를 정리했으므로 현재 활성 문서는
            # 정확히 하나만 존재해야 한다.
            assert len(document_rows) == 1, (
                f"file_idx={file_idx} expected one active document, "
                f"received {len(document_rows)}"
            )

            document = cast(
                Mapping[str, object],
                document_rows[0],
            )

            rag_document_idx = _int(
                document,
                "rag_document_idx",
            )

            chunk_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `Chunk_ID`
                                    AS `chunk_id`,
                                `RAG_Document_IDX`
                                    AS `rag_document_idx`,
                                `File_IDX`
                                    AS `file_idx`,
                                `Users_IDX`
                                    AS `users_idx`,
                                `Folder_IDX`
                                    AS `folder_idx`,
                                `Chunk_Index`
                                    AS `chunk_index`,
                                `Content`
                                    AS `content`,
                                `Token_Count`
                                    AS `token_count`,
                                `Page`
                                    AS `page`,
                                `Content_Hash`
                                    AS `content_hash`,
                                `Embedding_Model`
                                    AS `embedding_model`,
                                `Index_Version`
                                    AS `index_version`
                            FROM `RAG_Chunk`
                            WHERE `RAG_Document_IDX` = :rag_document_idx
                            ORDER BY `Chunk_Index`
                            """
                        ),
                        {
                            "rag_document_idx": rag_document_idx,
                        },
                    )
                )
                .mappings()
                .all()
            )

            chunks = tuple(
                cast(
                    Mapping[str, object],
                    row,
                )
                for row in chunk_rows
            )

            run_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                `RAG_Index_Run_IDX`
                                    AS `rag_index_run_idx`,
                                `RAG_Document_IDX`
                                    AS `rag_document_idx`,
                                `File_IDX`
                                    AS `file_idx`,
                                `Users_IDX`
                                    AS `users_idx`,
                                `Run_Type`
                                    AS `run_type`,
                                `Status`
                                    AS `status`,
                                `Parser_Type`
                                    AS `parser_type`,
                                `Parser_Version`
                                    AS `parser_version`,
                                `Embedding_Model`
                                    AS `embedding_model`,
                                `Chunk_Count`
                                    AS `chunk_count`,
                                (`Finished_At` IS NOT NULL)
                                    AS `is_finished`,
                                `Error_Message`
                                    AS `error_message`
                            FROM `RAG_Index_Run`
                            WHERE `RAG_Document_IDX` = :rag_document_idx
                            ORDER BY `RAG_Index_Run_IDX` DESC
                            LIMIT 1
                            """
                        ),
                        {
                            "rag_document_idx": rag_document_idx,
                        },
                    )
                )
                .mappings()
                .all()
            )

            assert len(run_rows) == 1, (
                f"rag_document_idx={rag_document_idx} "
                "must have one latest index run."
            )

            latest_run = cast(
                Mapping[str, object],
                run_rows[0],
            )

        return DatabaseState(
            document=document,
            chunks=chunks,
            latest_run=latest_run,
        )

    finally:
        await engine.dispose()


async def _cleanup_database(
    settings: Settings,
) -> None:
    """E2E 범위의 실행, 청크, 문서를 FK 역순으로 삭제한다."""

    engine = _db_engine(
        settings
    )

    parameters = {
        "users_idx": _TEST_USER_IDX,
        "file_idx_a": _ORCHID_FILE_IDX,
        "file_idx_b": _COBALT_FILE_IDX,
    }

    try:
        async with engine.begin() as connection:
            # RAG_Index_Run과 RAG_Chunk가 RAG_Document를 참조하므로
            # 자식 테이블부터 삭제한다.
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


# ============================================================
# Qdrant 조회 및 정리
# ============================================================


def _qdrant_client(
    settings: Settings,
) -> AsyncQdrantClient:
    """현재 테스트 환경의 실제 Qdrant 클라이언트를 생성한다."""

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
        timeout=max(
            1,
            ceil(
                settings.qdrant_timeout_seconds
            ),
        ),
    )


def _scope_filter(
    file_idxs: Sequence[int],
    *,
    active_only: bool,
) -> models.Filter:
    """E2E 사용자·파일 범위를 고정하고 선택적으로 활성 조건을 추가한다."""

    if active_only:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="users_idx",
                    match=models.MatchValue(
                        value=_TEST_USER_IDX
                    ),
                ),
                models.FieldCondition(
                    key="file_idx",
                    match=models.MatchAny(
                        any=list(
                            file_idxs
                        )
                    ),
                ),
                models.FieldCondition(
                    key="is_active",
                    match=models.MatchValue(
                        value=True
                    ),
                ),
            ]
        )

    # 정리 작업에서는 이전 실패나 재색인 과정에서 남은 비활성 Point도
    # 함께 제거해야 하므로 is_active 조건을 사용하지 않는다.
    return models.Filter(
        must=[
            models.FieldCondition(
                key="users_idx",
                match=models.MatchValue(
                    value=_TEST_USER_IDX
                ),
            ),
            models.FieldCondition(
                key="file_idx",
                match=models.MatchAny(
                    any=list(
                        file_idxs
                    )
                ),
            ),
        ]
    )


async def _active_points(
    settings: Settings,
    file_idx: int,
) -> tuple[models.Record, ...]:
    """지정 파일의 활성 Point와 payload·vector를 실제 Qdrant에서 읽는다."""

    client = _qdrant_client(
        settings
    )

    try:
        points, next_offset = await client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=_scope_filter(
                (
                    file_idx,
                ),
                active_only=True,
            ),
            limit=256,
            with_payload=True,
            with_vectors=True,
        )

        # E2E PDF는 매우 짧고 청크 수가 256개를 넘지 않아야 한다.
        assert next_offset is None

        return tuple(
            points
        )

    finally:
        await client.close()


async def _cleanup_qdrant(
    settings: Settings,
) -> None:
    """E2E 파일의 활성·비활성 Point를 payload filter로 삭제한다."""

    client = _qdrant_client(
        settings
    )

    try:
        collection_exists = await client.collection_exists(
            settings.qdrant_collection
        )

        # 첫 E2E 실행 전에는 Collection 자체가 없을 수 있다.
        if not collection_exists:
            return

        await client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=models.FilterSelector(
                filter=_scope_filter(
                    _FILE_IDXS,
                    active_only=False,
                )
            ),
            wait=True,
        )

    finally:
        await client.close()


async def _cleanup(
    settings: Settings,
) -> None:
    """Qdrant 복제 데이터와 Local RAG 원본 데이터를 정리한다."""

    # Qdrant를 먼저 정리하면 Local DB 행을 지운 뒤 Point ID를 다시 찾을
    # 필요가 없다.
    await _cleanup_qdrant(
        settings
    )

    await _cleanup_database(
        settings
    )


# ============================================================
# 실제 인제스트 공통 Fixture
# ============================================================


@pytest.fixture(
    scope="module",
    autouse=True,
)
def require_e2e_opt_in() -> None:
    """일반 테스트 실행에서 실제 Claude 및 Local 인프라 호출을 방지한다."""

    if os.getenv(
        _RUN_ENV
    ) != "1":
        pytest.skip(
            f"Set {_RUN_ENV}=1 to run real PDF RAG E2E tests."
        )


@pytest.fixture(
    scope="module",
)
def e2e_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[E2eRuntime]:
    """두 PDF를 실제로 인제스트하고 모듈 동안 상태를 유지한다."""

    settings = get_settings()

    # 정리 쿼리가 실행되므로 local 또는 development 프로필에서 실수로
    # 실행하는 것을 차단한다.
    if settings.app_env != "test":
        pytest.fail(
            "Real E2E cleanup is allowed only when "
            "JIPSA_RAG_APP_ENV=test.",
            pytrace=False,
        )

    # 앞선 단위 테스트가 별도 Claude 설정을 캐시했을 가능성을 제거하고
    # 현재 .env.test 또는 OS 환경 변수의 실제 값을 다시 검증한다.
    get_generation_settings.cache_clear()

    try:
        get_generation_settings()
    except ValidationError as error:
        # ValidationError 입력 원문에는 API Key가 포함될 수 있으므로
        # 전체 오류 문자열을 출력하지 않고 예외 타입만 표시한다.
        pytest.fail(
            "A valid Anthropic API key/model is required "
            f"for real E2E: {type(error).__name__}",
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

    # Backend manifest·callback와 PDF 다운로드 HTTP 경계만 MockTransport로
    # 고정한다.
    #
    # 아래 설정은 변경하지 않으므로 실제 Local 인프라를 사용한다.
    #
    # - database_url
    # - embedding_base_url
    # - embedding_model
    # - embedding_dim
    # - qdrant_url
    # - qdrant_collection
    # - Claude GenerationSettings
    http_settings = settings.model_copy(
        update={
            "app_server_base_url": (
                "https://backend.e2e.invalid"
            ),
            "app_server_max_attempts": 1,
            "app_server_retry_initial_delay_seconds": 0.0,
            "app_server_retry_max_delay_seconds": 0.0,
            "file_download_allowed_host_suffixes": (
                ".e2e.invalid"
            ),
        }
    )

    cases = {
        case.file_idx: case
        for case in _PDFS
    }

    recorder = BackendRecorder(
        settings=http_settings,
        cases=cases,
    )

    download_contract = DownloadContract(
        cases=cases
    )

    backend_client = ApplicationServerIngestClient(
        http_settings,
        transport=httpx2.MockTransport(
            recorder.handle
        ),
    )

    downloader = HttpFileDownloader(
        http_settings,
        transport=httpx2.MockTransport(
            download_contract.handle
        ),
        temp_directory=Path(
            tmp_path_factory.mktemp(
                "real-rag-e2e"
            )
        ),
    )

    def backend_dependency() -> ApplicationServerIngestClient:
        """POST /ingest에 Backend HTTP 계약 대역만 주입한다."""

        return backend_client

    def downloader_dependency() -> HttpFileDownloader:
        """실제 downloader에 PDF 응답 transport만 주입한다."""

        return downloader

    app.dependency_overrides[
        get_application_server_ingest_client
    ] = backend_dependency

    app.dependency_overrides[
        get_file_downloader
    ] = downloader_dependency

    try:
        # 이전 테스트 실패나 중단으로 남은 데이터가 현재 결과에 섞이지
        # 않도록 인제스트 전에 전용 범위를 정리한다.
        asyncio.run(
            _cleanup(
                settings
            )
        )

        with TestClient(
            app
        ) as client:
            client.headers["X-Internal-Token"] = (
                ingest_token.get_secret_value()
            )

            responses: dict[
                int,
                Mapping[str, object],
            ] = {}

            for case in _PDFS:
                # /ingest는 수신 본문을 직접 신뢰하지 않고 Mock Backend에서
                # 최신 manifest를 다시 조회한 뒤 실제 처리 파이프라인을 탄다.
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

                assert _bool(
                    body,
                    "success",
                ) is True

                assert _str(
                    body,
                    "code",
                ) == "FILE_INDEXING_COMPLETED"

                responses[case.file_idx] = _object(
                    body.get(
                        "data"
                    ),
                    "POST /ingest response data",
                )

            yield E2eRuntime(
                client=client,
                settings=settings,
                recorder=recorder,
                responses=responses,
            )

    finally:
        try:
            # 테스트 Assertion 실패 또는 Fixture 구성 도중 예외가 발생해도
            # E2E 전용 데이터를 정리한다.
            asyncio.run(
                _cleanup(
                    settings
                )
            )
        finally:
            app.dependency_overrides.pop(
                get_application_server_ingest_client,
                None,
            )

            app.dependency_overrides.pop(
                get_file_downloader,
                None,
            )

            get_generation_settings.cache_clear()


# ============================================================
# 1. E2E 전용 PDF와 질문·예상 출처 고정
# ============================================================


def test_fixed_pdf_question_and_expected_source_contract(
    e2e_runtime: E2eRuntime,
) -> None:
    """PDF 바이트, 텍스트, 질문 및 예상 출처를 회귀 기준으로 고정한다."""

    assert set(
        e2e_runtime.responses
    ) == set(
        _FILE_IDXS
    )

    for case in _PDFS:
        pdf_bytes = case.pdf_bytes

        # 생성 규칙이나 PDF 원문이 한 글자라도 변경되면 해시가 달라져
        # E2E 검색·답변 기준이 바뀌었음을 즉시 알 수 있어야 한다.
        assert hashlib.sha256(
            pdf_bytes
        ).hexdigest() == case.sha256

        assert pdf_bytes.startswith(
            b"%PDF-"
        )

        # 운영 파서와 동일한 pypdf를 사용하여 실제 텍스트 레이어가
        # 존재하는지 독립적으로 확인한다.
        reader = PdfReader(
            BytesIO(
                pdf_bytes
            ),
            strict=True,
        )

        assert len(
            reader.pages
        ) == 1

        extracted_text = reader.pages[0].extract_text()

        assert extracted_text is not None
        assert case.answer_token in extracted_text

    # 단일 문서 질문의 검색 범위와 예상 출처는 Orchid 문서 하나다.
    assert _QUESTIONS[0].reference_file_idxs == (
        _ORCHID_FILE_IDX,
    )

    assert _QUESTIONS[0].expected_source_file_idxs == frozenset(
        {
            _ORCHID_FILE_IDX,
        }
    )

    # 복수 문서 질문의 검색 범위와 예상 출처는 두 문서 모두다.
    assert _QUESTIONS[1].reference_file_idxs == _FILE_IDXS

    assert _QUESTIONS[1].expected_source_file_idxs == frozenset(
        _FILE_IDXS
    )


# ============================================================
# 2. 실제 PDF 인제스트와 완료 콜백 검증
# ============================================================


@pytest.mark.parametrize(
    "case",
    _PDFS,
    ids=(
        "orchid",
        "cobalt",
    ),
)
def test_actual_pdf_ingest_and_completion_callback(
    e2e_runtime: E2eRuntime,
    case: PdfCase,
) -> None:
    """실제 PDF 처리 응답과 Backend 완료 콜백을 대조한다."""

    response_data = e2e_runtime.responses[
        case.file_idx
    ]

    callback = e2e_runtime.recorder.callback(
        case.file_idx
    )

    # /ingest 처리 중 최신 manifest를 정확히 한 번 조회해야 한다.
    assert e2e_runtime.recorder.manifest_requests.count(
        case.file_idx
    ) == 1

    assert _int(
        response_data,
        "file_idx",
    ) == case.file_idx

    assert _int(
        response_data,
        "user_idx",
    ) == _TEST_USER_IDX

    assert _optional_int(
        response_data,
        "folder_idx",
    ) == _TEST_FOLDER_IDX

    assert _str(
        response_data,
        "file_name",
    ) == case.file_name

    assert _str(
        response_data,
        "file_type",
    ) == "pdf"

    # MockTransport가 제공한 전체 실제 PDF 바이트 수와 downloader 결과가
    # 일치해야 한다.
    assert _int(
        response_data,
        "file_size_bytes",
    ) == len(
        case.pdf_bytes
    )

    assert _int(
        response_data,
        "page_count",
    ) == 1

    assert _int(
        response_data,
        "text_unit_count",
    ) == 1

    assert _int(
        response_data,
        "chunk_count",
    ) > 0

    assert _str(
        response_data,
        "embedding_model",
    ) == e2e_runtime.settings.embedding_model

    assert _int(
        response_data,
        "embedding_dim",
    ) == e2e_runtime.settings.embedding_dim

    assert _str(
        response_data,
        "processing_status",
    ) == "INDEXED"

    callback_chunks = _objects(
        callback,
        "chunks",
    )

    assert _bool(
        callback,
        "success",
    ) is True

    assert _int(
        callback,
        "index_version",
    ) == _INDEX_VERSION

    assert _int(
        callback,
        "chunk_count",
    ) == len(
        callback_chunks
    )

    assert len(
        callback_chunks
    ) == _int(
        response_data,
        "chunk_count",
    )

    # 고정 PDF의 핵심 토큰이 실제 파싱·청킹된 뒤 Backend 동기화
    # payload까지 보존되어야 한다.
    assert case.answer_token in "\n".join(
        _str(
            chunk,
            "content",
        )
        for chunk in callback_chunks
    )

    for chunk in callback_chunks:
        assert _str(
            chunk,
            "chunk_id",
        )

        assert _int(
            chunk,
            "chunk_index",
        ) >= 0

        assert _SHA256_PATTERN.fullmatch(
            _str(
                chunk,
                "content_hash",
            )
        )

        source_metadata = _object(
            chunk.get(
                "source_metadata"
            ),
            "callback source_metadata",
        )

        # 두 fixture PDF 모두 단일 페이지이므로 모든 청크의 원본 위치는
        # PDF 1페이지여야 한다.
        assert _int(
            source_metadata,
            "page_number",
        ) == 1


# ============================================================
# 3. Local RAG DB 문서·청크·색인 상태 검증
# ============================================================


@pytest.mark.parametrize(
    "case",
    _PDFS,
    ids=(
        "orchid",
        "cobalt",
    ),
)
def test_local_rag_document_chunk_and_index_state(
    e2e_runtime: E2eRuntime,
    case: PdfCase,
) -> None:
    """RAG_Document, RAG_Chunk, RAG_Index_Run의 성공 상태를 검증한다."""

    state = asyncio.run(
        _database_state(
            e2e_runtime.settings,
            case.file_idx,
        )
    )

    document = state.document
    latest_run = state.latest_run

    # --------------------------------------------------------
    # RAG_Document
    # --------------------------------------------------------

    assert _int(
        document,
        "file_idx",
    ) == case.file_idx

    assert _int(
        document,
        "users_idx",
    ) == _TEST_USER_IDX

    assert _optional_int(
        document,
        "folder_idx",
    ) == _TEST_FOLDER_IDX

    assert _str(
        document,
        "file_name",
    ) == case.file_name

    assert _str(
        document,
        "file_type",
    ) == "PDF"

    assert _str(
        document,
        "file_hash",
    ) == case.sha256

    assert _int(
        document,
        "index_version",
    ) == _INDEX_VERSION

    assert _str(
        document,
        "parse_status",
    ) == "PARSED"

    assert _str(
        document,
        "index_status",
    ) == "INDEXED"

    assert _str(
        document,
        "parser_type",
    ) == _PARSER_TYPE

    assert _str(
        document,
        "parser_version",
    ) == _PARSER_VERSION

    assert _str(
        document,
        "embedding_model",
    ) == e2e_runtime.settings.embedding_model

    assert _db_bool(
        document,
        "is_deleted",
    ) is False

    assert _int(
        document,
        "chunk_count",
    ) == len(
        state.chunks
    )

    assert len(
        state.chunks
    ) > 0

    # --------------------------------------------------------
    # RAG_Chunk
    # --------------------------------------------------------

    # Chunk_Index는 문서 안에서 0부터 연속적으로 증가해야 한다.
    assert tuple(
        _int(
            chunk,
            "chunk_index",
        )
        for chunk in state.chunks
    ) == tuple(
        range(
            len(state.chunks)
        )
    )

    assert case.answer_token in "\n".join(
        _str(
            chunk,
            "content",
        )
        for chunk in state.chunks
    )

    # Chunk_ID는 문서 안에서 중복되지 않아야 한다.
    assert len(
        {
            _str(
                chunk,
                "chunk_id",
            )
            for chunk in state.chunks
        }
    ) == len(
        state.chunks
    )

    for chunk in state.chunks:
        assert _int(
            chunk,
            "rag_document_idx",
        ) == _int(
            document,
            "rag_document_idx",
        )

        assert _int(
            chunk,
            "file_idx",
        ) == case.file_idx

        assert _int(
            chunk,
            "users_idx",
        ) == _TEST_USER_IDX

        assert _optional_int(
            chunk,
            "folder_idx",
        ) == _TEST_FOLDER_IDX

        assert _optional_int(
            chunk,
            "page",
        ) == 1

        assert _SHA256_PATTERN.fullmatch(
            _str(
                chunk,
                "content_hash",
            )
        )

        assert _str(
            chunk,
            "embedding_model",
        ) == e2e_runtime.settings.embedding_model

        assert _int(
            chunk,
            "index_version",
        ) == _INDEX_VERSION

    # --------------------------------------------------------
    # RAG_Index_Run
    # --------------------------------------------------------

    assert _int(
        latest_run,
        "rag_index_run_idx",
    ) > 0

    assert _int(
        latest_run,
        "rag_document_idx",
    ) == _int(
        document,
        "rag_document_idx",
    )

    assert _int(
        latest_run,
        "file_idx",
    ) == case.file_idx

    assert _int(
        latest_run,
        "users_idx",
    ) == _TEST_USER_IDX

    assert _str(
        latest_run,
        "run_type",
    ) == "FULL"

    assert _str(
        latest_run,
        "status",
    ) == "SUCCESS"

    assert _str(
        latest_run,
        "parser_type",
    ) == _PARSER_TYPE

    assert _str(
        latest_run,
        "parser_version",
    ) == _PARSER_VERSION

    assert _str(
        latest_run,
        "embedding_model",
    ) == e2e_runtime.settings.embedding_model

    assert _int(
        latest_run,
        "chunk_count",
    ) == len(
        state.chunks
    )

    assert _db_bool(
        latest_run,
        "is_finished",
    ) is True

    assert latest_run.get(
        "error_message"
    ) is None


# ============================================================
# 4. Qdrant 활성 Point와 payload 검증
# ============================================================


@pytest.mark.parametrize(
    "case",
    _PDFS,
    ids=(
        "orchid",
        "cobalt",
    ),
)
def test_qdrant_active_points_and_payload(
    e2e_runtime: E2eRuntime,
    case: PdfCase,
) -> None:
    """활성 Point, vector 차원 및 Local RAG 청크와 payload를 대조한다."""

    state = asyncio.run(
        _database_state(
            e2e_runtime.settings,
            case.file_idx,
        )
    )

    points = asyncio.run(
        _active_points(
            e2e_runtime.settings,
            case.file_idx,
        )
    )

    chunks_by_id = {
        _str(
            chunk,
            "chunk_id",
        ): chunk
        for chunk in state.chunks
    }

    # 활성 Point 수는 Local RAG 원본 청크 수와 정확히 같아야 한다.
    assert len(
        points
    ) == len(
        chunks_by_id
    )

    # Qdrant Point ID는 Local RAG RAG_Chunk.Chunk_ID와 논리적으로
    # 1:1 대응해야 한다.
    assert {
        str(
            point.id
        )
        for point in points
    } == set(
        chunks_by_id
    )

    for point in points:
        assert point.payload is not None

        payload = cast(
            Mapping[str, object],
            point.payload,
        )

        point_id = str(
            point.id
        )

        local_chunk = chunks_by_id[
            point_id
        ]

        # 현재 Collection은 unnamed single dense vector를 사용한다.
        assert isinstance(
            point.vector,
            list,
        )

        assert len(
            point.vector
        ) == e2e_runtime.settings.embedding_dim

        assert all(
            not isinstance(value, bool)
            and isinstance(
                value,
                (
                    int,
                    float,
                ),
            )
            for value in point.vector
        )

        assert _str(
            payload,
            "chunk_id",
        ) == point_id

        assert _int(
            payload,
            "rag_document_idx",
        ) == _int(
            state.document,
            "rag_document_idx",
        )

        assert _int(
            payload,
            "file_idx",
        ) == case.file_idx

        assert _int(
            payload,
            "users_idx",
        ) == _TEST_USER_IDX

        assert _optional_int(
            payload,
            "folder_idx",
        ) == _TEST_FOLDER_IDX

        assert _int(
            payload,
            "chunk_index",
        ) == _int(
            local_chunk,
            "chunk_index",
        )

        assert _str(
            payload,
            "content",
        ) == _str(
            local_chunk,
            "content",
        )

        assert _str(
            payload,
            "file_name",
        ) == case.file_name

        assert _str(
            payload,
            "file_type",
        ) == "PDF"

        assert _str(
            payload,
            "file_hash",
        ) == case.sha256

        assert _str(
            payload,
            "content_hash",
        ) == _str(
            local_chunk,
            "content_hash",
        )

        assert _optional_int(
            payload,
            "token_count",
        ) == _optional_int(
            local_chunk,
            "token_count",
        )

        assert _optional_int(
            payload,
            "page",
        ) == 1

        assert _str(
            payload,
            "parser_version",
        ) == _PARSER_VERSION

        assert _str(
            payload,
            "embedding_model",
        ) == e2e_runtime.settings.embedding_model

        assert _int(
            payload,
            "embedding_dim",
        ) == e2e_runtime.settings.embedding_dim

        assert _int(
            payload,
            "index_version",
        ) == _INDEX_VERSION

        assert _bool(
            payload,
            "is_active",
        ) is True

        # 생성 시각은 비어 있지 않은 ISO-8601 문자열로 저장되어야 한다.
        assert _str(
            payload,
            "created_at",
        )


# ============================================================
# 5. 단일·복수 참조문서 기반 실제 Claude 답변 검증
# ============================================================


@pytest.mark.parametrize(
    "question",
    _QUESTIONS,
    ids=tuple(
        question.name
        for question in _QUESTIONS
    ),
)
def test_single_and_multiple_reference_real_claude_answer(
    e2e_runtime: E2eRuntime,
    question: QuestionCase,
) -> None:
    """실제 TEI·Qdrant 검색 결과로 Claude 답변과 출처를 검증한다."""

    response = e2e_runtime.client.post(
        "/api/v1/rag/answers",
        json={
            "user_idx": _TEST_USER_IDX,
            "reference_file_idxs": list(
                question.reference_file_idxs
            ),
            "query": question.query,
            # 두 PDF가 향후 청킹 정책 변경으로 여러 청크가 되더라도
            # 모든 관련 후보를 포함할 수 있도록 충분한 상한을 사용한다.
            "top_k": 10,
            # 점수 임계값은 사용하지 않는다.
            #
            # 대신 실제 Qdrant Repository가 적용하는 아래 세 조건으로
            # 검색 범위를 검증한다.
            #
            # - users_idx
            # - is_active=true
            # - file_idx IN reference_file_idxs
            "score_threshold": None,
        },
    )

    assert response.status_code == 200, (
        f"{question.name} Claude answer failed: "
        f"status={response.status_code}, "
        f"body={response.text}"
    )

    body = _object(
        response.json(),
        "RAG answer response",
    )

    assert _bool(
        body,
        "success",
    ) is True

    assert _str(
        body,
        "code",
    ) == "RAG_ANSWER_COMPLETED"

    data = _object(
        body.get(
            "data"
        ),
        "RAG answer response data",
    )

    answer = _str(
        data,
        "answer",
    )

    assert _str(
        data,
        "status",
    ) == "answered"

    # Stub 응답이 아니라 실제 Claude 응답 모델 ID가 포함되어야 한다.
    assert _str(
        data,
        "model",
    ).startswith(
        "claude-"
    )

    usage = _object(
        data.get(
            "usage"
        ),
        "RAG answer usage",
    )

    assert _int(
        usage,
        "input_tokens",
    ) > 0

    assert _int(
        usage,
        "output_tokens",
    ) > 0

    # 문서에 고정한 고유 토큰은 번역이나 표현 방식과 관계없이
    # Claude 답변에 원문 그대로 포함되어야 한다.
    for expected_token in question.answer_tokens:
        assert expected_token in answer

    # 단일 참조문서 질문에서는 선택하지 않은 PDF의 고유 토큰이 답변에
    # 유입되지 않아야 한다.
    for pdf_case in _PDFS:
        if pdf_case.file_idx not in question.reference_file_idxs:
            assert pdf_case.answer_token not in answer

    sources = _objects(
        data,
        "sources",
    )

    # 단일 질문은 Orchid 하나, 복수 질문은 Orchid와 Cobalt 모두가
    # 실제 사용 출처로 반환되어야 한다.
    assert frozenset(
        _int(
            source,
            "file_idx",
        )
        for source in sources
    ) == question.expected_source_file_idxs

    # RagAnswerService 계약에 따라 sources 순서는 답변 본문에서
    # SOURCE-N이 최초로 등장한 순서와 정확히 같아야 한다.
    cited_source_ids = tuple(
        dict.fromkeys(
            _SOURCE_PATTERN.findall(
                answer
            )
        )
    )

    response_source_ids = tuple(
        _str(
            source,
            "source_id",
        )
        for source in sources
    )

    assert cited_source_ids
    assert cited_source_ids == response_source_ids

    for source in sources:
        source_file_idx = _int(
            source,
            "file_idx",
        )

        # 최종 출처가 요청 당시 고정한 reference_file_idxs 범위를
        # 벗어나지 않아야 한다.
        assert source_file_idx in question.reference_file_idxs

        assert _str(
            source,
            "file_type",
        ) == "pdf"

        assert _optional_int(
            source,
            "page",
        ) == 1

        assert _str(
            source,
            "excerpt",
        )

        # 응답 source의 Chunk_ID가 실제 Local RAG DB에 저장된 청크 중
        # 하나인지 최종적으로 확인한다.
        source_database_state = asyncio.run(
            _database_state(
                e2e_runtime.settings,
                source_file_idx,
            )
        )

        assert _str(
            source,
            "chunk_id",
        ) in {
            _str(
                chunk,
                "chunk_id",
            )
            for chunk in source_database_state.chunks
        }