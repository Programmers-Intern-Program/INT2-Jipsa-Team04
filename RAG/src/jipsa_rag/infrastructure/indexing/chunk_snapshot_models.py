"""최종 활성 색인의 청크 스냅샷 모델을 정의한다."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from uuid import UUID

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")

# 애플리케이션 서버에 전달하는 source_metadata는 JSON으로 표현 가능한
# 단순 스칼라 값과 스칼라 배열만 허용한다.
#
# tuple을 허용하는 이유는 Local RAG 내부에서 메타데이터를 불변 값으로
# 유지하면서, HTTP payload 생성 시 Pydantic의 mode="json" 직렬화를 통해
# JSON 배열로 안전하게 변환하기 위해서다.
type SnapshotMetadataScalar = str | int | float | bool | None
type SnapshotMetadataValue = SnapshotMetadataScalar | tuple[SnapshotMetadataScalar, ...]
type SnapshotMetadata = Mapping[
    str,
    SnapshotMetadataValue,
]


def _normalize_sha256(
    value: str,
    *,
    field_name: str,
) -> str:
    """SHA-256 16진수 문자열을 소문자로 정규화하고 형식을 검증한다."""

    normalized_value = value.strip().lower()

    if _SHA256_PATTERN.fullmatch(normalized_value) is None:
        raise ValueError(f"{field_name} must be a 64-character SHA-256 hexadecimal string.")

    return normalized_value


def _freeze_metadata(
    metadata: SnapshotMetadata,
) -> SnapshotMetadata:
    """출처 메타데이터를 복사한 뒤 읽기 전용 Mapping으로 고정한다."""

    normalized_metadata: dict[
        str,
        SnapshotMetadataValue,
    ] = {}

    for key, value in metadata.items():
        normalized_key = key.strip()

        if not normalized_key:
            raise ValueError("source metadata keys must not be empty.")

        # tuple은 이미 불변이지만, 외부에서 전달된 tuple 하위 타입이나
        # 사용자 정의 객체 참조가 그대로 유지되지 않도록 새 tuple로 복사한다.
        if isinstance(value, tuple):
            normalized_value: SnapshotMetadataValue = tuple(value)
        else:
            normalized_value = value

        normalized_metadata[normalized_key] = normalized_value

    # 스냅샷 생성 이후 메타데이터가 변경되지 않도록
    # 일반 dict를 읽기 전용 MappingProxyType으로 감싼다.
    return MappingProxyType(normalized_metadata)


@dataclass(
    frozen=True,
    slots=True,
)
class IndexedChunkSnapshot:
    """최종 활성 문서에 속한 단일 청크의 전송용 읽기 전용 스냅샷.

    이 모델에는 AWS 애플리케이션 서버와 동기화해야 하는 원문과
    식별 정보만 포함한다.

    임베딩 벡터, Presigned URL, 내부 인증 토큰, DB 접속 정보는
    의도적으로 포함하지 않는다.
    """

    chunk_id: str
    chunk_index: int
    content: str
    content_hash: str
    token_count: int | None = None
    source_metadata: SnapshotMetadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        """청크 식별자, 순번, 원문, 해시와 메타데이터를 검증한다."""

        try:
            normalized_chunk_id = str(UUID(self.chunk_id.strip()))
        except ValueError as error:
            raise ValueError("chunk_id must be a valid UUID string.") from error

        # bool은 int의 하위 타입이므로 False가 0으로 처리되지 않도록
        # 명시적으로 거부한다.
        if (
            isinstance(
                self.chunk_index,
                bool,
            )
            or self.chunk_index < 0
        ):
            raise ValueError("chunk_index must be greater than or equal to zero.")

        # 청크 원문은 Content_Hash를 계산한 문자열과 정확히 같아야 하므로
        # strip()이나 줄바꿈 정규화를 수행하지 않는다.
        if not self.content:
            raise ValueError("content must not be empty.")

        # token_count는 임베딩 토크나이저 계산 전에는 None일 수 있다.
        #
        # 값이 존재하는 경우에는 bool이 아닌 0 이상의 정수만 허용한다.
        # 두 조건을 하나의 if로 결합하여 Ruff SIM102 규칙을 준수한다.
        if self.token_count is not None and (
            isinstance(
                self.token_count,
                bool,
            )
            or self.token_count < 0
        ):
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


@dataclass(
    frozen=True,
    slots=True,
)
class IndexedDocumentSnapshot:
    """최종 활성 문서와 해당 문서의 전체 청크를 묶은 스냅샷."""

    rag_document_idx: int
    users_idx: int
    file_idx: int
    index_version: int
    chunk_count: int
    chunks: tuple[
        IndexedChunkSnapshot,
        ...,
    ]

    def __post_init__(self) -> None:
        """문서 식별 정보와 전체 청크 집합의 일관성을 검증한다."""

        positive_integer_fields = {
            "rag_document_idx": (self.rag_document_idx),
            "users_idx": self.users_idx,
            "file_idx": self.file_idx,
            "index_version": (self.index_version),
            "chunk_count": self.chunk_count,
        }

        for (
            field_name,
            value,
        ) in positive_integer_fields.items():
            # bool은 int의 하위 타입이므로 True가 1로 처리되지 않도록
            # 명시적으로 거부한다.
            if (
                isinstance(
                    value,
                    bool,
                )
                or value <= 0
            ):
                raise ValueError(f"{field_name} must be greater than zero.")

        # 호출자가 list 등 변경 가능한 컬렉션을 전달하더라도
        # 모델 내부에서는 불변 tuple로 고정한다.
        normalized_chunks = tuple(self.chunks)

        if not normalized_chunks:
            raise ValueError("chunks must contain at least one active chunk.")

        # RAG_Document.Chunk_Count와 실제 조회된 RAG_Chunk 행 수가 다르면
        # 일부 청크만 전송되어 AWS DB가 불완전한 스냅샷을 저장할 수 있다.
        if self.chunk_count != len(normalized_chunks):
            raise ValueError("chunk_count must match the number of active chunks.")

        expected_chunk_indexes = tuple(range(len(normalized_chunks)))
        actual_chunk_indexes = tuple(chunk.chunk_index for chunk in normalized_chunks)

        # 애플리케이션 서버는 chunk_index를 기준으로 문서 순서를 복원하므로
        # 최종 활성 청크는 0부터 중간 누락 없이 연속되어야 한다.
        if actual_chunk_indexes != expected_chunk_indexes:
            raise ValueError("active chunk indexes must be contiguous and start at zero.")

        chunk_ids = tuple(chunk.chunk_id for chunk in normalized_chunks)

        # 동일 payload 안에서 같은 Chunk ID가 두 번 전달되면
        # AWS DB UPSERT 또는 유일성 제약 처리 결과가 불명확해질 수 있다.
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("active chunk IDs must be unique.")

        object.__setattr__(
            self,
            "chunks",
            normalized_chunks,
        )
