"""텍스트가 없는 문서에서 Hybrid OCR 파서가 수행하는 복구 경로를 검증한다."""

from pathlib import Path

import pytest

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.exceptions import DocumentTextNotFoundError
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    DocumentImageKind,
    ExtractedDocumentImage,
    ImageOnlyLocation,
)
from jipsa_rag.infrastructure.document.media_aware import OcrAwarePdfDocumentParser
from jipsa_rag.infrastructure.document.models import DocumentType, ParsedDocument
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser
from jipsa_rag.infrastructure.ocr.enrichment import OcrDocumentEnricher
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult, OcrTextLine


class _ImageOnlyExtractor:
    @property
    def file_type(self) -> DocumentType:
        return DocumentType.PDF

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        del file_path
        return DocumentImageExtraction(
            images=(
                ExtractedDocumentImage(
                    image_id="scan-page-1",
                    kind=DocumentImageKind.PDF_PAGE_RENDER,
                    content=b"scan-image",
                    media_type="image/png",
                    extension="png",
                    source_metadata={
                        "page_number": 1,
                        "image_index": 0,
                        "is_image_only_page": True,
                    },
                ),
            ),
            image_only_locations=(
                ImageOnlyLocation(
                    location_id="page:1",
                    source_metadata={"page_number": 1},
                ),
            ),
        )


class _TextOcrEngine:
    engine_name = "STUB_OCR"

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        del image
        return OcrRecognitionResult(
            lines=(
                OcrTextLine(
                    text="스캔 계약서 특약 사항",
                    confidence=0.99,
                    order=0,
                ),
            ),
            engine_name=self.engine_name,
            languages=("ko", "en"),
            device="cuda:0",
        )


class _EmptyOcrEngine:
    engine_name = "STUB_EMPTY_OCR"

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        del image
        return OcrRecognitionResult(
            lines=(),
            engine_name=self.engine_name,
            languages=("ko", "en"),
            device="cuda:0",
        )


async def _raise_text_not_found(
    parser: PdfDocumentParser,
    file_path: Path,
) -> ParsedDocument:
    del parser, file_path
    raise DocumentTextNotFoundError(DocumentType.PDF)


@pytest.mark.asyncio
async def test_image_only_pdf_is_recovered_when_ocr_returns_searchable_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """기존 PDF 파서가 텍스트 없음으로 실패해도 OCR unit이 있으면 파싱에 성공한다."""

    monkeypatch.setattr(PdfDocumentParser, "parse", _raise_text_not_found)
    settings = DocumentProcessingSettings(_env_file=None)
    parser = OcrAwarePdfDocumentParser(
        image_extractor=_ImageOnlyExtractor(),
        ocr_enricher=OcrDocumentEnricher(
            engine=_TextOcrEngine(),
            settings=settings,
        ),
    )

    result = await parser.parse(tmp_path / "scan.pdf")

    assert result.file_type is DocumentType.PDF
    assert result.text_unit_count == 1
    assert result.units[0].text == ""
    assert result.units[1].source_metadata["unit_type"] == "ocr_image"
    assert result.units[1].source_metadata["page_number"] == 1
    assert "스캔 계약서 특약 사항" in result.units[1].text


@pytest.mark.asyncio
async def test_image_only_pdf_preserves_text_not_found_failure_when_ocr_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """텍스트 레이어와 OCR 결과가 모두 비어 있으면 빈 문서를 정상 처리하지 않는다."""

    monkeypatch.setattr(PdfDocumentParser, "parse", _raise_text_not_found)
    settings = DocumentProcessingSettings(_env_file=None)
    parser = OcrAwarePdfDocumentParser(
        image_extractor=_ImageOnlyExtractor(),
        ocr_enricher=OcrDocumentEnricher(
            engine=_EmptyOcrEngine(),
            settings=settings,
        ),
    )

    with pytest.raises(DocumentTextNotFoundError):
        await parser.parse(tmp_path / "scan.pdf")
