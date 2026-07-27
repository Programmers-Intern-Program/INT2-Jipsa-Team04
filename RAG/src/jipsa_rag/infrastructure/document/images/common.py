"""문서 이미지 추출기가 공유하는 안전 검증과 XML 보조 함수를 제공한다."""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, LargeZipFile, ZipFile

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentReadError,
    EncryptedDocumentError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.images.models import ExtractedDocumentImage
from jipsa_rag.infrastructure.document.models import DocumentType

_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"


def build_image_id(*parts: object, content: bytes) -> str:
    """원본 위치와 이미지 내용으로 로그에 안전한 결정적 이미지 ID를 생성한다."""

    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="strict"))
        digest.update(b"\x00")
    digest.update(content)
    return digest.hexdigest()


def image_sha256(content: bytes) -> str:
    """이미지 원문을 노출하지 않는 SHA-256 식별자를 반환한다."""

    return hashlib.sha256(content).hexdigest()


def is_decorative_image(
    content: bytes,
    *,
    width_px: int | None,
    height_px: int | None,
    settings: DocumentProcessingSettings,
) -> bool:
    """작은 아이콘·로고·장식 이미지로 판단되는지 반환한다.

    크기를 알 수 없는 이미지는 오탐 방지를 위해 장식으로 단정하지 않는다.
    지나치게 작은 바이트, 폭·높이·면적 또는 극단적인 종횡비 중 하나라도
    기준을 위반하면 OCR 후보에서 제외한다.
    """

    if not settings.image_decorative_filter_enabled:
        return False
    if len(content) < settings.image_min_bytes:
        return True
    if width_px is None or height_px is None:
        return False
    if width_px < settings.image_min_width_px or height_px < settings.image_min_height_px:
        return True
    if width_px * height_px < settings.image_min_area_pixels:
        return True
    shorter = min(width_px, height_px)
    longer = max(width_px, height_px)
    return longer / max(shorter, 1) > settings.image_max_aspect_ratio


def validate_image_bytes(
    content: bytes,
    *,
    width_px: int | None,
    height_px: int | None,
    settings: DocumentProcessingSettings,
) -> bool:
    """비정상 크기와 과도한 해상도의 이미지를 거부한다."""

    if not content or len(content) > settings.image_max_bytes:
        return False
    if width_px is None or height_px is None:
        return True
    if width_px <= 0 or height_px <= 0:
        return False
    return width_px * height_px <= settings.image_max_pixels


def can_append_image(
    images: Sequence[ExtractedDocumentImage],
    content: bytes,
    *,
    width_px: int | None,
    height_px: int | None,
    settings: DocumentProcessingSettings,
    apply_decorative_filter: bool = True,
) -> bool:
    """중복·장식·크기·개수·총량 제한을 모두 만족하는지 확인한다."""

    if len(images) >= settings.image_max_count_per_document:
        return False
    if not validate_image_bytes(content, width_px=width_px, height_px=height_px, settings=settings):
        return False
    if apply_decorative_filter and is_decorative_image(
        content, width_px=width_px, height_px=height_px, settings=settings
    ):
        return False
    if settings.image_hash_dedup_enabled:
        digest = image_sha256(content)
        if any(image_sha256(image.content) == digest for image in images):
            return False
    current_bytes = sum(len(image.content) for image in images)
    return current_bytes + len(content) <= settings.image_max_total_bytes


def normalize_extension(extension: str | None) -> str:
    """이미지 확장자를 점 없이 소문자로 정규화한다."""

    normalized = (extension or "png").strip().lower().lstrip(".")
    return normalized or "png"


def guess_media_type(extension: str) -> str:
    """확장자로 MIME Type을 계산하고 알 수 없으면 octet-stream을 반환한다."""

    guessed, _ = mimetypes.guess_type(f"image.{extension}")
    return guessed or "application/octet-stream"


def parse_relationships(xml_bytes: bytes) -> dict[str, str]:
    """OOXML 관계 XML을 ``relationship id -> target`` 사전으로 변환한다."""

    root = ElementTree.fromstring(xml_bytes)
    relationships: dict[str, str] = {}

    for relationship in root.findall(f"{{{_RELATIONSHIP_NAMESPACE}}}Relationship"):
        relationship_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        target_mode = relationship.attrib.get("TargetMode")
        if relationship_id and target and target_mode != "External":
            relationships[relationship_id] = target

    return relationships


def resolve_part_target(source_part: str, target: str) -> str:
    """OOXML 상대 관계 경로를 ZIP 내부의 정규 POSIX 경로로 변환한다."""

    source_directory = PurePosixPath(source_part).parent
    candidate = source_directory / target
    normalized_parts: list[str] = []

    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)

    return PurePosixPath(*normalized_parts).as_posix()


def xml_text(element: ElementTree.Element, text_tag: str) -> str:
    """지정한 QName의 모든 텍스트를 읽기 쉬운 한 줄로 결합한다."""

    values = [
        node.text.strip() for node in element.iter(text_tag) if node.text and node.text.strip()
    ]
    return " ".join(values)


def validate_ooxml_image_package(
    file_path: Path,
    *,
    file_type: DocumentType,
    required_members: frozenset[str],
    settings: DocumentProcessingSettings,
) -> None:
    """이미지 바이트를 해제하기 전에 OOXML ZIP 안전 한계를 검증한다.

    기존 텍스트 파서도 동일한 방어 정책을 적용하지만 Hybrid Parser는 이미지
    추출을 텍스트 파싱보다 먼저 시작할 수 있다. 따라서 중앙 디렉터리만 읽는
    가벼운 검사를 이미지 추출 경계에서도 수행하여 ZIP bomb가 ``package.read()``에
    먼저 도달하지 않게 한다.
    """

    ensure_regular_file(file_path)

    try:
        with ZipFile(file_path, allowZip64=True) as package:
            infos = tuple(package.infolist())
    except (BadZipFile, LargeZipFile) as error:
        raise InvalidDocumentError(file_type) from error
    except OSError as error:
        raise DocumentReadError(file_path) from error

    if not infos or len(infos) > settings.ooxml_max_member_count:
        raise InvalidDocumentError(file_type)

    seen_names: set[str] = set()
    total_uncompressed_bytes = 0

    for info in infos:
        member_path = PurePosixPath(info.filename)
        if (
            not info.filename
            or "\\" in info.filename
            or "\x00" in info.filename
            or member_path.is_absolute()
            or ".." in member_path.parts
            or info.filename in seen_names
        ):
            raise InvalidDocumentError(file_type)
        seen_names.add(info.filename)

        if info.flag_bits & 0x1:
            raise EncryptedDocumentError(file_type)
        if info.file_size < 0 or info.compress_size < 0:
            raise InvalidDocumentError(file_type)
        if info.file_size > settings.ooxml_max_member_uncompressed_bytes:
            raise InvalidDocumentError(file_type)

        total_uncompressed_bytes += info.file_size
        if total_uncompressed_bytes > settings.ooxml_max_total_uncompressed_bytes:
            raise InvalidDocumentError(file_type)

        if info.is_dir() or info.file_size == 0:
            continue
        if info.compress_size == 0:
            raise InvalidDocumentError(file_type)
        if info.file_size / info.compress_size > settings.ooxml_max_compression_ratio:
            raise InvalidDocumentError(file_type)

    members = frozenset(seen_names)
    if not required_members.issubset(members):
        raise InvalidDocumentError(file_type)


def ensure_regular_file(file_path: Path) -> None:
    """이미지 추출 대상이 존재하는 일반 파일인지 검증한다."""

    if not file_path.is_file():
        raise DocumentFileNotFoundError(file_path)
