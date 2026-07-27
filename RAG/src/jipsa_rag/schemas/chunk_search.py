"""관련 청크 검색 API에서 사용하는 요청 및 응답 스키마를 정의한다."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.reference_files import (
    MAX_REFERENCE_FILE_COUNT,
    ReferenceFileIdxs,
)
from jipsa_rag.schemas.source_locator import SourceLocator, build_source_locator


class ChunkSearchRequest(BaseModel):
    """사용자가 선택한 문서 범위에서 관련 청크를 검색하기 위한 요청."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    user_idx: int = Field(
        gt=0,
        description=(
            "AWS 서버 DB Users.Users_IDX 식별자다. "
            "Qdrant users_idx 필터로 변환하여 사용자 간 결과를 격리한다."
        ),
        examples=[45],
    )
    reference_file_idxs: ReferenceFileIdxs = Field(
        description=(
            "검색 범위로 확정한 AWS 서버 DB File.File_IDX 목록이다. "
            f"1개 이상 {MAX_REFERENCE_FILE_COUNT}개 이하의 서로 다른 양의 정수만 허용한다. "
            "검증 후 tuple로 고정하여 검색 중 선택 범위가 변경되지 않게 한다."
        ),
        examples=[[123, 456]],
    )
    query: str = Field(
        min_length=1,
        max_length=4096,
        description="관련 청크를 찾을 사용자 질의",
        examples=["서로 다른 형식의 문서를 비교해줘"],
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="관련도 점수가 높은 순서로 반환할 최대 청크 수",
        examples=[5],
    )
    score_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Qdrant Cosine 검색 결과에 적용할 선택적 최소 점수",
        examples=[0.6],
    )


class ChunkSearchResult(BaseModel):
    """Qdrant에서 검색된 단일 활성 문서 청크 응답.

    ``page``·``slide_no``·``sheet_name``·``section_title``은 기존 클라이언트와
    테스트의 하위 호환성을 위해 유지한다. 새 코드와 외부 UI는 형식과 OCR을
    함께 표현하는 ``source_locator``를 기준으로 위치를 해석한다.
    """

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    chunk_id: str = Field(
        min_length=1,
        max_length=64,
        description="Local RAG DB RAG_Chunk.Chunk_ID와 동일한 Qdrant Point ID",
    )
    score: float = Field(
        ge=-1.0,
        le=1.0,
        description="질의 임베딩과 청크 임베딩 사이의 Cosine 관련도 점수",
    )
    rag_document_idx: int = Field(gt=0)
    file_idx: int = Field(gt=0)
    folder_idx: int | None = Field(default=None, gt=0)
    file_name: str = Field(min_length=1, max_length=255)
    file_type: SupportedFileType = Field(
        description="PDF, DOCX, PPTX, TXT 또는 XLSX 원본 형식",
    )
    chunk_index: int = Field(ge=0)
    content: str = Field(
        min_length=1,
        description="일반 텍스트 또는 OCR 텍스트를 포함하는 LLM 근거 원문",
    )
    token_count: int | None = Field(default=None, ge=0)

    # 기존 API 호환 필드
    page: int | None = Field(default=None, gt=0)
    slide_no: int | None = Field(default=None, gt=0)
    sheet_name: str | None = Field(default=None, min_length=1, max_length=255)
    section_title: str | None = Field(default=None, min_length=1, max_length=500)

    source_locator: SourceLocator | None = Field(
        default=None,
        description=(
            "문서 형식별 원본 위치와 OCR 이미지 위치를 함께 표현하는 공통 출처 위치"
        ),
    )

    parser_version: str = Field(min_length=1, max_length=100)
    embedding_model: str = Field(min_length=1, max_length=255)
    index_version: int = Field(gt=0)

    @model_validator(mode="after")
    def normalize_source_locator(self) -> Self:
        """legacy 위치만 전달된 경우에도 공통 locator를 항상 채운다."""

        locator = self.source_locator
        if locator is None:
            locator = build_source_locator(
                file_type=self.file_type,
                legacy_page=self.page,
                legacy_slide_no=self.slide_no,
                legacy_sheet_name=self.sheet_name,
                legacy_section_title=self.section_title,
            )
            object.__setattr__(self, "source_locator", locator)

        if locator.file_type is not self.file_type:
            raise ValueError("source_locator.file_type must match file_type.")

        # 새 locator와 legacy 필드가 동시에 전달되면 서로 다른 위치를 가리키지
        # 않도록 검증한다. 값이 없는 legacy 필드는 locator에서 역으로 채워
        # 기존 클라이언트가 계속 페이지·슬라이드·시트를 표시할 수 있게 한다.
        for field_name, locator_value in (
            ("page", locator.page),
            ("slide_no", locator.slide_no),
            ("sheet_name", locator.sheet_name),
            ("section_title", locator.section_title),
        ):
            legacy_value = getattr(self, field_name)
            if legacy_value is not None and locator_value is not None:
                if legacy_value != locator_value:
                    raise ValueError(f"{field_name} must match source_locator.")
            elif legacy_value is None and locator_value is not None:
                object.__setattr__(self, field_name, locator_value)

        return self


class ChunkSearchResponse(BaseModel):
    """사용자와 선택 문서 범위에 제한된 관련 청크 검색 결과."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    user_idx: int = Field(gt=0)
    result_count: int = Field(ge=0)
    results: tuple[ChunkSearchResult, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_result_count(self) -> Self:
        """result_count와 실제 결과 목록 길이가 일치하는지 검증한다."""

        if self.result_count != len(self.results):
            raise ValueError("result_count must match the number of results.")
        return self
