"""다운로드 임시 확장자를 사용하는 XLSX 이미지 추출 회귀를 검증한다."""

from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.drawing.image import Image as OpenpyxlImage  # type: ignore[import-untyped]
from PIL import Image

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.download_safe_xlsx import (
    DownloadSafeXlsxImageExtractor,
)
from jipsa_rag.infrastructure.document.images.models import DocumentImageKind
from jipsa_rag.infrastructure.document.rendering import OfficeVisualRenderResult
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.source_locator import build_source_locator


class _UnavailableRenderer:
    """차트가 없는 단위 테스트에서 Office COM 호출을 금지하는 렌더러 대역."""

    async def render_pptx_visuals(self, source_path: Path) -> OfficeVisualRenderResult:
        del source_path
        return OfficeVisualRenderResult.unavailable("unit_test")

    async def render_xlsx_charts(self, source_path: Path) -> OfficeVisualRenderResult:
        del source_path
        return OfficeVisualRenderResult.unavailable("unit_test")


def _png_bytes() -> bytes:
    """장식 필터 기준을 충분히 넘는 결정적인 PNG 바이트를 만든다."""

    image = Image.new("RGB", (320, 120), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_extracts_xlsx_image_from_downloader_document_extension(tmp_path: Path) -> None:
    """정상 XLSX 바이트가 ``*.document`` 경로여도 D2 이미지 위치를 보존한다."""

    workbook = Workbook()
    worksheet = workbook.active
    if worksheet is None:
        raise AssertionError("A new workbook must contain an active worksheet.")

    worksheet.title = "Images"
    worksheet["A1"] = "XLSX download-safe image extraction"

    image_stream = BytesIO(_png_bytes())
    worksheet_image = OpenpyxlImage(image_stream)
    worksheet.add_image(worksheet_image, "D2")

    workbook_buffer = BytesIO()
    workbook.save(workbook_buffer)
    workbook.close()

    # 운영 HttpFileDownloader와 동일하게 실제 확장자를 제거한 임시 경로를 사용한다.
    downloaded_path = tmp_path / "jipsa-rag-regression.document"
    downloaded_path.write_bytes(workbook_buffer.getvalue())

    settings = DocumentProcessingSettings(
        image_extraction_enabled=True,
        image_decorative_filter_enabled=False,
        office_rendering_enabled=False,
        ocr_enabled=False,
        ocr_gpu=False,
        ocr_gpu_required=False,
        _env_file=None,
    )
    extractor = DownloadSafeXlsxImageExtractor(settings, _UnavailableRenderer())

    extraction = await extractor.extract(downloaded_path)

    assert len(extraction.images) == 1
    extracted = extraction.images[0]
    assert extracted.kind is DocumentImageKind.XLSX_PICTURE
    assert extracted.source_metadata["sheet_index"] == 1
    # 이미지 추출기의 sheet_index는 이미 1-based다. 공통 Source Locator가
    # 이를 과거 0-based payload로 오인하지 않도록 표준 sheet_number도 같은
    # 값으로 명시해야 한다.
    assert extracted.source_metadata["sheet_number"] == 1
    assert extracted.source_metadata["sheet_name"] == "Images"
    assert extracted.source_metadata["image_index"] == 1
    assert extracted.source_metadata["cell_range"] == "D2"
    assert extracted.source_metadata["shape_path"] == "sheet:Images/image:1/anchor:D2"

    locator = build_source_locator(
        file_type=SupportedFileType.XLSX,
        source_metadata=extracted.source_metadata,
    )
    assert locator.sheet_number == 1
    assert locator.sheet_name == "Images"
    assert locator.cell_range == "D2"

    # 어댑터가 원본 다운로드 임시 파일을 삭제하거나 변경해서는 안 된다.
    assert downloaded_path.read_bytes() == workbook_buffer.getvalue()
