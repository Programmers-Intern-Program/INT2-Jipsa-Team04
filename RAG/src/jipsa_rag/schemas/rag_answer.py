"""근거 기반 RAG 답변 생성에서 사용하는 요청 및 응답 스키마를 정의한다."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jipsa_rag.schemas.file_processing import SupportedFileType


class RagAnswerStatus(StrEnum):
    """근거 기반 RAG 답변 처리 결과를 구분한다."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RagAnswerRequest(BaseModel):
    """사용자 문서에서 근거를 검색하고 답변을 생성하기 위한 요청."""

    # 정의하지 않은 필드를 거부하여 애플리케이션 서버와 RAG 서버 사이의
    # 답변 생성 계약이 의도하지 않게 확장되는 것을 조기에 탐지한다.
    #
    # 사용자 질문 앞뒤 공백은 검색 및 프롬프트 의미에 필요하지 않으므로
    # 입력 단계에서 제거한다.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    user_idx: int = Field(
        gt=0,
        description=(
            "AWS 서버 DB Users.Users_IDX 식별자다. "
            "관련 청크 검색 시 사용자 문서 범위를 제한하는 데 사용한다."
        ),
        examples=[45],
    )

    query: str = Field(
        min_length=1,
        max_length=4096,
        description="문서 근거를 검색하고 답변할 사용자 질문",
        examples=["프로젝트의 로컬 실행 절차를 알려줘"],
    )

    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="답변 근거 후보로 검색할 최대 청크 수",
        examples=[5],
    )

    score_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description=(
            "Qdrant Cosine 검색 결과에 적용할 선택적 최소 점수다. "
            "값이 없으면 점수 임계값을 적용하지 않는다."
        ),
        examples=[0.6],
    )


class RagAnswerSource(BaseModel):
    """최종 답변 작성에 실제로 사용한 단일 문서 청크 출처."""

    # 출처 응답에는 청크 원문 전체, Qdrant 내부 객체 또는 정의되지 않은
    # payload가 포함되지 않도록 외부 공개 필드만 허용한다.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    source_id: str = Field(
        min_length=8,
        max_length=32,
        pattern=r"^SOURCE-[1-9][0-9]*$",
        description="프롬프트와 답변 인용을 연결하는 요청 범위 출처 식별자",
        examples=["SOURCE-1"],
    )

    chunk_id: str = Field(
        min_length=1,
        max_length=64,
        description="Local RAG DB RAG_Chunk.Chunk_ID와 동일한 Qdrant Point ID",
        examples=["11111111-1111-1111-1111-111111111111"],
    )

    rag_document_idx: int = Field(
        gt=0,
        description="출처 청크가 속한 Local RAG DB RAG_Document 식별자",
        examples=[100],
    )

    file_idx: int = Field(
        gt=0,
        description="출처 원본 파일의 AWS 서버 DB File.File_IDX",
        examples=[123],
    )

    folder_idx: int | None = Field(
        default=None,
        gt=0,
        description="출처 원본 파일이 속한 AWS 서버 DB Folder.Folder_IDX",
        examples=[9],
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description="색인 시점 원본 파일 표시명 스냅샷",
        examples=["프로젝트 가이드.pdf"],
    )

    file_type: SupportedFileType = Field(
        description="출처 원본 파일 형식",
        examples=["pdf"],
    )

    chunk_index: int = Field(
        ge=0,
        description="원본 문서 안에서 0부터 시작하는 청크 순번",
        examples=[3],
    )

    score: float = Field(
        ge=-1.0,
        le=1.0,
        description="사용자 질문과 출처 청크 사이의 Cosine 관련도 점수",
        examples=[0.82],
    )

    page: int | None = Field(
        default=None,
        gt=0,
        description="PDF 원본 페이지 번호",
        examples=[2],
    )

    slide_no: int | None = Field(
        default=None,
        gt=0,
        description="PPTX 원본 슬라이드 번호",
        examples=[4],
    )

    sheet_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="XLSX 원본 시트 이름",
        examples=["요약"],
    )

    section_title: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="문서 파서가 추출한 선택적 섹션 제목",
        examples=["로컬 실행 방법"],
    )

    excerpt: str = Field(
        min_length=1,
        max_length=1000,
        description="사용자가 근거를 확인할 수 있도록 길이를 제한한 청크 발췌문",
        examples=["로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다."],
    )

    @model_validator(mode="after")
    def validate_primary_location(self) -> Self:
        """서로 다른 문서 형식의 원본 위치가 동시에 설정되지 않도록 검증한다."""

        primary_locations = (
            self.page is not None,
            self.slide_no is not None,
            self.sheet_name is not None,
        )

        if sum(primary_locations) > 1:
            raise ValueError("Only one of page, slide_no, or sheet_name may be provided.")

        return self


class RagAnswerUsage(BaseModel):
    """최종 답변을 생성한 단일 Claude 요청의 토큰 사용량."""

    model_config = ConfigDict(
        extra="forbid",
    )

    input_tokens: int = Field(
        ge=0,
        description="Claude API 요청에 사용된 입력 토큰 수",
        examples=[1024],
    )

    output_tokens: int = Field(
        ge=0,
        description="Claude API 응답에 사용된 출력 토큰 수",
        examples=[256],
    )


class RagAnswerResponse(BaseModel):
    """근거 기반 답변 또는 근거 부족 결과를 반환하는 응답."""

    # 생성된 답변의 Markdown, 코드 블록 및 줄바꿈을 보존해야 하므로
    # 모델 전체에 str_strip_whitespace를 적용하지 않는다.
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    answer: str = Field(
        min_length=1,
        description=("문서 근거 기반 답변 또는 근거가 부족할 때 반환하는 고정 안내 문구"),
        examples=["로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다. [SOURCE-1]"],
    )

    status: RagAnswerStatus = Field(
        description="정상 답변 생성 또는 근거 부족 여부",
        examples=["answered"],
    )

    sources: tuple[RagAnswerSource, ...] = Field(
        default_factory=tuple,
        description="답변 작성에 실제로 사용한 출처 목록",
    )

    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=("Claude가 답변을 생성한 경우의 실제 응답 모델 ID이며 생성 호출이 없으면 null"),
        examples=["claude-sonnet-5"],
    )

    usage: RagAnswerUsage | None = Field(
        default=None,
        description="Claude가 답변을 생성한 경우의 토큰 사용량",
    )

    stop_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Claude 응답 종료 사유이며 생성 호출이 없으면 null",
        examples=["end_turn"],
    )

    @field_validator("answer")
    @classmethod
    def validate_answer(
        cls,
        value: str,
    ) -> str:
        """답변 원문은 보존하면서 공백으로만 구성된 값을 거부한다."""

        if not value.strip():
            raise ValueError("answer must not be empty.")

        return value

    @field_validator(
        "model",
        "stop_reason",
    )
    @classmethod
    def normalize_optional_identifier(
        cls,
        value: str | None,
    ) -> str | None:
        """선택적 식별 문자열을 정규화하고 공백 값은 거부한다."""

        if value is None:
            return None

        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError("Optional identifier must not be empty when provided.")

        return normalized_value

    @model_validator(mode="after")
    def validate_status_contract(self) -> Self:
        """답변 상태와 출처 및 Claude 생성 메타데이터의 일관성을 검증한다."""

        source_ids = [source.source_id for source in self.sources]
        chunk_ids = [source.chunk_id for source in self.sources]

        if len(source_ids) != len(set(source_ids)):
            raise ValueError("sources must contain unique source_id values.")

        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("sources must contain unique chunk_id values.")

        if self.status is RagAnswerStatus.ANSWERED:
            if not self.sources:
                raise ValueError("answered responses must contain at least one source.")

            if self.model is None:
                raise ValueError("answered responses must contain a model.")

            if self.usage is None:
                raise ValueError("answered responses must contain usage.")

            return self

        if self.sources:
            raise ValueError("insufficient_evidence responses must not contain sources.")

        if self.model is not None or self.usage is not None or self.stop_reason is not None:
            raise ValueError(
                "insufficient_evidence responses must not contain generation metadata."
            )

        return self
