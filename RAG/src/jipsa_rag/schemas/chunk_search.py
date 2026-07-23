"""관련 청크 검색 API에서 사용하는 요청 및 응답 스키마를 정의한다."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jipsa_rag.schemas.file_processing import SupportedFileType


class ChunkSearchRequest(BaseModel):
    """사용자 문서 범위에서 관련 청크를 검색하기 위한 요청."""

    # 정의하지 않은 필드를 거부하여 애플리케이션 서버와 RAG 서버 사이의
    # 검색 계약이 의도하지 않게 확장되는 것을 조기에 탐지한다.
    #
    # 질의 앞뒤 공백은 검색 의미에 영향을 주지 않으므로 입력 단계에서 제거한다.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    user_idx: int = Field(
        gt=0,
        description=(
            "AWS 서버 DB Users.Users_IDX 식별자다. "
            "Qdrant의 users_idx payload 필터로 변환하여 사용자 간 검색 결과를 격리한다."
        ),
        examples=[45],
    )

    query: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "관련 청크를 찾을 사용자 질의다. "
            "TEI에 전달하기 전에 Qwen3 검색 질의 instruction을 결합한다."
        ),
        examples=["프로젝트의 배포 절차를 알려줘"],
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
        description=(
            "Qdrant Cosine 검색 결과에 적용할 선택적 최소 점수다. "
            "값이 없으면 점수 임계값을 적용하지 않는다."
        ),
        examples=[0.6],
    )


class ChunkSearchResult(BaseModel):
    """Qdrant에서 검색된 단일 활성 청크 응답."""

    # 응답에 내부 Qdrant 객체나 정의되지 않은 payload가 섞이지 않도록
    # 외부에 노출할 필드만 명시적으로 허용한다.
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    chunk_id: str = Field(
        min_length=1,
        max_length=64,
        description="Local RAG DB RAG_Chunk.Chunk_ID와 동일한 Qdrant Point ID",
        examples=["11111111-1111-1111-1111-111111111111"],
    )

    score: float = Field(
        ge=-1.0,
        le=1.0,
        description="질의 임베딩과 청크 임베딩 사이의 Cosine 관련도 점수",
        examples=[0.82],
    )

    rag_document_idx: int = Field(
        gt=0,
        description="검색된 청크가 속한 Local RAG DB RAG_Document 식별자",
        examples=[100],
    )

    file_idx: int = Field(
        gt=0,
        description="검색된 청크 원본 파일의 AWS 서버 DB File.File_IDX",
        examples=[123],
    )

    folder_idx: int | None = Field(
        default=None,
        gt=0,
        description="원본 파일이 속한 AWS 서버 DB Folder.Folder_IDX",
        examples=[9],
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description="색인 시점 원본 파일 표시명 스냅샷",
        examples=["프로젝트 가이드.pdf"],
    )

    file_type: SupportedFileType = Field(
        description="원본 파일 형식",
        examples=["pdf"],
    )

    chunk_index: int = Field(
        ge=0,
        description="원본 문서 안에서 0부터 시작하는 청크 순번",
        examples=[3],
    )

    content: str = Field(
        min_length=1,
        description="LLM 근거 컨텍스트로 사용할 청크 원문",
        examples=["로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다."],
    )

    token_count: int | None = Field(
        default=None,
        ge=0,
        description="청크 생성 시 계산된 토큰 수이며 계산하지 않은 경우 null",
        examples=[128],
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

    parser_version: str = Field(
        min_length=1,
        max_length=100,
        description="청크 생성에 사용한 문서 파서 버전",
        examples=["1.0.0"],
    )

    embedding_model: str = Field(
        min_length=1,
        max_length=255,
        description="청크 및 검색 질의 임베딩에 사용한 모델 식별자",
        examples=["Qwen/Qwen3-Embedding-0.6B"],
    )

    index_version: int = Field(
        gt=0,
        description="청킹 및 Chunk ID 생성 규칙을 식별하는 색인 버전",
        examples=[2],
    )


class ChunkSearchResponse(BaseModel):
    """사용자 범위 관련 청크 검색 결과."""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

    user_idx: int = Field(
        gt=0,
        description="검색 범위를 제한한 AWS 서버 DB Users.Users_IDX",
        examples=[45],
    )

    result_count: int = Field(
        ge=0,
        description="실제로 반환된 검색 결과 수",
        examples=[2],
    )

    results: tuple[ChunkSearchResult, ...] = Field(
        default_factory=tuple,
        description="관련도 점수 내림차순으로 정렬된 활성 청크 목록",
    )

    @model_validator(mode="after")
    def validate_result_count(self) -> Self:
        """result_count와 실제 결과 목록 길이가 일치하는지 검증한다."""

        if self.result_count != len(self.results):
            raise ValueError("result_count must match the number of results.")

        return self
