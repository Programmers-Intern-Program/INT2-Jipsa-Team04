"""RAG 인제스트 완료 콜백에서 사용하는 외부 API 스키마를 정의한다."""

import re
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# 문서 파서와 청커가 생성하는 출처 메타데이터는 JSON으로 전달할 수 있는
# 단순 스칼라 또는 스칼라 배열만 허용한다.
#
# tuple은 Local RAG 내부 모델에서 사용하는 읽기 전용 메타데이터 표현을
# 그대로 DTO로 옮길 수 있도록 허용한다. HTTP payload 생성 시
# model_dump(mode="json")을 사용하면 tuple은 JSON 배열로 변환된다.
type SourceMetadataScalar = str | int | float | bool | None
type SourceMetadataValue = SourceMetadataScalar | tuple[SourceMetadataScalar, ...]


class ChunkSynchronizationRequest(BaseModel):
    """애플리케이션 서버와 동기화할 단일 RAG 청크 데이터."""

    # 청크 원문은 Content_Hash를 계산할 때 사용한 문자열과
    # 완전히 같아야 한다.
    #
    # 따라서 전역 str_strip_whitespace는 적용하지 않고,
    # 식별자와 해시만 개별 validator에서 정규화한다.
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    chunk_id: str = Field(
        min_length=1,
        max_length=36,
        description=(
            "RAG가 결정적으로 생성한 UUID 형식의 청크 식별자다. "
            "AWS DB의 BIGINT AUTO_INCREMENT Chunk_IDX와 "
            "별도로 저장한다."
        ),
        examples=[
            "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612",
        ],
    )

    chunk_index: int = Field(
        ge=0,
        description="문서 안에서 0부터 시작하는 청크 순번",
        examples=[0],
    )

    content: str = Field(
        min_length=1,
        description=("애플리케이션 서버에서 검색과 인용에 사용할 청크 원문"),
        examples=[
            "동기화할 청크 원문",
        ],
    )

    content_hash: str = Field(
        min_length=64,
        max_length=64,
        description="청크 원문의 SHA-256 소문자 16진수 해시",
        examples=[
            "a" * 64,
        ],
    )

    token_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "임베딩 모델 토크나이저 기준 토큰 수다. 아직 계산하지 않은 경우에는 null을 전달한다."
        ),
        examples=[512],
    )

    source_metadata: dict[str, SourceMetadataValue] = Field(
        default_factory=dict,
        description=(
            "원문 위치를 나타내는 페이지, 슬라이드, 시트 또는 "
            "섹션 등의 JSON 직렬화 가능한 출처 메타데이터"
        ),
        examples=[
            {
                "page_number": 1,
            }
        ],
    )

    @field_validator("chunk_id")
    @classmethod
    def validate_chunk_id(
        cls,
        value: str,
    ) -> str:
        """Chunk ID를 표준 UUID 문자열로 정규화한다."""

        try:
            return str(
                UUID(
                    value.strip(),
                )
            )
        except ValueError as error:
            raise ValueError("chunk_id must be a valid UUID string.") from error

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(
        cls,
        value: str,
    ) -> str:
        """Content Hash를 소문자로 정규화하고 SHA-256 형식을 검증한다."""

        normalized_value = value.strip().lower()

        if _SHA256_PATTERN.fullmatch(normalized_value) is None:
            raise ValueError("content_hash must be a 64-character SHA-256 hexadecimal string.")

        return normalized_value


class IngestCompleteRequest(BaseModel):
    """RAG 서버가 애플리케이션 서버에 전달하는 인제스트 완료 결과."""

    # 백엔드와 RAG 사이의 계약에 정의되지 않은 필드가
    # 실수로 전송되지 않도록 추가 필드를 허용하지 않는다.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    success: bool = Field(
        description="파일 인제스트와 색인 처리의 최종 성공 여부",
        examples=[True],
    )

    index_version: int | None = Field(
        default=None,
        gt=0,
        description=(
            "전달한 청크를 생성할 때 사용한 색인 버전이다. "
            "청크 동기화 데이터가 없는 기존 성공 콜백 또는 "
            "실패 콜백에서는 생략한다."
        ),
        examples=[2],
    )

    chunk_count: int | None = Field(
        default=None,
        gt=0,
        description=(
            "동기화 payload에 포함된 청크 수다. "
            "청크 동기화 데이터가 없는 기존 성공 콜백 또는 "
            "실패 콜백에서는 생략한다."
        ),
        examples=[2],
    )

    chunks: tuple[ChunkSynchronizationRequest, ...] | None = Field(
        default=None,
        min_length=1,
        description=("AWS DB와 동기화할 최신 청크 데이터 목록이다. 임베딩 벡터는 포함하지 않는다."),
    )

    error_message: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "인제스트 실패 원인에 대한 외부 공개용 메시지다. 성공한 경우에는 전송하지 않는다."
        ),
        examples=[
            "INVALID_DOCUMENT: The document structure is invalid.",
        ],
    )

    @model_validator(mode="after")
    def validate_result_contract(self) -> Self:
        """성공 여부와 청크 동기화 및 오류 필드 조합을 검증한다."""

        synchronization_fields = (
            self.index_version,
            self.chunk_count,
            self.chunks,
        )

        has_any_synchronization_field = any(value is not None for value in synchronization_fields)

        has_all_synchronization_fields = all(value is not None for value in synchronization_fields)

        if self.success:
            if self.error_message is not None:
                raise ValueError("Successful ingestion must not include an error message.")

            # 최신 활성 청크 조회와 성공 콜백 연결은
            # 후속 작업에서 추가한다.
            #
            # 그 전까지 기존 success-only 콜백도 허용하되,
            # 동기화 데이터를 보내는 경우에는 index_version,
            # chunk_count, chunks를 하나의 완전한 묶음으로 전송한다.
            if has_any_synchronization_field and not has_all_synchronization_fields:
                raise ValueError(
                    "Chunk synchronization requires index_version, chunk_count, and chunks."
                )

            return self

        if not self.error_message:
            raise ValueError("Failed ingestion must include an error message.")

        if has_any_synchronization_field:
            raise ValueError("Failed ingestion must not include chunk synchronization data.")

        return self
