"""파일 처리 요청 API에서 사용하는 요청 및 응답 스키마를 정의한다."""

from enum import StrEnum
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class SupportedFileType(StrEnum):
    """현재 RAG 파일 처리 파이프라인에서 지원하는 파일 타입."""

    # 외부 애플리케이션 서버가 전달하는 파일 타입 값은
    # 소문자 확장자 형식을 사용한다.
    #
    # 현재 실제 파서 구현이 완료된 형식은 PDF뿐이다.
    # DOCX, XLSX, PPTX 또는 이미지 파서가 추가되면
    # 해당 값을 이 Enum과 DocumentParserFactory에 함께 등록한다.
    PDF = "pdf"


class FileProcessingRequest(BaseModel):
    """애플리케이션 서버가 전달하는 파일 처리 요청 정보."""

    # 정의되지 않은 요청 필드는 허용하지 않는다.
    #
    # 애플리케이션 서버와 RAG 서버 사이의 요청 계약이
    # 의도하지 않게 변경되는 것을 조기에 탐지하기 위한 설정이다.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    file_idx: int = Field(
        gt=0,
        description=(
            "AWS 서버 DB File.File_IDX 식별자이며 "
            "파일의 영구적인 정체성과 저장소 간 조인 키로 사용한다."
        ),
        examples=[123],
    )

    user_idx: int = Field(
        gt=0,
        description=(
            "AWS 서버 DB Users.Users_IDX 외부 참조값이며 사용자별 검색 스코프 제한에 사용한다."
        ),
        examples=[45],
    )

    folder_idx: int | None = Field(
        default=None,
        gt=0,
        description=(
            "AWS 서버 DB Folder.Folder_IDX 외부 참조값이며 "
            "폴더 단위 검색 필터에 사용한다. 루트 파일은 null일 수 있다."
        ),
        examples=[9],
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "요청 시점 File.Display_Name의 스냅샷이다. "
            "표시와 진단 목적으로만 사용하며 파일 참조에는 file_idx를 사용한다."
        ),
        examples=["2026 Q3 회의록.pdf"],
    )

    file_type: SupportedFileType = Field(
        description=(
            "실제 파일 형식이며 문서 파서 선택에 사용한다. "
            "현재 파일 처리 파이프라인은 pdf만 지원한다."
        ),
        examples=["pdf"],
    )

    download_url: str = Field(
        min_length=1,
        max_length=8192,
        description="원본 파일을 다운로드할 Presigned GET URL",
        examples=[
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
            "files/example-file.pdf?X-Amz-Signature=example"
        ],
    )

    url_expires_in: int = Field(
        gt=0,
        description=(
            "애플리케이션 서버가 Presigned GET URL을 발급할 때 설정한 유효 시간이며 단위는 초다."
        ),
        examples=[900],
    )

    @field_validator("download_url")
    @classmethod
    def validate_download_url(
        cls,
        value: str,
    ) -> str:
        """Presigned GET URL 원문을 유지하면서 기본 구조를 검증한다.

        이 URL은 원본 파일을 다운로드하는 동안에만 사용한다.
        Local RAG DB에는 URL 전체나 URL에서 추출한 S3_Key를 저장하지 않는다.
        S3 객체 위치의 기준 데이터는 AWS 서버 DB의 File.S3_Key다.

        url_expires_in은 URL 발급 시 설정한 TTL 정보다.
        요청에 URL 발급 시각이 포함되어 있지 않으므로 RAG 서버는
        url_expires_in 값만으로 현재 만료 시각을 계산할 수 없다.

        실제 서명과 만료 여부는 S3가 다운로드 요청을 수신할 때 검증하며,
        만료된 URL이면 다운로드 계층에서 FILE_DOWNLOAD_FAILED로 변환한다.
        """

        try:
            parsed = urlsplit(value)
            parsed_port = parsed.port
        except ValueError as error:
            raise ValueError("Download URL is invalid.") from error

        if parsed.scheme.lower() != "https":
            raise ValueError("Download URL must use HTTPS.")

        if parsed.hostname is None:
            raise ValueError("Download URL must contain a hostname.")

        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Download URL must not contain user information.")

        if parsed.fragment:
            raise ValueError("Download URL must not contain a fragment.")

        if parsed_port is not None and parsed_port != 443:
            raise ValueError("Download URL must use the default HTTPS port.")

        # Presigned URL은 서명 계산에 사용된 path와 query를
        # 임의로 정규화하거나 재구성하지 않고 전달받은 원문을 유지한다.
        return value

    @field_validator("file_name")
    @classmethod
    def validate_file_name(
        cls,
        value: str,
    ) -> str:
        """파일명에 디렉터리 경로 문자가 포함되지 않았는지 검증한다."""

        # file_name은 저장 경로나 디렉터리가 아닌
        # 표시용 순수 파일명만 허용한다.
        #
        # 경로 구분자를 허용하면 임시 파일 또는 후속 저장 과정에서
        # 의도하지 않은 경로를 참조할 가능성이 있다.
        if "/" in value or "\\" in value:
            raise ValueError("File name must not contain path separators.")

        return value

    @field_validator(
        "file_type",
        mode="before",
    )
    @classmethod
    def normalize_file_type(
        cls,
        value: object,
    ) -> object:
        """문자열 파일 타입을 소문자로 정규화한다."""

        if isinstance(value, str):
            return value.strip().lower()

        return value

    @model_validator(mode="after")
    def validate_file_extension(self) -> Self:
        """파일명 확장자와 요청 파일 타입이 일치하는지 검증한다."""

        if self.file_type is SupportedFileType.PDF and not self.file_name.lower().endswith(".pdf"):
            raise ValueError("PDF file type requires a .pdf file extension.")

        return self


class FileProcessingCompletedResponse(BaseModel):
    """다운로드부터 Local RAG DB 및 VectorDB 저장까지 완료된 처리 결과."""

    # 응답 스키마에 정의되지 않은 내부 데이터가
    # 외부 응답에 포함되지 않도록 제한한다.
    model_config = ConfigDict(extra="forbid")

    rag_document_idx: int = Field(
        gt=0,
        description="Local RAG DB RAG_Document 식별자",
        examples=[100],
    )

    file_idx: int = Field(
        gt=0,
        description="처리 대상 파일의 AWS 서버 DB File.File_IDX",
        examples=[123],
    )

    user_idx: int = Field(
        gt=0,
        description="파일 소유 사용자의 AWS 서버 DB Users.Users_IDX",
        examples=[45],
    )

    folder_idx: int | None = Field(
        default=None,
        gt=0,
        description="파일이 속한 폴더 식별자",
        examples=[9],
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description="처리 요청 시점의 파일 표시명 스냅샷",
        examples=["2026 Q3 회의록.pdf"],
    )

    file_type: SupportedFileType = Field(
        description="처리가 완료된 원본 파일 타입",
        examples=["pdf"],
    )

    file_size_bytes: int = Field(
        gt=0,
        description="다운로드와 검증이 완료된 원본 파일 크기",
        examples=[1048576],
    )

    page_count: int = Field(
        gt=0,
        description="PDF 파서가 확인한 원본 문서 전체 페이지 수",
        examples=[10],
    )

    text_unit_count: int = Field(
        gt=0,
        description="실제 추출 텍스트가 존재하는 페이지 단위 수",
        examples=[9],
    )

    chunk_count: int = Field(
        gt=0,
        description="Local RAG DB와 VectorDB에 저장된 청크 수",
        examples=[42],
    )

    embedding_model: str = Field(
        min_length=1,
        max_length=255,
        description="청크 임베딩 생성에 사용한 모델 식별자",
        examples=["Qwen/Qwen3-Embedding-0.6B"],
    )

    embedding_dim: int = Field(
        gt=0,
        description="청크별 임베딩 벡터 차원",
        examples=[1024],
    )

    processing_status: Literal["INDEXED"] = Field(
        default="INDEXED",
        description="Local RAG DB와 VectorDB 저장 완료 상태",
        examples=["INDEXED"],
    )
