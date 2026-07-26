"""생성 클라이언트가 사용하는 공급자 독립 요청 및 응답 모델을 정의한다."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType


def _validate_required_text(
    value: str,
    *,
    field_name: str,
) -> None:
    """원본 문자열은 보존하면서 공백만 있는 입력을 거부한다."""

    if not value.strip():
        raise ValueError(
            f"{field_name} must not be empty.",
        )


def _copy_read_only_mapping(
    value: Mapping[str, object],
) -> Mapping[str, object]:
    """호출자가 전달한 매핑을 깊은 복사한 뒤 읽기 전용으로 고정한다.

    JSON Schema와 구조화된 생성 결과는 중첩된 dict와 list를 포함할 수 있다.
    최상위 dict만 얕게 복사하면 호출자가 원본 중첩 객체를 변경했을 때 이미
    생성된 요청이나 결과의 의미가 달라질 수 있다.

    따라서 전체 구조를 깊은 복사한 뒤 최상위 매핑을 읽기 전용으로 노출한다.

    Args:
        value:
            복사하고 고정할 문자열 Key 기반 매핑이다.

    Returns:
        외부 원본과 독립적인 읽기 전용 매핑이다.
    """

    return MappingProxyType(
        deepcopy(
            dict(value),
        )
    )


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """텍스트 생성 공급자에 전달할 내부 요청 모델.

    Anthropic SDK의 ``MessageParam``이나 ``OutputConfigParam`` 같은 외부
    공급자 타입을 상위 서비스에 노출하지 않기 위해 프롬프트와 선택적 JSON
    Schema만 공급자 독립 형태로 보관한다.

    프롬프트의 줄바꿈과 들여쓰기는 RAG 문맥 구조에 영향을 줄 수 있으므로
    앞뒤 공백을 제거하지 않고 원문을 그대로 유지한다.

    ``output_schema``가 제공되면 생성 클라이언트는 공급자가 지원하는 구조화
    출력 기능을 사용해야 한다. 스키마는 요청 생성 시 깊은 복사하여 호출자의
    후속 변경이 이미 시작된 생성 요청에 영향을 주지 않게 한다.

    ``max_output_tokens``는 요청 단위 생성 제한기가 남은 누적 출력 예산에
    맞춰 단일 Claude 호출의 ``max_tokens``를 낮출 때 사용한다. 값이 없으면
    공급자 설정의 기본 최대 출력 토큰 수를 그대로 사용한다.
    """

    user_prompt: str
    system_prompt: str | None = None
    output_schema: Mapping[str, object] | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        """프롬프트와 선택적 구조화 출력 스키마 및 토큰 제한을 검증한다."""

        _validate_required_text(
            self.user_prompt,
            field_name="user_prompt",
        )

        if self.system_prompt is not None:
            _validate_required_text(
                self.system_prompt,
                field_name="system_prompt",
            )

        if (
            self.max_output_tokens is not None
            and self.max_output_tokens <= 0
        ):
            raise ValueError(
                "max_output_tokens must be greater than zero when provided."
            )

        if self.output_schema is None:
            return

        if not self.output_schema:
            raise ValueError(
                "output_schema must not be empty when provided."
            )

        object.__setattr__(
            self,
            "output_schema",
            _copy_read_only_mapping(
                self.output_schema,
            ),
        )


@dataclass(frozen=True, slots=True)
class GenerationUsage:
    """단일 생성 요청에서 사용된 입력 및 출력 토큰 수."""

    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        """토큰 사용량이 음수가 아닌지 검증한다."""

        if self.input_tokens < 0:
            raise ValueError(
                "input_tokens must be greater than or equal to zero."
            )

        if self.output_tokens < 0:
            raise ValueError(
                "output_tokens must be greater than or equal to zero."
            )

    @property
    def total_tokens(self) -> int:
        """입력 토큰과 출력 토큰의 합계를 반환한다."""

        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """외부 생성 공급자의 응답을 정규화한 내부 결과 모델.

    ``text``에는 공급자가 반환한 원본 텍스트를 보존한다. 일반 텍스트 생성은
    Markdown, 코드 블록 또는 줄바꿈을 포함할 수 있으며, 구조화 출력 생성은
    JSON 문자열을 포함할 수 있다.

    ``structured_output``은 구조화 출력을 요청한 경우 생성 클라이언트가 JSON
    객체로 안전하게 파싱한 결과다. 상위 서비스는 원본 JSON 문자열을 다시
    파싱하지 않고 이 매핑을 도메인별 모델로 검증한다.

    모델 ID와 종료 사유만 식별값으로 사용할 수 있도록 앞뒤 공백을 제거한다.
    구조화 출력 매핑은 결과 생성 시 깊은 복사하여 외부 변경을 차단한다.
    """

    text: str
    model: str
    usage: GenerationUsage
    stop_reason: str | None = None
    structured_output: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        """응답 텍스트, 식별값 및 선택적 구조화 출력 객체를 검증한다."""

        _validate_required_text(
            self.text,
            field_name="text",
        )

        normalized_model = self.model.strip()

        if not normalized_model:
            raise ValueError(
                "model must not be empty.",
            )

        object.__setattr__(
            self,
            "model",
            normalized_model,
        )

        if self.stop_reason is not None:
            normalized_stop_reason = self.stop_reason.strip()

            if not normalized_stop_reason:
                raise ValueError(
                    "stop_reason must not be empty when provided."
                )

            object.__setattr__(
                self,
                "stop_reason",
                normalized_stop_reason,
            )

        if self.structured_output is not None:
            object.__setattr__(
                self,
                "structured_output",
                _copy_read_only_mapping(
                    self.structured_output,
                ),
            )