"""확장자가 제거된 다운로드 임시 파일에서도 XLSX 이미지를 안전하게 추출한다.

``HttpFileDownloader``는 외부 파일명을 임시 경로에 사용하지 않고 모든 문서를
``*.document`` 확장자로 저장한다. 이 정책은 경로 조작과 파일명 충돌을 막지만,
``openpyxl.load_workbook()``과 Microsoft Excel COM은 실제 ``.xlsx`` 확장자를 요구할 수
있다.

이 어댑터는 공통 Office 경로 정규화 계층을 통해 검증된 원본 바이트를 무작위 임시
디렉터리의 ``source.xlsx``로 한 번 복사한 뒤 기존 추출기를 그대로 호출한다. 삽입 이미지,
차트 렌더링, 위치 메타데이터와 자원 제한 로직은 기존 구현을 재사용한다.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from jipsa_rag.infrastructure.document.images.download_safe_office import (
    download_safe_office_source_path,
)
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    ExtractedDocumentImage,
)
from jipsa_rag.infrastructure.document.images.xlsx import XlsxImageExtractor
from jipsa_rag.infrastructure.document.models import SourceMetadata

_XLSX_SUFFIX = ".xlsx"
_XLSX_TEMPORARY_PREFIX = "jipsa-rag-xlsx-"


class DownloadSafeXlsxImageExtractor(XlsxImageExtractor):
    """검증된 ``*.document`` XLSX를 임시 ``*.xlsx`` 경로로 중계하는 추출기."""

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        """openpyxl과 Excel COM이 같은 안전한 XLSX 경로를 사용하게 한다.

        실제 ``.xlsx`` 경로로 직접 호출된 경우에는 추가 I/O 없이 부모 구현을 실행한다.
        다운로드 임시 경로인 경우 복사본은 이 메서드 범위에서만 유지되며 성공, 부분 실패,
        timeout 결과 반환 또는 예외 발생 후 모두 정리된다.
        """

        async with download_safe_office_source_path(
            file_path,
            expected_suffix=_XLSX_SUFFIX,
            temporary_prefix=_XLSX_TEMPORARY_PREFIX,
        ) as normalized_path:
            extraction = await super().extract(normalized_path)
            return _normalize_sheet_numbers(extraction)


def _normalize_sheet_numbers(
    extraction: DocumentImageExtraction,
) -> DocumentImageExtraction:
    """XLSX 이미지의 1-based sheet_index를 표준 sheet_number로 명시한다.

    XlsxDocumentParser와 XlsxImageExtractor는 모두 ``enumerate(..., start=1)``로
    시트 순번을 생성한다. 공통 Source Locator는 오래된 0-based payload와의 호환을
    위해 sheet_index만 있으면 1을 더하는 fallback을 제공하므로, 신규 추출 결과가
    sheet_number를 생략하면 첫 시트가 두 번째 시트로 잘못 표시될 수 있다.

    이 어댑터는 원본 메타데이터를 변경하지 않고 같은 1-based 값을 명시적인
    ``sheet_number``로 복사한다. Source Locator는 표준 필드를 우선하므로 과거
    payload fallback은 유지하면서 신규 XLSX OCR 출처의 시트 위치를 정확히 보존한다.
    """

    normalized_images = tuple(_normalize_image_sheet_number(image) for image in extraction.images)
    if normalized_images == extraction.images:
        return extraction
    return replace(extraction, images=normalized_images)


def _normalize_image_sheet_number(
    image: ExtractedDocumentImage,
) -> ExtractedDocumentImage:
    """한 XLSX 이미지 메타데이터에 누락된 sheet_number만 안전하게 추가한다."""

    metadata = dict(image.source_metadata)
    if "sheet_number" in metadata:
        return image

    sheet_index = metadata.get("sheet_index")
    if isinstance(sheet_index, bool) or not isinstance(sheet_index, int) or sheet_index <= 0:
        return image

    normalized_metadata: SourceMetadata = {
        **metadata,
        "sheet_number": sheet_index,
    }
    return replace(image, source_metadata=normalized_metadata)
