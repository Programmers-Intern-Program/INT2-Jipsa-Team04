"""PDF 내부 이미지 추출과 스캔·이미지 전용 페이지 탐지를 구현한다."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.common import (
    build_image_id,
    can_append_image,
    ensure_regular_file,
    guess_media_type,
    normalize_extension,
)
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    DocumentImageKind,
    ExtractedDocumentImage,
    ImageOnlyLocation,
)
from jipsa_rag.infrastructure.document.models import DocumentType


class PdfImageExtractor:
    """PyMuPDF로 임베디드 이미지와 스캔 페이지 렌더 이미지를 추출한다."""

    def __init__(self, settings: DocumentProcessingSettings) -> None:
        self._settings = settings

    @property
    def file_type(self) -> DocumentType:
        return DocumentType.PDF

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        """PDF 렌더링과 이미지 추출을 이벤트 루프 외부에서 실행한다."""

        return await asyncio.to_thread(self._extract_sync, file_path)

    def _extract_sync(self, file_path: Path) -> DocumentImageExtraction:
        ensure_regular_file(file_path)

        try:
            import fitz
        except ImportError as error:
            raise RuntimeError("PyMuPDF is required for PDF image extraction.") from error

        images: list[ExtractedDocumentImage] = []
        image_only_locations: list[ImageOnlyLocation] = []

        with fitz.open(file_path) as document:
            page_count = document.page_count
            for page_index in range(page_count):
                if len(images) >= self._settings.image_max_count_per_document:
                    break

                page = document.load_page(page_index)
                page_number = page_index + 1
                page_text = page.get_text("text").strip()
                compact_text_length = len("".join(page_text.split()))
                page_area = max(page.rect.width * page.rect.height, 1.0)
                image_references = page.get_images(full=True)
                combined_image_coverage = 0.0

                for image_index, image_reference in enumerate(image_references, start=1):
                    if len(images) >= self._settings.image_max_count_per_document:
                        break

                    xref = int(image_reference[0])
                    extracted = document.extract_image(xref)
                    content = bytes(extracted.get("image", b""))
                    extension = normalize_extension(str(extracted.get("ext", "png")))
                    width_px = _optional_positive_int(extracted.get("width"))
                    height_px = _optional_positive_int(extracted.get("height"))

                    coverage = 0.0
                    try:
                        rectangles = page.get_image_rects(xref)
                        coverage = min(
                            sum(rect.width * rect.height for rect in rectangles) / page_area,
                            1.0,
                        )
                    except Exception:
                        # 잘못된 XObject 좌표는 이미지 바이트 추출 성공 여부와 분리한다.
                        coverage = 0.0
                    combined_image_coverage = min(combined_image_coverage + coverage, 1.0)

                    if not can_append_image(
                        images,
                        content,
                        width_px=width_px,
                        height_px=height_px,
                        settings=self._settings,
                    ):
                        continue

                    images.append(
                        ExtractedDocumentImage(
                            image_id=build_image_id(
                                "pdf",
                                page_number,
                                image_index,
                                xref,
                                content=content,
                            ),
                            kind=DocumentImageKind.PDF_EMBEDDED,
                            content=content,
                            media_type=guess_media_type(extension),
                            extension=extension,
                            width_px=width_px,
                            height_px=height_px,
                            source_metadata={
                                "page_number": page_number,
                                "image_index": image_index,
                                "pdf_xref": xref,
                                "image_page_coverage": round(coverage, 6),
                                "shape_path": f"page:{page_number}/image:{image_index}",
                            },
                            context_current=page_text,
                        )
                    )

                # 완전히 빈 페이지를 스캔 페이지로 오인하지 않도록, 이미지 XObject 외에
                # 벡터 도형이 존재하는지도 확인한다. 일부 스캐너와 변환 도구는 글자를
                # 이미지가 아니라 벡터 경로로 저장하므로 해당 페이지는 전체 렌더 OCR
                # 후보로 유지한다. 손상된 drawing stream은 이미지 추출 자체와 분리하여
                # 안전하게 "비텍스트 콘텐츠 없음"으로 처리한다.
                try:
                    has_vector_content = bool(page.get_drawings())
                except Exception:
                    has_vector_content = False

                is_image_only = _is_image_only_page(
                    compact_text_length=compact_text_length,
                    image_count=len(image_references),
                    image_coverage_ratio=combined_image_coverage,
                    has_non_text_content=(bool(image_references) or has_vector_content),
                    settings=self._settings,
                )

                if not is_image_only:
                    continue

                image_only_locations.append(
                    ImageOnlyLocation(
                        location_id=f"page:{page_number}",
                        source_metadata={
                            "page_number": page_number,
                            "location_kind": "pdf_page",
                        },
                    )
                )

                # 스캔 페이지는 개별 XObject보다 페이지 전체 렌더가 읽기 순서와
                # 회전·배치를 보존한다. 따라서 전체 페이지 PNG를 우선 OCR 후보로 추가한다.
                if len(images) >= self._settings.image_max_count_per_document:
                    continue

                scale = self._settings.scan_pdf_render_dpi / 72.0
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                rendered_content = pixmap.tobytes("png")

                if not can_append_image(
                    images,
                    rendered_content,
                    width_px=pixmap.width,
                    height_px=pixmap.height,
                    settings=self._settings,
                ):
                    continue

                images.append(
                    ExtractedDocumentImage(
                        image_id=build_image_id(
                            "pdf-page-render",
                            page_number,
                            content=rendered_content,
                        ),
                        kind=DocumentImageKind.PDF_PAGE_RENDER,
                        content=rendered_content,
                        media_type="image/png",
                        extension="png",
                        width_px=pixmap.width,
                        height_px=pixmap.height,
                        source_metadata={
                            "page_number": page_number,
                            "image_index": len(images) + 1,
                            "page_image_index": len(image_references) + 1,
                            "is_image_only_page": True,
                            "shape_path": f"page:{page_number}/render",
                        },
                        context_current=page_text,
                    )
                )

                # 같은 스캔 페이지의 개별 임베디드 이미지는 전체 페이지 렌더와 중복될
                # 가능성이 높다. 위치 보존을 위해 결과에는 남기되 OCR 후보에서는 제외한다.
                for image_index in range(len(images) - 1):
                    image = images[image_index]
                    if (
                        image.kind is DocumentImageKind.PDF_EMBEDDED
                        and image.source_metadata.get("page_number") == page_number
                        and image.ocr_candidate
                    ):
                        images[image_index] = ExtractedDocumentImage(
                            image_id=image.image_id,
                            kind=image.kind,
                            content=image.content,
                            media_type=image.media_type,
                            extension=image.extension,
                            width_px=image.width_px,
                            height_px=image.height_px,
                            ocr_candidate=False,
                            source_metadata=image.source_metadata,
                            context_before=image.context_before,
                            context_current=image.context_current,
                            context_after=image.context_after,
                        )

        return DocumentImageExtraction(
            images=tuple(images),
            image_only_locations=tuple(image_only_locations),
            document_metadata={
                "page_count": page_count,
                "extracted_image_count": len(images),
                "image_only_page_count": len(image_only_locations),
            },
        )


def _is_image_only_page(
    *,
    compact_text_length: int,
    image_count: int,
    image_coverage_ratio: float,
    has_non_text_content: bool,
    settings: DocumentProcessingSettings,
) -> bool:
    """텍스트량, 이미지 점유율 및 비텍스트 콘텐츠로 OCR 필요 여부를 판단한다.

    텍스트와 시각 요소가 모두 없는 완전한 빈 페이지는 OCR해도 검색 가능한 결과를
    만들 수 없으므로 제외한다. 반대로 이미지 XObject가 없더라도 벡터 경로가 있으면
    글자가 outline으로 변환된 문서일 수 있어 전체 페이지 렌더 OCR 후보로 유지한다.
    """

    if compact_text_length > settings.scan_pdf_text_threshold_chars:
        return False
    if not has_non_text_content:
        return False
    if image_count == 0:
        return compact_text_length == 0
    if compact_text_length == 0:
        return True
    return image_coverage_ratio >= settings.scan_pdf_image_coverage_ratio


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
