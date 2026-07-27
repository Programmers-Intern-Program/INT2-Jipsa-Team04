"""OCR 결과를 주변 문맥과 연결한 ParsedDocumentUnit으로 변환한다."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageExtraction,
    ExtractedDocumentImage,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
    SourceMetadataValue,
)
from jipsa_rag.infrastructure.ocr.exceptions import OcrError
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult
from jipsa_rag.infrastructure.ocr.normalization import normalize_ocr_text
from jipsa_rag.infrastructure.ocr.protocol import OcrEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RecognizedImageUnit:
    image: ExtractedDocumentImage
    unit: ParsedDocumentUnit


class OcrDocumentEnricher:
    """이미지 OCR 결과를 기존 문단·슬라이드·시트 위치에 결정적으로 삽입한다."""

    def __init__(
        self,
        *,
        engine: OcrEngine,
        settings: DocumentProcessingSettings,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.ocr_max_concurrency)

    async def enrich(
        self,
        *,
        document: ParsedDocument,
        extraction: DocumentImageExtraction,
    ) -> ParsedDocument:
        """OCR 후보를 제한된 동시성으로 처리하고 검색 가능한 단위로 병합한다.

        같은 이미지 바이트가 여러 페이지·문단·슬라이드·시트 위치에서 반복되어도
        실제 OCR 추론은 SHA-256별 한 번만 수행한다. 인식 결과는 각 원본 위치의
        주변 문맥으로 다시 직렬화하므로 중복 추론을 막으면서 출처 위치는 잃지 않는다.
        """

        candidates, resource_skipped_count = _select_ocr_candidates(
            extraction,
            settings=self._settings,
        )
        if not candidates:
            return _decorate_document_metadata(
                document,
                extraction=extraction,
                selected_candidate_count=0,
                unique_candidate_count=0,
                resource_skipped_count=resource_skipped_count,
                ocr_unit_count=0,
                ocr_failed_image_count=0,
            )

        # Python dict의 삽입 순서를 이용하여 문서 등장 순서 기준의 결정적인
        # 대표 이미지를 선택한다. 같은 Hash의 나머지 이미지는 추론 결과만 재사용한다.
        representative_by_hash: dict[str, ExtractedDocumentImage] = {}
        candidate_hashes: list[str] = []
        for image in candidates:
            image_hash = hashlib.sha256(image.content).hexdigest()
            candidate_hashes.append(image_hash)
            representative_by_hash.setdefault(image_hash, image)

        unique_hashes = tuple(representative_by_hash)
        unique_images = tuple(representative_by_hash[value] for value in unique_hashes)
        raw_results, document_timeout_count = await self._recognize_unique_images(unique_images)
        result_by_hash = dict(zip(unique_hashes, raw_results, strict=True))

        successful_units: list[_RecognizedImageUnit] = []
        for image, image_hash in zip(candidates, candidate_hashes, strict=True):
            result = result_by_hash[image_hash]
            if result is None:
                continue
            recognized = self._build_recognized_unit(image, result=result)
            if recognized is not None:
                successful_units.append(recognized)

        successful = tuple(successful_units)
        failed_count = len(candidates) - len(successful)
        merged_units = _merge_units(document, successful)
        enriched = ParsedDocument(
            file_type=document.file_type,
            units=merged_units,
            document_metadata={
                **document.document_metadata,
                **extraction.document_metadata,
                "extracted_image_count": len(extraction.images),
                "extracted_ocr_candidate_count": extraction.ocr_candidate_count,
                "ocr_candidate_count": len(candidates),
                "ocr_unique_candidate_count": len(unique_images),
                "ocr_deduplicated_candidate_count": len(candidates) - len(unique_images),
                "ocr_resource_skipped_count": resource_skipped_count,
                "ocr_unit_count": len(successful),
                "ocr_failed_image_count": failed_count,
                "ocr_document_timed_out_image_count": document_timeout_count,
                "image_only_location_count": len(extraction.image_only_locations),
                "ocr_engine": self._engine.engine_name,
                "ocr_languages": self._settings.ocr_languages,
                "ocr_gpu_enabled": self._settings.ocr_gpu,
            },
        )
        return enriched

    async def _recognize_unique_images(
        self,
        images: tuple[ExtractedDocumentImage, ...],
    ) -> tuple[tuple[OcrRecognitionResult | None, ...], int]:
        """문서 전체 OCR 제한 시간 안에서 고유 이미지 결과를 순서대로 반환한다.

        단일 이미지 timeout과 별도로 문서 전체 제한을 적용한다. 제한 시간에 도달하면
        완료되지 않은 작업만 취소하고 이미 완료된 결과는 유지하여 부분 성공을 보장한다.
        """

        tasks = tuple(asyncio.create_task(self._recognize_image_content(image)) for image in images)
        _, pending = await asyncio.wait(
            tasks,
            timeout=self._settings.ocr_document_timeout_seconds,
        )

        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning(
                "ocr_document_timed_out",
                extra={
                    "ocr_document_timeout_seconds": (self._settings.ocr_document_timeout_seconds),
                    "timed_out_image_count": len(pending),
                },
            )

        results: list[OcrRecognitionResult | None] = []
        for task in tasks:
            if task in pending:
                results.append(None)
                continue
            results.append(task.result())

        return tuple(results), len(pending)

    async def _recognize_image_content(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult | None:
        """한 이미지의 OCR 추론을 동시성·시간 제한 안에서 실행한다."""

        async with self._semaphore:
            try:
                return await asyncio.wait_for(
                    self._engine.recognize(image),
                    timeout=self._settings.ocr_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "ocr_image_timed_out",
                    extra={
                        "image_kind": image.kind.value,
                        "ocr_timeout_seconds": self._settings.ocr_timeout_seconds,
                    },
                )
                return None
            except OcrError as error:
                # 이미지 바이트, OCR 원문, 모델 경로는 로그에 남기지 않는다.
                logger.warning(
                    "ocr_image_failed",
                    extra={
                        "image_kind": image.kind.value,
                        "ocr_error_type": type(error).__name__,
                    },
                )
                return None

    def _build_recognized_unit(
        self,
        image: ExtractedDocumentImage,
        *,
        result: OcrRecognitionResult,
    ) -> _RecognizedImageUnit | None:
        """공유 가능한 OCR 결과를 이미지별 위치와 주변 문맥에 다시 연결한다."""

        # OCR 구현체가 병렬 후처리로 line tuple을 구성하더라도 결과의 명시적
        # ``order`` 값을 기준으로 정렬하여 Content Hash와 Chunk ID를 결정적으로 유지한다.
        accepted_lines = tuple(
            sorted(
                (
                    line
                    for line in result.lines
                    if line.confidence >= self._settings.ocr_min_confidence
                ),
                key=lambda line: line.order,
            )
        )
        raw_text = "\n".join(line.text for line in accepted_lines)
        normalized_text = normalize_ocr_text(
            raw_text,
            maximum_chars=self._settings.ocr_text_max_chars_per_image,
        )
        if not normalized_text:
            return None

        context_text = _build_context_text(
            image,
            maximum_chars=self._settings.ocr_context_max_chars,
        )
        searchable_text = _build_searchable_text(
            ocr_text=normalized_text,
            context_text=context_text,
        )
        mean_confidence = (
            sum(line.confidence for line in accepted_lines) / len(accepted_lines)
            if accepted_lines
            else 0.0
        )
        image_hash = hashlib.sha256(image.content).hexdigest()
        metadata: dict[str, SourceMetadataValue] = {
            **image.source_metadata,
            "unit_type": "ocr_image",
            "content_origin": "ocr",
            "image_kind": image.kind.value,
            "image_id": image.image_id,
            "image_sha256": image_hash,
            "ocr_engine": result.engine_name,
            "ocr_languages": result.languages,
            "ocr_device": result.device,
            "ocr_mean_confidence": round(mean_confidence, 6),
            "ocr_line_count": len(accepted_lines),
            "context_linked": bool(context_text),
        }

        return _RecognizedImageUnit(
            image=image,
            unit=ParsedDocumentUnit(
                text=searchable_text,
                source_metadata=metadata,
            ),
        )


def _select_ocr_candidates(
    extraction: DocumentImageExtraction,
    *,
    settings: DocumentProcessingSettings,
) -> tuple[tuple[ExtractedDocumentImage, ...], int]:
    """개수와 문서 전체 바이트 상한 안에서 OCR 후보를 결정적으로 선택한다."""

    selected: list[ExtractedDocumentImage] = []
    selected_bytes = 0
    skipped_count = 0

    for image in extraction.images:
        if not image.ocr_candidate:
            continue
        if len(selected) >= settings.image_max_count_per_document:
            skipped_count += 1
            continue
        if selected_bytes + len(image.content) > settings.image_max_total_bytes:
            skipped_count += 1
            continue
        selected.append(image)
        selected_bytes += len(image.content)

    return tuple(selected), skipped_count


def _decorate_document_metadata(
    document: ParsedDocument,
    *,
    extraction: DocumentImageExtraction,
    selected_candidate_count: int,
    unique_candidate_count: int,
    resource_skipped_count: int,
    ocr_unit_count: int,
    ocr_failed_image_count: int,
) -> ParsedDocument:
    return ParsedDocument(
        file_type=document.file_type,
        units=document.units,
        document_metadata={
            **document.document_metadata,
            **extraction.document_metadata,
            "extracted_image_count": len(extraction.images),
            "extracted_ocr_candidate_count": extraction.ocr_candidate_count,
            "ocr_candidate_count": selected_candidate_count,
            "ocr_unique_candidate_count": unique_candidate_count,
            "ocr_deduplicated_candidate_count": (selected_candidate_count - unique_candidate_count),
            "ocr_resource_skipped_count": resource_skipped_count,
            "ocr_unit_count": ocr_unit_count,
            "ocr_failed_image_count": ocr_failed_image_count,
            "ocr_document_timed_out_image_count": 0,
            "image_only_location_count": len(extraction.image_only_locations),
        },
    )


def _build_context_text(
    image: ExtractedDocumentImage,
    *,
    maximum_chars: int,
) -> str:
    """문단·슬라이드·시트 주변 문맥을 중복 없이 제한 길이로 결합한다."""

    if maximum_chars <= 0:
        return ""

    values: list[str] = []
    for value in (
        image.context_before,
        image.context_current,
        image.context_after,
    ):
        normalized = normalize_ocr_text(value, maximum_chars=maximum_chars)
        if normalized and normalized not in values:
            values.append(normalized)

    combined = "\n".join(values)
    return combined[:maximum_chars].rstrip()


def _build_searchable_text(*, ocr_text: str, context_text: str) -> str:
    """OCR 텍스트가 임베딩될 때 주변 구조 문맥도 함께 검색되도록 직렬화한다."""

    if not context_text:
        return f"[이미지 OCR]\n{ocr_text}"
    return f"[이미지 OCR]\n{ocr_text}\n\n[주변 문맥]\n{context_text}"


def _merge_units(
    document: ParsedDocument,
    recognized: tuple[_RecognizedImageUnit, ...],
) -> tuple[ParsedDocumentUnit, ...]:
    """OCR 단위를 같은 페이지·문단·슬라이드·시트의 마지막 텍스트 뒤에 삽입한다."""

    if not recognized:
        return document.units

    anchor_to_units: dict[str, list[ParsedDocumentUnit]] = defaultdict(list)
    unmatched: list[ParsedDocumentUnit] = []

    for item in recognized:
        anchor = _anchor_key(document.file_type, item.image.source_metadata)
        if anchor is None:
            unmatched.append(item.unit)
        else:
            anchor_to_units[anchor].append(item.unit)

    last_indexes: dict[str, int] = {}
    for index, unit in enumerate(document.units):
        anchor = _anchor_key(document.file_type, unit.source_metadata)
        if anchor is not None:
            last_indexes[anchor] = index

    merged: list[ParsedDocumentUnit] = []
    inserted_anchors: set[str] = set()

    for index, unit in enumerate(document.units):
        merged.append(unit)
        anchor = _anchor_key(document.file_type, unit.source_metadata)
        if anchor is None or last_indexes.get(anchor) != index:
            continue
        merged.extend(anchor_to_units.get(anchor, ()))
        inserted_anchors.add(anchor)

    # 텍스트 파서가 해당 위치의 빈 unit을 만들지 않은 경우에도 OCR 결과를 잃지 않는다.
    for anchor, units in anchor_to_units.items():
        if anchor not in inserted_anchors:
            merged.extend(units)
    merged.extend(unmatched)
    return tuple(merged)


def _anchor_key(
    file_type: DocumentType,
    metadata: Mapping[str, SourceMetadataValue],
) -> str | None:
    """문서 형식별 원본 위치를 OCR unit 병합용 안정적인 문자열 키로 변환한다."""

    mapping = metadata

    if file_type is DocumentType.PDF:
        page_number = mapping.get("page_number")
        return f"page:{page_number}" if _is_int(page_number) else None

    if file_type is DocumentType.DOCX:
        paragraph_index = mapping.get("paragraph_index")
        if _is_int(paragraph_index):
            return f"paragraph:{paragraph_index}"
        block_index = mapping.get("block_index")
        return f"block:{block_index}" if _is_int(block_index) else None

    if file_type is DocumentType.PPTX:
        slide_number = mapping.get("slide_number")
        return f"slide:{slide_number}" if _is_int(slide_number) else None

    if file_type is DocumentType.XLSX:
        sheet_name = mapping.get("sheet_name")
        if isinstance(sheet_name, str) and sheet_name:
            return f"sheet:{sheet_name}"
        sheet_index = mapping.get("sheet_index")
        return f"sheet-index:{sheet_index}" if _is_int(sheet_index) else None

    return None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
