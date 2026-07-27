"""이미지 메타데이터·중복·장식 필터와 자원 제한을 검증한다."""

from typing import Any

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.common import (
    can_append_image,
    image_sha256,
    is_decorative_image,
)
from jipsa_rag.infrastructure.document.images.models import (
    DocumentImageKind,
    ExtractedDocumentImage,
)


def _settings(**overrides: Any) -> DocumentProcessingSettings:
    values: dict[str, Any] = {
        "image_min_bytes": 10,
        "image_min_width_px": 32,
        "image_min_height_px": 16,
        "image_min_area_pixels": 512,
        "image_max_aspect_ratio": 8.0,
        "ocr_timeout_seconds": 1.0,
        "ocr_document_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return DocumentProcessingSettings(**values)


def _image(content: bytes) -> ExtractedDocumentImage:
    return ExtractedDocumentImage(
        image_id="safe-id",
        kind=DocumentImageKind.PDF_EMBEDDED,
        content=content,
        media_type="image/png",
        extension="png",
        width_px=100,
        height_px=100,
        source_metadata={"page_number": 1, "image_index": 1},
    )


def test_sha256_is_deterministic_without_exposing_original_bytes() -> None:
    content = b"same-image-content"

    digest = image_sha256(content)

    assert digest == image_sha256(content)
    assert content.decode() not in digest
    assert len(digest) == 64


def test_duplicate_image_is_rejected_by_content_hash() -> None:
    content = b"a" * 128
    settings = _settings()

    assert not can_append_image(
        (_image(content),),
        content,
        width_px=100,
        height_px=100,
        settings=settings,
    )


def test_small_icon_and_extreme_banner_are_decorative() -> None:
    settings = _settings()

    assert is_decorative_image(
        b"a" * 128,
        width_px=16,
        height_px=16,
        settings=settings,
    )
    assert is_decorative_image(
        b"a" * 128,
        width_px=800,
        height_px=20,
        settings=settings,
    )


def test_regular_document_image_is_not_decorative() -> None:
    assert not is_decorative_image(
        b"a" * 128,
        width_px=320,
        height_px=180,
        settings=_settings(),
    )


def test_image_count_and_total_byte_limits_are_enforced() -> None:
    # 설정 모델이 허용하는 최소 문서 총량(1 MiB) 안에서 두 이미지의 합이
    # 한계를 넘도록 구성한다. 테스트를 위해 운영 안전 하한을 낮추지 않는다.
    existing = (_image(b"a" * 600_000),)

    assert not can_append_image(
        existing,
        b"b" * 128,
        width_px=100,
        height_px=100,
        settings=_settings(image_max_count_per_document=1),
    )
    assert not can_append_image(
        existing,
        b"b" * 600_000,
        width_px=100,
        height_px=100,
        settings=_settings(
            image_max_total_bytes=1_048_576,
            image_max_bytes=1_048_576,
        ),
    )
