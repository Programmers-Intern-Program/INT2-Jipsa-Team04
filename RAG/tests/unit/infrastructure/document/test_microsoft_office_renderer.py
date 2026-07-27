"""Microsoft Office 렌더러의 프로세스 격리와 메타데이터 계약을 검증한다."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import DocumentImageKind
from jipsa_rag.infrastructure.document.images.xlsx import (
    XlsxImageExtractor,
    _ChartContext,
)
from jipsa_rag.infrastructure.document.rendering import microsoft_office
from jipsa_rag.infrastructure.document.rendering.microsoft_office import (
    MicrosoftOfficeProcessBackend,
    MicrosoftOfficeRenderClient,
    _excel_cell_address,
    _powerpoint_shape_kind,
    _write_worker_result,
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


class _FakeWorkerProcess:
    """실제 subprocess 없이 worker 종료 상태를 제어하는 테스트 대역."""

    def __init__(
        self,
        *,
        returncode: int,
        timeout: bool = False,
    ) -> None:
        self.returncode: int | None = returncode
        self.timeout = timeout
        self.killed = False

    def communicate(
        self,
        input: str | None = None,
        timeout: float | None = None,
    ) -> tuple[str, str]:
        """실제 ``Popen[str].communicate``와 같은 호출 계약을 제공한다."""

        del input

        if self.timeout:
            # TimeoutExpired.timeout은 float을 요구한다. 테스트가 timeout=None으로
            # 호출되더라도 명확한 숫자 값을 사용해 타입 계약을 지킨다.
            effective_timeout = timeout if timeout is not None else 0.0
            raise subprocess.TimeoutExpired(
                cmd="office-worker",
                timeout=effective_timeout,
            )

        return "", ""

    def kill(self) -> None:
        self.killed = True
        self.returncode = 1

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode or 0


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


def test_process_backend_accepts_completed_manifest_after_native_worker_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COM 정리 중 worker가 종료돼도 미리 저장한 정상 결과를 부모가 사용한다."""

    source_path = tmp_path / "sample.pptx"
    source_path.write_bytes(b"test-pptx-placeholder")

    def launcher(
        command: list[str],
        environment: Mapping[str, str],
    ) -> _FakeWorkerProcess:
        del environment
        request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        output_directory = Path(request["output_directory"])
        response_path = Path(request["response_path"])

        image_buffer = BytesIO()
        Image.new("RGB", (80, 40), "white").save(image_buffer, format="PNG")
        _write_worker_result(
            response_path=response_path,
            output_directory=output_directory,
            result=OfficeVisualRenderResult(
                visuals=(
                    RenderedOfficeVisual(
                        kind=DocumentImageKind.PPTX_CHART_RENDER,
                        content=image_buffer.getvalue(),
                        width_px=80,
                        height_px=40,
                        source_metadata={
                            "slide_number": 1,
                            "shape_index": 1,
                        },
                    ),
                ),
                renderer_available=True,
            ),
        )

        # 0x80010108은 이전 실제 환경에서 확인된 RPC_E_DISCONNECTED 코드다.
        return _FakeWorkerProcess(returncode=0x80010108)

    monkeypatch.setattr(
        microsoft_office,
        "_environment_unavailable_reason",
        lambda _: None,
    )
    monkeypatch.setattr(
        microsoft_office,
        "_snapshot_office_process_ids",
        lambda _: frozenset(),
    )
    monkeypatch.setattr(
        microsoft_office,
        "_cleanup_worker_office_process",
        lambda **_: None,
    )

    settings = DocumentProcessingSettings(
        office_rendering_enabled=True,
        office_render_timeout_seconds=10,
        _env_file=None,
    )
    backend = MicrosoftOfficeProcessBackend(settings, launcher=launcher)

    result = backend.render_pptx_visuals(source_path, dpi=160)

    assert result.renderer_available is True
    assert len(result.visuals) == 1
    assert result.visuals[0].width_px == 80
    assert result.visuals[0].height_px == 40


def test_process_backend_converts_worker_timeout_to_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """응답 manifest가 없는 worker timeout은 부모 프로세스 실패로 확산하지 않는다."""

    source_path = tmp_path / "sample.xlsx"
    source_path.write_bytes(b"test-xlsx-placeholder")
    fake_process = _FakeWorkerProcess(returncode=0, timeout=True)

    def launcher(
        command: list[str],
        environment: Mapping[str, str],
    ) -> _FakeWorkerProcess:
        del command, environment
        return fake_process

    monkeypatch.setattr(
        microsoft_office,
        "_environment_unavailable_reason",
        lambda _: None,
    )
    monkeypatch.setattr(
        microsoft_office,
        "_snapshot_office_process_ids",
        lambda _: frozenset(),
    )
    monkeypatch.setattr(
        microsoft_office,
        "_cleanup_worker_office_process",
        lambda **_: None,
    )

    settings = DocumentProcessingSettings(
        office_rendering_enabled=True,
        office_render_timeout_seconds=1,
        _env_file=None,
    )
    backend = MicrosoftOfficeProcessBackend(settings, launcher=launcher)

    result = backend.render_xlsx_charts(source_path)

    assert fake_process.killed is True
    assert result.renderer_available is False
    assert result.failure_reason == "timeout"


def test_powerpoint_shape_kind_detects_chart_and_smartart() -> None:
    """COM True(-1) 값을 차트와 SmartArt 종류로 안전하게 변환한다."""

    chart_shape = _Object(HasChart=-1, HasSmartArt=0)
    smartart_shape = _Object(HasChart=0, HasSmartArt=-1)
    plain_shape = _Object(HasChart=0, HasSmartArt=0)

    assert _powerpoint_shape_kind(chart_shape) is DocumentImageKind.PPTX_CHART_RENDER
    assert _powerpoint_shape_kind(smartart_shape) is DocumentImageKind.PPTX_SMARTART_RENDER
    assert _powerpoint_shape_kind(plain_shape) is None


def test_excel_cell_address_supports_method_and_property_bindings() -> None:
    """Excel 주소가 메서드 또는 문자열 속성으로 노출돼도 동일하게 정규화한다."""

    method_cell = _Cell("$D$15")
    property_cell = _PropertyCell("$F$20")

    assert _excel_cell_address(method_cell) == "D15"
    assert _excel_cell_address(property_cell) == "F20"
    assert _excel_cell_address(None) == ""


def test_xlsx_ooxml_anchor_overrides_incorrect_com_fallback() -> None:
    """COM이 A1을 반환해도 원본 OOXML chart anchor인 D15를 최종 위치로 사용한다."""

    settings = DocumentProcessingSettings(
        office_rendering_enabled=True,
        image_decorative_filter_enabled=False,
        _env_file=None,
    )
    extractor = XlsxImageExtractor(
        settings,
        MicrosoftOfficeRenderClient(settings, backend=_FakeBackend()),
    )
    rendered_result = OfficeVisualRenderResult(
        visuals=(
            RenderedOfficeVisual(
                kind=DocumentImageKind.XLSX_CHART_RENDER,
                content=b"png-content",
                width_px=640,
                height_px=360,
                source_metadata={
                    "sheet_index": 2,
                    "sheet_name": "ChartTarget",
                    "chart_index": 1,
                    "anchor_cell": "A1",
                    "cell_range": "A1",
                    "shape_path": "sheet:ChartTarget/chart:1/anchor:A1",
                },
            ),
        ),
        renderer_available=True,
    )
    chart_contexts = (
        _ChartContext(
            sheet_index=2,
            sheet_name="ChartTarget",
            chart_index=1,
            anchor_cell="D15",
            cell_range="C14:N36",
            context="Category | Value",
        ),
    )

    images = extractor._build_rendered_images(
        rendered_result=rendered_result,
        chart_contexts=chart_contexts,
        existing_images=(),
        maximum_count=10,
        image_index_offset=0,
    )

    assert len(images) == 1
    metadata = images[0].source_metadata
    assert metadata["anchor_cell"] == "D15"
    assert metadata["cell_range"] == "C14:N36"
    assert metadata["office_anchor_cell"] == "A1"
    assert metadata["shape_path"] == "sheet:ChartTarget/chart:1/anchor:D15"


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


class _PropertyCell:
    def __init__(self, address: str) -> None:
        self.Address = address
