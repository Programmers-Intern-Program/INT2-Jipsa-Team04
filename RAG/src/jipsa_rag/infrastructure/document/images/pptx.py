"""PPTX 그림과 Microsoft PowerPoint 차트·SmartArt 이미지를 추출한다."""

from __future__ import annotations

import asyncio
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.common import (
    build_image_id,
    can_append_image,
    guess_media_type,
    normalize_extension,
    parse_relationships,
    resolve_part_target,
    validate_ooxml_image_package,
    xml_text,
)
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    DocumentImageKind,
    ExtractedDocumentImage,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.rendering import (
    OfficeRenderClient,
    OfficeVisualRenderResult,
)

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_SLIDE_PATH_PATTERN = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_PPTX_REQUIRED_MEMBERS = frozenset({"[Content_Types].xml", "ppt/presentation.xml"})


@dataclass(frozen=True, slots=True)
class _VisualFrame:
    """OOXML에서 탐지한 차트 또는 SmartArt graphicFrame 식별 정보."""

    kind: DocumentImageKind
    shape_index: int
    shape_path: str


class PptxImageExtractor:
    """슬라이드 그림 파트와 PowerPoint COM 렌더 결과를 함께 추출한다."""

    def __init__(
        self,
        settings: DocumentProcessingSettings,
        renderer: OfficeRenderClient,
    ) -> None:
        self._settings = settings
        self._renderer = renderer

    @property
    def file_type(self) -> DocumentType:
        return DocumentType.PPTX

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        """OOXML 검증 후 삽입 이미지와 차트·SmartArt PNG를 추출한다.

        Microsoft Office COM은 문서를 실제로 여는 외부 응용 프로그램 경계다.
        손상되거나 비정상적인 OOXML이 Office에 전달되지 않도록 ZIP 안전 검증과
        graphicFrame 분석을 먼저 완료하고, 렌더링 대상이 확인된 문서만 연다.
        """

        embedded_images, slide_contexts, frames = await asyncio.to_thread(
            self._extract_embedded_sync,
            file_path,
        )
        has_renderable_frames = any(frames.values())

        if has_renderable_frames:
            rendered_result = await self._renderer.render_pptx_visuals(file_path)
        else:
            rendered_result = OfficeVisualRenderResult.unavailable("no_visual_frames")

        rendered_images = self._build_rendered_images(
            rendered_result=rendered_result,
            slide_contexts=slide_contexts,
            existing_images=embedded_images,
            maximum_count=max(
                self._settings.image_max_count_per_document - len(embedded_images),
                0,
            ),
            image_index_offset=len(embedded_images),
        )

        images = tuple((*embedded_images, *rendered_images))
        return DocumentImageExtraction(
            images=images,
            document_metadata={
                "slide_count": len(slide_contexts),
                "extracted_image_count": len(images),
                "pptx_chart_render_count": sum(
                    image.kind is DocumentImageKind.PPTX_CHART_RENDER for image in images
                ),
                "pptx_smartart_render_count": sum(
                    image.kind is DocumentImageKind.PPTX_SMARTART_RENDER for image in images
                ),
                "office_renderer": "microsoft_office_com",
                "office_renderer_available": rendered_result.renderer_available,
            },
        )

    def _extract_embedded_sync(
        self,
        file_path: Path,
    ) -> tuple[
        tuple[ExtractedDocumentImage, ...],
        dict[int, str],
        dict[int, tuple[_VisualFrame, ...]],
    ]:
        """삽입 그림, 슬라이드 텍스트 문맥 및 렌더링 대상 frame을 읽는다."""

        validate_ooxml_image_package(
            file_path,
            file_type=self.file_type,
            required_members=_PPTX_REQUIRED_MEMBERS,
            settings=self._settings,
        )
        images: list[ExtractedDocumentImage] = []
        slide_contexts: dict[int, str] = {}
        frames: dict[int, tuple[_VisualFrame, ...]] = {}

        with zipfile.ZipFile(file_path) as package:
            slide_paths = sorted(
                (path for path in package.namelist() if _SLIDE_PATH_PATTERN.fullmatch(path)),
                key=_slide_number,
            )

            for slide_path in slide_paths:
                slide_number = _slide_number(slide_path)
                root = ElementTree.fromstring(package.read(slide_path))
                slide_text = xml_text(root, f"{{{_A}}}t")
                slide_contexts[slide_number] = slide_text
                relation_path = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
                relationships = (
                    parse_relationships(package.read(relation_path))
                    if relation_path in package.namelist()
                    else {}
                )

                for picture_index, picture in enumerate(
                    root.iter(f"{{{_P}}}pic"),
                    start=1,
                ):
                    if len(images) >= self._settings.image_max_count_per_document:
                        break
                    blip = picture.find(f".//{{{_A}}}blip")
                    if blip is None:
                        continue
                    relationship_id = blip.attrib.get(f"{{{_R}}}embed")
                    if relationship_id is None:
                        continue
                    target = relationships.get(relationship_id)
                    if target is None:
                        continue
                    part_path = resolve_part_target(slide_path, target)
                    try:
                        content = package.read(part_path)
                    except KeyError:
                        continue
                    extension = normalize_extension(Path(part_path).suffix)
                    width_px, height_px = _picture_pixel_size(picture)
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
                                "pptx",
                                slide_number,
                                picture_index,
                                relationship_id,
                                content=content,
                            ),
                            kind=DocumentImageKind.PPTX_PICTURE,
                            content=content,
                            media_type=guess_media_type(extension),
                            extension=extension,
                            width_px=width_px,
                            height_px=height_px,
                            source_metadata={
                                "slide_number": slide_number,
                                "shape_index": picture_index,
                                "slide_image_index": picture_index,
                                "image_index": len(images) + 1,
                                "relationship_id": relationship_id,
                                # StructuredDocumentChunker가 slide prefix를 추가하므로
                                # 형식 내부 경로만 저장하여 중복 경로를 만들지 않는다.
                                "shape_path": f"picture:{picture_index}",
                            },
                            context_current=slide_text,
                        )
                    )

                frames[slide_number] = tuple(_find_visual_frames(root))

        return tuple(images), slide_contexts, frames

    def _build_rendered_images(
        self,
        *,
        rendered_result: OfficeVisualRenderResult,
        slide_contexts: dict[int, str],
        existing_images: tuple[ExtractedDocumentImage, ...],
        maximum_count: int,
        image_index_offset: int,
    ) -> tuple[ExtractedDocumentImage, ...]:
        """PowerPoint가 직접 출력한 PNG를 안전 제한과 슬라이드 문맥에 연결한다."""

        if maximum_count <= 0:
            return ()

        images: list[ExtractedDocumentImage] = []
        for visual in rendered_result.visuals:
            if len(images) >= maximum_count:
                break
            if visual.kind not in {
                DocumentImageKind.PPTX_CHART_RENDER,
                DocumentImageKind.PPTX_SMARTART_RENDER,
            }:
                continue
            if not can_append_image(
                (*existing_images, *images),
                visual.content,
                width_px=visual.width_px,
                height_px=visual.height_px,
                settings=self._settings,
            ):
                continue

            slide_number = _metadata_int(visual.source_metadata.get("slide_number"))
            shape_index = _metadata_int(visual.source_metadata.get("shape_index"))
            images.append(
                ExtractedDocumentImage(
                    image_id=build_image_id(
                        "pptx-office-render",
                        slide_number,
                        shape_index,
                        visual.kind.value,
                        content=visual.content,
                    ),
                    kind=visual.kind,
                    content=visual.content,
                    media_type="image/png",
                    extension="png",
                    width_px=visual.width_px,
                    height_px=visual.height_px,
                    source_metadata={
                        **visual.source_metadata,
                        "image_index": image_index_offset + len(images) + 1,
                    },
                    context_current=slide_contexts.get(slide_number, ""),
                )
            )

        return tuple(images)


def _find_visual_frames(root: ElementTree.Element) -> list[_VisualFrame]:
    """OOXML graphicFrame에서 차트와 SmartArt의 존재를 안전하게 탐지한다."""

    frames: list[_VisualFrame] = []
    for shape_index, frame in enumerate(root.iter(f"{{{_P}}}graphicFrame"), start=1):
        graphic_data = frame.find(f".//{{{_A}}}graphicData")
        if graphic_data is None:
            continue
        uri = graphic_data.attrib.get("uri", "")
        has_chart = graphic_data.find(f".//{{{_C}}}chart") is not None or "chart" in uri
        has_smartart = graphic_data.find(f".//{{{_DGM}}}relIds") is not None or "diagram" in uri
        if not has_chart and not has_smartart:
            continue

        kind = (
            DocumentImageKind.PPTX_CHART_RENDER
            if has_chart
            else DocumentImageKind.PPTX_SMARTART_RENDER
        )
        visual_name = "chart" if has_chart else "smartart"
        frames.append(
            _VisualFrame(
                kind=kind,
                shape_index=shape_index,
                shape_path=f"graphic-frame:{shape_index}/{visual_name}",
            )
        )
    return frames


def _picture_pixel_size(picture: ElementTree.Element) -> tuple[int | None, int | None]:
    """DrawingML ext 크기를 96 DPI 기준 픽셀로 변환한다."""

    extension = picture.find(f".//{{{_A}}}ext")
    if extension is None:
        return None, None
    try:
        width_emu = int(extension.attrib["cx"])
        height_emu = int(extension.attrib["cy"])
    except (KeyError, ValueError):
        return None, None
    return max(round(width_emu / 9_525), 1), max(round(height_emu / 9_525), 1)


def _metadata_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _slide_number(slide_path: str) -> int:
    match = _SLIDE_PATH_PATTERN.fullmatch(slide_path)
    if match is None:
        raise ValueError(f"Invalid slide part path: {slide_path}")
    return int(match.group(1))
