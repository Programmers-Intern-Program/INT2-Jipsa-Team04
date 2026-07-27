"""Office 렌더링 결과를 이미지 추출 계층에 전달하는 불변 모델을 정의한다."""

from dataclasses import dataclass, field
from types import MappingProxyType

from jipsa_rag.infrastructure.document.images.models import DocumentImageKind
from jipsa_rag.infrastructure.document.models import SourceMetadata


@dataclass(frozen=True, slots=True)
class RenderedOfficeVisual:
    """Microsoft Office가 원본 시각 요소에서 직접 내보낸 단일 PNG 이미지."""

    kind: DocumentImageKind
    content: bytes
    width_px: int | None
    height_px: int | None
    source_metadata: SourceMetadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        """외부 가변 참조가 렌더 결과를 변경하지 못하도록 복사해 고정한다."""

        object.__setattr__(self, "content", bytes(self.content))
        object.__setattr__(
            self,
            "source_metadata",
            MappingProxyType(dict(self.source_metadata)),
        )


@dataclass(frozen=True, slots=True)
class OfficeVisualRenderResult:
    """한 Office 문서에서 렌더링한 시각 요소와 렌더러 상태를 보관한다."""

    visuals: tuple[RenderedOfficeVisual, ...]
    renderer_available: bool
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "visuals", tuple(self.visuals))

    @classmethod
    def unavailable(cls, reason: str) -> "OfficeVisualRenderResult":
        """렌더러 비활성화 또는 실행 불가 상태를 일관된 결과로 반환한다."""

        return cls(
            visuals=(),
            renderer_available=False,
            failure_reason=reason,
        )
