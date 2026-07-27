"""스캔 PDF 및 이미지 전용 페이지 탐지 기준을 검증한다."""

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.pdf import _is_image_only_page


def test_vector_only_page_without_xobject_is_rendered_for_ocr() -> None:
    """텍스트·XObject가 없어도 벡터 경로가 있으면 outline 문서 후보로 처리한다."""

    assert _is_image_only_page(
        compact_text_length=0,
        image_count=0,
        image_coverage_ratio=0.0,
        has_non_text_content=True,
        settings=DocumentProcessingSettings(),
    )


def test_completely_blank_page_is_not_classified_as_scan() -> None:
    """텍스트와 시각 요소가 모두 없는 빈 페이지는 불필요한 OCR에서 제외한다."""

    assert not _is_image_only_page(
        compact_text_length=0,
        image_count=0,
        image_coverage_ratio=0.0,
        has_non_text_content=False,
        settings=DocumentProcessingSettings(),
    )


def test_short_text_page_requires_configured_image_coverage() -> None:
    """소량 텍스트가 있는 페이지는 이미지가 충분히 넓을 때만 스캔으로 판단한다."""

    settings = DocumentProcessingSettings(
        scan_pdf_text_threshold_chars=24,
        scan_pdf_image_coverage_ratio=0.60,
    )

    assert _is_image_only_page(
        compact_text_length=10,
        image_count=1,
        image_coverage_ratio=0.80,
        has_non_text_content=True,
        settings=settings,
    )
    assert not _is_image_only_page(
        compact_text_length=10,
        image_count=1,
        image_coverage_ratio=0.20,
        has_non_text_content=True,
        settings=settings,
    )


def test_normal_text_page_is_not_classified_as_scan() -> None:
    """충분한 텍스트 레이어가 존재하면 큰 배경 이미지가 있어도 OCR 렌더를 피한다."""

    assert not _is_image_only_page(
        compact_text_length=500,
        image_count=1,
        image_coverage_ratio=1.0,
        has_non_text_content=True,
        settings=DocumentProcessingSettings(),
    )
