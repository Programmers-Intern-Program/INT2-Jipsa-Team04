"""파일 다운로드 환경 설정과 호스트 제한값 검증을 테스트한다."""

from typing import Any

import pytest
from pydantic import ValidationError

from jipsa_rag.core.config import Settings


def _create_settings(
    **overrides: Any,
) -> Settings:
    """환경 파일에 의존하지 않는 테스트 Settings를 생성한다."""

    values: dict[str, Any] = {
        "app_env": "test",
        "database_host": "127.0.0.1",
        "database_port": 3306,
        "database_name": "Jipsa_Local_RAG",
        "database_user": "test_user",
        "database_password": "test_password",
        "database_charset": "utf8mb4",
        "database_echo": False,
        "database_check_on_startup": False,
        "file_download_allowed_host_suffixes": (".amazonaws.com"),
        "file_download_connect_timeout_seconds": 5.0,
        "file_download_read_timeout_seconds": 60.0,
        "file_download_max_size_bytes": 52_428_800,
        "_env_file": None,
    }

    values.update(overrides)

    return Settings(**values)


def test_file_download_settings_use_expected_defaults() -> None:
    """파일 다운로드 설정의 기본값이 안전한 범위인지 확인한다."""

    settings = _create_settings()

    assert settings.file_download_allowed_host_suffixes == ".amazonaws.com"
    assert settings.parsed_file_download_allowed_host_suffixes == (".amazonaws.com",)
    assert settings.file_download_connect_timeout_seconds == 5.0
    assert settings.file_download_read_timeout_seconds == 60.0
    assert settings.file_download_max_size_bytes == 52_428_800


def test_file_download_host_suffixes_are_normalized() -> None:
    """호스트 suffix의 공백, 대소문자 및 wildcard를 정규화한다."""

    settings = _create_settings(
        file_download_allowed_host_suffixes=(
            " *.AmazonAWS.com, files.example.com, .amazonaws.com "
        ),
    )

    # amazonaws.com 항목은 입력 형식이 달라도
    # 하나의 정규화된 값으로 중복 제거되어야 한다.
    assert settings.file_download_allowed_host_suffixes == ".amazonaws.com,.files.example.com"
    assert settings.parsed_file_download_allowed_host_suffixes == (
        ".amazonaws.com",
        ".files.example.com",
    )


@pytest.mark.parametrize(
    "invalid_suffix",
    [
        "",
        "   ",
        "https://amazonaws.com",
        "amazonaws.com:443",
        "user@amazonaws.com",
        "amazonaws.com/path",
        "*internal*.example.com",
    ],
)
def test_file_download_host_suffixes_reject_invalid_value(
    invalid_suffix: str,
) -> None:
    """도메인 suffix가 아닌 허용 호스트 설정을 거부한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            file_download_allowed_host_suffixes=(invalid_suffix),
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "file_download_connect_timeout_seconds",
        "file_download_read_timeout_seconds",
        "file_download_max_size_bytes",
    ],
)
@pytest.mark.parametrize(
    "invalid_value",
    [
        0,
        -1,
    ],
)
def test_file_download_positive_settings_reject_invalid_value(
    field_name: str,
    invalid_value: int,
) -> None:
    """다운로드 시간과 최대 크기는 0보다 커야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            **{
                field_name: invalid_value,
            }
        )


def test_file_download_max_size_rejects_over_one_gibibyte() -> None:
    """단일 파일 최대 크기는 1 GiB를 초과할 수 없어야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            file_download_max_size_bytes=(1024 * 1024 * 1024 + 1),
        )
