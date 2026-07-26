"""운영 기본 Document Parser 구현체를 한곳에서 내보낸다.

Parser Factory는 이 모듈만 import하여 지원 파서를 등록한다. 구체 파서 파일의
경로가 바뀌더라도 외부 import 지점을 이 모듈로 제한하면 변경 범위를 줄일 수 있다.

PDF 파서는 기존 구현을 그대로 재사용하여 페이지 단위 텍스트와 ``page_number``
메타데이터의 하위 호환성을 유지한다.
"""

from jipsa_rag.infrastructure.document.parsers.docx import DocxDocumentParser
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser
from jipsa_rag.infrastructure.document.parsers.pptx import PptxDocumentParser
from jipsa_rag.infrastructure.document.parsers.txt import TxtDocumentParser
from jipsa_rag.infrastructure.document.parsers.xlsx import XlsxDocumentParser

__all__ = [
    "DocxDocumentParser",
    "PdfDocumentParser",
    "PptxDocumentParser",
    "TxtDocumentParser",
    "XlsxDocumentParser",
]
