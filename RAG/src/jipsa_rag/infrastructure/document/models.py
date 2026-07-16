"""문서 형식별 파서가 공통으로 반환하는 결과 모델을 정의한다."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class DocumentType(StrEnum):
    """문서 파서 계층에서 식별하는 원본 문서 형식."""

    PDF = "PDF"
    DOCX = "DOCX"
    XLSX = "XLSX"
    PPTX = "PPTX"


type SourceMetadataScalar = str | int | float | bool | None
type SourceMetadataValue = SourceMetadataScalar | tuple[SourceMetadataScalar, ...]
type SourceMetadata = Mapping[str, SourceMetadataValue]


def _freeze_metadata(
    metadata: SourceMetadata,
) -> SourceMetadata:
    """호출자가 전달한 메타데이터를 복사하고 읽기 전용으로 변환한다."""

    return MappingProxyType(dict(metadata))


@dataclass(frozen=True, slots=True)
class ParsedDocumentUnit:
    """문서에서 원본 위치 단위로 추출한 텍스트와 위치 정보."""

    text: str
    source_metadata: SourceMetadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        """외부 변경이 반영되지 않도록 메타데이터를 고정한다."""

        object.__setattr__(
            self,
            "source_metadata",
            _freeze_metadata(self.source_metadata),
        )


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """형식별 파서가 반환하는 문서 전체의 공통 파싱 결과."""

    file_type: DocumentType
    units: tuple[ParsedDocumentUnit, ...]
    document_metadata: SourceMetadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        """단위 목록과 문서 메타데이터를 불변 형태로 정규화한다."""

        object.__setattr__(
            self,
            "units",
            tuple(self.units),
        )
        object.__setattr__(
            self,
            "document_metadata",
            _freeze_metadata(self.document_metadata),
        )

    @property
    def text(self) -> str:
        """비어 있지 않은 단위 텍스트를 원본 순서대로 결합한다."""

        return "\n\n".join(unit.text for unit in self.units if unit.text)

    @property
    def unit_count(self) -> int:
        """원본 위치 단위의 전체 개수를 반환한다."""

        return len(self.units)

    @property
    def text_unit_count(self) -> int:
        """실제 추출 텍스트가 존재하는 단위의 개수를 반환한다."""

        return sum(bool(unit.text) for unit in self.units)
