"""Microsoft Office 2024 COM 기반 PPTX·XLSX 시각 요소 렌더러를 제공한다.

PowerPoint와 Excel을 문서당 한 번만 실행하고, 문서 안의 모든 대상 객체를 같은
COM 세션에서 순차적으로 PNG로 내보낸다. Office COM은 STA 기반이며 동시 자동화에
취약하므로 프로세스 안의 모든 렌더링을 전역 Lock으로 직렬화한다.
"""

from __future__ import annotations

import asyncio
import gc
import importlib
import logging
import os
import sys
import tempfile
import threading
from collections.abc import Callable
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar, Final, Protocol

from PIL import Image

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import DocumentImageKind
from jipsa_rag.infrastructure.document.rendering.models import (
    OfficeVisualRenderResult,
    RenderedOfficeVisual,
)

logger = logging.getLogger(__name__)

_POWERPOINT_PROG_ID: Final[str] = "PowerPoint.Application"
_EXCEL_PROG_ID: Final[str] = "Excel.Application"
_MSO_AUTOMATION_SECURITY_FORCE_DISABLE: Final[int] = 3
_MSO_TRUE: Final[int] = -1
_MSO_FALSE: Final[int] = 0
_PP_SHAPE_FORMAT_PNG: Final[int] = 2


class MicrosoftOfficeBackend(Protocol):
    """동기 COM 작업을 테스트 대역으로 교체할 수 있게 하는 내부 계약."""

    def render_pptx_visuals(
        self,
        source_path: Path,
        *,
        dpi: int,
    ) -> OfficeVisualRenderResult:
        """PowerPoint 시각 요소를 동기적으로 렌더링한다."""

        ...

    def render_xlsx_charts(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        """Excel 차트를 동기적으로 렌더링한다."""

        ...


class MicrosoftOfficeRenderClient:
    """PowerPoint·Excel COM 백엔드를 비동기 문서 처리 흐름에 연결한다.

    실제 COM 호출은 이벤트 루프를 막지 않도록 작업 스레드에서 수행한다. Office
    응용 프로그램은 동시에 여러 문서를 자동화할 때 대화상자, 전역 상태 및 STA
    충돌이 발생할 수 있으므로 클래스 전역 Lock으로 한 번에 한 문서만 처리한다.
    """

    _render_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        settings: DocumentProcessingSettings,
        *,
        backend: MicrosoftOfficeBackend | None = None,
    ) -> None:
        self._settings = settings
        self._backend = backend or MicrosoftOfficeComBackend(settings)

    async def render_pptx_visuals(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        """PPTX 차트와 SmartArt를 제한 시간 안에서 PNG로 렌더링한다."""

        return await self._render_with_timeout(
            lambda: self._backend.render_pptx_visuals(
                source_path,
                dpi=self._settings.office_render_dpi,
            ),
            document_type="pptx",
        )

    async def render_xlsx_charts(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        """XLSX 차트를 제한 시간 안에서 PNG로 렌더링한다."""

        return await self._render_with_timeout(
            lambda: self._backend.render_xlsx_charts(source_path),
            document_type="xlsx",
        )

    async def _render_with_timeout(
        self,
        operation: Callable[[], OfficeVisualRenderResult],
        *,
        document_type: str,
    ) -> OfficeVisualRenderResult:
        """비활성화·timeout·예외를 문서 부분 실패 결과로 변환한다."""

        if not self._settings.office_rendering_enabled:
            return OfficeVisualRenderResult.unavailable("disabled")

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_serialized, operation),
                timeout=self._settings.office_render_timeout_seconds,
            )
        except TimeoutError:
            # asyncio timeout은 호출자 대기 시간을 제한한다. 이미 실행 중인 COM 호출은
            # finally 블록에서 문서와 응용 프로그램을 닫은 뒤 작업 스레드가 종료된다.
            logger.warning(
                "office_com_render_timed_out",
                extra={
                    "document_type": document_type,
                    "office_render_timeout_seconds": (self._settings.office_render_timeout_seconds),
                },
            )
            return OfficeVisualRenderResult.unavailable("timeout")
        except Exception as error:
            # 원본 경로, 임시 경로 및 Office 오류 메시지는 문서 정보를 포함할 수 있어
            # 기록하지 않는다. 진단에는 문서 형식과 예외 클래스만 남긴다.
            logger.warning(
                "office_com_render_failed",
                extra={
                    "document_type": document_type,
                    "office_error_type": type(error).__name__,
                },
            )
            return OfficeVisualRenderResult.unavailable("render_failed")

    @classmethod
    def _run_serialized(
        cls,
        operation: Callable[[], OfficeVisualRenderResult],
    ) -> OfficeVisualRenderResult:
        with cls._render_lock:
            return operation()


class MicrosoftOfficeComBackend:
    """pywin32로 설치된 PowerPoint와 Excel을 직접 제어하는 동기 백엔드."""

    def __init__(self, settings: DocumentProcessingSettings) -> None:
        self._settings = settings

    def render_pptx_visuals(
        self,
        source_path: Path,
        *,
        dpi: int,
    ) -> OfficeVisualRenderResult:
        """PowerPoint Shape.Export로 차트와 SmartArt를 직접 PNG 출력한다."""

        unavailable_reason = self._environment_unavailable_reason()
        if unavailable_reason is not None:
            return OfficeVisualRenderResult.unavailable(unavailable_reason)

        pythoncom, win32_client = _import_pywin32()
        application: Any | None = None
        presentation: Any | None = None
        pythoncom.CoInitialize()

        try:
            application = win32_client.DispatchEx(_POWERPOINT_PROG_ID)
            _configure_office_application(application)
            presentation = application.Presentations.Open(
                str(source_path.resolve()),
                _MSO_TRUE,
                _MSO_FALSE,
                _MSO_FALSE,
            )

            visuals: list[RenderedOfficeVisual] = []
            with tempfile.TemporaryDirectory(prefix="jipsa-pptx-com-") as directory:
                output_directory = Path(directory)
                slides = presentation.Slides

                for slide_number in range(1, int(slides.Count) + 1):
                    slide = slides.Item(slide_number)
                    shapes = slide.Shapes

                    for shape_index in range(1, int(shapes.Count) + 1):
                        shape = shapes.Item(shape_index)
                        kind = _powerpoint_shape_kind(shape)
                        if kind is None:
                            continue

                        output_path = output_directory / (
                            f"slide-{slide_number}-shape-{shape_index}.png"
                        )
                        if not _export_powerpoint_shape(
                            shape,
                            output_path=output_path,
                            dpi=dpi,
                        ):
                            logger.warning(
                                "powerpoint_shape_export_failed",
                                extra={
                                    "slide_number": slide_number,
                                    "shape_index": shape_index,
                                    "image_kind": kind.value,
                                },
                            )
                            continue

                        visual = _read_rendered_visual(
                            output_path,
                            kind=kind,
                            source_metadata={
                                "slide_number": slide_number,
                                "shape_index": shape_index,
                                "slide_image_index": shape_index,
                                "shape_name": _safe_text(getattr(shape, "Name", "")),
                                "shape_path": (
                                    f"slide:{slide_number}/shape:{shape_index}/"
                                    f"{_shape_kind_path(kind)}"
                                ),
                                "rendered_visual": True,
                                "office_renderer": "microsoft_office_com",
                            },
                        )
                        if visual is not None:
                            visuals.append(visual)

            return OfficeVisualRenderResult(
                visuals=tuple(visuals),
                renderer_available=True,
            )
        except Exception as error:
            logger.warning(
                "powerpoint_com_unavailable",
                extra={"office_error_type": type(error).__name__},
            )
            return OfficeVisualRenderResult.unavailable("powerpoint_unavailable")
        finally:
            _close_powerpoint(presentation, application)
            pythoncom.CoUninitialize()
            gc.collect()

    def render_xlsx_charts(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        """Excel Chart.Export로 모든 워크시트 차트를 직접 PNG 출력한다."""

        unavailable_reason = self._environment_unavailable_reason()
        if unavailable_reason is not None:
            return OfficeVisualRenderResult.unavailable(unavailable_reason)

        pythoncom, win32_client = _import_pywin32()
        application: Any | None = None
        workbook: Any | None = None
        pythoncom.CoInitialize()

        try:
            application = win32_client.DispatchEx(_EXCEL_PROG_ID)
            _configure_office_application(application)
            application.AskToUpdateLinks = False
            application.EnableEvents = False
            application.ScreenUpdating = False

            workbook = application.Workbooks.Open(
                Filename=str(source_path.resolve()),
                UpdateLinks=0,
                ReadOnly=True,
                IgnoreReadOnlyRecommended=True,
                AddToMru=False,
            )

            visuals: list[RenderedOfficeVisual] = []
            with tempfile.TemporaryDirectory(prefix="jipsa-xlsx-com-") as directory:
                output_directory = Path(directory)
                worksheets = workbook.Worksheets

                for sheet_index in range(1, int(worksheets.Count) + 1):
                    worksheet = worksheets.Item(sheet_index)
                    chart_objects = worksheet.ChartObjects()

                    for chart_index in range(1, int(chart_objects.Count) + 1):
                        chart_object = chart_objects.Item(chart_index)
                        output_path = output_directory / (
                            f"sheet-{sheet_index}-chart-{chart_index}.png"
                        )
                        exported = bool(
                            chart_object.Chart.Export(
                                str(output_path),
                                "PNG",
                                False,
                            )
                        )
                        if not exported or not output_path.is_file():
                            logger.warning(
                                "excel_chart_export_failed",
                                extra={
                                    "sheet_index": sheet_index,
                                    "chart_index": chart_index,
                                },
                            )
                            continue

                        sheet_name = _safe_text(getattr(worksheet, "Name", ""))
                        anchor_cell = _excel_cell_address(
                            getattr(chart_object, "TopLeftCell", None)
                        )
                        end_cell = _excel_cell_address(
                            getattr(chart_object, "BottomRightCell", None)
                        )
                        cell_range = (
                            f"{anchor_cell}:{end_cell}" if end_cell != anchor_cell else anchor_cell
                        )
                        visual = _read_rendered_visual(
                            output_path,
                            kind=DocumentImageKind.XLSX_CHART_RENDER,
                            source_metadata={
                                "sheet_index": sheet_index,
                                "sheet_name": sheet_name,
                                "chart_index": chart_index,
                                "sheet_image_index": chart_index,
                                "anchor_cell": anchor_cell,
                                "cell_range": cell_range,
                                "shape_name": _safe_text(getattr(chart_object, "Name", "")),
                                "shape_path": (
                                    f"sheet:{sheet_name}/chart:{chart_index}/anchor:{anchor_cell}"
                                ),
                                "rendered_visual": True,
                                "office_renderer": "microsoft_office_com",
                            },
                        )
                        if visual is not None:
                            visuals.append(visual)

            return OfficeVisualRenderResult(
                visuals=tuple(visuals),
                renderer_available=True,
            )
        except Exception as error:
            logger.warning(
                "excel_com_unavailable",
                extra={"office_error_type": type(error).__name__},
            )
            return OfficeVisualRenderResult.unavailable("excel_unavailable")
        finally:
            _close_excel(workbook, application)
            pythoncom.CoUninitialize()
            gc.collect()

    def _environment_unavailable_reason(self) -> str | None:
        """Windows 대화형 사용자 세션에서만 COM 자동화를 허용한다."""

        if sys.platform != "win32":
            return "windows_required"
        if self._settings.office_com_require_interactive_session and not _is_interactive_session():
            return "interactive_session_required"
        return None


def _import_pywin32() -> tuple[Any, Any]:
    """Windows 전용 의존성을 지연 import하여 비 Windows 테스트를 유지한다."""

    try:
        pythoncom = importlib.import_module("pythoncom")
        win32_client = importlib.import_module("win32com.client")
    except ImportError as error:
        raise RuntimeError("pywin32 is required for Microsoft Office COM rendering.") from error
    return pythoncom, win32_client


def _configure_office_application(application: Any) -> None:
    """매크로·경고 대화상자를 차단하고 사용자 화면 노출을 최소화한다."""

    _safe_setattr(application, "DisplayAlerts", False)
    _safe_setattr(
        application,
        "AutomationSecurity",
        _MSO_AUTOMATION_SECURITY_FORCE_DISABLE,
    )


def _powerpoint_shape_kind(shape: Any) -> DocumentImageKind | None:
    """PowerPoint COM Shape가 차트 또는 SmartArt인지 안전하게 판정한다."""

    if _com_true(getattr(shape, "HasChart", False)):
        return DocumentImageKind.PPTX_CHART_RENDER
    if _com_true(getattr(shape, "HasSmartArt", False)):
        return DocumentImageKind.PPTX_SMARTART_RENDER
    return None


def _export_powerpoint_shape(
    shape: Any,
    *,
    output_path: Path,
    dpi: int,
) -> bool:
    """Shape.Export를 우선 사용하고 차트는 Chart.Export로 한 번 더 시도한다."""

    try:
        width = max(round(float(shape.Width) * dpi / 72.0), 1)
        height = max(round(float(shape.Height) * dpi / 72.0), 1)
        shape.Export(
            str(output_path),
            _PP_SHAPE_FORMAT_PNG,
            width,
            height,
        )
    except Exception:
        try:
            if not _com_true(getattr(shape, "HasChart", False)):
                return False
            exported = bool(shape.Chart.Export(str(output_path), "PNG", False))
            if not exported:
                return False
        except Exception:
            return False

    return output_path.is_file() and output_path.stat().st_size > 0


def _read_rendered_visual(
    output_path: Path,
    *,
    kind: DocumentImageKind,
    source_metadata: dict[str, str | int | float | bool | None],
) -> RenderedOfficeVisual | None:
    """PNG 바이트와 실제 픽셀 크기를 읽고 임시 경로는 결과에서 제거한다."""

    try:
        content = output_path.read_bytes()
        with Image.open(BytesIO(content)) as image:
            width_px, height_px = image.size
    except (OSError, ValueError):
        return None

    if not content or width_px <= 0 or height_px <= 0:
        return None

    return RenderedOfficeVisual(
        kind=kind,
        content=content,
        width_px=width_px,
        height_px=height_px,
        source_metadata=source_metadata,
    )


def _shape_kind_path(kind: DocumentImageKind) -> str:
    if kind is DocumentImageKind.PPTX_CHART_RENDER:
        return "chart"
    return "smartart"


def _excel_cell_address(cell: Any | None) -> str:
    if cell is None:
        return "A1"
    try:
        address = cell.Address(False, False)
    except Exception:
        return "A1"
    normalized = _safe_text(address).replace("$", "")
    return normalized or "A1"


def _close_powerpoint(presentation: Any | None, application: Any | None) -> None:
    if presentation is not None:
        with suppress(Exception):
            presentation.Close()
    if application is not None:
        with suppress(Exception):
            application.Quit()


def _close_excel(workbook: Any | None, application: Any | None) -> None:
    if workbook is not None:
        with suppress(Exception):
            workbook.Close(SaveChanges=False)
    if application is not None:
        with suppress(Exception):
            application.Quit()


def _safe_setattr(target: Any, name: str, value: object) -> None:
    # Office 버전별로 제공되지 않는 선택적 속성은 렌더링 자체를 막지 않는다.
    with suppress(Exception):
        setattr(target, name, value)


def _safe_text(value: object) -> str:
    try:
        return str(value).strip()
    except Exception:
        return ""


def _com_true(value: object) -> bool:
    """COM의 True(-1)와 Python bool을 모두 참으로 해석한다."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return False


def _is_interactive_session() -> bool:
    """Windows 서비스·SYSTEM 계정을 제외하고 로컬/RDP 로그인 세션을 허용한다."""

    username = os.environ.get("USERNAME", "").strip().lower()
    session_name = os.environ.get("SESSIONNAME", "").strip().lower()
    if username in {"system", "local service", "network service"}:
        return False
    return session_name != "services"
