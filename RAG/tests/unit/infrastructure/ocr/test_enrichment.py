"""OCR 텍스트의 청크 변환과 원본 문맥 연결 계약을 검증한다."""

import pytest

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    DocumentImageKind,
    ExtractedDocumentImage,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)
from jipsa_rag.infrastructure.ocr.enrichment import OcrDocumentEnricher
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult, OcrTextLine


class _StubOcrEngine:
    """네트워크·CUDA 없이 고정 OCR 결과를 반환하는 테스트 구현체."""

    engine_name = "STUB_OCR"

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        del image
        return OcrRecognitionResult(
            lines=(
                OcrTextLine(text="관리비 120,000원", confidence=0.98, order=0),
                OcrTextLine(text="신뢰도 미달", confidence=0.10, order=1),
            ),
            engine_name=self.engine_name,
            languages=("ko", "en"),
            device="cuda:0",
        )


@pytest.mark.asyncio
async def test_enricher_inserts_searchable_ocr_unit_after_same_pdf_page() -> None:
    """PDF 페이지 OCR을 같은 페이지 텍스트 뒤에 삽입하고 주변 문맥을 연결한다."""

    settings = DocumentProcessingSettings(
        ocr_min_confidence=0.35,
        ocr_context_max_chars=300,
    )
    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="첫 페이지 본문",
                source_metadata={"page_number": 1},
            ),
            ParsedDocumentUnit(
                text="둘째 페이지 본문",
                source_metadata={"page_number": 2},
            ),
        ),
    )
    image = ExtractedDocumentImage(
        image_id="pdf-page-1-image-1",
        kind=DocumentImageKind.PDF_EMBEDDED,
        content=b"test-image-bytes",
        media_type="image/png",
        extension="png",
        source_metadata={
            "page_number": 1,
            "image_index": 1,
            "shape_path": "page:1/image:1",
        },
        context_before="이전 문단",
        context_current="첫 페이지 본문",
        context_after="다음 문단",
    )
    enricher = OcrDocumentEnricher(
        engine=_StubOcrEngine(),
        settings=settings,
    )

    result = await enricher.enrich(
        document=document,
        extraction=DocumentImageExtraction(images=(image,)),
    )

    assert [unit.source_metadata.get("unit_type") for unit in result.units] == [
        None,
        "ocr_image",
        None,
    ]
    ocr_unit = result.units[1]
    assert "[이미지 OCR]\n관리비 120,000원" in ocr_unit.text
    assert "[주변 문맥]\n이전 문단\n첫 페이지 본문\n다음 문단" in ocr_unit.text
    assert "신뢰도 미달" not in ocr_unit.text
    assert ocr_unit.source_metadata["page_number"] == 1
    assert ocr_unit.source_metadata["content_origin"] == "ocr"
    assert ocr_unit.source_metadata["ocr_device"] == "cuda:0"
    assert result.document_metadata["ocr_unit_count"] == 1
    assert result.document_metadata["ocr_failed_image_count"] == 0


@pytest.mark.asyncio
async def test_enricher_skips_images_that_are_not_ocr_candidates() -> None:
    """스캔 페이지 전체 렌더와 중복되는 내부 이미지는 OCR 호출 대상에서 제외한다."""

    settings = DocumentProcessingSettings()
    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(ParsedDocumentUnit(text="본문", source_metadata={"page_number": 1}),),
    )
    image = ExtractedDocumentImage(
        image_id="duplicate-image",
        kind=DocumentImageKind.PDF_EMBEDDED,
        content=b"duplicate",
        media_type="image/png",
        extension="png",
        ocr_candidate=False,
        source_metadata={"page_number": 1},
    )
    enricher = OcrDocumentEnricher(
        engine=_StubOcrEngine(),
        settings=settings,
    )

    result = await enricher.enrich(
        document=document,
        extraction=DocumentImageExtraction(images=(image,)),
    )

    assert result.units == document.units
    assert result.document_metadata["ocr_candidate_count"] == 0
    assert result.document_metadata["ocr_unit_count"] == 0


@pytest.mark.asyncio
async def test_enricher_limits_total_ocr_image_bytes_per_document() -> None:
    """작은 이미지가 누적되어 문서 단위 메모리 한계를 넘는 경우 뒤 후보를 건너뛴다."""

    settings = DocumentProcessingSettings(
        image_max_bytes=800_000,
        image_max_total_bytes=1_048_576,
        _env_file=None,
    )
    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(ParsedDocumentUnit(text="본문", source_metadata={"page_number": 1}),),
    )
    images = tuple(
        ExtractedDocumentImage(
            image_id=f"image-{index}",
            kind=DocumentImageKind.PDF_EMBEDDED,
            content=b"x" * 700_000,
            media_type="image/png",
            extension="png",
            source_metadata={"page_number": 1, "image_index": index},
        )
        for index in (1, 2)
    )
    enricher = OcrDocumentEnricher(
        engine=_StubOcrEngine(),
        settings=settings,
    )

    result = await enricher.enrich(
        document=document,
        extraction=DocumentImageExtraction(images=images),
    )

    assert result.document_metadata["extracted_ocr_candidate_count"] == 2
    assert result.document_metadata["ocr_candidate_count"] == 1
    assert result.document_metadata["ocr_resource_skipped_count"] == 1
    assert result.document_metadata["ocr_unit_count"] == 1


class _CountingOcrEngine(_StubOcrEngine):
    """동일 이미지 Hash가 실제 OCR 호출을 중복 생성하지 않는지 기록한다."""

    def __init__(self) -> None:
        self.call_count = 0

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        self.call_count += 1
        return await super().recognize(image)


@pytest.mark.asyncio
async def test_enricher_reuses_ocr_result_for_duplicate_image_content() -> None:
    """같은 이미지 바이트는 한 번만 추론하고 두 원본 위치에 각각 연결한다."""

    settings = DocumentProcessingSettings(_env_file=None)
    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(text="첫 페이지", source_metadata={"page_number": 1}),
            ParsedDocumentUnit(text="둘째 페이지", source_metadata={"page_number": 2}),
        ),
    )
    images = (
        ExtractedDocumentImage(
            image_id="duplicate-1",
            kind=DocumentImageKind.PDF_EMBEDDED,
            content=b"same-image-content",
            media_type="image/png",
            extension="png",
            source_metadata={"page_number": 1, "image_index": 1},
            context_current="첫 페이지",
        ),
        ExtractedDocumentImage(
            image_id="duplicate-2",
            kind=DocumentImageKind.PDF_EMBEDDED,
            content=b"same-image-content",
            media_type="image/png",
            extension="png",
            source_metadata={"page_number": 2, "image_index": 1},
            context_current="둘째 페이지",
        ),
    )
    engine = _CountingOcrEngine()
    enricher = OcrDocumentEnricher(engine=engine, settings=settings)

    result = await enricher.enrich(
        document=document,
        extraction=DocumentImageExtraction(images=images),
    )

    assert engine.call_count == 1
    assert result.document_metadata["ocr_candidate_count"] == 2
    assert result.document_metadata["ocr_unique_candidate_count"] == 1
    assert result.document_metadata["ocr_deduplicated_candidate_count"] == 1
    assert result.document_metadata["ocr_unit_count"] == 2
    assert [
        unit.source_metadata.get("page_number")
        for unit in result.units
        if unit.source_metadata.get("unit_type") == "ocr_image"
    ] == [1, 2]


class _SlowOcrEngine(_StubOcrEngine):
    """제한 시간 검증을 위해 완료되지 않는 OCR 호출을 모사한다."""

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        import asyncio

        del image
        await asyncio.sleep(0.1)
        raise AssertionError("OCR timeout should cancel this coroutine.")


@pytest.mark.asyncio
async def test_enricher_treats_single_image_timeout_as_partial_failure() -> None:
    """OCR timeout은 문서 전체 예외가 아니라 해당 이미지의 부분 실패로 처리한다."""

    settings = DocumentProcessingSettings(
        ocr_timeout_seconds=0.001,
        _env_file=None,
    )
    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(ParsedDocumentUnit(text="본문", source_metadata={"page_number": 1}),),
    )
    image = ExtractedDocumentImage(
        image_id="timeout-image",
        kind=DocumentImageKind.PDF_EMBEDDED,
        content=b"timeout-content",
        media_type="image/png",
        extension="png",
        source_metadata={"page_number": 1},
    )
    enricher = OcrDocumentEnricher(engine=_SlowOcrEngine(), settings=settings)

    result = await enricher.enrich(
        document=document,
        extraction=DocumentImageExtraction(images=(image,)),
    )

    assert result.units == document.units
    assert result.document_metadata["ocr_unit_count"] == 0
    assert result.document_metadata["ocr_failed_image_count"] == 1


class _MixedSpeedOcrEngine(_StubOcrEngine):
    """문서 제한 시간 이전·이후에 끝나는 OCR 호출을 함께 모사한다."""

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        import asyncio

        if image.image_id == "slow-image":
            await asyncio.sleep(0.1)
        return await super().recognize(image)


@pytest.mark.asyncio
async def test_enricher_preserves_completed_results_on_document_timeout() -> None:
    """문서 OCR timeout 시 완료 결과는 유지하고 미완료 이미지만 실패 처리한다."""

    settings = DocumentProcessingSettings(
        ocr_max_concurrency=2,
        ocr_timeout_seconds=0.02,
        ocr_document_timeout_seconds=0.02,
        _env_file=None,
    )
    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(ParsedDocumentUnit(text="본문", source_metadata={"page_number": 1}),),
    )
    images = (
        ExtractedDocumentImage(
            image_id="fast-image",
            kind=DocumentImageKind.PDF_EMBEDDED,
            content=b"fast-content",
            media_type="image/png",
            extension="png",
            source_metadata={"page_number": 1, "image_index": 1},
        ),
        ExtractedDocumentImage(
            image_id="slow-image",
            kind=DocumentImageKind.PDF_EMBEDDED,
            content=b"slow-content",
            media_type="image/png",
            extension="png",
            source_metadata={"page_number": 1, "image_index": 2},
        ),
    )
    enricher = OcrDocumentEnricher(
        engine=_MixedSpeedOcrEngine(),
        settings=settings,
    )

    result = await enricher.enrich(
        document=document,
        extraction=DocumentImageExtraction(images=images),
    )

    assert result.document_metadata["ocr_unit_count"] == 1
    assert result.document_metadata["ocr_failed_image_count"] == 1
    assert result.document_metadata["ocr_document_timed_out_image_count"] == 1
