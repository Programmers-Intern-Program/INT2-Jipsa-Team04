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


class ManagedOcrEngine(OcrEngine, Protocol):
    """애플리케이션 생명주기에서 시작·종료할 수 있는 OCR 엔진 계약.

    실제 CUDA EasyOCR 구현은 별도 worker process를 소유한다. FastAPI lifespan은
    요청이 끝난 뒤에도 worker가 남지 않도록 이 계약의 ``close()``를 호출한다.
    ``start()``는 worker를 미리 준비해야 하는 통합 테스트와 진단에서 사용하며,
    운영 요청 경로에서는 첫 OCR 호출 시 지연 시작할 수 있다.
    """

    async def start(self) -> None:
        """설정된 수만큼 OCR worker process를 준비한다."""

        ...

    async def close(self) -> None:
        """소유한 worker process와 IPC 자원을 모두 종료한다."""

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
