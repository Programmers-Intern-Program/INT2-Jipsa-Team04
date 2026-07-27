"""문서 형식별 이미지 추출기를 생성하는 Factory를 제공한다."""

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.docx import DocxImageExtractor
from jipsa_rag.infrastructure.document.images.pdf import PdfImageExtractor
from jipsa_rag.infrastructure.document.images.pptx import PptxImageExtractor
from jipsa_rag.infrastructure.document.images.protocol import DocumentImageExtractor
from jipsa_rag.infrastructure.document.images.xlsx import XlsxImageExtractor
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.rendering import MicrosoftOfficeRenderClient


class DocumentImageExtractorFactory:
    """PDF와 OOXML 형식에 대응하는 이미지 추출기를 공유 자원과 함께 관리한다."""

    def __init__(self, settings: DocumentProcessingSettings) -> None:
        renderer = MicrosoftOfficeRenderClient(settings)
        extractors: tuple[DocumentImageExtractor, ...] = (
            PdfImageExtractor(settings),
            DocxImageExtractor(settings),
            PptxImageExtractor(settings, renderer),
            XlsxImageExtractor(settings, renderer),
        )
        self._extractors = {extractor.file_type: extractor for extractor in extractors}

    def get(self, file_type: DocumentType) -> DocumentImageExtractor | None:
        """TXT처럼 이미지 추출 대상이 아닌 형식에는 ``None``을 반환한다."""

        return self._extractors.get(file_type)
