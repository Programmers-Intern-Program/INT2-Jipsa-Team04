"""OCR 인터페이스, 격리된 CUDA EasyOCR 구현과 문서 보강 서비스를 내보낸다."""

from jipsa_rag.infrastructure.ocr.easyocr import EasyOcrRuntime
from jipsa_rag.infrastructure.ocr.enrichment import OcrDocumentEnricher
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult, OcrTextLine
from jipsa_rag.infrastructure.ocr.normalization import normalize_ocr_text
from jipsa_rag.infrastructure.ocr.process_manager import EasyOcrEngine
from jipsa_rag.infrastructure.ocr.protocol import (
    ManagedOcrEngine,
    OcrDocumentEnricherProtocol,
    OcrEngine,
)

__all__ = [
    "EasyOcrEngine",
    "EasyOcrRuntime",
    "ManagedOcrEngine",
    "OcrDocumentEnricher",
    "OcrDocumentEnricherProtocol",
    "OcrEngine",
    "OcrRecognitionResult",
    "OcrTextLine",
    "normalize_ocr_text",
]
