"""확장자가 제거된 다운로드 임시 파일에서도 PPTX 이미지를 안전하게 추출한다.

PPTX 삽입 이미지는 ZIP/XML에서 직접 읽을 수 있지만 차트와 SmartArt는 PowerPoint COM이
문서를 다시 열어야 한다. ``*.document`` 경로를 그대로 전달하면 PowerPoint의 확장자
판별에 따라 시각 요소 렌더링만 누락될 수 있다.

이 어댑터는 다운로드된 PPTX를 무작위 임시 디렉터리의 ``source.pptx``로 복사한 뒤 기존
``PptxImageExtractor``를 호출한다. 따라서 삽입 이미지의 ZIP/XML 분석과 PowerPoint COM
렌더링이 동일한 검증된 복사본을 사용한다.
"""

from __future__ import annotations

from pathlib import Path

from jipsa_rag.infrastructure.document.images.download_safe_office import (
    download_safe_office_source_path,
)
from jipsa_rag.infrastructure.document.images.models import DocumentImageExtraction
from jipsa_rag.infrastructure.document.images.pptx import PptxImageExtractor

_PPTX_SUFFIX = ".pptx"
_PPTX_TEMPORARY_PREFIX = "jipsa-rag-pptx-"


class DownloadSafePptxImageExtractor(PptxImageExtractor):
    """검증된 ``*.document`` PPTX를 임시 ``*.pptx`` 경로로 중계하는 추출기."""

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        """ZIP/XML 추출과 PowerPoint COM이 같은 안전한 PPTX 경로를 사용하게 한다.

        실제 ``.pptx`` 경로로 직접 호출된 경우에는 추가 복사를 수행하지 않는다.
        확장자가 제거된 다운로드 경로는 ``source.pptx``로 복사하며, 부모 추출기가
        정상 반환하거나 예외를 발생시켜도 context 종료 시 임시 자원이 정리된다.
        """

        async with download_safe_office_source_path(
            file_path,
            expected_suffix=_PPTX_SUFFIX,
            temporary_prefix=_PPTX_TEMPORARY_PREFIX,
        ) as normalized_path:
            return await super().extract(normalized_path)
