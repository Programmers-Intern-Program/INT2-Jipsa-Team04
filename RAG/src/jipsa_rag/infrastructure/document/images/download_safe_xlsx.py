"""확장자가 제거된 다운로드 임시 파일에서도 XLSX 이미지를 안전하게 추출한다.

``HttpFileDownloader``는 외부 파일명을 임시 경로에 사용하지 않고 모든 문서를
``*.document`` 확장자로 저장한다. 이 정책은 경로 조작과 파일명 충돌을 막지만,
``openpyxl.load_workbook()``에 문자열 경로를 전달하는 기존 ``XlsxImageExtractor``는
확장자만 보고 정상 XLSX 바이트를 거부할 수 있다.

이 어댑터는 이미 다운로드·MIME·Magic Byte·OOXML 루트 검증을 통과한 원본 바이트를
무작위 임시 디렉터리의 ``source.xlsx``로 한 번 복사한 뒤 기존 추출기를 그대로
호출한다. 따라서 삽입 이미지, 차트 렌더링, 위치 메타데이터와 자원 제한 로직은
기존 구현을 재사용하면서 다운로드 경로의 확장자 비호환성만 격리한다.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from jipsa_rag.infrastructure.document.images.models import DocumentImageExtraction
from jipsa_rag.infrastructure.document.images.xlsx import XlsxImageExtractor


class DownloadSafeXlsxImageExtractor(XlsxImageExtractor):
    """검증된 ``*.document`` XLSX를 임시 ``*.xlsx`` 경로로 중계하는 추출기."""

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        """원본 확장자가 없을 때만 안전한 임시 XLSX 복사본을 사용한다.

        실제 ``.xlsx`` 경로로 직접 호출된 경우에는 추가 I/O 없이 부모 구현을
        실행한다. 다운로드 임시 경로인 경우 복사와 삭제는 이 메서드 범위 안에서만
        이루어지며, 성공·실패와 관계없이 ``TemporaryDirectory``가 복사본을 정리한다.
        """

        if file_path.suffix.lower() == ".xlsx":
            return await super().extract(file_path)

        # Windows에서는 열린 NamedTemporaryFile을 Office와 openpyxl이 다시 열지
        # 못할 수 있다. 별도 임시 디렉터리와 닫힌 일반 파일을 사용하여 파일 잠금
        # 문제를 피하고, 원본 외부 파일명은 경로에 포함하지 않는다.
        with tempfile.TemporaryDirectory(prefix="jipsa-rag-xlsx-") as temp_directory:
            temporary_xlsx_path = Path(temp_directory) / "source.xlsx"

            # 큰 XLSX 복사는 이벤트 루프를 점유할 수 있으므로 작업 스레드에서 수행한다.
            # 원본 파일은 이미 공통 다운로더 검증을 통과했으며 여기서는 바이트를
            # 변경하지 않고 그대로 복제한다.
            await asyncio.to_thread(
                shutil.copyfile,
                file_path,
                temporary_xlsx_path,
            )

            return await super().extract(temporary_xlsx_path)
