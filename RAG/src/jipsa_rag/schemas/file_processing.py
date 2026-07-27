"""RAG 파일 처리 API의 요청·응답 Pydantic 스키마를 정의한다.

이 모듈은 애플리케이션 서버와 Local RAG 서버 사이의 외부 계약 경계다.
지원 문서 형식, 파일명 확장자, Presigned URL의 기본 구조와 응답 필드를
여기서 검증하여 잘못된 요청이 다운로드, 파싱, 임베딩 또는 저장 단계까지
진입하지 않게 한다.

지원 형식은 PDF, DOCX, PPTX, TXT, XLSX이며, 각 요청은 ``file_type``과
``file_name``의 마지막 확장자가 반드시 일치해야 한다. 실제 파일 내용은
다운로드 계층의 MIME Type·Magic Byte 검증과 형식별 파서의 구조 검증에서
추가로 확인한다.
"""

from enum import StrEnum
from pathlib import Path
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
    """RAG 인제스트, 파싱, 청킹과 검색 출처 계약에서 지원하는 문서 형식.

    값은 애플리케이션 서버의 요청 JSON과 Local RAG API 응답에 사용되므로
    소문자 확장자 형태를 유지한다. 내부 파서 계층의 ``DocumentType``은 대문자
    값을 사용하지만 ``StrEnum``은 문자열 비교와 직렬화에서 자연스럽게 처리된다.
    """

    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    TXT = "txt"
    XLSX = "xlsx"


# file_type과 file_name을 교차 검증할 때 사용할 정확한 마지막 확장자 맵이다.
# enum에 새 형식이 추가되면 comprehension이 자동으로 같은 이름의 확장자를 만든다.
_EXPECTED_EXTENSION_BY_FILE_TYPE: dict[SupportedFileType, str] = {
    file_type: f".{file_type.value}" for file_type in SupportedFileType
}


class FileProcessingRequest(BaseModel):
    """애플리케이션 서버가 Local RAG에 전달하는 문서 처리 요청.

    이 요청은 원본 파일의 영구 식별자와 표시 정보, Presigned GET URL을 전달한다.
    Local RAG는 AWS DB를 직접 조회하거나 S3 자격 증명을 보관하지 않으며, 전달받은
    URL을 다운로드에만 사용한다.
    """

    # 정의되지 않은 필드를 조용히 무시하면 애플리케이션 서버와 RAG 서버의 계약이
    # 서로 달라졌을 때 문제를 늦게 발견할 수 있다. extra="forbid"로 즉시 실패시킨다.
    # 문자열 앞뒤 공백은 공통적으로 제거하되 Presigned URL validator는 원문 구조를
    # 다시 생성하지 않고 전달된 문자열을 그대로 반환한다.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    file_idx: int = Field(
        gt=0,
        description=(
            "AWS 서버 DB File.File_IDX 식별자이며 파일의 영구적인 정체성과 "
            "저장소 간 조인 키로 사용한다."
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
            "AWS 서버 DB Folder.Folder_IDX 외부 참조값이며 폴더 단위 검색 "
            "필터에 사용한다. 루트 파일은 null일 수 있다."
        ),
        examples=[9],
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "요청 시점 File.Display_Name의 표시용 스냅샷이다. 실제 파일 참조는 "
            "file_idx를 사용하고, 이 값은 확장자 검증과 출처 표시에 사용한다."
        ),
        examples=["2026 Q3 회의록.docx"],
    )

    file_type: SupportedFileType = Field(
        description=("실제 원본 문서 형식이며 DocumentParserFactory의 파서 선택에 사용한다."),
        examples=["docx"],
    )

    download_url: str = Field(
        min_length=1,
        max_length=8192,
        description=(
            "애플리케이션 서버가 발급한 원본 문서용 Presigned GET URL이다. "
            "Local RAG DB에는 저장하지 않는다."
        ),
        examples=[
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
            "files/example-file.docx?X-Amz-Signature=example"
        ],
    )

    url_expires_in: int = Field(
        gt=0,
        description=(
            "Presigned GET URL 발급 시 설정된 유효 시간이며 단위는 초다. "
            "발급 시각이 요청에 없으므로 이 값만으로 현재 만료 여부를 계산하지 않는다."
        ),
        examples=[900],
    )

    @field_validator("download_url")
    @classmethod
    def validate_download_url(cls, value: str) -> str:
        """Presigned GET URL의 기본 구조를 검증하고 원문을 그대로 반환한다.

        서명 검증에는 path와 query의 정확한 원문이 사용될 수 있으므로 URL을
        재조합하거나 query 파라미터를 정렬하지 않는다. 허용 호스트 suffix에 대한
        최종 SSRF 방어는 환경 설정이 필요한 ``HttpFileDownloader``가 수행한다.
        """

        try:
            parsed = urlsplit(value)
            parsed_port = parsed.port
        except ValueError as error:
            # 잘못된 IPv6 괄호나 포트 문자열 등 urlsplit 속성 해석 오류를
            # Pydantic 필드 검증 오류로 변환한다.
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

        return value

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        """표시용 파일명에 디렉터리 경로 구분자가 없는지 검증한다.

        파일명은 실제 임시 경로 생성에 사용하지 않지만, 순수 표시명 계약을 유지하고
        출처 UI나 후속 코드에서 경로처럼 오인되는 것을 방지한다.
        """

        if "/" in value or "\\" in value:
            raise ValueError("File name must not contain path separators.")

        return value

    @field_validator(
        "file_type",
        mode="before",
    )
    @classmethod
    def normalize_file_type(cls, value: object) -> object:
        """문자열 file_type의 앞뒤 공백과 대소문자를 정규화한다."""

        if isinstance(value, str):
            return value.strip().lower()

        return value

    @model_validator(mode="after")
    def validate_file_extension(self) -> Self:
        """선언한 문서 형식과 파일명의 마지막 확장자를 교차 검증한다.

        예를 들어 ``file_type="docx"``인데 ``file_name="report.pdf"``인 요청은
        네트워크 다운로드 전에 422로 거부한다. 이 검증만으로 실제 파일 내용을
        신뢰하지는 않으며, 다운로드 후 MIME Type, Magic Byte와 패키지 구조를 다시
        확인한다.
        """

        expected_extension = _EXPECTED_EXTENSION_BY_FILE_TYPE[self.file_type]
        actual_extension = Path(self.file_name).suffix.lower()

        if actual_extension != expected_extension:
            raise ValueError(
                f"{self.file_type.value.upper()} file type requires a "
                f"{expected_extension} file extension."
            )

        return self


class FileProcessingCompletedResponse(BaseModel):
    """다운로드부터 Local RAG DB와 Qdrant 색인까지 완료된 응답 데이터.

    기존 PDF 응답의 ``page_count`` 필드명을 유지하여 하위 호환성을 보장한다.
    PDF에서는 실제 페이지 수이고, 다른 형식에서는 파서가 생성한 원본 위치 unit
    수를 전달한다. 새로운 일반명 필드를 동시에 추가하면 애플리케이션 서버 계약을
    변경해야 하므로 현재 변경에서는 기존 필드를 재사용한다.
    """

    # 내부 객체의 추가 속성이 실수로 API 응답에 직렬화되지 않도록 정의된 필드만 허용한다.
    model_config = ConfigDict(extra="forbid")

    rag_document_idx: int = Field(
        gt=0,
        description="Local RAG DB RAG_Document 식별자",
    )

    file_idx: int = Field(
        gt=0,
        description="처리 대상 AWS 서버 DB File.File_IDX",
    )

    user_idx: int = Field(
        gt=0,
        description="파일 소유 사용자 식별자",
    )

    folder_idx: int | None = Field(
        default=None,
        gt=0,
        description="파일이 속한 폴더 식별자",
    )

    file_name: str = Field(
        min_length=1,
        max_length=255,
        description="처리 요청 시점의 파일 표시명 스냅샷",
    )

    file_type: SupportedFileType = Field(
        description="처리가 완료된 원본 파일 형식",
    )

    file_size_bytes: int = Field(
        gt=0,
        description="다운로드와 검증이 완료된 원본 파일의 실제 바이트 크기",
    )

    page_count: int = Field(
        gt=0,
        description=(
            "기존 PDF 응답 하위 호환성을 위한 원본 단위 수다. PDF에서는 실제 "
            "페이지 수이며, DOCX/PPTX/TXT/XLSX에서는 파서가 생성한 원본 위치 "
            "단위 수다."
        ),
    )

    text_unit_count: int = Field(
        gt=0,
        description="실제 추출 텍스트가 존재하는 원본 위치 단위 수",
    )

    chunk_count: int = Field(
        gt=0,
        description="Local RAG DB와 Qdrant에 저장된 검색 청크 수",
    )

    embedding_model: str = Field(
        min_length=1,
        max_length=255,
        description="청크 임베딩 생성에 사용한 모델 식별자",
    )

    embedding_dim: int = Field(
        gt=0,
        description="청크별 임베딩 벡터 차원",
    )

    processing_status: Literal["INDEXED"] = Field(
        default="INDEXED",
        description="Local RAG DB와 Qdrant 색인이 확정된 완료 상태",
    )
