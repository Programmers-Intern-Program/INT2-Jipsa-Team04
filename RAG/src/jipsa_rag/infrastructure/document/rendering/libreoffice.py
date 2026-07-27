"""이전 LibreOffice import 경로를 위한 Microsoft Office COM 호환 모듈.

기존 브랜치나 외부 코드가 ``LibreOfficeRenderClient``를 import하더라도 soffice를
실행하지 않는다. 실제 구현은 ``MicrosoftOfficeRenderClient``로 위임하며, 신규
코드는 명시적인 Microsoft Office 클래스 이름을 사용해야 한다.
"""

from jipsa_rag.infrastructure.document.rendering.microsoft_office import (
    MicrosoftOfficeRenderClient,
)
from jipsa_rag.infrastructure.document.rendering.models import OfficeVisualRenderResult

LibreOfficeRenderClient = MicrosoftOfficeRenderClient
RenderedOfficeDocument = OfficeVisualRenderResult

__all__ = ["LibreOfficeRenderClient", "RenderedOfficeDocument"]
