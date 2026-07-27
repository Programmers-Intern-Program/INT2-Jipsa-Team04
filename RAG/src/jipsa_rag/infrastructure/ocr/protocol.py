"""이미지 OCR 처리와 문서 보강 인터페이스를 정의한다."""

from typing import Protocol

from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    ExtractedDocumentImage,
)
from jipsa_rag.infrastructure.document.models import ParsedDocument
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult


class OcrEngine(Protocol):
    """문서 이미지 바이트를 텍스트로 변환하는 비동기 계약."""

    @property
    def engine_name(self) -> str:
        """메타데이터와 운영 진단에 사용할 안정적인 엔진 이름을 반환한다."""

        ...

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        """한 이미지의 텍스트, 신뢰도, 언어 및 실행 장치를 반환한다."""

        ...


class OcrDocumentEnricherProtocol(Protocol):
    """기존 문서에 OCR 단위를 추가하는 비동기 보강 계약."""

    async def enrich(
        self,
        *,
        document: ParsedDocument,
        extraction: DocumentImageExtraction,
    ) -> ParsedDocument:
        """추출 이미지 OCR 결과를 기존 문서 위치와 문맥에 병합한다."""

        ...
