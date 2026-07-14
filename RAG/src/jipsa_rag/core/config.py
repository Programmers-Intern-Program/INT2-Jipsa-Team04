"""로컬에서 실행되는 Jipsa RAG 애플리케이션의 환경과 설정을 관리한다."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, cast
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
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

    environment = os.getenv(
        "JIPSA_RAG_APP_ENV",
        "local",
    ).strip().lower()

    if environment not in SUPPORTED_ENVIRONMENTS:
        supported = ", ".join(sorted(SUPPORTED_ENVIRONMENTS))

        raise ValueError(
            f"지원하지 않는 실행 환경입니다: {environment}. "
            f"지원 환경: {supported}"
        )

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
    # 애플리케이션 서버가 전달한 Object Key가 files/{uuid}
    # 경로에 속하는지 검증할 때만 이 값을 사용한다.
    s3_allowed_key_prefix: S3AllowedKeyPrefix = "files/"

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

    # 애플리케이션 서버와 연결을 수립할 때 기다리는 최대 시간(초)이다.
    app_server_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
    )

    # 연결 후 애플리케이션 서버 응답을 기다리는 최대 시간(초)이다.
    app_server_read_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
    )

    model_config = SettingsConfigDict(
        # 예:
        # database_host
        # -> JIPSA_RAG_DATABASE_HOST
        #
        # app_server_base_url
        # -> JIPSA_RAG_APP_SERVER_BASE_URL
        env_prefix="JIPSA_RAG_",

        # Windows와 Linux의 환경 변수 대소문자 처리 차이를 줄인다.
        case_sensitive=False,

        # 아직 코드에 반영하지 않은 환경 변수가 dotenv에 있더라도
        # 설정 객체 생성을 실패시키지 않는다.
        extra="ignore",

        # dotenv 파일을 UTF-8로 읽는다.
        env_file_encoding="utf-8",
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
        "app_server_base_url",
        "app_server_api_v1_prefix",
        mode="before",
    )
    @classmethod
    def strip_non_secret_text(cls, value: object) -> object:
        """비밀값이 아닌 문자열 설정의 앞뒤 공백을 제거한다."""

        if isinstance(value, str):
            return value.strip()

        return value

    @field_validator(
        "api_v1_prefix",
        "app_server_api_v1_prefix",
    )
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """FastAPI 및 애플리케이션 서버 API prefix 형식을 검증한다."""

        if not value.startswith("/"):
            raise ValueError("API prefix는 '/'로 시작해야 합니다.")

        if value.endswith("/"):
            raise ValueError("API prefix는 '/'로 끝날 수 없습니다.")

        if "//" in value:
            raise ValueError("API prefix에는 연속된 '/'를 사용할 수 없습니다.")

        return value

    @field_validator("app_server_base_url")
    @classmethod
    def validate_app_server_base_url(cls, value: str) -> str:
        """애플리케이션 서버 기본 URL의 형식을 검증한다."""

        if value.endswith("/"):
            raise ValueError(
                "애플리케이션 서버 기본 URL은 '/'로 끝날 수 없습니다."
            )

        parsed = urlsplit(value)

        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                "애플리케이션 서버 기본 URL은 "
                "http 또는 https 스킴을 사용해야 합니다."
            )

        if not parsed.netloc:
            raise ValueError(
                "애플리케이션 서버 기본 URL에는 호스트가 필요합니다."
            )

        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "애플리케이션 서버 기본 URL에 인증 정보를 포함할 수 없습니다."
            )

        if parsed.query or parsed.fragment:
            raise ValueError(
                "애플리케이션 서버 기본 URL에 "
                "query 또는 fragment를 포함할 수 없습니다."
            )

        try:
            parsed_port = parsed.port
        except ValueError as error:
            raise ValueError(
                "애플리케이션 서버 기본 URL의 포트가 올바르지 않습니다."
            ) from error

        if parsed_port is not None and not 1 <= parsed_port <= 65535:
            raise ValueError(
                "애플리케이션 서버 포트는 1부터 65535 사이여야 합니다."
            )

        return value

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

        return (
            f"{self.app_server_base_url}"
            f"{self.app_server_api_v1_prefix}"
        )


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