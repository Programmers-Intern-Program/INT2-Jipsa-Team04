"""생성 클라이언트가 사용하는 공급자 독립 요청 및 응답 모델을 정의한다."""

from dataclasses import dataclass


def _validate_required_text(
    value: str,
    *,
    field_name: str,
) -> None:
    """원본 문자열은 보존하면서 공백만 있는 입력을 거부한다."""

    if not value.strip():
        raise ValueError(f"{field_name} must not be empty.")


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """텍스트 생성 공급자에 전달할 내부 요청 모델.

    Anthropic SDK의 MessageParam과 같은 외부 타입을 상위 서비스에
    노출하지 않기 위해 시스템 프롬프트와 사용자 프롬프트만 보관한다.

    프롬프트의 줄바꿈과 들여쓰기는 RAG 문맥 구조에 영향을 줄 수 있으므로
    앞뒤 공백을 제거하지 않고 원문을 그대로 유지한다.
    """

    user_prompt: str
    system_prompt: str | None = None

    def __post_init__(self) -> None:
        """사용자 및 시스템 프롬프트가 공백으로만 구성되지 않았는지 검증한다."""

        _validate_required_text(
            self.user_prompt,
            field_name="user_prompt",
        )

        if self.system_prompt is not None:
            _validate_required_text(
                self.system_prompt,
                field_name="system_prompt",
            )


@dataclass(frozen=True, slots=True)
class GenerationUsage:
    """단일 생성 요청에서 사용된 입력 및 출력 토큰 수."""

    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        """토큰 사용량이 음수가 아닌지 검증한다."""

        if self.input_tokens < 0:
            raise ValueError("input_tokens must be greater than or equal to zero.")

        if self.output_tokens < 0:
            raise ValueError("output_tokens must be greater than or equal to zero.")

    @property
    def total_tokens(self) -> int:
        """입력 토큰과 출력 토큰의 합계를 반환한다."""

        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """외부 생성 공급자의 응답을 정규화한 내부 결과 모델.

    생성 텍스트는 Markdown, 코드 블록 또는 줄바꿈을 포함할 수 있으므로
    검증 과정에서 내용을 변경하지 않는다. 모델 ID와 종료 사유만 식별값으로
    사용할 수 있도록 앞뒤 공백을 제거한다.
    """

    text: str
    model: str
    usage: GenerationUsage
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        """응답 텍스트, 모델 ID 및 선택적 종료 사유를 검증한다."""

        _validate_required_text(
            self.text,
            field_name="text",
        )

        normalized_model = self.model.strip()

        if not normalized_model:
            raise ValueError("model must not be empty.")

        object.__setattr__(
            self,
            "model",
            normalized_model,
        )

        if self.stop_reason is None:
            return

        normalized_stop_reason = self.stop_reason.strip()

        if not normalized_stop_reason:
            raise ValueError("stop_reason must not be empty when provided.")

        object.__setattr__(
            self,
            "stop_reason",
            normalized_stop_reason,
        )
