"""애플리케이션 환경 선택과 공통 설정 검증 기능을 테스트한다."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from jipsa_rag.core.config import (
    Settings,
    resolve_env_file,
    resolve_environment,
)

# dotenv 파일 또는 OS 환경 변수에 동일한 이름의 값이 남아 있어도
# 단위 테스트 결과에 영향을 주지 않도록 제거할 환경 변수 목록이다.
_SETTING_ENVIRONMENT_VARIABLES = (
    "JIPSA_RAG_APP_NAME",
    "JIPSA_RAG_APP_VERSION",
    "JIPSA_RAG_API_V1_PREFIX",
    "JIPSA_RAG_HOST",
    "JIPSA_RAG_PORT",
    "JIPSA_RAG_DEBUG",
    "JIPSA_RAG_DATABASE_HOST",
    "JIPSA_RAG_DATABASE_PORT",
    "JIPSA_RAG_DATABASE_NAME",
    "JIPSA_RAG_DATABASE_USER",
    "JIPSA_RAG_DATABASE_PASSWORD",
    "JIPSA_RAG_DATABASE_CHARSET",
    "JIPSA_RAG_DATABASE_ECHO",
    "JIPSA_RAG_DATABASE_CHECK_ON_STARTUP",
    "JIPSA_RAG_S3_ALLOWED_KEY_PREFIX",
    "JIPSA_RAG_APP_SERVER_BASE_URL",
    "JIPSA_RAG_APP_SERVER_API_V1_PREFIX",
    "JIPSA_RAG_APP_SERVER_CONNECT_TIMEOUT_SECONDS",
    "JIPSA_RAG_APP_SERVER_READ_TIMEOUT_SECONDS",
)


def _clear_setting_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings 테스트에 영향을 줄 수 있는 OS 환경 변수를 제거한다."""

    for variable_name in _SETTING_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(
            variable_name,
            raising=False,
        )


def _write_test_env_file(
    env_file: Path,
    *,
    app_name: str = "Development RAG Service",
    debug: bool = False,
) -> None:
    """Settings 생성에 필요한 테스트용 dotenv 파일을 작성한다."""

    env_file.write_text(
        "\n".join(
            [
                f'JIPSA_RAG_APP_NAME="{app_name}"',
                "JIPSA_RAG_APP_VERSION=0.1.0",
                "JIPSA_RAG_API_V1_PREFIX=/api/v1",
                "JIPSA_RAG_HOST=127.0.0.1",
                "JIPSA_RAG_PORT=8000",
                f"JIPSA_RAG_DEBUG={str(debug).lower()}",
                "JIPSA_RAG_DATABASE_HOST=127.0.0.1",
                "JIPSA_RAG_DATABASE_PORT=3306",
                "JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG",
                "JIPSA_RAG_DATABASE_USER=test_user",
                "JIPSA_RAG_DATABASE_PASSWORD=test_password",
                "JIPSA_RAG_DATABASE_CHARSET=utf8mb4",
                "JIPSA_RAG_DATABASE_ECHO=false",
                "JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=false",
                "JIPSA_RAG_S3_ALLOWED_KEY_PREFIX=files/",
                ("JIPSA_RAG_APP_SERVER_BASE_URL=http://127.0.0.1:8080"),
                "JIPSA_RAG_APP_SERVER_API_V1_PREFIX=/api/v1",
                ("JIPSA_RAG_APP_SERVER_CONNECT_TIMEOUT_SECONDS=5.0"),
                ("JIPSA_RAG_APP_SERVER_READ_TIMEOUT_SECONDS=30.0"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _create_settings(
    **overrides: Any,
) -> Settings:
    """환경 파일에 의존하지 않는 기본 테스트 Settings를 생성한다."""

    values: dict[str, Any] = {
        "app_env": "test",
        "app_name": "Jipsa RAG Service Test",
        "app_version": "0.1.0",
        "api_v1_prefix": "/api/v1",
        "host": "127.0.0.1",
        "port": 8001,
        "debug": False,
        "database_host": "127.0.0.1",
        "database_port": 3306,
        "database_name": "Jipsa_Local_RAG",
        "database_user": "test_user",
        "database_password": "test_password",
        "database_charset": "utf8mb4",
        "database_echo": False,
        "database_check_on_startup": False,
        "s3_allowed_key_prefix": "files/",
        "app_server_base_url": "http://127.0.0.1:8080",
        "app_server_api_v1_prefix": "/api/v1",
        "app_server_connect_timeout_seconds": 5.0,
        "app_server_read_timeout_seconds": 30.0,
        "_env_file": None,
    }

    values.update(overrides)

    return Settings(**values)


def test_resolve_environment_defaults_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실행 환경이 지정되지 않으면 local을 사용해야 한다."""

    monkeypatch.delenv(
        "JIPSA_RAG_APP_ENV",
        raising=False,
    )

    assert resolve_environment() == "local"


def test_resolve_environment_reads_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OS 환경 변수에 지정된 development 환경을 읽어야 한다."""

    monkeypatch.setenv(
        "JIPSA_RAG_APP_ENV",
        "development",
    )

    assert resolve_environment() == "development"


def test_resolve_environment_normalizes_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실행 환경 문자열의 앞뒤 공백과 대소문자를 정규화해야 한다."""

    monkeypatch.setenv(
        "JIPSA_RAG_APP_ENV",
        "  DEVELOPMENT  ",
    )

    assert resolve_environment() == "development"


def test_resolve_environment_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정의되지 않은 실행 환경을 거부해야 한다."""

    monkeypatch.setenv(
        "JIPSA_RAG_APP_ENV",
        "invalid",
    )

    with pytest.raises(
        ValueError,
        match="지원하지 않는 실행 환경",
    ):
        resolve_environment()


def test_resolve_environment_rejects_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """로컬 전용 RAG에서는 production 환경을 허용하지 않아야 한다."""

    monkeypatch.setenv(
        "JIPSA_RAG_APP_ENV",
        "production",
    )

    with pytest.raises(
        ValueError,
        match="지원하지 않는 실행 환경",
    ):
        resolve_environment()


def test_resolve_env_file_returns_profile_file(
    tmp_path: Path,
) -> None:
    """실행 환경과 일치하는 dotenv 파일 경로를 반환해야 한다."""

    env_file = tmp_path / ".env.development"
    _write_test_env_file(env_file)

    result = resolve_env_file(
        "development",
        tmp_path,
    )

    assert result == env_file


def test_resolve_env_file_returns_none_when_file_is_missing(
    tmp_path: Path,
) -> None:
    """대상 dotenv 파일이 없으면 None을 반환해야 한다."""

    result = resolve_env_file(
        "test",
        tmp_path,
    )

    assert result is None


def test_settings_loads_selected_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """선택한 dotenv 파일에서 전체 설정을 읽어야 한다."""

    _clear_setting_environment_variables(monkeypatch)

    env_file = tmp_path / ".env.development"
    _write_test_env_file(
        env_file,
        app_name="Development RAG Service",
        debug=False,
    )

    settings = Settings(
        app_env="development",
        _env_file=env_file,
        _env_file_encoding="utf-8",
    )

    assert settings.app_env == "development"
    assert settings.app_name == "Development RAG Service"
    assert settings.app_version == "0.1.0"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.debug is False

    assert settings.database_host == "127.0.0.1"
    assert settings.database_port == 3306
    assert settings.database_name == "Jipsa_Local_RAG"
    assert settings.database_user == "test_user"
    assert settings.database_password.get_secret_value() == "test_password"
    assert settings.database_charset == "utf8mb4"
    assert settings.database_echo is False
    assert settings.database_check_on_startup is False

    assert settings.s3_allowed_key_prefix == "files/"

    assert settings.app_server_base_url == "http://127.0.0.1:8080"
    assert settings.app_server_api_v1_prefix == "/api/v1"
    assert settings.app_server_connect_timeout_seconds == 5.0
    assert settings.app_server_read_timeout_seconds == 30.0


def test_settings_strips_non_secret_text() -> None:
    """일반 문자열 설정의 앞뒤 공백을 제거해야 한다."""

    settings = _create_settings(
        app_name="  Jipsa RAG Service  ",
        database_host="  127.0.0.1  ",
        database_name="  Jipsa_Local_RAG  ",
        database_user="  test_user  ",
        s3_allowed_key_prefix="  files/  ",
        app_server_base_url="  http://127.0.0.1:8080  ",
    )

    assert settings.app_name == "Jipsa RAG Service"
    assert settings.database_host == "127.0.0.1"
    assert settings.database_name == "Jipsa_Local_RAG"
    assert settings.database_user == "test_user"
    assert settings.s3_allowed_key_prefix == "files/"
    assert settings.app_server_base_url == "http://127.0.0.1:8080"


def test_settings_creates_asyncmy_database_url() -> None:
    """비동기 MySQL 연결에 사용할 SQLAlchemy URL을 생성해야 한다."""

    settings = _create_settings()

    database_url = settings.database_url

    assert database_url.drivername == "mysql+asyncmy"
    assert database_url.username == "test_user"
    assert database_url.password == "test_password"
    assert database_url.host == "127.0.0.1"
    assert database_url.port == 3306
    assert database_url.database == "Jipsa_Local_RAG"
    assert database_url.query["charset"] == "utf8mb4"


def test_settings_builds_application_server_api_base_url() -> None:
    """서버 기본 주소와 API prefix를 결합해야 한다."""

    settings = _create_settings(
        app_server_base_url="http://127.0.0.1:8080",
        app_server_api_v1_prefix="/api/v1",
    )

    assert settings.app_server_api_base_url == "http://127.0.0.1:8080/api/v1"


@pytest.mark.parametrize(
    "field_name",
    [
        "api_v1_prefix",
        "app_server_api_v1_prefix",
    ],
)
def test_api_prefix_must_start_with_slash(
    field_name: str,
) -> None:
    """API prefix는 슬래시로 시작해야 한다."""

    with pytest.raises(
        ValidationError,
        match="API prefix는 '/'로 시작해야 합니다",
    ):
        _create_settings(
            **{
                field_name: "api/v1",
            }
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "api_v1_prefix",
        "app_server_api_v1_prefix",
    ],
)
def test_api_prefix_must_not_end_with_slash(
    field_name: str,
) -> None:
    """API prefix는 슬래시로 끝날 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match="API prefix는 '/'로 끝날 수 없습니다",
    ):
        _create_settings(
            **{
                field_name: "/api/v1/",
            }
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "api_v1_prefix",
        "app_server_api_v1_prefix",
    ],
)
def test_api_prefix_rejects_consecutive_slashes(
    field_name: str,
) -> None:
    """API prefix에 연속된 슬래시가 포함되면 거부해야 한다."""

    with pytest.raises(
        ValidationError,
        match="연속된 '/'를 사용할 수 없습니다",
    ):
        _create_settings(
            **{
                field_name: "/api//v1",
            }
        )


def test_application_server_base_url_accepts_https() -> None:
    """애플리케이션 서버 주소는 HTTPS 스킴도 허용해야 한다."""

    settings = _create_settings(
        app_server_base_url="https://development.example.com",
    )

    assert settings.app_server_base_url == "https://development.example.com"


def test_application_server_base_url_must_not_end_with_slash() -> None:
    """애플리케이션 서버 기본 주소는 슬래시로 끝날 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match="기본 URL은 '/'로 끝날 수 없습니다",
    ):
        _create_settings(
            app_server_base_url="http://127.0.0.1:8080/",
        )


def test_application_server_base_url_requires_http_scheme() -> None:
    """애플리케이션 서버 주소는 HTTP 또는 HTTPS를 사용해야 한다."""

    with pytest.raises(
        ValidationError,
        match="http 또는 https 스킴",
    ):
        _create_settings(
            app_server_base_url="ftp://127.0.0.1:8080",
        )


def test_application_server_base_url_requires_host() -> None:
    """애플리케이션 서버 주소에는 호스트가 포함되어야 한다."""

    with pytest.raises(
        ValidationError,
        match="호스트가 필요합니다",
    ):
        _create_settings(
            app_server_base_url="http:///api",
        )


def test_application_server_base_url_rejects_credentials() -> None:
    """기본 URL 안에 사용자 이름이나 비밀번호를 포함할 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match="인증 정보를 포함할 수 없습니다",
    ):
        _create_settings(
            app_server_base_url=("http://user:password@127.0.0.1:8080"),
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8080?file=1",
        "http://127.0.0.1:8080#section",
    ],
)
def test_application_server_base_url_rejects_query_and_fragment(
    base_url: str,
) -> None:
    """기본 URL에 query 또는 fragment를 포함할 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match="query 또는 fragment",
    ):
        _create_settings(
            app_server_base_url=base_url,
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:not-a-port",
        "http://127.0.0.1:70000",
    ],
)
def test_application_server_base_url_rejects_invalid_port(
    base_url: str,
) -> None:
    """애플리케이션 서버 포트가 유효하지 않으면 거부해야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            app_server_base_url=base_url,
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "app_server_connect_timeout_seconds",
        "app_server_read_timeout_seconds",
    ],
)
@pytest.mark.parametrize(
    "invalid_timeout",
    [
        0,
        -1,
    ],
)
def test_application_server_timeout_must_be_positive(
    field_name: str,
    invalid_timeout: float,
) -> None:
    """애플리케이션 서버 제한 시간은 0보다 커야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            **{
                field_name: invalid_timeout,
            }
        )


def test_s3_allowed_key_prefix_rejects_unknown_prefix() -> None:
    """S3 Object Key prefix는 files/만 허용해야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            s3_allowed_key_prefix="uploads/",
        )
