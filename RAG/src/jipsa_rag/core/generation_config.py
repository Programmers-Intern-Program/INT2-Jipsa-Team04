"""Claude API 텍스트 생성에 필요한 환경 설정을 관리한다."""

from functools import lru_cache
from typing import Final, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jipsa_rag.core.config import resolve_env_file, resolve_environment

# 현재 생성 공급자는 Anthropic Claude API로 고정한다.
#
# 상위 서비스는 이 문자열 대신 GenerationClient Protocol에 의존하고,
# 공급자 선택과 SDK 세부 구현은 인프라 및 설정 계층에서만 처리한다.
GenerationProvider = Literal["anthropic"]

# .env.example의 안내 문구나 일반적인 예시 문자열을 실제 API Key로
# 잘못 사용하는 경우를 설정 로딩 단계에서 차단한다.
_ANTHROPIC_API_KEY_PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    {
        "change_me",
        "change_me_to_anthropic_api_key",
        "replace_with_anthropic_api_key",
        "your_anthropic_api_key",
    }
)


class GenerationSettings(BaseSettings):
    """Claude API 생성 클라이언트 구성에 필요한 설정."""

    # =========================================================
    # Generation Provider
    # =========================================================

    # 텍스트 생성 공급자다.
    #
    # 현재 구현 범위에서는 Anthropic Claude API만 허용한다.
    generation_provider: GenerationProvider = "anthropic"

    # =========================================================
    # Anthropic Claude API
    # =========================================================

    # Anthropic Claude API 인증에 사용하는 비밀값이다.
    #
    # 공식 SDK가 기본으로 인식하는 ANTHROPIC_API_KEY와 프로젝트의
    # JIPSA_RAG_ 접두사 규칙을 적용한 환경 변수 이름을 모두 허용한다.
    #
    # SecretStr를 사용하므로 Settings repr, 문자열 변환 및 일반 로그에는
    # API Key 원문이 노출되지 않는다.
    anthropic_api_key: SecretStr = Field(
        min_length=20,
        max_length=512,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY",
            "JIPSA_RAG_ANTHROPIC_API_KEY",
        ),
    )

    # Claude Messages API 요청에 사용할 모델 ID다.
    #
    # 모델 교체 시 소스 코드를 수정하지 않고 환경 변수만 변경할 수 있도록
    # 설정값으로 관리한다.
    anthropic_model: str = Field(
        default="claude-sonnet-5",
        min_length=1,
        max_length=128,
    )

    # 한 번의 생성 요청에서 허용할 최대 출력 토큰 수다.
    #
    # 현재 Claude 모델군의 최대 출력 한도를 넘는 비정상 설정을 방지하기 위해
    # 상한을 128,000으로 제한한다. 실제 허용 한도는 선택한 모델에 따라
    # 더 작을 수 있으며, 해당 검증은 Claude API가 최종적으로 수행한다.
    anthropic_max_output_tokens: int = Field(
        default=4096,
        ge=1,
        le=128_000,
    )

    # Claude API 요청 전체에 적용할 제한 시간이다.
    #
    # 단위는 초이며 네트워크 장애로 요청이 무기한 대기하지 않도록 한다.
    anthropic_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
    )

    model_config = SettingsConfigDict(
        # generation_provider
        # -> JIPSA_RAG_GENERATION_PROVIDER
        #
        # anthropic_model
        # -> JIPSA_RAG_ANTHROPIC_MODEL
        #
        # anthropic_max_output_tokens
        # -> JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS
        #
        # anthropic_timeout_seconds
        # -> JIPSA_RAG_ANTHROPIC_TIMEOUT_SECONDS
        #
        # anthropic_api_key는 공식 환경 변수 이름과 프로젝트 접두사 이름을
        # 모두 허용하기 위해 필드의 validation_alias에서 별도로 정의한다.
        env_prefix="JIPSA_RAG_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
        populate_by_name=True,
        # API Key 검증이 실패하더라도 ValidationError 문자열에
        # 사용자가 입력한 원문이 포함되지 않도록 한다.
        hide_input_in_errors=True,
    )

    @field_validator(
        "generation_provider",
        mode="before",
    )
    @classmethod
    def normalize_generation_provider(
        cls,
        value: object,
    ) -> object:
        """생성 공급자 문자열의 앞뒤 공백과 대소문자를 정규화한다."""

        if isinstance(value, str):
            return value.strip().lower()

        return value

    @field_validator(
        "anthropic_api_key",
        mode="before",
    )
    @classmethod
    def validate_anthropic_api_key(
        cls,
        value: object,
    ) -> object:
        """Anthropic API Key 형식을 검증하되 원문을 오류에 포함하지 않는다."""

        if isinstance(value, SecretStr):
            raw_value = value.get_secret_value()
        elif isinstance(value, str):
            raw_value = value
        else:
            return value

        normalized_value = raw_value.strip()

        if not normalized_value:
            raise ValueError("Anthropic API Key는 비어 있을 수 없습니다.")

        # API Key 내부의 공백, 탭 또는 개행은 복사 과정에서 값이
        # 손상되었을 가능성이 높으므로 외부 요청 전에 거부한다.
        if any(character.isspace() for character in normalized_value):
            raise ValueError("Anthropic API Key에는 공백 문자를 포함할 수 없습니다.")

        if normalized_value.casefold() in _ANTHROPIC_API_KEY_PLACEHOLDERS:
            raise ValueError("Anthropic API Key에 예시용 placeholder를 사용할 수 없습니다.")

        return normalized_value

    @field_validator(
        "anthropic_model",
        mode="before",
    )
    @classmethod
    def normalize_anthropic_model(
        cls,
        value: object,
    ) -> object:
        """Claude 모델 ID의 앞뒤 공백을 제거한다."""

        if isinstance(value, str):
            return value.strip()

        return value

    @field_validator("anthropic_model")
    @classmethod
    def validate_anthropic_model(
        cls,
        value: str,
    ) -> str:
        """Anthropic Claude API 모델 ID의 기본 형식을 검증한다."""

        if not value.startswith("claude-"):
            raise ValueError("Anthropic 모델 ID는 'claude-'로 시작해야 합니다.")

        if any(character.isspace() for character in value):
            raise ValueError("Anthropic 모델 ID에는 공백 문자를 포함할 수 없습니다.")

        if any(
            character in value
            for character in (
                "/",
                "\\",
                "?",
                "#",
            )
        ):
            raise ValueError("Anthropic 모델 ID에는 경로 문자를 포함할 수 없습니다.")

        return value


@lru_cache(maxsize=1)
def get_generation_settings() -> GenerationSettings:
    """현재 실행 환경의 Claude API 생성 설정을 생성하고 재사용한다."""

    environment = resolve_environment()
    env_file = resolve_env_file(environment)

    return GenerationSettings(
        # 기존 Settings와 동일한 .env.local, .env.development,
        # .env.test 파일에서 Claude API 설정을 읽는다.
        _env_file=env_file,
    )
