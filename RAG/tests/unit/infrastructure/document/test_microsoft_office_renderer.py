"""Microsoft Office COM 렌더러의 비동기 경계와 메타데이터 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import DocumentImageKind
from jipsa_rag.infrastructure.document.rendering.microsoft_office import (
    MicrosoftOfficeRenderClient,
    _excel_cell_address,
    _powerpoint_shape_kind,
)
from jipsa_rag.infrastructure.document.rendering.models import (
    OfficeVisualRenderResult,
    RenderedOfficeVisual,
)


class _FakeBackend:
    """실제 Office를 실행하지 않고 동기 백엔드 호출을 기록한다."""

    def __init__(self) -> None:
        self.pptx_calls: list[tuple[Path, int]] = []
        self.xlsx_calls: list[Path] = []

    def render_pptx_visuals(
        self,
        source_path: Path,
        *,
        dpi: int,
    ) -> OfficeVisualRenderResult:
        self.pptx_calls.append((source_path, dpi))
        return OfficeVisualRenderResult(
            visuals=(
                RenderedOfficeVisual(
                    kind=DocumentImageKind.PPTX_CHART_RENDER,
                    content=b"png-content",
                    width_px=640,
                    height_px=360,
                    source_metadata={"slide_number": 1, "shape_index": 2},
                ),
            ),
            renderer_available=True,
        )

    def render_xlsx_charts(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        self.xlsx_calls.append(source_path)
        return OfficeVisualRenderResult(
            visuals=(),
            renderer_available=True,
        )


@pytest.mark.asyncio
async def test_render_client_runs_backend_once_with_configured_dpi(
    tmp_path: Path,
) -> None:
    """PPTX 한 문서는 한 백엔드 호출로 처리하고 DPI 설정을 전달한다."""

    source_path = tmp_path / "sample.pptx"
    backend = _FakeBackend()
    settings = DocumentProcessingSettings(
        office_rendering_enabled=True,
        office_render_dpi=180,
        _env_file=None,
    )
    client = MicrosoftOfficeRenderClient(settings, backend=backend)

    result = await client.render_pptx_visuals(source_path)

    assert backend.pptx_calls == [(source_path, 180)]
    assert result.renderer_available is True
    assert result.visuals[0].kind is DocumentImageKind.PPTX_CHART_RENDER


@pytest.mark.asyncio
async def test_render_client_does_not_call_backend_when_disabled(
    tmp_path: Path,
) -> None:
    """Office 렌더링 비활성화 시 외부 응용 프로그램 경계를 호출하지 않는다."""

    backend = _FakeBackend()
    settings = DocumentProcessingSettings(
        office_rendering_enabled=False,
        _env_file=None,
    )
    client = MicrosoftOfficeRenderClient(settings, backend=backend)

    result = await client.render_xlsx_charts(tmp_path / "sample.xlsx")

    assert backend.xlsx_calls == []
    assert result.renderer_available is False
    assert result.failure_reason == "disabled"


def test_powerpoint_shape_kind_detects_chart_and_smartart() -> None:
    """COM True(-1) 값을 차트와 SmartArt 종류로 안전하게 변환한다."""

    chart_shape = _Object(HasChart=-1, HasSmartArt=0)
    smartart_shape = _Object(HasChart=0, HasSmartArt=-1)
    plain_shape = _Object(HasChart=0, HasSmartArt=0)

    assert _powerpoint_shape_kind(chart_shape) is DocumentImageKind.PPTX_CHART_RENDER
    assert _powerpoint_shape_kind(smartart_shape) is DocumentImageKind.PPTX_SMARTART_RENDER
    assert _powerpoint_shape_kind(plain_shape) is None


def test_excel_cell_address_removes_absolute_markers() -> None:
    """Excel COM의 절대 셀 주소를 검색 메타데이터용 A1 주소로 정규화한다."""

    cell = _Cell("$D$15")

    assert _excel_cell_address(cell) == "D15"
    assert _excel_cell_address(None) == "A1"


class _Object:
    def __init__(self, **attributes: Any) -> None:
        for name, value in attributes.items():
            setattr(self, name, value)


class _Cell:
    def __init__(self, address: str) -> None:
        self._address = address

    def Address(self, row_absolute: bool, column_absolute: bool) -> str:
        del row_absolute, column_absolute
        return self._address
