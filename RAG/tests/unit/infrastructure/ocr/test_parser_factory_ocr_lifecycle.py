"""DocumentParserFactory의 공유 OCR 엔진 소유권과 종료 계약을 검증한다."""

from __future__ import annotations

import pytest

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import ExtractedDocumentImage
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser_factory import DocumentParserFactory
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult


class _ManagedStubOcrEngine:
    """process를 만들지 않고 Factory 생명주기 호출만 기록하는 OCR 엔진 대역."""

    engine_name = "MANAGED_STUB_OCR"

    def __init__(self) -> None:
        self.start_count = 0
        self.close_count = 0

    async def start(self) -> None:
        self.start_count += 1

    async def close(self) -> None:
        self.close_count += 1

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


@pytest.mark.asyncio
async def test_factory_owns_one_injected_engine_for_all_ocr_document_types() -> None:
    """PDF·DOCX·PPTX·XLSX 기본 파서가 Factory의 단일 엔진 생명주기에 속한다."""

    settings = DocumentProcessingSettings(
        image_extraction_enabled=True,
        ocr_enabled=True,
        ocr_gpu=False,
        ocr_gpu_required=False,
        _env_file=None,
    )
    engine = _ManagedStubOcrEngine()
    factory = DocumentParserFactory(settings=settings, ocr_engine=engine)

    assert factory.managed_ocr_engine is engine
    assert factory.registered_file_types == frozenset(DocumentType)

    # Factory는 Reader/process를 요청 시점에 지연 시작하므로 생성만으로 start를
    # 호출하지 않는다. shutdown에서는 소유 엔진을 정확히 한 번 닫는다.
    assert engine.start_count == 0
    await factory.close()
    await factory.close()
    assert engine.close_count == 1


@pytest.mark.asyncio
async def test_explicit_parser_factory_does_not_own_ocr_resources() -> None:
    """Stub 파서 전용 Factory 종료는 외부 CUDA 자원을 생성하거나 종료하지 않는다."""

    factory = DocumentParserFactory(parsers=())

    assert factory.managed_ocr_engine is None
    await factory.close()
