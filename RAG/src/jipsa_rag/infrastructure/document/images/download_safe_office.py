"""다운로드 임시 경로를 실제 Office 문서 확장자로 안전하게 정규화한다.

``HttpFileDownloader``는 외부 파일명을 임시 경로에 노출하지 않기 위해 다운로드한
문서를 ``*.document``로 저장한다. ZIP/XML 기반 파서는 파일 바이트를 직접 읽을 수
있지만 Microsoft Office COM과 일부 외부 라이브러리는 경로 확장자를 파일 형식 판별에
사용한다.

이 모듈은 검증된 다운로드 파일을 무작위 임시 디렉터리의 고정된 ``source.pptx`` 또는
``source.xlsx`` 경로로 복사한다. 원본 외부 파일명은 재사용하지 않으며, 성공·예외·취소
여부와 관계없이 context 종료 시 복사본과 임시 디렉터리를 정리한다.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

_SUPPORTED_OFFICE_SUFFIXES = frozenset({".pptx", ".xlsx"})


@asynccontextmanager
async def download_safe_office_source_path(
    file_path: Path,
    *,
    expected_suffix: str,
    temporary_prefix: str,
) -> AsyncIterator[Path]:
    """Office가 열 수 있는 실제 확장자의 입력 경로를 비동기로 제공한다.

    이미 기대 확장자를 가진 경로는 복사하지 않고 그대로 반환한다. 다운로드 임시
    경로처럼 확장자가 다른 경우에는 원본 바이트를 무작위 전용 디렉터리의 고정된
    파일명으로 복사한다.

    임시 디렉터리의 수명은 호출자의 ``async with`` 범위와 정확히 일치한다. 따라서
    Office 렌더링 성공, 부분 실패, timeout 결과 반환 또는 예외 발생 후에도 복사본이
    남지 않는다.
    """

    normalized_suffix = _normalize_expected_suffix(expected_suffix)

    if file_path.suffix.casefold() == normalized_suffix:
        yield file_path
        return

    # Windows에서는 열린 NamedTemporaryFile을 PowerPoint, Excel 또는 openpyxl이
    # 다시 열지 못할 수 있다. 닫힌 일반 파일과 전용 임시 디렉터리를 사용해 파일 잠금
    # 문제를 피한다. 고정 파일명만 사용하므로 외부에서 받은 원본 파일명도 노출하지 않는다.
    with tempfile.TemporaryDirectory(prefix=temporary_prefix) as temporary_directory:
        normalized_path = Path(temporary_directory) / f"source{normalized_suffix}"

        # 대용량 Office 문서 복사가 이벤트 루프를 막지 않도록 작업 스레드에서 수행한다.
        # 바이트를 변경하거나 압축을 다시 생성하지 않으며 검증된 원본을 그대로 복제한다.
        await asyncio.to_thread(
            shutil.copyfile,
            file_path,
            normalized_path,
        )

        yield normalized_path


def _normalize_expected_suffix(expected_suffix: str) -> str:
    """허용된 Office 확장자를 소문자 점 표기 형식으로 검증한다."""

    normalized_suffix = expected_suffix.casefold()
    if not normalized_suffix.startswith("."):
        normalized_suffix = f".{normalized_suffix}"

    if normalized_suffix not in _SUPPORTED_OFFICE_SUFFIXES:
        raise ValueError(f"Unsupported Office document suffix: {expected_suffix}")

    return normalized_suffix
