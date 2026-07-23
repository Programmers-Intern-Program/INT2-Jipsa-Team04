"""Claude API 생성 설정의 로딩과 비밀값 검증을 테스트한다."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from jipsa_rag.core.generation_config import GenerationSettings

_TEST_API_KEY = "sk-ant-test-0123456789abcdef0123456789abcdef"

_GENERATION_ENVIRONMENT_VARIABLES = (
    "JIPSA_RAG_GENERATION_PROVIDER",
    "ANTHROPIC_API_KEY",
    "JIPSA_RAG_ANTHROPIC_API_KEY",
    "JIPSA_RAG_ANTHROPIC_MODEL",
    "JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS",
    "JIPSA_RAG_ANTHROPIC_TIMEOUT_SECONDS",
)


def _create_settings(
    **overrides: Any,
) -> GenerationSettings:
    """OS 환경 변수에 의존하지 않는 Claude API 생성 설정을 만든다."""

    values: dict[str, Any] = {
        "generation_provider": "anthropic",
        "anthropic_api_key": _TEST_API_KEY,
        "anthropic_model": "claude-sonnet-5",
        "anthropic_max_output_tokens": 4096,
        "anthropic_timeout_seconds": 60.0,
        "_env_file": None,
    }

    values.update(overrides)

    return GenerationSettings(**values)


def _clear_generation_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """생성 설정 테스트에 영향을 줄 수 있는 환경 변수를 제거한다."""

    for variable_name in _GENERATION_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(
            variable_name,
            raising=False,
        )


def test_generation_settings_store_api_key_as_secret_str() -> None:
    """Anthropic API Key는 SecretStr로 저장되고 repr에서 가려져야 한다."""

    settings = _create_settings()

    assert isinstance(settings.anthropic_api_key, SecretStr)
    assert settings.anthropic_api_key.get_secret_value() == _TEST_API_KEY
    assert _TEST_API_KEY not in repr(settings)


def test_generation_settings_load_environment_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """공통 dotenv 파일에서 Claude API 설정을 읽어야 한다."""

    _clear_generation_environment_variables(monkeypatch)

    env_file = tmp_path / ".env.development"
    env_file.write_text(
        "\n".join(
            [
                "JIPSA_RAG_GENERATION_PROVIDER=ANTHROPIC",
                f"ANTHROPIC_API_KEY={_TEST_API_KEY}",
                "JIPSA_RAG_ANTHROPIC_MODEL=claude-sonnet-5",
                "JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS=8192",
                "JIPSA_RAG_ANTHROPIC_TIMEOUT_SECONDS=90",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = GenerationSettings(
        _env_file=env_file,
        _env_file_encoding="utf-8",
    )

    assert settings.generation_provider == "anthropic"
    assert settings.anthropic_api_key.get_secret_value() == _TEST_API_KEY
    assert settings.anthropic_model == "claude-sonnet-5"
    assert settings.anthropic_max_output_tokens == 8192
    assert settings.anthropic_timeout_seconds == 90.0


def test_generation_settings_accept_project_prefixed_api_key_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """프로젝트 접두사를 적용한 API Key 환경 변수도 허용해야 한다."""

    _clear_generation_environment_variables(monkeypatch)

    monkeypatch.setenv(
        "JIPSA_RAG_ANTHROPIC_API_KEY",
        _TEST_API_KEY,
    )

    settings = GenerationSettings(
        _env_file=None,
    )

    assert settings.anthropic_api_key.get_secret_value() == _TEST_API_KEY


def test_generation_settings_normalize_provider_model_and_api_key() -> None:
    """공급자, 모델 ID 및 API Key의 불필요한 바깥 공백을 제거해야 한다."""

    settings = _create_settings(
        generation_provider="  ANTHROPIC  ",
        anthropic_api_key=f"  {_TEST_API_KEY}  ",
        anthropic_model="  claude-sonnet-5  ",
    )

    assert settings.generation_provider == "anthropic"
    assert settings.anthropic_api_key.get_secret_value() == _TEST_API_KEY
    assert settings.anthropic_model == "claude-sonnet-5"


@pytest.mark.parametrize(
    "placeholder",
    [
        "CHANGE_ME",
        "CHANGE_ME_TO_ANTHROPIC_API_KEY",
        "REPLACE_WITH_ANTHROPIC_API_KEY",
        "YOUR_ANTHROPIC_API_KEY",
    ],
)
def test_generation_settings_reject_placeholder_api_key(
    placeholder: str,
) -> None:
    """예시용 placeholder 문자열을 실제 API Key로 허용하지 않아야 한다."""

    with pytest.raises(
        ValidationError,
        match="placeholder",
    ):
        _create_settings(
            anthropic_api_key=placeholder,
        )


def test_generation_settings_reject_api_key_with_internal_whitespace_without_leak() -> None:
    """손상된 API Key를 거부하고 ValidationError에 원문을 노출하지 않아야 한다."""

    invalid_api_key = "sk-ant-sensitive-key-with hidden-whitespace-0123456789"

    with pytest.raises(ValidationError) as exc_info:
        _create_settings(
            anthropic_api_key=invalid_api_key,
        )

    assert "공백 문자" in str(exc_info.value)
    assert invalid_api_key not in str(exc_info.value)


def test_generation_settings_reject_too_short_api_key() -> None:
    """최소 길이에 미달하는 Anthropic API Key를 거부해야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            anthropic_api_key="sk-ant-short",
        )


@pytest.mark.parametrize(
    "invalid_model",
    [
        "sonnet-5",
        "claude sonnet 5",
        "claude-sonnet-5/api",
    ],
)
def test_generation_settings_reject_invalid_model_id(
    invalid_model: str,
) -> None:
    """Claude 모델 ID 기본 형식에 맞지 않는 값을 거부해야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            anthropic_model=invalid_model,
        )


@pytest.mark.parametrize(
    "invalid_max_output_tokens",
    [
        0,
        128_001,
    ],
)
def test_generation_settings_reject_invalid_max_output_tokens(
    invalid_max_output_tokens: int,
) -> None:
    """최대 출력 토큰 수는 허용 범위 안에 있어야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            anthropic_max_output_tokens=invalid_max_output_tokens,
        )


@pytest.mark.parametrize(
    "invalid_timeout",
    [
        0,
        600.1,
    ],
)
def test_generation_settings_reject_invalid_timeout(
    invalid_timeout: float,
) -> None:
    """Claude API 제한 시간은 0초 초과 600초 이하여야 한다."""

    with pytest.raises(ValidationError):
        _create_settings(
            anthropic_timeout_seconds=invalid_timeout,
        )
