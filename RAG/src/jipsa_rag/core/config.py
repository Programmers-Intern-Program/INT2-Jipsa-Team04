import os
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, cast

from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnvironment = Literal[
    "local",
    "development",
    "test",
    "production",
]

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

SUPPORTED_ENVIRONMENTS: Final[frozenset[str]] = frozenset(
    {
        "local",
        "development",
        "test",
        "production",
    }
)


def resolve_environment() -> AppEnvironment:
    """실행 환경을 OS 환경 변수에서 확인한다.

    별도 값이 없으면 로컬 개발 환경을 기본값으로 사용한다.
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

    production 환경은 로컬 dotenv 파일을 사용하지 않고,
    서버 환경 변수나 Secret 저장소에서 설정을 주입받는다.
    """

    if environment == "production":
        return None

    env_file = project_root / f".env.{environment}"

    if not env_file.is_file():
        return None

    return env_file


class Settings(BaseSettings):
    """RAG 서비스 애플리케이션 설정."""

    app_name: str = "Jipsa RAG Service"
    app_version: str = "0.1.0"
    app_env: AppEnvironment = "local"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    model_config = SettingsConfigDict(
        env_prefix="JIPSA_RAG_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """현재 실행 환경의 설정을 한 번만 생성해 반환한다."""

    environment = resolve_environment()
    env_file = resolve_env_file(environment)

    return Settings(
        app_env=environment,
        _env_file=env_file,
        _env_file_encoding="utf-8",
    )
