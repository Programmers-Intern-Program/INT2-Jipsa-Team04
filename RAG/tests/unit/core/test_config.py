from pathlib import Path

import pytest

from jipsa_rag.core.config import (
    Settings,
    resolve_env_file,
    resolve_environment,
)


def test_resolve_environment_defaults_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "JIPSA_RAG_APP_ENV",
        raising=False,
    )

    assert resolve_environment() == "local"


def test_resolve_environment_reads_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "JIPSA_RAG_APP_ENV",
        "development",
    )

    assert resolve_environment() == "development"


def test_resolve_environment_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "JIPSA_RAG_APP_ENV",
        "invalid",
    )

    with pytest.raises(
        ValueError,
        match="지원하지 않는 실행 환경",
    ):
        resolve_environment()


def test_resolve_env_file_returns_profile_file(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.development"
    env_file.write_text(
        "JIPSA_RAG_DEBUG=false\n",
        encoding="utf-8",
    )

    result = resolve_env_file(
        "development",
        tmp_path,
    )

    assert result == env_file


def test_resolve_env_file_returns_none_for_production(
    tmp_path: Path,
) -> None:
    assert resolve_env_file("production", tmp_path) is None


def test_settings_loads_selected_env_file(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.development"
    env_file.write_text(
        "\n".join(
            [
                "JIPSA_RAG_APP_NAME=Development RAG Service",
                "JIPSA_RAG_DEBUG=false",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(
        app_env="development",
        _env_file=env_file,
        _env_file_encoding="utf-8",
    )

    assert settings.app_env == "development"
    assert settings.app_name == "Development RAG Service"
    assert settings.debug is False
