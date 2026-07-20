"""로컬에서 실행되는 Jipsa RAG 애플리케이션의 환경과 설정을 관리한다."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, cast
from urllib.parse import SplitResult, urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# RAG는 로컬에서만 실행하며 세 가지 설정 프로필을 사용한다.
AppEnvironment = Literal[
    "local",
    "development",
    "test",
]

# Local RAG DB의 DDL과 동일한 문자 집합만 허용한다.
DatabaseCharset = Literal["utf8mb4"]

# 애플리케이션 서버에서 전달받는 S3 Object Key는
# files/{uuid} 형식만 사용한다.
S3AllowedKeyPrefix = Literal["files/"]

# 임베딩 생성 서버는 Hugging Face Text Embeddings Inference만 사용한다.
EmbeddingProvider = Literal["tei"]

# Qdrant Collection의 벡터 유사도 계산에는 Cosine 거리를 사용한다.
EmbeddingDistance = Literal["cosine"]

# 현재 VectorDB 구현체는 Qdrant로 고정한다.
VectorDatabaseProvider = Literal["qdrant"]


# 현재 파일 위치:
# RAG/src/jipsa_rag/core/config.py
#
# parents[0] = core
# parents[1] = jipsa_rag
# parents[2] = src
# parents[3] = RAG 프로젝트 루트
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]


# 런타임에서 허용할 실행 환경을 검증하는 데 사용한다.
SUPPORTED_ENVIRONMENTS: Final[frozenset[str]] = frozenset(
    {
        "local",
        "development",
        "test",
    }
)


def resolve_environment() -> AppEnvironment:
    """OS 환경 변수에서 현재 RAG 실행 환경을 확인한다.

    JIPSA_RAG_APP_ENV가 없으면 local 환경을 기본값으로 사용한다.
    """

    environment = (
        os.getenv(
            "JIPSA_RAG_APP_ENV",
            "local",
        )
        .strip()
        .lower()
    )

    if environment not in SUPPORTED_ENVIRONMENTS:
        supported = ", ".join(sorted(SUPPORTED_ENVIRONMENTS))

        raise ValueError(f"지원하지 않는 실행 환경입니다: {environment}. 지원 환경: {supported}")

    return cast(AppEnvironment, environment)


def resolve_env_file(
    environment: AppEnvironment,
    project_root: Path = PROJECT_ROOT,
) -> Path | None:
    """실행 환경에 대응하는 dotenv 파일 경로를 반환한다.

    local은 .env.local, development는 .env.development,
    test는 .env.test 파일을 사용한다.

    대상 파일이 없으면 OS 환경 변수만으로 실행할 수 있도록
    예외 대신 None을 반환한다.
    """

    env_file = project_root / f".env.{environment}"

    if not env_file.is_file():
        return None

    return env_file


def _parse_http_base_url(
    value: str,
    *,
    setting_name: str,
) -> SplitResult:
    """외부 HTTP 서비스 기본 URL을 공통 규칙으로 검증한다."""

    if value.endswith("/"):
        raise ValueError(f"{setting_name}은 '/'로 끝날 수 없습니다.")

    parsed = urlsplit(value)

    if parsed.scheme not in {
        "http",
        "https",
    }:
        raise ValueError(f"{setting_name}은 http 또는 https 스킴을 사용해야 합니다.")

    if not parsed.netloc or parsed.hostname is None:
        raise ValueError(f"{setting_name}에는 호스트가 필요합니다.")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{setting_name}에 인증 정보를 포함할 수 없습니다.")

    if parsed.query or parsed.fragment:
        raise ValueError(f"{setting_name}에 query 또는 fragment를 포함할 수 없습니다.")

    if parsed.path:
        raise ValueError(f"{setting_name}에 경로를 포함할 수 없습니다.")

    try:
        parsed_port = parsed.port
    except ValueError as error:
        raise ValueError(f"{setting_name}의 포트가 올바르지 않습니다.") from error

    if parsed_port is not None and not 1 <= parsed_port <= 65535:
        raise ValueError(f"{setting_name} 포트는 1부터 65535 사이여야 합니다.")

    return parsed


class Settings(BaseSettings):
    """Jipsa RAG 실행에 필요한 애플리케이션 및 외부 연동 설정."""

    # =========================================================
    # Application
    # =========================================================

    # Swagger UI와 OpenAPI 문서에 표시되는 서비스 이름이다.
    app_name: str = Field(
        default="Jipsa RAG Service",
        min_length=1,
    )

    # 현재 RAG 애플리케이션 버전이다.
    app_version: str = Field(
        default="0.1.0",
        min_length=1,
    )

    # 현재 로컬 실행 환경이다.
    #
    # get_settings()가 JIPSA_RAG_APP_ENV를 기준으로 값을 주입한다.
    app_env: AppEnvironment = "local"

    # RAG API v1 라우터에 공통으로 적용할 URL prefix이다.
    api_v1_prefix: str = Field(
        default="/api/v1",
        min_length=1,
    )

    # RAG는 로컬에서만 실행하므로 루프백 주소를 기본값으로 사용한다.
    host: str = Field(
        default="127.0.0.1",
        min_length=1,
    )

    # FastAPI 애플리케이션 서버 포트이다.
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
    )

    # FastAPI 디버그 모드 사용 여부이다.
    debug: bool = False

    # =========================================================
    # Internal Ingestion Authentication
    # =========================================================

    # 애플리케이션 서버가 POST /ingest 요청을 호출할 때 사용하는
    # 서비스 간 공유 시크릿이다.
    #
    # 백엔드의 RAG_INGEST_TOKEN 환경 변수와 반드시 동일해야 한다.
    #
    # SecretStr를 사용하여 Settings 객체가 문자열이나 로그로 출력될 때
    # 실제 토큰값이 노출되지 않도록 한다.
    #
    # 프로젝트의 기존 JIPSA_RAG_ 접두사 형식도 사용할 수 있도록
    # 다음 환경 변수 이름을 모두 허용한다.
    #
    # - RAG_INGEST_TOKEN
    # - JIPSA_RAG_INGEST_TOKEN
    rag_ingest_token: SecretStr | None = Field(
        default=None,
        min_length=32,
        max_length=512,
        validation_alias=AliasChoices(
            "RAG_INGEST_TOKEN",
            "JIPSA_RAG_INGEST_TOKEN",
        ),
    )

    # =========================================================
    # Local RAG MySQL
    # =========================================================

    # Local RAG MySQL 서버 주소이다.
    database_host: str = Field(
        min_length=1,
    )

    # Local RAG MySQL 연결 포트이다.
    database_port: int = Field(
        default=3306,
        ge=1,
        le=65535,
    )

    # 연결할 Local RAG 데이터베이스 이름이다.
    database_name: str = Field(
        min_length=1,
    )

    # Local RAG 데이터베이스 접속 계정이다.
    database_user: str = Field(
        min_length=1,
    )

    # DB 비밀번호 원문이 Settings 출력이나 로그에 노출되지 않도록 한다.
    database_password: SecretStr = Field(
        min_length=1,
    )

    # Local RAG DB 연결 문자 집합이다.
    database_charset: DatabaseCharset = "utf8mb4"

    # SQLAlchemy가 실행한 SQL을 콘솔에 출력할지 결정한다.
    database_echo: bool = False

    # 애플리케이션 시작 시 SELECT 1 연결 검사를 실행할지 결정한다.
    database_check_on_startup: bool = False

    # =========================================================
    # S3 Object Key Validation
    # =========================================================

    # RAG는 S3에 직접 인증하지 않는다.
    #
    # 애플리케이션 서버가 전달한 Object Key가 files/{uuid}
    # 경로에 속하는지 검증할 때만 이 값을 사용한다.
    s3_allowed_key_prefix: S3AllowedKeyPrefix = "files/"

    # =========================================================
    # File Download
    # =========================================================

    # Presigned GET URL에서 허용할 호스트 suffix 목록이다.
    #
    # 쉼표로 여러 도메인을 구분할 수 있으며, 내부에서는 모두
    # ".example.com" 형식으로 정규화하여 비교한다.
    #
    # 다운로드 URL의 호스트를 제한하여 임의의 내부 서버나
    # 허용되지 않은 외부 서버로 요청하는 것을 차단한다.
    file_download_allowed_host_suffixes: str = Field(
        default=".amazonaws.com",
        min_length=1,
    )

    # Presigned GET URL 서버와 TCP 연결을 수립할 때
    # 기다리는 최대 시간이다.
    file_download_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=300,
    )

    # 연결 이후 파일 데이터 청크를 기다리는 최대 시간이다.
    #
    # 전체 다운로드 제한 시간이 아니라 연속된 데이터 청크 사이에
    # 허용되는 최대 대기 시간이다.
    file_download_read_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
    )

    # 다운로드할 수 있는 단일 파일의 최대 크기이다.
    #
    # 기본값은 50 MiB이며 최대 1 GiB까지 설정할 수 있다.
    file_download_max_size_bytes: int = Field(
        default=50 * 1024 * 1024,
        gt=0,
        le=1024 * 1024 * 1024,
    )

    # =========================================================
    # Embedding Service
    # =========================================================

    # 임베딩 생성 서버 구현체이다.
    #
    # 모델 실행과 CUDA 의존성은 RAG 프로세스가 아니라
    # 별도의 Hugging Face TEI Docker 컨테이너가 담당한다.
    embedding_provider: EmbeddingProvider = "tei"

    # 로컬 RAG 서버가 TEI HTTP API에 접근하는 기본 주소이다.
    #
    # 애플리케이션 서버의 8080 포트와 충돌하지 않도록
    # TEI는 호스트 포트 18081을 사용한다.
    embedding_base_url: str = Field(
        default="http://127.0.0.1:18081",
        min_length=1,
    )

    # TEI 컨테이너에서 로드하는 Hugging Face 모델 식별자이다.
    embedding_model: str = Field(
        default="Qwen/Qwen3-Embedding-0.6B",
        min_length=1,
    )

    # Qwen3-Embedding-0.6B가 반환하는 벡터 차원이다.
    #
    # Qdrant Collection의 vector size와 반드시 동일해야 한다.
    embedding_dim: int = Field(
        default=1024,
        gt=0,
    )

    # 한 번의 TEI /embed 요청에 포함할 최대 청크 수이다.
    #
    # 현재 TEI 컨테이너의 기본 max-client-batch-size가 32이므로
    # 클라이언트 설정도 최대 32로 제한한다.
    embedding_batch_size: int = Field(
        default=32,
        ge=1,
        le=32,
    )

    # Qdrant에서 벡터 유사도를 계산할 때 사용할 거리 함수이다.
    embedding_distance: EmbeddingDistance = "cosine"

    # TEI 임베딩 HTTP 요청의 전체 제한 시간이다.
    embedding_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
    )

    # =========================================================
    # Qdrant Vector Database
    # =========================================================

    # VectorDB 구현체이다.
    vector_db_provider: VectorDatabaseProvider = "qdrant"

    # 로컬 RAG 서버가 Qdrant REST API에 접근하는 기본 주소이다.
    qdrant_url: str = Field(
        default="http://127.0.0.1:6333",
        min_length=1,
    )

    # Qwen3-Embedding-0.6B의 1024차원 벡터를 저장할 Collection 이름이다.
    #
    # 모델 또는 임베딩 차원이 변경되면 기존 Collection을 재사용하지 않고
    # 모델과 차원을 식별할 수 있는 별도 Collection을 생성한다.
    qdrant_collection: str = Field(
        default="rag_chunk_vector_qwen3_embedding_0_6b_1024",
        min_length=1,
        max_length=255,
    )

    # Qdrant gRPC 연결 포트이다.
    qdrant_grpc_port: int = Field(
        default=6334,
        ge=1,
        le=65535,
    )

    # Qdrant 클라이언트에서 REST 대신 gRPC를 우선 사용할지 결정한다.
    #
    # 초기 구현은 디버깅과 검증이 쉬운 REST를 사용한다.
    qdrant_prefer_grpc: bool = False

    # 원격 또는 인증이 활성화된 Qdrant에서 사용할 API Key이다.
    #
    # 현재 로컬 Qdrant는 API Key를 사용하지 않으므로 None이 기본값이다.
    # 값이 존재하더라도 Settings 출력과 로그에서는 원문을 숨긴다.
    qdrant_api_key: SecretStr | None = None

    # Qdrant 요청 제한 시간이다.
    qdrant_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
    )

    # =========================================================
    # Application Server
    # =========================================================

    # 파일 정보와 Presigned GET URL을 요청할 애플리케이션 서버 주소이다.
    #
    # RAG가 AWS 요청에 직접 서명하지 않으므로 AWS Access Key,
    # Secret Access Key, Region은 RAG 설정에 포함하지 않는다.
    app_server_base_url: str = Field(
        default="http://127.0.0.1:8080",
        min_length=1,
    )

    # 애플리케이션 서버 API에 공통으로 적용되는 URL prefix이다.
    app_server_api_v1_prefix: str = Field(
        default="/api/v1",
        min_length=1,
    )

    # 애플리케이션 서버와 연결을 수립할 때 기다리는 최대 시간이다.
    app_server_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
    )

    # 연결 후 애플리케이션 서버 응답을 기다리는 최대 시간이다.
    app_server_read_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
    )

    model_config = SettingsConfigDict(
        # 일반 설정 예:
        #
        # database_host
        # -> JIPSA_RAG_DATABASE_HOST
        #
        # file_download_max_size_bytes
        # -> JIPSA_RAG_FILE_DOWNLOAD_MAX_SIZE_BYTES
        #
        # embedding_base_url
        # -> JIPSA_RAG_EMBEDDING_BASE_URL
        #
        # qdrant_collection
        # -> JIPSA_RAG_QDRANT_COLLECTION
        #
        # app_server_base_url
        # -> JIPSA_RAG_APP_SERVER_BASE_URL
        #
        # 내부 인제스트 토큰은 백엔드와 환경 변수 이름을 일치시키기 위해
        # rag_ingest_token 필드의 validation_alias에서 별도로 정의한다.
        env_prefix="JIPSA_RAG_",
        # Windows와 Linux의 환경 변수 대소문자 처리 차이를 줄인다.
        case_sensitive=False,
        # 아직 코드에 반영하지 않은 환경 변수가 dotenv에 있더라도
        # 설정 객체 생성을 실패시키지 않는다.
        extra="ignore",
        # dotenv 파일을 UTF-8로 읽는다.
        env_file_encoding="utf-8",
        # validation_alias가 존재하는 필드도 Python 코드에서는
        # 실제 필드명으로 값을 주입할 수 있도록 한다.
        populate_by_name=True,
    )

    @field_validator(
        "app_name",
        "app_version",
        "api_v1_prefix",
        "host",
        "database_host",
        "database_name",
        "database_user",
        "s3_allowed_key_prefix",
        "file_download_allowed_host_suffixes",
        "embedding_base_url",
        "embedding_model",
        "qdrant_url",
        "qdrant_collection",
        "app_server_base_url",
        "app_server_api_v1_prefix",
        mode="before",
    )
    @classmethod
    def strip_non_secret_text(
        cls,
        value: object,
    ) -> object:
        """비밀값이 아닌 문자열 설정의 앞뒤 공백을 제거한다."""

        if isinstance(value, str):
            return value.strip()

        return value

    @field_validator(
        "embedding_provider",
        "embedding_distance",
        "vector_db_provider",
        mode="before",
    )
    @classmethod
    def normalize_choice_text(
        cls,
        value: object,
    ) -> object:
        """provider와 거리 함수 문자열을 소문자로 정규화한다."""

        if isinstance(value, str):
            return value.strip().lower()

        return value

    @field_validator(
        "rag_ingest_token",
        mode="before",
    )
    @classmethod
    def normalize_optional_rag_ingest_token(
        cls,
        value: object,
    ) -> object:
        """공백으로만 구성된 내부 인제스트 토큰을 미설정 상태로 변환한다.

        토큰 문자열 앞뒤의 공백은 인증값의 일부로 취급하지 않는다.

        토큰 원문은 이 validator에서 로그나 오류 메시지에 포함하지 않는다.
        """

        if isinstance(value, str):
            normalized_value = value.strip()

            if not normalized_value:
                return None

            return normalized_value

        return value

    @field_validator("qdrant_api_key", mode="before")
    @classmethod
    def normalize_optional_qdrant_api_key(
        cls,
        value: object,
    ) -> object:
        """공백으로만 구성된 Qdrant API Key를 미설정 상태로 변환한다."""

        if isinstance(value, str) and not value.strip():
            return None

        return value

    @field_validator(
        "api_v1_prefix",
        "app_server_api_v1_prefix",
    )
    @classmethod
    def validate_api_prefix(
        cls,
        value: str,
    ) -> str:
        """FastAPI 및 애플리케이션 서버 API prefix 형식을 검증한다."""

        if not value.startswith("/"):
            raise ValueError("API prefix는 '/'로 시작해야 합니다.")

        if value.endswith("/"):
            raise ValueError("API prefix는 '/'로 끝날 수 없습니다.")

        if "//" in value:
            raise ValueError("API prefix에는 연속된 '/'를 사용할 수 없습니다.")

        return value

    @field_validator("file_download_allowed_host_suffixes")
    @classmethod
    def validate_file_download_allowed_host_suffixes(
        cls,
        value: str,
    ) -> str:
        """파일 다운로드에 허용할 호스트 suffix 목록을 정규화한다."""

        normalized_suffixes: list[str] = []

        for raw_suffix in value.split(","):
            suffix = raw_suffix.strip().lower()

            # 쉼표가 연속되거나 마지막에 쉼표가 있는 경우
            # 생성되는 빈 값은 무시한다.
            if not suffix:
                continue

            # "*.amazonaws.com" 입력은 ".amazonaws.com"으로 변환한다.
            #
            # 실제 호스트 비교는 문자열 suffix 비교로 수행하므로
            # 별표 문자는 보관할 필요가 없다.
            if suffix.startswith("*."):
                suffix = suffix[1:]
            elif not suffix.startswith("."):
                suffix = f".{suffix}"

            # 경로, 포트, 사용자 정보와 같은 값은 도메인 suffix로
            # 사용할 수 없으므로 설정 단계에서 거부한다.
            if any(
                character in suffix
                for character in (
                    "/",
                    "\\",
                    ":",
                    "?",
                    "#",
                    "@",
                )
            ):
                raise ValueError(
                    "파일 다운로드 허용 호스트에는 도메인 suffix만 사용할 수 있습니다."
                )

            # 시작 위치의 "*." 이외에 남아 있는 wildcard는
            # 허용 호스트 범위를 불명확하게 만들 수 있으므로 거부한다.
            if "*" in suffix:
                raise ValueError(
                    "파일 다운로드 허용 호스트에는 중간 wildcard를 사용할 수 없습니다."
                )

            normalized_suffixes.append(suffix)

        if not normalized_suffixes:
            raise ValueError("파일 다운로드 허용 호스트를 하나 이상 지정해야 합니다.")

        # 중복 항목은 제거하되 관리자가 작성한 입력 순서는 유지한다.
        return ",".join(dict.fromkeys(normalized_suffixes))

    @field_validator("embedding_base_url")
    @classmethod
    def validate_embedding_base_url(
        cls,
        value: str,
    ) -> str:
        """TEI 임베딩 서버 기본 URL의 형식을 검증한다."""

        _parse_http_base_url(
            value,
            setting_name="임베딩 서버 기본 URL",
        )

        return value

    @field_validator("qdrant_url")
    @classmethod
    def validate_qdrant_url(
        cls,
        value: str,
    ) -> str:
        """Qdrant REST API 기본 URL의 형식을 검증한다."""

        _parse_http_base_url(
            value,
            setting_name="Qdrant 기본 URL",
        )

        return value

    @field_validator("qdrant_collection")
    @classmethod
    def validate_qdrant_collection(
        cls,
        value: str,
    ) -> str:
        """Qdrant Collection 이름에 경로나 공백이 포함되지 않도록 검증한다."""

        if any(character.isspace() for character in value):
            raise ValueError("Qdrant Collection 이름에는 공백을 사용할 수 없습니다.")

        if any(
            character in value
            for character in (
                "/",
                "\\",
                "?",
                "#",
            )
        ):
            raise ValueError("Qdrant Collection 이름에는 경로 문자를 사용할 수 없습니다.")

        return value

    @field_validator("app_server_base_url")
    @classmethod
    def validate_app_server_base_url(
        cls,
        value: str,
    ) -> str:
        """애플리케이션 서버 기본 URL의 형식을 검증한다."""

        _parse_http_base_url(
            value,
            setting_name="애플리케이션 서버 기본 URL",
        )

        return value

    @property
    def parsed_file_download_allowed_host_suffixes(
        self,
    ) -> tuple[str, ...]:
        """정규화된 파일 다운로드 허용 호스트 목록을 반환한다."""

        return tuple(self.file_download_allowed_host_suffixes.split(","))

    @property
    def database_url(self) -> URL:
        """SQLAlchemy asyncmy 드라이버용 DB 연결 URL을 생성한다."""

        return URL.create(
            drivername="mysql+asyncmy",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
            query={
                "charset": self.database_charset,
            },
        )

    @property
    def app_server_api_base_url(self) -> str:
        """애플리케이션 서버의 API v1 기본 URL을 반환한다."""

        return f"{self.app_server_base_url}{self.app_server_api_v1_prefix}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """현재 실행 환경의 설정 객체를 생성하고 재사용한다."""

    # dotenv 파일을 선택하기 전에 실행 환경을 결정해야 하므로
    # JIPSA_RAG_APP_ENV는 dotenv가 아닌 OS 환경 변수에서 읽는다.
    environment = resolve_environment()
    env_file = resolve_env_file(environment)

    return Settings(
        # OS 환경 변수로 결정한 실행 환경을 명시적으로 전달한다.
        app_env=environment,
        # local, development, test에 대응하는 dotenv 파일을 전달한다.
        # 파일이 없으면 OS 환경 변수만 사용한다.
        _env_file=env_file,
    )
