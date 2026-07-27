"""DOCX 본문의 인라인 및 플로팅 이미지를 OOXML 관계와 함께 추출한다.

텍스트 파서가 사용하는 본문 직계 블록 순서와 동일하게 ``w:body``를 순회한다.
따라서 이미지 OCR 단위의 ``block_index``가 기존 문단 또는 표 unit과 정확히 연결된다.
표 안의 이미지는 표의 본문 블록 위치에 연결하면서 행·열 위치도 추가로 보존한다.
"""

from __future__ import annotations

import asyncio
import zipfile
from collections.abc import Mapping
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
from jipsa_rag.infrastructure.document.models import DocumentType, SourceMetadataValue

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOCX_REQUIRED_MEMBERS = frozenset({"[Content_Types].xml", "word/document.xml"})


class DocxImageExtractor:
    """WordprocessingML의 ``wp:inline``과 ``wp:anchor`` 배치를 모두 처리한다."""

    def __init__(self, settings: DocumentProcessingSettings) -> None:
        self._settings = settings

    @property
    def file_type(self) -> DocumentType:
        return DocumentType.DOCX

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        """동기 ZIP/XML 처리를 이벤트 루프 외부 작업 스레드에서 실행한다."""

        return await asyncio.to_thread(self._extract_sync, file_path)

    def _extract_sync(self, file_path: Path) -> DocumentImageExtraction:
        validate_ooxml_image_package(
            file_path,
            file_type=self.file_type,
            required_members=_DOCX_REQUIRED_MEMBERS,
            settings=self._settings,
        )
        images: list[ExtractedDocumentImage] = []

        with zipfile.ZipFile(file_path) as package:
            document_xml = package.read("word/document.xml")
            relationship_path = "word/_rels/document.xml.rels"
            relationships = (
                parse_relationships(package.read(relationship_path))
                if relationship_path in package.namelist()
                else {}
            )
            root = ElementTree.fromstring(document_xml)
            body = root.find(f"{{{_W}}}body")
            if body is None:
                return DocumentImageExtraction.empty()

            body_children = tuple(body)
            block_contexts = tuple(_block_text(child) for child in body_children)
            section_index = 1
            paragraph_index = 0
            table_index = 0

            for block_index, child in enumerate(body_children, start=1):
                if len(images) >= self._settings.image_max_count_per_document:
                    break

                before = _previous_non_empty(block_contexts, block_index - 1)
                current = block_contexts[block_index - 1]
                after = _next_non_empty(block_contexts, block_index - 1)
                local_metadata: dict[str, SourceMetadataValue] = {
                    "section_index": section_index,
                    "block_index": block_index,
                }

                if child.tag == f"{{{_W}}}p":
                    paragraph_index += 1
                    local_metadata["paragraph_index"] = paragraph_index
                    self._extract_drawings_from_container(
                        package=package,
                        relationships=relationships,
                        container=child,
                        metadata=local_metadata,
                        context_before=before,
                        context_current=current,
                        context_after=after,
                        images=images,
                    )

                    # 문단 내부 sectPr는 해당 문단 뒤에서 섹션이 종료된다는 의미다.
                    paragraph_properties = child.find(f"{{{_W}}}pPr")
                    if (
                        paragraph_properties is not None
                        and paragraph_properties.find(f"{{{_W}}}sectPr") is not None
                    ):
                        section_index += 1

                elif child.tag == f"{{{_W}}}tbl":
                    table_index += 1
                    local_metadata["table_index"] = table_index
                    self._extract_table_drawings(
                        package=package,
                        relationships=relationships,
                        table=child,
                        metadata=local_metadata,
                        context_before=before,
                        context_current=current,
                        context_after=after,
                        images=images,
                    )

        return DocumentImageExtraction(
            images=tuple(images),
            document_metadata={
                "extracted_image_count": len(images),
                "docx_inline_image_count": sum(
                    image.kind is DocumentImageKind.DOCX_INLINE for image in images
                ),
                "docx_floating_image_count": sum(
                    image.kind is DocumentImageKind.DOCX_FLOATING for image in images
                ),
            },
        )

    def _extract_table_drawings(
        self,
        *,
        package: zipfile.ZipFile,
        relationships: Mapping[str, str],
        table: ElementTree.Element,
        metadata: Mapping[str, SourceMetadataValue],
        context_before: str,
        context_current: str,
        context_after: str,
        images: list[ExtractedDocumentImage],
    ) -> None:
        """표 셀 내부 이미지에 행·열 위치를 추가하고 표 unit에 연결한다."""

        for row_index, row in enumerate(table.findall(f"{{{_W}}}tr"), start=1):
            for column_index, cell in enumerate(row.findall(f"{{{_W}}}tc"), start=1):
                if len(images) >= self._settings.image_max_count_per_document:
                    return
                cell_text = xml_text(cell, f"{{{_W}}}t")
                self._extract_drawings_from_container(
                    package=package,
                    relationships=relationships,
                    container=cell,
                    metadata={
                        **metadata,
                        "table_row_index": row_index,
                        "table_column_index": column_index,
                    },
                    context_before=context_before,
                    context_current=cell_text or context_current,
                    context_after=context_after,
                    images=images,
                )

    def _extract_drawings_from_container(
        self,
        *,
        package: zipfile.ZipFile,
        relationships: Mapping[str, str],
        container: ElementTree.Element,
        metadata: Mapping[str, SourceMetadataValue],
        context_before: str,
        context_current: str,
        context_after: str,
        images: list[ExtractedDocumentImage],
    ) -> None:
        """단일 문단 또는 표 셀의 drawing/blip 관계를 이미지 모델로 변환한다."""

        for drawing_index, drawing in enumerate(
            container.iter(f"{{{_W}}}drawing"),
            start=1,
        ):
            if len(images) >= self._settings.image_max_count_per_document:
                return

            placement = _drawing_placement(drawing)
            extent = drawing.find(f".//{{{_WP}}}extent")
            width_px, height_px = _extent_to_pixels(extent)

            for blip_index, blip in enumerate(
                drawing.iter(f"{{{_A}}}blip"),
                start=1,
            ):
                if len(images) >= self._settings.image_max_count_per_document:
                    return

                relationship_id = blip.attrib.get(f"{{{_R}}}embed")
                if relationship_id is None:
                    continue
                target = relationships.get(relationship_id)
                if target is None:
                    continue

                part_path = resolve_part_target("word/document.xml", target)
                try:
                    content = package.read(part_path)
                except KeyError:
                    continue

                extension = normalize_extension(Path(part_path).suffix)
                if not can_append_image(
                    images,
                    content,
                    width_px=width_px,
                    height_px=height_px,
                    settings=self._settings,
                ):
                    continue

                kind = (
                    DocumentImageKind.DOCX_FLOATING
                    if placement == "floating"
                    else DocumentImageKind.DOCX_INLINE
                )
                image_index = len(images) + 1
                block_index = metadata.get("block_index")
                images.append(
                    ExtractedDocumentImage(
                        image_id=build_image_id(
                            "docx",
                            block_index,
                            drawing_index,
                            blip_index,
                            relationship_id,
                            content=content,
                        ),
                        kind=kind,
                        content=content,
                        media_type=guess_media_type(extension),
                        extension=extension,
                        width_px=width_px,
                        height_px=height_px,
                        source_metadata={
                            **metadata,
                            "image_index": image_index,
                            "paragraph_image_index": blip_index,
                            "section_index": metadata.get("section_index", 1),
                            "drawing_index": drawing_index,
                            "drawing_placement": placement,
                            "relationship_id": relationship_id,
                            "shape_path": (
                                f"block:{block_index}/drawing:{drawing_index}/image:{blip_index}"
                            ),
                        },
                        context_before=context_before,
                        context_current=context_current,
                        context_after=context_after,
                    )
                )


def _block_text(block: ElementTree.Element) -> str:
    """문단·표 블록의 모든 텍스트를 주변 문맥용 한 줄로 직렬화한다."""

    return xml_text(block, f"{{{_W}}}t")


def _previous_non_empty(values: tuple[str, ...], index: int) -> str:
    for candidate_index in range(index - 1, -1, -1):
        if values[candidate_index]:
            return values[candidate_index]
    return ""


def _next_non_empty(values: tuple[str, ...], index: int) -> str:
    for candidate_index in range(index + 1, len(values)):
        if values[candidate_index]:
            return values[candidate_index]
    return ""


def _drawing_placement(drawing: ElementTree.Element) -> str:
    if drawing.find(f".//{{{_WP}}}anchor") is not None:
        return "floating"
    return "inline"


def _extent_to_pixels(
    extent: ElementTree.Element | None,
) -> tuple[int | None, int | None]:
    """EMU 크기를 96 DPI 기준 픽셀로 변환한다."""

    if extent is None:
        return None, None

    try:
        width_emu = int(extent.attrib["cx"])
        height_emu = int(extent.attrib["cy"])
    except (KeyError, ValueError):
        return None, None

    # OOXML에서 1 inch는 914400 EMU이고 일반 화면 기준은 96 DPI다.
    width_px = max(round(width_emu / 914_400 * 96), 1)
    height_px = max(round(height_emu / 914_400 * 96), 1)
    return width_px, height_px
