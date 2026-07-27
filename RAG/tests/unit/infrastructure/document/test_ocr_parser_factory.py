"""설정에 따른 기본 텍스트 파서와 Hybrid OCR 파서 선택을 검증한다."""

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.media_aware import (
    OcrAwareDocxDocumentParser,
    OcrAwarePdfDocumentParser,
    OcrAwarePptxDocumentParser,
    OcrAwareXlsxDocumentParser,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser_factory import DocumentParserFactory
from jipsa_rag.infrastructure.document.parsers import (
    DocxDocumentParser,
    PdfDocumentParser,
    PptxDocumentParser,
    TxtDocumentParser,
    XlsxDocumentParser,
)


def test_factory_uses_hybrid_ocr_parsers_when_image_ocr_is_enabled() -> None:
    """운영 기본값에서 이미지 지원 형식만 OCR 인식 가능 구현체로 교체한다."""

    factory = DocumentParserFactory(settings=DocumentProcessingSettings())

    assert isinstance(factory.get_parser(DocumentType.PDF), OcrAwarePdfDocumentParser)
    assert isinstance(factory.get_parser(DocumentType.DOCX), OcrAwareDocxDocumentParser)
    assert isinstance(factory.get_parser(DocumentType.PPTX), OcrAwarePptxDocumentParser)
    assert isinstance(factory.get_parser(DocumentType.XLSX), OcrAwareXlsxDocumentParser)
    assert isinstance(factory.get_parser(DocumentType.TXT), TxtDocumentParser)


def test_factory_preserves_original_parsers_when_ocr_is_disabled() -> None:
    """기능 플래그 비활성화 시 기존 파서 하위 호환성을 유지한다."""

    factory = DocumentParserFactory(
        settings=DocumentProcessingSettings(ocr_enabled=False),
    )

    assert type(factory.get_parser(DocumentType.PDF)) is PdfDocumentParser
    assert type(factory.get_parser(DocumentType.DOCX)) is DocxDocumentParser
    assert type(factory.get_parser(DocumentType.PPTX)) is PptxDocumentParser
    assert type(factory.get_parser(DocumentType.XLSX)) is XlsxDocumentParser
    assert type(factory.get_parser(DocumentType.TXT)) is TxtDocumentParser
