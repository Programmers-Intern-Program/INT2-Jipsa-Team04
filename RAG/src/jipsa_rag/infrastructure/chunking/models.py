"""문서 청킹 단계에서 사용하는 공통 입력 및 결과 모델을 정의한다."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from uuid import UUID

from jipsa_rag.infrastructure.document.models import DocumentType

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


type ChunkMetadataScalar = str | int | float | bool | None
type ChunkMetadataValue = ChunkMetadataScalar | tuple[ChunkMetadataScalar, ...]
type ChunkMetadata = Mapping[str, ChunkMetadataValue]


def _normalize_sha256(
    value: str,
    *,
    field_name: str,
) -> str:
    """SHA-256 hex 문자열을 소문자로 정규화하고 형식을 검증한다."""

    normalized_value = value.strip().lower()

    if _SHA256_PATTERN.fullmatch(normalized_value) is None:
        raise ValueError(f"{field_name} must be a 64-character SHA-256 hexadecimal string.")

    return normalized_value


def _normalize_required_text(
    value: str,
    *,
    field_name: str,
) -> str:
    """Chunk ID 식별 정보에 사용할 필수 문자열을 정규화한다."""

    normalized_value = value.strip()

    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty.")

    return normalized_value


def _freeze_metadata(
    metadata: ChunkMetadata,
) -> ChunkMetadata:
    """메타데이터를 복사한 뒤 읽기 전용 Mapping으로 변환한다."""

    return MappingProxyType(dict(metadata))


@dataclass(frozen=True, slots=True)
class ChunkingContext:
    """결정적 Chunk ID를 생성하는 데 필요한 문서·파서·임베딩 식별 정보.

    동일한 원본 파일이라도 파서 버전이나 임베딩 모델이 바뀌면
    이전 청크와 검색 의미가 달라질 수 있다.

    따라서 Chunk ID는 파일 식별 정보뿐 아니라 파서 버전과
    임베딩 모델 식별자까지 포함하여 서로 다른 색인 결과가
    같은 Qdrant Point ID 또는 Local RAG Chunk_ID를 공유하지 않게 한다.
    """

    users_idx: int
    file_idx: int
    file_hash: str
    parser_version: str
    embedding_model: str
    index_version: int = 1

    def __post_init__(self) -> None:
        """식별자, 파일 해시, 파서·임베딩 정보 및 색인 버전을 검증한다."""

        if self.users_idx <= 0:
            raise ValueError("users_idx must be greater than zero.")

        if self.file_idx <= 0:
            raise ValueError("file_idx must be greater than zero.")

        if self.index_version <= 0:
            raise ValueError("index_version must be greater than zero.")

        object.__setattr__(
            self,
            "file_hash",
            _normalize_sha256(
                self.file_hash,
                field_name="file_hash",
            ),
        )
        object.__setattr__(
            self,
            "parser_version",
            _normalize_required_text(
                self.parser_version,
                field_name="parser_version",
            ),
        )
        object.__setattr__(
            self,
            "embedding_model",
            _normalize_required_text(
                self.embedding_model,
                field_name="embedding_model",
            ),
        )


@dataclass(frozen=True, slots=True)
class TextChunk:
    """원본 문서의 일정 구간에서 생성된 단일 텍스트 청크."""

    chunk_id: str
    chunk_index: int
    content: str
    content_hash: str

    # Start/End Offset은 ParsedDocument.text 기준 문자 위치다.
    #
    # start_offset은 청크 첫 문자의 위치이며,
    # end_offset은 Python 문자열 슬라이싱과 동일한 exclusive 위치다.
    start_offset: int
    end_offset: int

    source_metadata: ChunkMetadata = field(default_factory=dict)

    # 정확한 Token Count는 실제 임베딩 모델의 토크나이저 기준으로
    # 계산해야 하므로 현재 문자 청킹 단계에서는 None으로 유지한다.
    token_count: int | None = None

    def __post_init__(self) -> None:
        """UUID, 해시, 순번, 오프셋 및 메타데이터를 검증한다."""

        try:
            normalized_chunk_id = str(UUID(self.chunk_id))
        except ValueError as error:
            raise ValueError("chunk_id must be a valid UUID string.") from error

        if self.chunk_index < 0:
            raise ValueError("chunk_index must be greater than or equal to zero.")

        if not self.content:
            raise ValueError("content must not be empty.")

        if self.start_offset < 0:
            raise ValueError("start_offset must be greater than or equal to zero.")

        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset.")

        # CharacterTextChunker는 원본 문자열의 연속 구간을 그대로 사용한다.
        #
        # 따라서 오프셋 구간의 문자 수와 실제 Content 길이가 다르면
        # 청크 위치 정보가 원문과 일치하지 않는 상태다.
        if self.end_offset - self.start_offset != len(self.content):
            raise ValueError("The chunk offset range must match the content length.")

        if self.token_count is not None and self.token_count < 0:
            raise ValueError("token_count must be greater than or equal to zero.")

        object.__setattr__(
            self,
            "chunk_id",
            normalized_chunk_id,
        )
        object.__setattr__(
            self,
            "content_hash",
            _normalize_sha256(
                self.content_hash,
                field_name="content_hash",
            ),
        )
        object.__setattr__(
            self,
            "source_metadata",
            _freeze_metadata(self.source_metadata),
        )


@dataclass(frozen=True, slots=True)
class ChunkedDocument:
    """문서 하나에서 생성된 전체 텍스트 청크 결과."""

    file_type: DocumentType
    chunks: tuple[TextChunk, ...]

    # 원본 ParsedDocument가 가진 전체 단위 수와
    # 실제 텍스트가 존재하는 단위 수를 함께 보관한다.
    source_unit_count: int
    text_unit_count: int

    def __post_init__(self) -> None:
        """청크 목록과 원본 단위 개수의 일관성을 검증한다."""

        normalized_chunks = tuple(self.chunks)

        if not normalized_chunks:
            raise ValueError("chunks must contain at least one text chunk.")

        if self.source_unit_count <= 0:
            raise ValueError("source_unit_count must be greater than zero.")

        if self.text_unit_count <= 0:
            raise ValueError("text_unit_count must be greater than zero.")

        if self.text_unit_count > self.source_unit_count:
            raise ValueError("text_unit_count must not exceed source_unit_count.")

        expected_chunk_indexes = tuple(range(len(normalized_chunks)))
        actual_chunk_indexes = tuple(chunk.chunk_index for chunk in normalized_chunks)

        # DB의 Chunk_Index는 문서 전체에서 0부터 연속적으로 증가하도록
        # 서비스 계층 정책을 고정한다.
        if actual_chunk_indexes != expected_chunk_indexes:
            raise ValueError("chunk_index values must start at zero and be contiguous.")

        chunk_ids = tuple(chunk.chunk_id for chunk in normalized_chunks)

        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("chunk_id values must be unique.")

        object.__setattr__(
            self,
            "chunks",
            normalized_chunks,
        )

    @property
    def chunk_count(self) -> int:
        """문서에서 생성된 전체 청크 수를 반환한다."""

        return len(self.chunks)
