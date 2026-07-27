"""문서 내부 이미지의 공통 모델과 추출 계약을 내보낸다.

형식별 extractor와 Factory는 DOCX/PPTX/XLSX 라이브러리를 가져오므로 package import
시점에 eager import하지 않는다. 운영 조립 코드는 ``images.factory``에서 Factory를
명시적으로 가져오고, OCR 계층은 가벼운 모델과 Protocol만 사용할 수 있다.
"""

from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    DocumentImageKind,
    ExtractedDocumentImage,
    ImageOnlyLocation,
)
from jipsa_rag.infrastructure.document.images.protocol import DocumentImageExtractor

__all__ = [
    "DocumentImageExtraction",
    "DocumentImageExtractor",
    "DocumentImageKind",
    "ExtractedDocumentImage",
    "ImageOnlyLocation",
]
