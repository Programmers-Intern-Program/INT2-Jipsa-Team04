"""문서 이미지 추출 Factory의 다운로드 안전 어댑터 등록을 검증한다."""

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.download_safe_pptx import (
    DownloadSafePptxImageExtractor,
)
from jipsa_rag.infrastructure.document.images.download_safe_xlsx import (
    DownloadSafeXlsxImageExtractor,
)
from jipsa_rag.infrastructure.document.images.factory import DocumentImageExtractorFactory
from jipsa_rag.infrastructure.document.models import DocumentType


def test_factory_registers_download_safe_office_extractors() -> None:
    """PPTX와 XLSX 요청이 모두 확장자 정규화 어댑터를 통과하도록 조립한다."""

    factory = DocumentImageExtractorFactory(
        DocumentProcessingSettings(
            office_rendering_enabled=False,
            ocr_enabled=False,
            ocr_gpu=False,
            ocr_gpu_required=False,
            _env_file=None,
        )
    )

    assert isinstance(
        factory.get(DocumentType.PPTX),
        DownloadSafePptxImageExtractor,
    )
    assert isinstance(
        factory.get(DocumentType.XLSX),
        DownloadSafeXlsxImageExtractor,
    )
