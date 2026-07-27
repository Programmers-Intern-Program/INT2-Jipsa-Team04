"""격리된 Microsoft Office COM 렌더링 worker 진입점을 제공한다.

이 모듈은 부모 RAG 프로세스가 ``python -m``으로 실행한다. COM 결과를 종료 정리 전에
원자적으로 저장하고, 정상 경로에서도 ``os._exit``로 Python interpreter teardown을
건너뛴다. pywin32 전역 종료 훅이나 연결이 끊긴 COM 프록시 소멸자가 부모 프로세스와
pytest를 중단시키지 않게 하는 것이 목적이다.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import cast

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.rendering.microsoft_office import (
    MicrosoftOfficeComBackend,
    _write_worker_result,
)
from jipsa_rag.infrastructure.document.rendering.models import OfficeVisualRenderResult


def main(request_path: Path) -> int:
    """요청 JSON을 검증하고 단일 PPTX 또는 XLSX COM 작업을 실행한다."""

    request = _load_request(request_path)
    response_path = Path(_required_text(request, "response_path"))
    output_directory = Path(_required_text(request, "output_directory"))
    office_pid_path = Path(_required_text(request, "office_pid_path"))
    source_path = Path(_required_text(request, "source_path"))
    operation = _required_text(request, "operation")
    require_interactive_session = _required_bool(
        request,
        "require_interactive_session",
    )

    settings = DocumentProcessingSettings(
        office_rendering_enabled=True,
        office_com_require_interactive_session=require_interactive_session,
        _env_file=None,
    )
    backend = MicrosoftOfficeComBackend(settings)

    def result_sink(result: OfficeVisualRenderResult) -> None:
        _write_worker_result(
            response_path=response_path,
            output_directory=output_directory,
            result=result,
        )

    def office_pid_sink(process_id: int) -> None:
        _write_text_atomic(office_pid_path, str(process_id))

    try:
        if operation == "pptx":
            dpi = _required_positive_int(request, "dpi")
            result = backend.render_pptx_visuals(
                source_path,
                dpi=dpi,
                result_sink=result_sink,
                office_pid_sink=office_pid_sink,
            )
        elif operation == "xlsx":
            result = backend.render_xlsx_charts(
                source_path,
                result_sink=result_sink,
                office_pid_sink=office_pid_sink,
            )
        else:
            result = OfficeVisualRenderResult.unavailable("unsupported_operation")
            result_sink(result)
    except Exception:
        # Python 예외 원문에는 원본 경로 또는 Office 문서 정보가 포함될 수 있다.
        # worker는 정형화된 실패 상태만 부모 프로세스에 전달한다.
        result = OfficeVisualRenderResult.unavailable("worker_exception")
        result_sink(result)
        return 1

    # sink는 COM 정리 전에 호출되지만, 백엔드가 정상 반환한 경우에도 manifest가
    # 존재하는지 확인하여 향후 구현 변경으로 결과 저장이 누락되는 것을 방지한다.
    if not response_path.is_file():
        result_sink(result)
    return 0 if result.renderer_available else 2


def _load_request(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Office worker request must be a JSON object.")
    if any(not isinstance(key, str) for key in payload):
        raise ValueError("Office worker request keys must be strings.")
    return cast(dict[str, object], payload)


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Office worker request field is invalid: {key}")
    return value


def _required_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"Office worker request field is invalid: {key}")
    return value


def _required_positive_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Office worker request field is invalid: {key}")
    return value


def _write_text_atomic(path: Path, text: str) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(text, encoding="ascii")
    os.replace(temporary_path, path)


if __name__ == "__main__":
    exit_code = 1
    try:
        if len(sys.argv) != 2:
            raise ValueError("Office worker requires one request JSON path.")
        exit_code = main(Path(sys.argv[1]))
    except Exception:
        exit_code = 1
    finally:
        # 정상적인 interpreter shutdown은 pywin32 전역 객체의 소멸자를 다시 실행할
        # 수 있다. 모든 결과 파일은 이미 닫혀 있으므로 즉시 프로세스를 종료한다.
        with suppress(Exception):
            sys.stdout.flush()
        with suppress(Exception):
            sys.stderr.flush()
        os._exit(exit_code)
