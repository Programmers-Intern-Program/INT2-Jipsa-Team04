"""근거 기반 RAG 답변 생성에서 사용하는 요청 및 응답 스키마를 정의한다."""

import re
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.reference_files import (
    MAX_REFERENCE_FILE_COUNT,
    ReferenceFileIdxs,
)
from jipsa_rag.schemas.source_locator import SourceLocator, build_source_locator

_INSUFFICIENT_EVIDENCE_ANSWER: Final[str] = "제공된 문서 근거만으로는 답변할 수 없습니다."
_SOURCE_CITATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[(?P<source_id>SOURCE-[0-9]+)\]")
_VALID_SOURCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^SOURCE-[1-9][0-9]*$")


class RagAnswerStatus(StrEnum):
    """근거 기반 RAG 답변 처리 결과를 구분한다."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RagAnswerRequest(BaseModel):
    """선택한 사용자 문서에서 근거를 검색하고 답변을 생성하기 위한 요청."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    user_idx: int = Field(
        gt=0,
        description="AWS 서버 DB Users.Users_IDX 식별자",
        examples=[45],
    )
    reference_file_idxs: ReferenceFileIdxs = Field(
        description=(
            "질문 전송 시점에 답변 범위로 확정한 File.File_IDX 목록이다. "
            f"1개 이상 {MAX_REFERENCE_FILE_COUNT}개 이하의 서로 다른 양의 정수만 허용한다."
        ),
        examples=[[123, 456]],
    )
    query: str = Field(
        min_length=1,
        max_length=4096,
        description="문서 근거를 검색하고 답변할 사용자 질문",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="lookup 또는 문서별 synthesis 검색에 사용할 최대 청크 수",
    )
    score_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Qdrant Cosine 검색 결과에 적용할 선택적 최소 점수",
    )


class RagAnswerSource(BaseModel):
    """최종 답변에 실제로 인용된 단일 문서 청크 출처.

    후보 검색 결과 전체가 아니라 답변 본문에 실제 등장한 ``SOURCE-N``만
    응답에 포함된다. ``source_locator``는 PDF 페이지부터 OCR 이미지까지 같은
    구조로 표현하며, 기존 전용 위치 필드는 하위 호환 목적으로 유지한다.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    source_id: str = Field(
        min_length=8,
        max_length=32,
        pattern=r"^SOURCE-[1-9][0-9]*$",
    )
    chunk_id: str = Field(min_length=1, max_length=64)
    rag_document_idx: int = Field(gt=0)
    file_idx: int = Field(gt=0)
    folder_idx: int | None = Field(default=None, gt=0)
    file_name: str = Field(min_length=1, max_length=255)
    file_type: SupportedFileType
    chunk_index: int = Field(ge=0)
    score: float = Field(ge=-1.0, le=1.0)

    # 기존 외부 응답 필드. source_locator와 값이 일치해야 한다.
    page: int | None = Field(default=None, gt=0)
    slide_no: int | None = Field(default=None, gt=0)
    sheet_name: str | None = Field(default=None, min_length=1, max_length=255)
    section_title: str | None = Field(default=None, min_length=1, max_length=500)

    source_locator: SourceLocator | None = Field(
        default=None,
        description="문서 형식별 위치와 OCR 이미지 위치를 포함하는 공통 locator",
    )
    excerpt: str = Field(
        min_length=1,
        max_length=1000,
        description="사용자가 근거를 확인할 수 있도록 길이를 제한한 청크 발췌문",
    )

    @model_validator(mode="after")
    def validate_and_fill_source_locator(self) -> Self:
        """공통 locator를 채우고 기존 위치 필드와의 일관성을 검증한다."""

        primary_locations = (
            self.page is not None,
            self.slide_no is not None,
            self.sheet_name is not None,
        )
        if sum(primary_locations) > 1:
            raise ValueError("Only one of page, slide_no, or sheet_name may be provided.")

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
            raise ValueError("source_locator.file_type must match source file_type.")

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


class RagAnswerUsage(BaseModel):
    """최종 Claude 요청의 토큰 사용량."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class RagAnswerResponse(BaseModel):
    """근거 기반 답변 또는 근거 부족 결과를 반환하는 응답.

    ``cited_source_ids``는 단순한 생성 모델의 선언값이 아니다. 응답 모델은
    답변 본문의 ``[SOURCE-N]``을 왼쪽부터 읽어 최초 등장 순서를 계산하고,
    최종 ``sources``의 순서와 정확히 일치하는지 다시 검증한다.

    서비스가 이 필드를 생략한 기존 호출 경로에서는 검증된 본문 인용 순서를
    자동으로 채운다. 외부 입력이 필드를 명시한 경우에는 자동 교정하지 않고
    본문 인용 및 ``sources`` 순서와 다르면 거부한다.
    """

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    answer: str = Field(min_length=1)
    status: RagAnswerStatus
    cited_source_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description=("answer 본문의 SOURCE-N을 중복 없이 최초 등장 순서로 나열한 식별자 목록"),
    )
    sources: tuple[RagAnswerSource, ...] = Field(
        default_factory=tuple,
        description="최종 답변 본문에 실제 인용된 출처만 인용 순서대로 포함",
    )
    model: str | None = Field(default=None, min_length=1, max_length=128)
    usage: RagAnswerUsage | None = None
    stop_reason: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, value: str) -> str:
        """Markdown 서식은 보존하면서 공백만 있는 답변을 거부한다."""

        if not value.strip():
            raise ValueError("answer must not be empty.")
        return value

    @field_validator("cited_source_ids")
    @classmethod
    def validate_cited_source_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        """공개 응답의 출처 ID 형식과 중복을 검증한다."""

        if any(_VALID_SOURCE_ID_PATTERN.fullmatch(source_id) is None for source_id in value):
            raise ValueError("cited_source_ids must contain only valid SOURCE-N values.")
        if len(value) != len(set(value)):
            raise ValueError("cited_source_ids must not contain duplicates.")
        return value

    @field_validator("model", "stop_reason")
    @classmethod
    def normalize_optional_identifier(cls, value: str | None) -> str | None:
        """선택 식별자를 정규화하고 공백 값은 거부한다."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Optional identifier must not be empty when provided.")
        return normalized

    @model_validator(mode="after")
    def validate_status_contract(self) -> Self:
        """상태, 본문 인용, 최종 출처 및 생성 메타데이터를 함께 검증한다."""

        source_ids = tuple(source.source_id for source in self.sources)
        chunk_ids = tuple(source.chunk_id for source in self.sources)

        # 중복 오류는 인용 순서 오류보다 먼저 검증한다. 동일 SOURCE 또는 청크가
        # 조용히 덮어써진 뒤 정상 인용처럼 보이는 상황을 방지하기 위함이다.
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("sources must contain unique source_id values.")
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("sources must contain unique chunk_id values.")

        body_citation_ids = _extract_unique_source_ids(self.answer)

        if self.status is RagAnswerStatus.ANSWERED:
            # 기존 응답 계약의 필수 요소를 먼저 확인해 기존 오류 의미를 유지한다.
            if not self.sources:
                raise ValueError("answered responses must contain at least one source.")
            if self.model is None:
                raise ValueError("answered responses must contain a model.")
            if self.usage is None:
                raise ValueError("answered responses must contain usage.")
            if not body_citation_ids:
                raise ValueError("answered responses must contain answer citations.")

            # sources가 후보 전체를 포함하면 사용하지 않은 원문 발췌문이 외부로
            # 노출될 수 있다. 본문의 실제 인용 순서와 정확히 같은 출처만 허용한다.
            if source_ids != body_citation_ids:
                raise ValueError("sources must match answer citations in first appearance order.")

            if "cited_source_ids" in self.model_fields_set:
                if self.cited_source_ids != body_citation_ids:
                    raise ValueError(
                        "cited_source_ids must match answer citations in first appearance order."
                    )
            else:
                # 서비스의 기존 생성 경로는 sources만 전달한다. 해당 경로도 외부
                # 응답에는 명시적인 cited_source_ids가 포함되도록 안전하게 채운다.
                object.__setattr__(self, "cited_source_ids", body_citation_ids)

            return self

        # 근거 부족 응답은 Claude 최종 호출이 생략된 결과일 수 있으므로 출처,
        # 인용 선언 및 생성 사용량을 어떤 형태로도 포함하지 않는다.
        if self.sources:
            raise ValueError("insufficient_evidence responses must not contain sources.")
        if body_citation_ids or self.cited_source_ids:
            raise ValueError("insufficient_evidence responses must not contain citations.")
        if self.model is not None or self.usage is not None or self.stop_reason is not None:
            raise ValueError(
                "insufficient_evidence responses must not contain generation metadata."
            )
        if self.answer.strip() != _INSUFFICIENT_EVIDENCE_ANSWER:
            raise ValueError("insufficient_evidence responses must use the fixed answer.")
        return self


def _extract_unique_source_ids(answer: str) -> tuple[str, ...]:
    """본문 인용을 중복 없이 최초 등장 순서로 반환한다."""

    ordered: list[str] = []
    seen: set[str] = set()

    for match in _SOURCE_CITATION_PATTERN.finditer(answer):
        source_id = match.group("source_id")
        if source_id in seen:
            continue
        seen.add(source_id)
        ordered.append(source_id)

    return tuple(ordered)
