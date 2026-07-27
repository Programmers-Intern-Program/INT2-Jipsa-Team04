"""Microsoft Office 2024 COM 기반 PPTX·XLSX 시각 요소 렌더러를 제공한다.

PowerPoint와 Excel COM 자동화는 Windows 네이티브 COM 서버와 pywin32 프록시의
종료 순서에 따라 ``RPC_E_DISCONNECTED(0x80010108)`` 같은 프로세스 수준 예외를
발생시킬 수 있다. 이런 예외는 일반 Python ``try/except``로 안정적으로 복구할 수
없으므로 실제 COM 호출은 전용 자식 프로세스에 격리한다.

부모 RAG 프로세스는 자식 프로세스가 만든 검증된 PNG와 JSON manifest만 읽는다.
자식 프로세스가 결과 저장 이후 COM 정리 과정에서 비정상 종료하더라도 부모 프로세스,
pytest 및 API 서버는 계속 동작하며 완성된 결과만 안전하게 사용할 수 있다.
"""

from __future__ import annotations

import asyncio
import csv
import ctypes
import importlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar, Final, Protocol, cast

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
_WORKER_MODULE: Final[str] = "jipsa_rag.infrastructure.document.rendering.microsoft_office_worker"
_WORKER_REQUEST_NAME: Final[str] = "request.json"
_WORKER_RESPONSE_NAME: Final[str] = "response.json"
_WORKER_OFFICE_PID_NAME: Final[str] = "office.pid"
_WORKER_TIMEOUT_MARGIN_SECONDS: Final[float] = 3.0
_OFFICE_PROCESS_EXIT_GRACE_SECONDS: Final[float] = 5.0
_WINDOWS_NO_WINDOW: Final[int] = 0x08000000


class MicrosoftOfficeBackend(Protocol):
    """동기 Office 작업을 실제 프로세스 또는 테스트 대역으로 교체하는 계약."""

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


class OfficeWorkerProcess(Protocol):
    """subprocess.Popen의 테스트 가능한 최소 인터페이스."""

    returncode: int | None

    def communicate(
        self,
        input: str | None = None,
        timeout: float | None = None,
    ) -> tuple[str, str]:
        """자식 프로세스 종료를 기다리고 텍스트 stdout, stderr를 반환한다.

        ``subprocess.Popen[str]``의 실제 ``communicate`` 시그니처와 동일하게
        입력 문자열과 timeout을 모두 선언한다. 현재 worker에는 입력을 전달하지
        않지만, 프로토콜이 표준 라이브러리 구현과 구조적으로 호환돼야 실제
        ``Popen[str]`` 객체를 안전하게 반환할 수 있다.
        """

        ...

    def kill(self) -> None:
        """자식 프로세스를 강제 종료한다."""

        ...

    def wait(self, timeout: float | None = None) -> int:
        """자식 프로세스 종료를 기다린다."""

        ...


OfficeWorkerLauncher = Callable[[list[str], Mapping[str, str]], OfficeWorkerProcess]
OfficeResultSink = Callable[[OfficeVisualRenderResult], None]
OfficePidSink = Callable[[int], None]


class MicrosoftOfficeRenderClient:
    """Office 렌더링 백엔드를 비동기 문서 처리 흐름에 연결한다.

    기본 백엔드는 실제 COM을 현재 RAG 프로세스에서 실행하지 않는다. 대신 전용 Python
    자식 프로세스에서 한 문서를 처리하고, 부모 프로세스는 결과 파일만 읽는다. 전역 Lock은
    여러 인제스트 요청이 PowerPoint 또는 Excel을 동시에 자동화하지 못하도록 제한한다.
    """

    _render_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        settings: DocumentProcessingSettings,
        *,
        backend: MicrosoftOfficeBackend | None = None,
    ) -> None:
        self._settings = settings
        self._backend = backend or MicrosoftOfficeProcessBackend(settings)

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

        # 기본 프로세스 백엔드는 내부에서 자식 프로세스 hard timeout을 처리한다.
        # 외부 wait_for는 테스트 대역이나 예상하지 못한 상위 정지를 한 번 더 제한한다.
        outer_timeout = (
            self._settings.office_render_timeout_seconds + _WORKER_TIMEOUT_MARGIN_SECONDS
        )

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run_serialized, operation),
                timeout=outer_timeout,
            )
        except TimeoutError:
            logger.warning(
                "office_render_timed_out",
                extra={
                    "document_type": document_type,
                    "office_render_timeout_seconds": (self._settings.office_render_timeout_seconds),
                },
            )
            return OfficeVisualRenderResult.unavailable("timeout")
        except Exception as error:
            # 원본 경로, 임시 경로와 Office 오류 원문은 사용자 문서 정보를 포함할 수 있다.
            logger.warning(
                "office_render_failed",
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


class MicrosoftOfficeProcessBackend:
    """실제 Office COM을 격리된 자식 Python 프로세스에서 실행한다.

    Office 또는 pywin32가 네이티브 예외로 자식 프로세스를 종료해도 부모 프로세스는
    영향을 받지 않는다. 자식은 COM 정리 전에 결과 manifest를 원자적으로 저장한다.
    따라서 렌더링이 완료된 뒤 정리 단계에서만 비정상 종료한 경우에는 완성된 결과를
    사용하고, 새로 생성된 Office 프로세스가 남으면 해당 PID만 선택적으로 종료한다.
    """

    def __init__(
        self,
        settings: DocumentProcessingSettings,
        *,
        launcher: OfficeWorkerLauncher | None = None,
    ) -> None:
        self._settings = settings
        self._launcher = launcher or _launch_office_worker

    def render_pptx_visuals(
        self,
        source_path: Path,
        *,
        dpi: int,
    ) -> OfficeVisualRenderResult:
        """PowerPoint 전용 worker를 실행하고 저장된 PNG 결과를 읽는다."""

        return self._run_worker(
            operation="pptx",
            source_path=source_path,
            dpi=dpi,
        )

    def render_xlsx_charts(
        self,
        source_path: Path,
    ) -> OfficeVisualRenderResult:
        """Excel 전용 worker를 실행하고 저장된 PNG 결과를 읽는다."""

        return self._run_worker(
            operation="xlsx",
            source_path=source_path,
            dpi=None,
        )

    def _run_worker(
        self,
        *,
        operation: str,
        source_path: Path,
        dpi: int | None,
    ) -> OfficeVisualRenderResult:
        """worker 종료 코드와 무관하게 완성된 manifest를 우선 검증한다."""

        unavailable_reason = _environment_unavailable_reason(self._settings)
        if unavailable_reason is not None:
            return OfficeVisualRenderResult.unavailable(unavailable_reason)

        if not source_path.is_file():
            return OfficeVisualRenderResult.unavailable("source_not_found")

        preexisting_office_pids = _snapshot_office_process_ids(operation)

        with tempfile.TemporaryDirectory(prefix="jipsa-office-worker-") as directory:
            working_directory = Path(directory)
            request_path = working_directory / _WORKER_REQUEST_NAME
            response_path = working_directory / _WORKER_RESPONSE_NAME
            office_pid_path = working_directory / _WORKER_OFFICE_PID_NAME

            request_payload: dict[str, object] = {
                "operation": operation,
                "source_path": str(source_path.resolve()),
                "output_directory": str(working_directory.resolve()),
                "response_path": str(response_path.resolve()),
                "office_pid_path": str(office_pid_path.resolve()),
                "dpi": dpi,
                "require_interactive_session": (
                    self._settings.office_com_require_interactive_session
                ),
            }
            _write_json_atomic(request_path, request_payload)

            command = [sys.executable, "-m", _WORKER_MODULE, str(request_path)]
            worker_environment = dict(os.environ)

            # 자식 프로세스의 네이티브 예외는 부모가 종료 코드로 처리한다. 자식의
            # faulthandler stack을 부모 stderr로 노출하지 않아 테스트 프로세스가
            # PowerShell NativeCommandError로 중단되는 문제도 방지한다.
            worker_environment.pop("PYTHONFAULTHANDLER", None)
            worker_environment["PYTHONUTF8"] = "1"
            worker_environment["PYTHONIOENCODING"] = "utf-8"

            process = self._launcher(command, worker_environment)
            timed_out = False

            try:
                process.communicate(
                    timeout=self._settings.office_render_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_worker_process(process)

            office_pid = _read_positive_integer(office_pid_path)
            worker_exit_code = process.returncode

            result = _load_worker_result(
                response_path=response_path,
                output_directory=working_directory,
            )

            # DispatchEx가 만든 신규 Office PID만 정리한다. 테스트 시작 전부터 존재한
            # 사용자의 PowerPoint·Excel 프로세스는 절대로 강제 종료하지 않는다.
            _cleanup_worker_office_process(
                office_pid=office_pid,
                preexisting_pids=preexisting_office_pids,
                force=timed_out or worker_exit_code not in {0, None},
            )

            if result is not None:
                if worker_exit_code not in {0, None}:
                    logger.warning(
                        "office_worker_cleanup_failed_after_result",
                        extra={
                            "document_type": operation,
                            "worker_exit_code": worker_exit_code,
                        },
                    )
                return result

            if timed_out:
                return OfficeVisualRenderResult.unavailable("timeout")

            logger.warning(
                "office_worker_failed_without_result",
                extra={
                    "document_type": operation,
                    "worker_exit_code": worker_exit_code,
                },
            )
            return OfficeVisualRenderResult.unavailable("worker_failed")


class MicrosoftOfficeComBackend:
    """pywin32로 PowerPoint와 Excel을 직접 제어하는 worker 전용 백엔드.

    이 클래스는 기본 RAG 프로세스에서 직접 사용하지 않는다. worker는 렌더링 결과를
    COM 종료 전에 ``result_sink``로 저장한다. 이후 정리 단계에서 네이티브 예외가
    발생하더라도 부모 프로세스가 완성된 manifest를 사용할 수 있다.
    """

    def __init__(self, settings: DocumentProcessingSettings) -> None:
        self._settings = settings

    def render_pptx_visuals(
        self,
        source_path: Path,
        *,
        dpi: int,
        result_sink: OfficeResultSink | None = None,
        office_pid_sink: OfficePidSink | None = None,
    ) -> OfficeVisualRenderResult:
        """PowerPoint Shape.Export로 차트와 SmartArt를 직접 PNG 출력한다."""

        unavailable_reason = _environment_unavailable_reason(self._settings)
        if unavailable_reason is not None:
            result = OfficeVisualRenderResult.unavailable(unavailable_reason)
            _emit_result(result, result_sink)
            return result

        pythoncom, win32_client = _import_pywin32()
        application: Any | None = None
        presentation: Any | None = None
        slides: Any | None = None
        slide: Any | None = None
        shapes: Any | None = None
        shape: Any | None = None
        pythoncom.CoInitialize()

        try:
            application = win32_client.DispatchEx(_POWERPOINT_PROG_ID)
            _emit_office_pid(application, office_pid_sink)
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
                            shape = None
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
                            shape = None
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
                        shape = None

                    shapes = None
                    slide = None

                slides = None

            result = OfficeVisualRenderResult(
                visuals=tuple(visuals),
                renderer_available=True,
            )
            _emit_result(result, result_sink)
            return result
        except Exception as error:
            logger.warning(
                "powerpoint_com_unavailable",
                extra={"office_error_type": type(error).__name__},
            )
            result = OfficeVisualRenderResult.unavailable("powerpoint_unavailable")
            _emit_result(result, result_sink)
            return result
        finally:
            # CPython 참조 카운팅으로 자식 프록시를 부모 객체보다 먼저 해제한다.
            # gc.collect()는 연결이 끊긴 COM 프록시의 소멸자를 강제로 실행할 수 있으므로
            # worker에서는 사용하지 않고, 프로세스 마지막에 os._exit()로 종료한다.
            shape = None
            shapes = None
            slide = None
            slides = None
            _close_powerpoint(presentation, application)
            presentation = None
            application = None
            pythoncom.CoUninitialize()

    def render_xlsx_charts(
        self,
        source_path: Path,
        *,
        result_sink: OfficeResultSink | None = None,
        office_pid_sink: OfficePidSink | None = None,
    ) -> OfficeVisualRenderResult:
        """Excel Chart.Export로 모든 워크시트 차트를 직접 PNG 출력한다."""

        unavailable_reason = _environment_unavailable_reason(self._settings)
        if unavailable_reason is not None:
            result = OfficeVisualRenderResult.unavailable(unavailable_reason)
            _emit_result(result, result_sink)
            return result

        pythoncom, win32_client = _import_pywin32()
        application: Any | None = None
        workbook: Any | None = None
        worksheets: Any | None = None
        worksheet: Any | None = None
        chart_objects: Any | None = None
        chart_object: Any | None = None
        chart: Any | None = None
        pythoncom.CoInitialize()

        try:
            application = win32_client.DispatchEx(_EXCEL_PROG_ID)
            _emit_office_pid(application, office_pid_sink)
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
                        chart = chart_object.Chart
                        output_path = output_directory / (
                            f"sheet-{sheet_index}-chart-{chart_index}.png"
                        )
                        exported = bool(
                            chart.Export(
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
                            chart = None
                            chart_object = None
                            continue

                        sheet_name = _safe_text(getattr(worksheet, "Name", ""))
                        anchor_cell = _excel_cell_address(
                            getattr(chart_object, "TopLeftCell", None)
                        )
                        end_cell = _excel_cell_address(
                            getattr(chart_object, "BottomRightCell", None)
                        )
                        cell_range = (
                            f"{anchor_cell}:{end_cell}"
                            if anchor_cell and end_cell and end_cell != anchor_cell
                            else anchor_cell
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

                        chart = None
                        chart_object = None

                    chart_objects = None
                    worksheet = None

                worksheets = None

            result = OfficeVisualRenderResult(
                visuals=tuple(visuals),
                renderer_available=True,
            )
            _emit_result(result, result_sink)
            return result
        except Exception as error:
            logger.warning(
                "excel_com_unavailable",
                extra={"office_error_type": type(error).__name__},
            )
            result = OfficeVisualRenderResult.unavailable("excel_unavailable")
            _emit_result(result, result_sink)
            return result
        finally:
            chart = None
            chart_object = None
            chart_objects = None
            worksheet = None
            worksheets = None
            _close_excel(workbook, application)
            workbook = None
            application = None
            pythoncom.CoUninitialize()


def _launch_office_worker(
    command: list[str],
    environment: Mapping[str, str],
) -> OfficeWorkerProcess:
    """콘솔 창 없이 Office worker Python 프로세스를 시작한다."""

    creation_flags = _WINDOWS_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(environment),
        creationflags=creation_flags,
    )


def _kill_worker_process(process: OfficeWorkerProcess) -> None:
    """timeout worker를 종료하고 반환 코드가 확정될 때까지 짧게 기다린다."""

    with suppress(Exception):
        process.kill()
    with suppress(Exception):
        process.wait(timeout=5.0)


def _write_worker_result(
    *,
    response_path: Path,
    output_directory: Path,
    result: OfficeVisualRenderResult,
) -> None:
    """렌더 결과 PNG와 manifest를 원자적으로 저장한다.

    manifest는 COM 종료 전에 저장된다. JSON이 완전히 교체된 뒤에만 부모 프로세스가
    결과를 읽으므로 worker가 정리 도중 종료돼도 부분 JSON을 성공으로 오인하지 않는다.
    """

    output_directory.mkdir(parents=True, exist_ok=True)
    visual_payloads: list[dict[str, object]] = []

    for index, visual in enumerate(result.visuals, start=1):
        file_name = f"visual-{index:04d}-{visual.kind.value}.png"
        image_path = output_directory / file_name
        image_path.write_bytes(visual.content)
        visual_payloads.append(
            {
                "kind": visual.kind.value,
                "file_name": file_name,
                "width_px": visual.width_px,
                "height_px": visual.height_px,
                "source_metadata": dict(visual.source_metadata),
            }
        )

    payload: dict[str, object] = {
        "renderer_available": result.renderer_available,
        "failure_reason": result.failure_reason,
        "visuals": visual_payloads,
    }
    _write_json_atomic(response_path, payload)


def _load_worker_result(
    *,
    response_path: Path,
    output_directory: Path,
) -> OfficeVisualRenderResult | None:
    """worker manifest와 PNG 경로를 검증해 메모리 결과로 복원한다."""

    try:
        payload_object = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not isinstance(payload_object, dict):
        return None
    payload = cast(dict[str, object], payload_object)

    renderer_available = payload.get("renderer_available")
    if not isinstance(renderer_available, bool):
        return None

    failure_reason_object = payload.get("failure_reason")
    if failure_reason_object is not None and not isinstance(
        failure_reason_object,
        str,
    ):
        return None
    # 위 조건문에서 None 또는 str로 타입을 좁혔으므로 추가 cast가 필요하지 않다.
    failure_reason = failure_reason_object

    visuals_object = payload.get("visuals")
    if not isinstance(visuals_object, list):
        return None

    output_root = output_directory.resolve()
    visuals: list[RenderedOfficeVisual] = []

    for visual_object in cast(list[object], visuals_object):
        if not isinstance(visual_object, dict):
            return None
        visual_payload = cast(dict[str, object], visual_object)

        kind_value = visual_payload.get("kind")
        file_name = visual_payload.get("file_name")
        width_px = _optional_positive_int(visual_payload.get("width_px"))
        height_px = _optional_positive_int(visual_payload.get("height_px"))
        metadata_object = visual_payload.get("source_metadata")

        if not isinstance(kind_value, str) or not isinstance(file_name, str):
            return None
        if Path(file_name).name != file_name:
            return None
        if not isinstance(metadata_object, dict):
            return None
        if any(not isinstance(key, str) for key in metadata_object):
            return None

        try:
            kind = DocumentImageKind(kind_value)
        except ValueError:
            return None

        image_path = (output_root / file_name).resolve()
        if not image_path.is_relative_to(output_root):
            return None

        try:
            content = image_path.read_bytes()
            with Image.open(BytesIO(content)) as image:
                actual_width, actual_height = image.size
        except (OSError, ValueError):
            return None

        if not content or actual_width <= 0 or actual_height <= 0:
            return None
        if width_px is not None and width_px != actual_width:
            return None
        if height_px is not None and height_px != actual_height:
            return None

        visuals.append(
            RenderedOfficeVisual(
                kind=kind,
                content=content,
                width_px=actual_width,
                height_px=actual_height,
                source_metadata=cast(dict[str, Any], metadata_object),
            )
        )

    return OfficeVisualRenderResult(
        visuals=tuple(visuals),
        renderer_available=renderer_available,
        failure_reason=failure_reason,
    )


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    """동일 디렉터리 임시 파일을 사용해 JSON을 원자적으로 교체한다."""

    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _read_positive_integer(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return None
    return value if value > 0 else None


def _snapshot_office_process_ids(operation: str) -> frozenset[int]:
    """worker 시작 전에 존재한 Office PID를 기록한다."""

    if sys.platform != "win32":
        return frozenset()

    image_name = "POWERPNT.EXE" if operation == "pptx" else "EXCEL.EXE"
    return _tasklist_process_ids(image_name=image_name)


def _tasklist_process_ids(
    *,
    image_name: str | None = None,
    pid: int | None = None,
) -> frozenset[int]:
    """Windows tasklist CSV에서 조건에 맞는 PID만 추출한다."""

    if sys.platform != "win32":
        return frozenset()

    if image_name is not None:
        filter_expression = f"IMAGENAME eq {image_name}"
    elif pid is not None:
        filter_expression = f"PID eq {pid}"
    else:
        return frozenset()

    completed = subprocess.run(
        ["tasklist", "/FI", filter_expression, "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_WINDOWS_NO_WINDOW,
    )

    process_ids: set[int] = set()
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2:
            continue
        try:
            process_id = int(row[1])
        except ValueError:
            continue
        if process_id > 0:
            process_ids.add(process_id)
    return frozenset(process_ids)


def _cleanup_worker_office_process(
    *,
    office_pid: int | None,
    preexisting_pids: frozenset[int],
    force: bool,
) -> None:
    """worker가 새로 생성한 Office 프로세스만 선택적으로 종료한다."""

    if sys.platform != "win32" or office_pid is None:
        return
    if office_pid in preexisting_pids:
        return

    deadline = time.monotonic() + _OFFICE_PROCESS_EXIT_GRACE_SECONDS
    while time.monotonic() < deadline:
        if office_pid not in _tasklist_process_ids(pid=office_pid):
            return
        time.sleep(0.1)

    # 정상 worker도 Office 종료가 지연될 수 있다. 새 PID임이 확실한 경우만 종료한다.
    # ``force``는 진단용으로 로그 수준을 결정하며 사용자 기존 프로세스는 건드리지 않는다.
    completed = subprocess.run(
        ["taskkill", "/PID", str(office_pid), "/T", "/F"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_WINDOWS_NO_WINDOW,
    )
    logger.warning(
        "office_worker_orphan_process_terminated",
        extra={
            "office_process_pid": office_pid,
            "taskkill_exit_code": completed.returncode,
            "worker_failed": force,
        },
    )


def _emit_result(
    result: OfficeVisualRenderResult,
    sink: OfficeResultSink | None,
) -> None:
    if sink is not None:
        sink(result)


def _emit_office_pid(
    application: Any,
    sink: OfficePidSink | None,
) -> None:
    if sink is None:
        return
    process_id = _office_application_process_id(application)
    if process_id is not None:
        sink(process_id)


def _office_application_process_id(application: Any) -> int | None:
    """Office Application HWND로 현재 DispatchEx 인스턴스의 PID를 구한다."""

    if sys.platform != "win32":
        return None

    try:
        window_handle = int(application.HWND)
    except Exception:
        try:
            window_handle = int(application.Hwnd)
        except Exception:
            return None

    if window_handle <= 0:
        return None

    process_id = ctypes.c_ulong(0)
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_window_process_id = user32.GetWindowThreadProcessId
        get_window_process_id.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        get_window_process_id.restype = ctypes.c_ulong
        get_window_process_id(
            ctypes.c_void_p(window_handle),
            ctypes.byref(process_id),
        )
    except Exception:
        return None
    return int(process_id.value) if process_id.value > 0 else None


def _environment_unavailable_reason(
    settings: DocumentProcessingSettings,
) -> str | None:
    """Windows 대화형 사용자 세션에서만 COM 자동화를 허용한다."""

    if sys.platform != "win32":
        return "windows_required"
    if settings.office_com_require_interactive_session and not _is_interactive_session():
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

    chart: Any | None = None
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
            chart = shape.Chart
            exported = bool(chart.Export(str(output_path), "PNG", False))
            if not exported:
                return False
        except Exception:
            return False
        finally:
            chart = None

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
    """Excel Range의 A1 주소를 메서드·속성 COM 바인딩 모두에서 읽는다."""

    if cell is None:
        return ""

    try:
        address_member = cell.Address
    except Exception:
        return ""

    if callable(address_member):
        address: object = ""
        for arguments in ((False, False), ()):
            try:
                address = address_member(*arguments)
                break
            except Exception:
                continue
    else:
        address = address_member

    return _safe_text(address).replace("$", "")


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


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _is_interactive_session() -> bool:
    """Windows 서비스·SYSTEM 계정을 제외하고 로컬/RDP 로그인 세션을 허용한다."""

    username = os.environ.get("USERNAME", "").strip().lower()
    session_name = os.environ.get("SESSIONNAME", "").strip().lower()
    if username in {"system", "local service", "network service"}:
        return False
    return session_name != "services"
