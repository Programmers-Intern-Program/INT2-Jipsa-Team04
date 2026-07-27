"""고정 문서 Fixture로 다중 형식 다운로드·검증·파싱·이미지 위치 계약을 검증한다.

이 테스트는 Issue #123의 첫 여섯 작업을 하나의 재현 가능한 기준선으로 묶는다.
테스트 실행 시 문서를 동적으로 생성하지 않고 저장소에 고정한 실제 바이너리를 사용한다.
따라서 문서 생성 라이브러리 버전이나 실행 OS가 달라져도 SHA-256, 원문 토큰, 문단·표·
슬라이드·시트·줄 위치와 이미지 원본 위치의 예상값이 흔들리지 않는다.

검증 범위는 다음과 같다.

- PDF, DOCX, PPTX, TXT, XLSX 고정 원문 문서와 위치 메타데이터
- PDF, DOCX, PPTX, XLSX에 실제로 삽입된 이미지와 원본 위치
- 스캔 PDF 및 혼합 PDF의 이미지 전용 페이지 탐지
- 빈 파일, 손상 PDF, 암호화 PDF와 확장자·실제 내용 불일치
- 운영 ``HttpFileDownloader``의 HTTPS 다운로드, MIME, Magic Byte, OOXML 루트,
  SHA-256 및 임시 파일 정리 계약

Local RAG DB, Qdrant, CUDA TEI와 Claude까지 사용하는 비용성 E2E는 기존
``test_real_pdf_rag_e2e.py``와 ``test_real_non_pdf_multiformat_rag_e2e.py``가 담당한다.
이 모듈은 그보다 앞선 문서 입력 경계를 항상 실행 가능한 결정적 회귀 테스트로 고정한다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, cast

import httpx2
import pytest
from pydantic import ValidationError

from jipsa_rag.core.config import Settings
from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.document.exceptions import (
    EncryptedDocumentError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.images.docx import DocxImageExtractor
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    DocumentImageKind,
)
from jipsa_rag.infrastructure.document.images.pdf import PdfImageExtractor
from jipsa_rag.infrastructure.document.images.pptx import PptxImageExtractor
from jipsa_rag.infrastructure.document.images.xlsx import XlsxImageExtractor
from jipsa_rag.infrastructure.document.models import ParsedDocument
from jipsa_rag.infrastructure.document.parsers.docx import DocxDocumentParser
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser
from jipsa_rag.infrastructure.document.parsers.pptx import PptxDocumentParser
from jipsa_rag.infrastructure.document.parsers.txt import TxtDocumentParser
from jipsa_rag.infrastructure.document.parsers.xlsx import XlsxDocumentParser
from jipsa_rag.infrastructure.document.rendering import OfficeVisualRenderResult
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader
from jipsa_rag.schemas.file_processing import FileProcessingRequest, SupportedFileType

_FIXTURE_ROOT: Final[Path] = Path(__file__).resolve().parents[1] / "fixtures/e2e_documents"
_MANIFEST_PATH: Final[Path] = _FIXTURE_ROOT / "manifest.json"
_DOWNLOAD_HOST: Final[str] = "files.e2e.invalid"
_DOWNLOAD_URL_PREFIX: Final[str] = f"https://{_DOWNLOAD_HOST}/fixed-fixtures"
_TEST_USERS_IDX: Final[int] = 95_123
_TEST_FILE_IDX_BASE: Final[int] = 951_230

_TEXT_CASE_IDS: Final[tuple[str, ...]] = (
    "pdf-text-table",
    "docx-structure",
    "pptx-structure",
    "xlsx-structure",
    "txt-lines-utf8",
)
_IMAGE_CASE_IDS: Final[tuple[str, ...]] = (
    "pdf-with-image",
    "docx-with-image",
    "pptx-with-image",
    "xlsx-with-image",
)
_SCANNED_CASE_IDS: Final[tuple[str, ...]] = (
    "scanned-document",
    "hybrid-image-only-page",
)
_INVALID_CASE_IDS: Final[tuple[str, ...]] = (
    "corrupted-pdf",
    "empty-pdf",
    "encrypted-pdf",
    "docx-payload-named-pdf",
)
_ALL_CASE_IDS: Final[tuple[str, ...]] = (
    *_TEXT_CASE_IDS,
    *_IMAGE_CASE_IDS,
    *_SCANNED_CASE_IDS,
    *_INVALID_CASE_IDS,
)


def _object(value: object, label: str) -> dict[str, object]:
    """동적 JSON 값을 문자열 key를 갖는 객체로 안전하게 좁힌다."""

    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be a JSON object.")
    if any(not isinstance(key, str) for key in value):
        raise AssertionError(f"{label} must contain only string keys.")
    return cast(dict[str, object], value)


def _objects(mapping: Mapping[str, object], key: str) -> list[dict[str, object]]:
    """JSON 객체 배열을 읽고 모든 원소의 구조를 확인한다."""

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


def _mapping(mapping: Mapping[str, object], key: str) -> dict[str, object]:
    """매핑의 하위 JSON 객체를 읽는다."""

    return _object(mapping.get(key), key)


def _load_manifest() -> dict[str, object]:
    """UTF-8 manifest를 읽고 최상위 JSON 객체 계약을 확인한다."""

    return _object(json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")), "manifest")


_MANIFEST: Final[dict[str, object]] = _load_manifest()
_DOCUMENTS: Final[tuple[dict[str, object], ...]] = tuple(_objects(_MANIFEST, "documents"))
_DOCUMENTS_BY_ID: Final[dict[str, dict[str, object]]] = {
    _str(document, "id"): document for document in _DOCUMENTS
}


def _case(case_id: str) -> dict[str, object]:
    """고정 ID로 manifest 문서 계약을 조회한다."""

    try:
        return _DOCUMENTS_BY_ID[case_id]
    except KeyError as error:
        raise AssertionError(f"Unknown fixed fixture id: {case_id}") from error


def _fixture_path(case: Mapping[str, object]) -> Path:
    """manifest 상대 경로를 Fixture 루트 아래의 실제 파일 경로로 변환한다."""

    relative_path = Path(_str(case, "path"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise AssertionError("Fixture paths must stay below the fixed fixture root.")
    return _FIXTURE_ROOT / relative_path


def _download_url(case: Mapping[str, object]) -> str:
    """실제 HTTPS 검증을 통과하는 테스트 전용 Presigned URL 형태를 만든다."""

    case_id = _str(case, "id")
    file_name = _fixture_path(case).name
    return f"{_DOWNLOAD_URL_PREFIX}/{case_id}/{file_name}?X-Amz-Signature=fixed-{case_id}"


def _normalized_content_type(case: Mapping[str, object]) -> str:
    """HTTP 헤더 파라미터를 제거한 MIME Type 기대값을 반환한다."""

    return _str(case, "content_type").partition(";")[0].strip().lower()


def _sha256(path: Path) -> str:
    """고정 문서 바이트의 소문자 SHA-256을 계산한다."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _equivalent(actual: object, expected: object) -> bool:
    """JSON 배열과 런타임 tuple 차이를 허용하면서 값 의미를 비교한다."""

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
        expected_mapping = cast(dict[object, object], expected)
        actual_mapping = cast(Mapping[object, object], actual)
        return all(
            key in actual_mapping and _equivalent(actual_mapping[key], value)
            for key, value in expected_mapping.items()
        )

    return actual == expected


def _assert_subset(
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    label: str,
) -> None:
    """예상 메타데이터 key가 실제 결과에 같은 의미의 값으로 존재하는지 확인한다."""

    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        assert _equivalent(actual_value, expected_value), (
            f"{label}[{key!r}] mismatch: "
            f"expected={expected_value!r}, actual={actual_value!r}"
        )


def _settings() -> Settings:
    """실제 다운로더가 사용하는 파일 제한과 허용 호스트 설정을 고정한다."""

    return Settings(
        app_env="test",
        database_host="127.0.0.1",
        database_name="Jipsa_Local_RAG",
        database_user="test_user",
        database_password="test_password",
        file_download_allowed_host_suffixes=".e2e.invalid",
        file_download_connect_timeout_seconds=5.0,
        file_download_read_timeout_seconds=30.0,
        file_download_max_size_bytes=32 * 1024 * 1024,
        _env_file=None,
    )


def _processing_settings() -> DocumentProcessingSettings:
    """GPU OCR 호출 없이 실제 이미지 추출·스캔 페이지 탐지만 실행하도록 설정한다."""

    return DocumentProcessingSettings(
        image_extraction_enabled=True,
        image_decorative_filter_enabled=False,
        office_rendering_enabled=False,
        ocr_enabled=False,
        ocr_gpu=False,
        ocr_gpu_required=False,
        _env_file=None,
    )


class _UnavailableOfficeRenderer:
    """고정 문서에 차트가 없을 때 COM을 호출하지 않는 Office 렌더러 대역이다."""

    async def render_pptx_visuals(self, source_path: Path) -> OfficeVisualRenderResult:
        """PPTX 그림 추출에는 필요하지 않은 Office 렌더 결과를 명시한다."""

        del source_path
        return OfficeVisualRenderResult.unavailable("fixed_fixture_contract")

    async def render_xlsx_charts(self, source_path: Path) -> OfficeVisualRenderResult:
        """XLSX 삽입 그림 추출에는 필요하지 않은 Office 렌더 결과를 명시한다."""

        del source_path
        return OfficeVisualRenderResult.unavailable("fixed_fixture_contract")


def _parser(case: Mapping[str, object]) -> (
    PdfDocumentParser
    | DocxDocumentParser
    | PptxDocumentParser
    | TxtDocumentParser
    | XlsxDocumentParser
):
    """manifest file_type에 대응하는 운영 텍스트 파서를 반환한다."""

    file_type = _str(case, "file_type")
    if file_type == "pdf":
        return PdfDocumentParser()
    if file_type == "docx":
        return DocxDocumentParser()
    if file_type == "pptx":
        return PptxDocumentParser()
    if file_type == "txt":
        return TxtDocumentParser()
    if file_type == "xlsx":
        return XlsxDocumentParser()
    raise AssertionError(f"Unsupported fixed parser type: {file_type}")


def _image_extractor(case: Mapping[str, object]) -> (
    PdfImageExtractor | DocxImageExtractor | PptxImageExtractor | XlsxImageExtractor
):
    """manifest file_type에 대응하는 실제 이미지 추출기를 반환한다."""

    processing_settings = _processing_settings()
    renderer = _UnavailableOfficeRenderer()
    file_type = _str(case, "file_type")
    if file_type == "pdf":
        return PdfImageExtractor(processing_settings)
    if file_type == "docx":
        return DocxImageExtractor(processing_settings)
    if file_type == "pptx":
        return PptxImageExtractor(processing_settings, renderer)
    if file_type == "xlsx":
        return XlsxImageExtractor(processing_settings, renderer)
    raise AssertionError(f"Unsupported fixed image type: {file_type}")


def _transport(case: Mapping[str, object]) -> httpx2.MockTransport:
    """한 고정 문서를 실제 ByteStream으로 반환하고 요청 계약을 검사한다."""

    fixture_path = _fixture_path(case)
    expected_url = _download_url(case)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == expected_url
        assert request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"
        payload = fixture_path.read_bytes()
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": _str(case, "content_type"),
                "Content-Length": str(len(payload)),
            },
            stream=httpx2.ByteStream(payload),
        )

    return httpx2.MockTransport(handler)


async def _download_and_parse(
    case: Mapping[str, object],
    *,
    temp_directory: Path,
    file_idx: int,
) -> ParsedDocument:
    """운영 다운로더 검증 후 같은 임시 파일을 운영 파서에 전달한다."""

    downloader = HttpFileDownloader(
        _settings(),
        transport=_transport(case),
        temp_directory=temp_directory,
    )
    downloaded_path: Path | None = None

    async with downloader.download_and_validate(
        file_url=_download_url(case),
        users_idx=_TEST_USERS_IDX,
        file_idx=file_idx,
        expected_sha256=_str(case, "sha256"),
    ) as downloaded_file:
        downloaded_path = downloaded_file.path
        assert downloaded_file.size_bytes == _int(case, "size_bytes")
        assert downloaded_file.sha256 == _str(case, "sha256")
        assert downloaded_file.content_type == _normalized_content_type(case)
        assert downloaded_file.path.read_bytes() == _fixture_path(case).read_bytes()
        parsed = await _parser(case).parse(downloaded_file.path)

    assert downloaded_path is not None
    assert not downloaded_path.exists()
    assert list(temp_directory.iterdir()) == []
    return parsed


async def _download_and_extract_images(
    case: Mapping[str, object],
    *,
    temp_directory: Path,
    file_idx: int,
) -> DocumentImageExtraction:
    """운영 다운로더가 검증한 임시 파일에서 실제 이미지와 위치를 추출한다."""

    downloader = HttpFileDownloader(
        _settings(),
        transport=_transport(case),
        temp_directory=temp_directory,
    )
    downloaded_path: Path | None = None

    async with downloader.download_and_validate(
        file_url=_download_url(case),
        users_idx=_TEST_USERS_IDX,
        file_idx=file_idx,
        expected_sha256=_str(case, "sha256"),
    ) as downloaded_file:
        downloaded_path = downloaded_file.path
        extraction = await _image_extractor(case).extract(downloaded_file.path)

    assert downloaded_path is not None
    assert not downloaded_path.exists()
    assert list(temp_directory.iterdir()) == []
    return extraction


def test_manifest_fixes_complete_issue_123_document_matrix() -> None:
    """요청된 정상·이미지·스캔·실패 문서가 누락이나 중복 없이 고정됐는지 검증한다."""

    assert _int(_MANIFEST, "schema_version") == 1
    assert _str(_MANIFEST, "fixture_set") == "jipsa-rag-issue-123-fixed-documents-v1"
    assert _int(_MANIFEST, "issue") == 123
    assert _str(_MANIFEST, "source_branch") == "test/123"
    assert tuple(_DOCUMENTS_BY_ID) == _ALL_CASE_IDS
    assert len(_DOCUMENTS_BY_ID) == len(_DOCUMENTS)

    assert {_str(_case(case_id), "file_type") for case_id in _TEXT_CASE_IDS} == {
        "pdf",
        "docx",
        "pptx",
        "txt",
        "xlsx",
    }
    assert {_str(_case(case_id), "file_type") for case_id in _IMAGE_CASE_IDS} == {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
    }


@pytest.mark.parametrize("case_id", _ALL_CASE_IDS)
def test_fixed_fixture_bytes_match_manifest_checksum_and_size(case_id: str) -> None:
    """Fixture의 우발적 재저장이나 바이트 변경을 SHA-256으로 즉시 탐지한다."""

    case = _case(case_id)
    fixture_path = _fixture_path(case)
    assert fixture_path.is_file()
    assert fixture_path.stat().st_size == _int(case, "size_bytes")
    assert _sha256(fixture_path) == _str(case, "sha256")


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", _TEXT_CASE_IDS)
async def test_downloads_validates_and_parses_fixed_text_documents(
    case_id: str,
    tmp_path: Path,
) -> None:
    """다섯 지원 형식의 실제 다운로드부터 위치 보존 파싱까지 연속 검증한다."""

    case = _case(case_id)
    parsed = await _download_and_parse(
        case,
        temp_directory=tmp_path,
        file_idx=_TEST_FILE_IDX_BASE + _TEXT_CASE_IDS.index(case_id),
    )
    parser = _parser(case)

    assert parsed.file_type.value.lower() == _str(case, "file_type")
    assert parser.parser_type == _str(case, "expected_parser_type")
    assert parser.parser_version == _str(case, "expected_parser_version")
    _assert_subset(
        parsed.document_metadata,
        _mapping(case, "expected_document_metadata"),
        label=f"{case_id}.document_metadata",
    )

    for expected_unit in _objects(case, "expected_units"):
        exact_text = expected_unit.get("exact_text")
        contains = expected_unit.get("contains")

        if isinstance(exact_text, str):
            matched_unit = next(
                (unit for unit in parsed.units if unit.text == exact_text),
                None,
            )
        elif isinstance(contains, str):
            matched_unit = next(
                (unit for unit in parsed.units if contains in unit.text),
                None,
            )
        else:
            raise AssertionError("Each expected unit requires exact_text or contains.")

        assert matched_unit is not None, (
            f"{case_id} did not produce the expected unit: {expected_unit!r}"
        )
        _assert_subset(
            matched_unit.source_metadata,
            _mapping(expected_unit, "metadata"),
            label=f"{case_id}.unit_metadata",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", (*_IMAGE_CASE_IDS, *_SCANNED_CASE_IDS))
async def test_downloads_validates_and_extracts_fixed_document_images(
    case_id: str,
    tmp_path: Path,
) -> None:
    """문서 내부 그림과 스캔 페이지가 실제 원본 위치 메타데이터로 추출되는지 검증한다."""

    case = _case(case_id)
    extraction = await _download_and_extract_images(
        case,
        temp_directory=tmp_path,
        file_idx=_TEST_FILE_IDX_BASE + 100 + (*_IMAGE_CASE_IDS, *_SCANNED_CASE_IDS).index(case_id),
    )
    expected_kind = DocumentImageKind[_str(case, "expected_image_kind")]
    expected_metadata = _mapping(case, "expected_image_metadata")

    matched_image = next(
        (
            image
            for image in extraction.images
            if image.kind is expected_kind
            and all(
                _equivalent(image.source_metadata.get(key), value)
                for key, value in expected_metadata.items()
            )
        ),
        None,
    )
    assert matched_image is not None, (
        f"{case_id} did not produce {expected_kind.name} with {expected_metadata!r}."
    )
    assert matched_image.content
    assert matched_image.width_px is not None and matched_image.width_px > 0
    assert matched_image.height_px is not None and matched_image.height_px > 0

    expected_locations_value = case.get("expected_image_only_locations", [])
    if not isinstance(expected_locations_value, list):
        raise AssertionError("expected_image_only_locations must be an array.")

    expected_locations = [
        _object(item, "expected_image_only_locations item")
        for item in cast(list[object], expected_locations_value)
    ]
    assert len(extraction.image_only_locations) == len(expected_locations)
    for expected_location in expected_locations:
        assert any(
            all(
                _equivalent(location.source_metadata.get(key), value)
                for key, value in expected_location.items()
            )
            for location in extraction.image_only_locations
        )


@pytest.mark.asyncio
async def test_empty_file_is_rejected_during_stream_download_and_temp_file_is_removed(
    tmp_path: Path,
) -> None:
    """0 Byte 응답은 파서 진입 전에 INVALID_FILE로 거부하고 임시 파일을 정리한다."""

    case = _case("empty-pdf")
    downloader = HttpFileDownloader(
        _settings(),
        transport=_transport(case),
        temp_directory=tmp_path,
    )

    with pytest.raises(AppException) as exception_info:
        async with downloader.download_and_validate(
            file_url=_download_url(case),
            users_idx=_TEST_USERS_IDX,
            file_idx=_TEST_FILE_IDX_BASE + 200,
        ):
            pass

    assert exception_info.value.code == _str(case, "expected_error_code")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_id", "expected_exception"),
    [
        pytest.param("corrupted-pdf", InvalidDocumentError, id="corrupted-pdf"),
        pytest.param("encrypted-pdf", EncryptedDocumentError, id="encrypted-pdf"),
    ],
)
async def test_invalid_pdf_reaches_parser_only_after_successful_download_validation(
    case_id: str,
    expected_exception: type[InvalidDocumentError] | type[EncryptedDocumentError],
    tmp_path: Path,
) -> None:
    """PDF Magic은 맞지만 구조가 손상됐거나 암호화된 문서를 파서 경계에서 구분한다."""

    case = _case(case_id)
    downloader = HttpFileDownloader(
        _settings(),
        transport=_transport(case),
        temp_directory=tmp_path,
    )
    downloaded_path: Path | None = None

    with pytest.raises(expected_exception):
        async with downloader.download_and_validate(
            file_url=_download_url(case),
            users_idx=_TEST_USERS_IDX,
            file_idx=_TEST_FILE_IDX_BASE + 201 + _INVALID_CASE_IDS.index(case_id),
            expected_sha256=_str(case, "sha256"),
        ) as downloaded_file:
            downloaded_path = downloaded_file.path
            assert downloaded_file.sha256 == _str(case, "sha256")
            await PdfDocumentParser().parse(downloaded_file.path)

    assert downloaded_path is not None
    assert not downloaded_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_wrong_extension_is_rejected_by_request_contract_before_download() -> None:
    """실제 DOCX를 PDF 이름으로 전달하는 요청은 file_type·확장자 불일치로 거부한다."""

    case = _case("docx-payload-named-pdf")
    with pytest.raises(ValidationError):
        FileProcessingRequest(
            file_idx=_TEST_FILE_IDX_BASE + 220,
            user_idx=_TEST_USERS_IDX,
            folder_idx=None,
            file_name=_fixture_path(case).name,
            file_type=SupportedFileType.DOCX,
            download_url=_download_url(case),
            url_expires_in=900,
        )


@pytest.mark.asyncio
async def test_wrong_extension_payload_is_rejected_by_mime_and_magic_validation(
    tmp_path: Path,
) -> None:
    """PDF로 선언된 URL이 OOXML 바이트를 반환하면 다운로더가 파서 전에 차단한다."""

    case = _case("docx-payload-named-pdf")
    downloader = HttpFileDownloader(
        _settings(),
        transport=_transport(case),
        temp_directory=tmp_path,
    )

    with pytest.raises(AppException) as exception_info:
        async with downloader.download_and_validate(
            file_url=_download_url(case),
            users_idx=_TEST_USERS_IDX,
            file_idx=_TEST_FILE_IDX_BASE + 221,
        ):
            pass

    assert exception_info.value.code == _str(case, "expected_error_code")
    assert list(tmp_path.iterdir()) == []
