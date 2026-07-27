"""문서 내부에서 추출하거나 렌더링한 이미지의 불변 모델을 정의한다."""

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from jipsa_rag.infrastructure.document.models import SourceMetadata


class DocumentImageKind(StrEnum):
    """OCR 후보 이미지가 문서에서 생성된 방식을 구분한다."""

    PDF_EMBEDDED = "pdf_embedded"
    PDF_PAGE_RENDER = "pdf_page_render"
    DOCX_INLINE = "docx_inline"
    DOCX_FLOATING = "docx_floating"
    PPTX_PICTURE = "pptx_picture"
    PPTX_CHART_RENDER = "pptx_chart_render"
    PPTX_SMARTART_RENDER = "pptx_smartart_render"
    XLSX_PICTURE = "xlsx_picture"
    XLSX_CHART_RENDER = "xlsx_chart_render"


@dataclass(frozen=True, slots=True)
class ExtractedDocumentImage:
    """메모리 안에서 OCR에 전달할 단일 이미지와 원본 위치를 보관한다."""

    image_id: str
    kind: DocumentImageKind
    content: bytes
    media_type: str
    extension: str
    width_px: int | None = None
    height_px: int | None = None
    ocr_candidate: bool = True
    source_metadata: SourceMetadata = field(default_factory=dict)
    context_before: str = ""
    context_current: str = ""
    context_after: str = ""

    @property
    def sha256(self) -> str:
        """중복 판정과 추적에 사용하는 원문 비노출 해시를 반환한다."""

        return hashlib.sha256(self.content).hexdigest()

    def __post_init__(self) -> None:
        """가변 바이트·메타데이터 참조가 외부에서 변경되지 않도록 고정한다."""

        object.__setattr__(self, "content", bytes(self.content))
        object.__setattr__(
            self,
            "source_metadata",
            MappingProxyType(dict(self.source_metadata)),
        )


@dataclass(frozen=True, slots=True)
class ImageOnlyLocation:
    """텍스트가 거의 없고 이미지 OCR이 필요한 페이지·슬라이드·시트를 나타낸다."""

    location_id: str
    source_metadata: SourceMetadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_metadata",
            MappingProxyType(dict(self.source_metadata)),
        )


@dataclass(frozen=True, slots=True)
class DocumentImageExtraction:
    """한 문서에서 추출된 이미지와 이미지 전용 위치 탐지 결과다."""

    images: tuple[ExtractedDocumentImage, ...]
    image_only_locations: tuple[ImageOnlyLocation, ...] = ()
    document_metadata: SourceMetadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(
            self,
            "image_only_locations",
            tuple(self.image_only_locations),
        )
        object.__setattr__(
            self,
            "document_metadata",
            MappingProxyType(dict(self.document_metadata)),
        )

    @classmethod
    def empty(cls) -> "DocumentImageExtraction":
        """이미지 추출 기능이 비활성화되었거나 대상이 없는 결과를 반환한다."""

        return cls(images=())

    @property
    def ocr_candidate_count(self) -> int:
        """실제로 OCR 엔진에 전달할 이미지 개수를 반환한다."""

        return sum(image.ocr_candidate for image in self.images)
