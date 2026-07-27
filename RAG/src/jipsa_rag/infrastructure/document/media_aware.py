"""기존 텍스트 파서에 이미지 추출과 OCR 보강을 결합한다."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Final, TypeGuard

from jipsa_rag.infrastructure.document.exceptions import DocumentTextNotFoundError
from jipsa_rag.infrastructure.document.images.models import DocumentImageExtraction
from jipsa_rag.infrastructure.document.images.protocol import DocumentImageExtractor
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
    SourceMetadataScalar,
    SourceMetadataValue,
)
from jipsa_rag.infrastructure.document.parsers.docx import DocxDocumentParser
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser
from jipsa_rag.infrastructure.document.parsers.pptx import PptxDocumentParser
from jipsa_rag.infrastructure.document.parsers.xlsx import XlsxDocumentParser
from jipsa_rag.infrastructure.ocr.protocol import OcrDocumentEnricherProtocol

logger = logging.getLogger(__name__)
_OCR_PARSER_VERSION: Final[str] = "2.0.0"


class _OcrAwareParserMixin:
    """형식별 기존 파서 결과를 변경하지 않고 OCR 단위만 보강하는 공통 구현."""

    _image_extractor: DocumentImageExtractor
    _ocr_enricher: OcrDocumentEnricherProtocol

    def _initialize_ocr(
        self,
        *,
        image_extractor: DocumentImageExtractor,
        ocr_enricher: OcrDocumentEnricherProtocol,
    ) -> None:
        self._image_extractor = image_extractor
        self._ocr_enricher = ocr_enricher

    async def _parse_with_ocr(
        self,
        *,
        file_path: Path,
        file_type: DocumentType,
        parse_text: Callable[[Path], Awaitable[ParsedDocument]],
    ) -> ParsedDocument:
        """이미지 추출, 텍스트 파싱, OCR 병합과 이미지 전용 문서 복구를 수행한다."""

        try:
            extraction = await self._image_extractor.extract(file_path)
        except Exception as error:
            # 정상 텍스트가 있는 문서는 선택적 이미지 처리 실패만으로 인제스트 전체를
            # 중단하지 않는다. 경로와 원문은 로그에 남기지 않고 예외 종류만 기록한다.
            logger.warning(
                "document_image_extraction_failed",
                extra={
                    "file_type": file_type.value,
                    "image_error_type": type(error).__name__,
                },
            )
            extraction = DocumentImageExtraction.empty()

        text_not_found_error: DocumentTextNotFoundError | None = None
        try:
            parsed_document = await parse_text(file_path)
        except DocumentTextNotFoundError as error:
            # 스캔 PDF나 이미지 전용 Office 문서는 기존 텍스트 파서가 실패한다. 이미지
            # 추출 결과의 원본 위치를 빈 unit으로 복구한 뒤 OCR 결과가 실제 텍스트를
            # 만들었을 때만 성공으로 전환한다.
            text_not_found_error = error
            parsed_document = _build_image_only_document(
                file_type=file_type,
                extraction=extraction,
            )

        enriched_document = await self._ocr_enricher.enrich(
            document=parsed_document,
            extraction=extraction,
        )

        if text_not_found_error is not None and enriched_document.text_unit_count == 0:
            raise text_not_found_error

        return enriched_document


class OcrAwarePdfDocumentParser(_OcrAwareParserMixin, PdfDocumentParser):
    """PDF 텍스트 레이어와 페이지 이미지 OCR을 결합한다."""

    def __init__(
        self,
        *,
        image_extractor: DocumentImageExtractor,
        ocr_enricher: OcrDocumentEnricherProtocol,
    ) -> None:
        self._initialize_ocr(
            image_extractor=image_extractor,
            ocr_enricher=ocr_enricher,
        )

    @property
    def parser_type(self) -> str:
        return "PDF_HYBRID_OCR"

    @property
    def parser_version(self) -> str:
        return _OCR_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        return await self._parse_with_ocr(
            file_path=file_path,
            file_type=self.file_type,
            parse_text=super().parse,
        )


class OcrAwareDocxDocumentParser(_OcrAwareParserMixin, DocxDocumentParser):
    """DOCX 문단·표와 인라인·플로팅 이미지 OCR을 결합한다."""

    def __init__(
        self,
        *,
        image_extractor: DocumentImageExtractor,
        ocr_enricher: OcrDocumentEnricherProtocol,
    ) -> None:
        self._initialize_ocr(
            image_extractor=image_extractor,
            ocr_enricher=ocr_enricher,
        )

    @property
    def parser_type(self) -> str:
        return "DOCX_HYBRID_OCR"

    @property
    def parser_version(self) -> str:
        return _OCR_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        return await self._parse_with_ocr(
            file_path=file_path,
            file_type=self.file_type,
            parse_text=super().parse,
        )


class OcrAwarePptxDocumentParser(_OcrAwareParserMixin, PptxDocumentParser):
    """PPTX 텍스트, 그림, 차트 및 SmartArt OCR을 결합한다."""

    def __init__(
        self,
        *,
        image_extractor: DocumentImageExtractor,
        ocr_enricher: OcrDocumentEnricherProtocol,
    ) -> None:
        self._initialize_ocr(
            image_extractor=image_extractor,
            ocr_enricher=ocr_enricher,
        )

    @property
    def parser_type(self) -> str:
        return "PPTX_HYBRID_OCR"

    @property
    def parser_version(self) -> str:
        return _OCR_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        return await self._parse_with_ocr(
            file_path=file_path,
            file_type=self.file_type,
            parse_text=super().parse,
        )


class OcrAwareXlsxDocumentParser(_OcrAwareParserMixin, XlsxDocumentParser):
    """XLSX 셀·표와 삽입 이미지·차트 렌더 OCR을 결합한다."""

    def __init__(
        self,
        *,
        image_extractor: DocumentImageExtractor,
        ocr_enricher: OcrDocumentEnricherProtocol,
    ) -> None:
        self._initialize_ocr(
            image_extractor=image_extractor,
            ocr_enricher=ocr_enricher,
        )

    @property
    def parser_type(self) -> str:
        return "XLSX_HYBRID_OCR"

    @property
    def parser_version(self) -> str:
        return _OCR_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        return await self._parse_with_ocr(
            file_path=file_path,
            file_type=self.file_type,
            parse_text=super().parse,
        )


def _build_image_only_document(
    *,
    file_type: DocumentType,
    extraction: DocumentImageExtraction,
) -> ParsedDocument:
    """OCR 단위를 원본 위치에 삽입할 수 있도록 빈 위치 unit을 복원한다."""

    locations: list[dict[str, SourceMetadataScalar]] = []
    seen: set[tuple[tuple[str, object], ...]] = set()

    for location in extraction.image_only_locations:
        metadata = {
            key: value for key, value in location.source_metadata.items() if _is_scalar(value)
        }
        key = tuple(sorted(metadata.items()))
        if key not in seen:
            seen.add(key)
            locations.append(metadata)

    if not locations:
        for image in extraction.images:
            metadata = _anchor_metadata(file_type, image.source_metadata)
            key = tuple(sorted(metadata.items()))
            if metadata and key not in seen:
                seen.add(key)
                locations.append(metadata)

    units = tuple(
        ParsedDocumentUnit(
            text="",
            source_metadata={
                **metadata,
                "unit_type": "image_only_anchor",
            },
        )
        for metadata in locations
    )
    return ParsedDocument(
        file_type=file_type,
        units=units,
        document_metadata=extraction.document_metadata,
    )


def _anchor_metadata(
    file_type: DocumentType,
    metadata: Mapping[str, SourceMetadataValue],
) -> dict[str, SourceMetadataScalar]:
    """이미지 위치에서 문서 형식별 병합 기준이 되는 scalar 필드만 복사한다."""

    source = metadata

    if file_type is DocumentType.PDF:
        page_number = source.get("page_number")
        return {"page_number": page_number} if _is_scalar(page_number) else {}

    if file_type is DocumentType.DOCX:
        docx_result: dict[str, SourceMetadataScalar] = {}
        for key in ("section_index", "block_index", "paragraph_index"):
            value = source.get(key)
            if _is_scalar(value):
                docx_result[key] = value
        return docx_result

    if file_type is DocumentType.PPTX:
        slide_number = source.get("slide_number")
        return {"slide_number": slide_number} if _is_scalar(slide_number) else {}

    if file_type is DocumentType.XLSX:
        xlsx_result: dict[str, SourceMetadataScalar] = {}
        for key in ("sheet_index", "sheet_name", "cell_range"):
            value = source.get(key)
            if _is_scalar(value):
                xlsx_result[key] = value
        return xlsx_result

    return {}


def _is_scalar(value: object) -> TypeGuard[SourceMetadataScalar]:
    """Source metadata에서 JSON scalar로 저장 가능한 값인지 반환한다."""

    return value is None or isinstance(value, str | int | float | bool)
