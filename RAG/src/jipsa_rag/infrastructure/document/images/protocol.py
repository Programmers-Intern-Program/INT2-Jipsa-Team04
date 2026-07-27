"""문서 형식별 이미지 추출기의 공통 인터페이스를 정의한다."""

from pathlib import Path
from typing import Protocol

from jipsa_rag.infrastructure.document.images.models import DocumentImageExtraction
from jipsa_rag.infrastructure.document.models import DocumentType


class DocumentImageExtractor(Protocol):
    """원본 문서에서 OCR 후보 이미지를 추출하는 비동기 계약."""

    @property
    def file_type(self) -> DocumentType:
        """현재 추출기가 책임지는 문서 형식을 반환한다."""

        ...

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        """원본 파일을 읽어 이미지와 안전한 원본 위치 메타데이터를 반환한다."""

        ...
