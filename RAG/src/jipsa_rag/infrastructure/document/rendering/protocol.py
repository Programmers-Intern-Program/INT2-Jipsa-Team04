"""Office 문서 시각 요소 렌더러의 공통 비동기 계약을 정의한다."""

from pathlib import Path
from typing import Protocol

from jipsa_rag.infrastructure.document.rendering.models import OfficeVisualRenderResult


class OfficeRenderClient(Protocol):
    """PPTX와 XLSX의 렌더링 가능한 시각 요소를 PNG로 반환하는 계약."""

    async def render_pptx_visuals(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        """PowerPoint 차트와 SmartArt를 PNG로 렌더링한다."""

        ...

    async def render_xlsx_charts(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        """Excel 워크시트의 차트를 PNG로 렌더링한다."""

        ...
