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

    PDF = "PDF"


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

    file_url: str = Field(
        min_length=1,
        max_length=8192,
        description="원본 파일을 다운로드할 Presigned GET URL",
        examples=[
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
            "files/example-file.pdf?X-Amz-Signature=example"
        ],
    )

    users_idx: int = Field(
        gt=0,
        description="AWS 서버 DB Users.Users_IDX 외부 참조값",
        examples=[1],
    )

    file_idx: int = Field(
        gt=0,
        description="AWS 서버 DB File.File_IDX 외부 참조값",
        examples=[10],
    )

    folder_idx: int | None = Field(
        default=None,
        gt=0,
        description="AWS 서버 DB Folder.Folder_IDX 외부 참조값",
        examples=[3],
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description="확장자를 포함한 원본 파일명",
        examples=["project-guide.pdf"],
    )

    file_type: SupportedFileType = Field(
        description="RAG 파일 처리 파이프라인에서 처리할 파일 타입",
        examples=["PDF"],
    )

    file_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="애플리케이션 서버에서 계산한 SHA-256 hex 파일 해시",
        examples=["bd3772a0604c47f896ef0bb4a3fd944e6b3266b6a27bb9e1e07ca64f7bd20309"],
    )

    @field_validator("file_url")
    @classmethod
    def validate_file_url(
        cls,
        value: str,
    ) -> str:
        """Presigned GET URL 원문을 유지하면서 기본 구조를 검증한다."""

        try:
            parsed = urlsplit(value)
            parsed_port = parsed.port
        except ValueError as error:
            raise ValueError("File URL is invalid.") from error

        if parsed.scheme.lower() != "https":
            raise ValueError("File URL must use HTTPS.")

        if parsed.hostname is None:
            raise ValueError("File URL must contain a hostname.")

        if parsed.username is not None or parsed.password is not None:
            raise ValueError("File URL must not contain user information.")

        if parsed.fragment:
            raise ValueError("File URL must not contain a fragment.")

        if parsed_port is not None and parsed_port != 443:
            raise ValueError("File URL must use the default HTTPS port.")

        # Presigned URL은 서명 계산에 사용된 path와 query를
        # 임의로 정규화하지 않고 전달받은 문자열 그대로 사용한다.
        return value

    @field_validator("file_name")
    @classmethod
    def validate_file_name(
        cls,
        value: str,
    ) -> str:
        """파일명에 디렉터리 경로 문자가 포함되지 않았는지 검증한다."""

        # 파일명은 저장 경로나 디렉터리가 아닌
        # 순수한 파일명만 허용한다.
        #
        # 경로 구분자를 허용하면 임시 파일 또는 후속 저장 과정에서
        # 의도하지 않은 경로를 참조할 수 있다.
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
        """문자열 파일 타입을 대문자로 정규화한다."""

        if isinstance(value, str):
            return value.strip().upper()

        return value

    @field_validator("file_hash")
    @classmethod
    def normalize_file_hash(
        cls,
        value: str,
    ) -> str:
        """SHA-256 hex 문자열을 소문자로 정규화한다."""

        return value.lower()

    @model_validator(mode="after")
    def validate_file_extension(self) -> Self:
        """파일명 확장자와 요청 파일 타입이 일치하는지 검증한다."""

        if self.file_type is SupportedFileType.PDF and not self.file_name.lower().endswith(".pdf"):
            raise ValueError("PDF file type requires a .pdf file extension.")

        return self


class FileProcessingAcceptedResponse(BaseModel):
    """파일 다운로드, 검증 및 문서 파싱이 완료되었을 때 반환하는 데이터."""

    # 응답 스키마에 정의되지 않은 내부 데이터가
    # 외부 응답에 포함되지 않도록 제한한다.
    model_config = ConfigDict(extra="forbid")

    users_idx: int = Field(
        gt=0,
        description="파일 소유 사용자 식별자",
        examples=[1],
    )

    file_idx: int = Field(
        gt=0,
        description="처리 대상 파일 식별자",
        examples=[10],
    )

    folder_idx: int | None = Field(
        default=None,
        gt=0,
        description="파일이 속한 폴더 식별자",
        examples=[3],
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description="처리 대상 파일명",
        examples=["project-guide.pdf"],
    )

    file_type: SupportedFileType = Field(
        description="처리 대상 파일 타입",
        examples=["PDF"],
    )

    file_size_bytes: int = Field(
        gt=0,
        description="검증이 완료된 원본 파일 크기",
        examples=[1048576],
    )

    file_hash_verified: Literal[True] = Field(
        default=True,
        description="전달받은 SHA-256 해시와 실제 파일 해시의 일치 여부",
        examples=[True],
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

    processing_status: Literal["PARSED"] = Field(
        default="PARSED",
        description="원본 파일 다운로드, 검증 및 문서 파싱 완료 상태",
        examples=["PARSED"],
    )
