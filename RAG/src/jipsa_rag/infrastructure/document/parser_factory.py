"""요청한 문서 형식에 대응하는 텍스트·이미지 OCR 파서를 선택한다.

기존 API와 서비스 계층은 구체 파서를 직접 생성하지 않는다. Factory는 기본 텍스트
파서를 유지하면서, 이미지 추출과 OCR이 활성화된 환경에서는 PDF/DOCX/PPTX/XLSX
파서를 해당 형식의 OCR 인식 가능 하위 클래스로 교체한다. TXT는 문서 내부 이미지가
존재하지 않으므로 기존 텍스트 파서를 그대로 사용한다.

운영 Factory는 하나의 ``EasyOcrEngine``과 ``OcrDocumentEnricher``를 네 가지 이미지
지원 문서 파서가 함께 사용한다. FastAPI lifespan에서 Factory 인스턴스 하나를 공유하면
OCR worker pool, CUDA 모델 복제 수와 동시성 제한도 모든 요청에 전역으로 적용된다.
"""

from collections.abc import Iterable

from jipsa_rag.core.document_processing import (
    DocumentProcessingSettings,
    get_document_processing_settings,
)
from jipsa_rag.infrastructure.document.exceptions import (
    DuplicateDocumentParserError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.images.factory import (
    DocumentImageExtractorFactory,
)
from jipsa_rag.infrastructure.document.media_aware import (
    OcrAwareDocxDocumentParser,
    OcrAwarePdfDocumentParser,
    OcrAwarePptxDocumentParser,
    OcrAwareXlsxDocumentParser,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser import DocumentParser
from jipsa_rag.infrastructure.document.parsers import (
    DocxDocumentParser,
    PdfDocumentParser,
    PptxDocumentParser,
    TxtDocumentParser,
    XlsxDocumentParser,
)
from jipsa_rag.infrastructure.ocr import (
    EasyOcrEngine,
    ManagedOcrEngine,
    OcrDocumentEnricher,
)


class DocumentParserFactory:
    """등록된 형식별 파서와 선택적 공유 OCR 엔진의 생명주기를 관리한다.

    ``parsers``를 생략하면 운영 설정에 따라 기본 파서를 만든다.

    - 이미지/OCR 활성화: PDF, DOCX, PPTX, XLSX는 Hybrid OCR 파서
    - 이미지/OCR 비활성화: 기존 텍스트 파서
    - TXT: 항상 기존 텍스트 파서

    호출자가 명시적인 Iterable을 전달하면 설정과 CUDA 자원을 읽지 않고 해당 목록만
    등록한다. 따라서 단위 테스트는 EasyOCR, Microsoft Office COM 또는 GPU 없이 작은
    Stub 파서만 주입할 수 있다.

    운영 기본 파서를 생성한 Factory는 ``close()``에서 자신이 소유한 OCR worker pool을
    종료한다. 명시적 파서 목록을 받은 Factory는 외부 자원을 소유하지 않는다.
    """

    def __init__(
        self,
        parsers: Iterable[DocumentParser] | None = None,
        *,
        settings: DocumentProcessingSettings | None = None,
        ocr_engine: ManagedOcrEngine | None = None,
    ) -> None:
        self._managed_ocr_engine: ManagedOcrEngine | None = None
        self._closed = False

        if parsers is None:
            processing_settings = settings or get_document_processing_settings()
            registered_parsers = self._build_default_parsers(
                processing_settings,
                ocr_engine=ocr_engine,
            )
        else:
            if ocr_engine is not None:
                raise ValueError(
                    "ocr_engine cannot be supplied with an explicit parser collection."
                )
            registered_parsers = tuple(parsers)

        self._parsers: dict[DocumentType, DocumentParser] = {}
        for parser in registered_parsers:
            self.register(parser)

    def _build_default_parsers(
        self,
        settings: DocumentProcessingSettings,
        *,
        ocr_engine: ManagedOcrEngine | None,
    ) -> tuple[DocumentParser, ...]:
        """설정에 맞는 운영 기본 파서와 애플리케이션 공유 OCR 자원을 생성한다."""

        if not settings.image_extraction_enabled or not settings.ocr_enabled:
            return (
                PdfDocumentParser(),
                DocxDocumentParser(),
                PptxDocumentParser(),
                TxtDocumentParser(),
                XlsxDocumentParser(),
            )

        image_extractors = DocumentImageExtractorFactory(settings)

        # 주입된 엔진은 lifespan 또는 통합 테스트가 소유권을 Factory에 위임한 것으로
        # 간주한다. 기본 경로에서는 종료 가능한 process 기반 EasyOCR 엔진을 한 번만
        # 만들고 네 형식의 OcrAware parser가 동일한 Enricher를 공유한다.
        managed_engine = ocr_engine or EasyOcrEngine(settings)
        self._managed_ocr_engine = managed_engine
        ocr_enricher = OcrDocumentEnricher(
            engine=managed_engine,
            settings=settings,
        )

        pdf_extractor = image_extractors.get(DocumentType.PDF)
        docx_extractor = image_extractors.get(DocumentType.DOCX)
        pptx_extractor = image_extractors.get(DocumentType.PPTX)
        xlsx_extractor = image_extractors.get(DocumentType.XLSX)
        if (
            pdf_extractor is None
            or docx_extractor is None
            or pptx_extractor is None
            or xlsx_extractor is None
        ):
            raise RuntimeError("A required document image extractor is not registered.")

        return (
            OcrAwarePdfDocumentParser(
                image_extractor=pdf_extractor,
                ocr_enricher=ocr_enricher,
            ),
            OcrAwareDocxDocumentParser(
                image_extractor=docx_extractor,
                ocr_enricher=ocr_enricher,
            ),
            OcrAwarePptxDocumentParser(
                image_extractor=pptx_extractor,
                ocr_enricher=ocr_enricher,
            ),
            TxtDocumentParser(),
            OcrAwareXlsxDocumentParser(
                image_extractor=xlsx_extractor,
                ocr_enricher=ocr_enricher,
            ),
        )

    async def close(self) -> None:
        """Factory가 소유한 OCR worker process와 IPC 자원을 반복 안전하게 종료한다."""

        if self._closed:
            return
        try:
            if self._managed_ocr_engine is not None:
                await self._managed_ocr_engine.close()
        finally:
            self._closed = True

    @property
    def managed_ocr_engine(self) -> ManagedOcrEngine | None:
        """운영 진단과 생명주기 테스트에 사용할 공유 OCR 엔진을 반환한다."""

        return self._managed_ocr_engine

    def register(self, parser: DocumentParser) -> None:
        """파서가 담당하는 형식을 키로 사용하여 중복 없이 등록한다."""

        file_type = parser.file_type
        if file_type in self._parsers:
            raise DuplicateDocumentParserError(file_type)
        self._parsers[file_type] = parser

    def get_parser(
        self,
        file_type: DocumentType | str,
    ) -> DocumentParser:
        """문서 형식을 정규화한 뒤 대응하는 등록 파서를 반환한다."""

        normalized_file_type = self._normalize_file_type(file_type)
        try:
            return self._parsers[normalized_file_type]
        except KeyError as error:
            raise UnsupportedDocumentTypeError(normalized_file_type) from error

    def supports(
        self,
        file_type: DocumentType | str,
    ) -> bool:
        """요청한 형식의 파서가 등록되어 있는지 예외 없이 반환한다."""

        try:
            normalized_file_type = self._normalize_file_type(file_type)
        except UnsupportedDocumentTypeError:
            return False
        return normalized_file_type in self._parsers

    @property
    def registered_file_types(self) -> frozenset[DocumentType]:
        """현재 등록 형식을 외부에서 변경할 수 없는 집합으로 반환한다."""

        return frozenset(self._parsers)

    @staticmethod
    def _normalize_file_type(
        file_type: DocumentType | str,
    ) -> DocumentType:
        """Enum 또는 문자열 형식 입력을 ``DocumentType``으로 통일한다."""

        if isinstance(file_type, DocumentType):
            return file_type

        normalized_value = file_type.strip().upper()
        try:
            return DocumentType(normalized_value)
        except ValueError as error:
            raise UnsupportedDocumentTypeError(file_type) from error
