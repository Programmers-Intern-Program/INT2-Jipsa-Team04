"""공급자 독립 생성 요청 및 응답 모델을 테스트한다."""

import pytest

from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
)


def test_generation_request_preserves_prompt_formatting() -> None:
    """프롬프트 검증 후에도 줄바꿈과 들여쓰기를 그대로 유지해야 한다."""

    user_prompt = "\n질문:\n  계약 해지 조건을 알려줘.\n"
    system_prompt = "\n문서 근거만 사용해 답변한다.\n"

    request = GenerationRequest(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )

    assert request.user_prompt == user_prompt
    assert request.system_prompt == system_prompt


@pytest.mark.parametrize(
    "user_prompt",
    [
        "",
        "   ",
        "\n\t",
    ],
)
def test_generation_request_rejects_blank_user_prompt(
    user_prompt: str,
) -> None:
    """사용자 프롬프트가 비어 있거나 공백만 있으면 거부해야 한다."""

    with pytest.raises(
        ValueError,
        match="user_prompt",
    ):
        GenerationRequest(
            user_prompt=user_prompt,
        )


def test_generation_request_rejects_blank_system_prompt_when_provided() -> None:
    """명시적으로 전달된 시스템 프롬프트는 공백만으로 구성될 수 없다."""

    with pytest.raises(
        ValueError,
        match="system_prompt",
    ):
        GenerationRequest(
            user_prompt="정상 질문",
            system_prompt="   ",
        )


def test_generation_usage_returns_total_tokens() -> None:
    """입력과 출력 토큰의 합계를 계산해야 한다."""

    usage = GenerationUsage(
        input_tokens=120,
        output_tokens=30,
    )

    assert usage.total_tokens == 150


@pytest.mark.parametrize(
    (
        "input_tokens",
        "output_tokens",
        "expected_message",
    ),
    [
        (-1, 0, "input_tokens"),
        (0, -1, "output_tokens"),
    ],
)
def test_generation_usage_rejects_negative_token_count(
    input_tokens: int,
    output_tokens: int,
    expected_message: str,
) -> None:
    """입력 또는 출력 토큰 수가 음수이면 거부해야 한다."""

    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        GenerationUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def test_generation_result_preserves_text_and_normalizes_identifiers() -> None:
    """생성 텍스트는 보존하고 모델 ID와 종료 사유만 정규화해야 한다."""

    generated_text = "\n답변입니다.\n"

    usage = GenerationUsage(
        input_tokens=10,
        output_tokens=5,
    )

    result = GenerationResult(
        text=generated_text,
        model="  claude-sonnet-5  ",
        usage=usage,
        stop_reason="  end_turn  ",
    )

    assert result.text == generated_text
    assert result.model == "claude-sonnet-5"
    assert result.usage is usage
    assert result.stop_reason == "end_turn"


@pytest.mark.parametrize(
    (
        "text",
        "model",
        "stop_reason",
        "expected_message",
    ),
    [
        ("   ", "claude-sonnet-5", None, "text"),
        ("정상 답변", "   ", None, "model"),
        ("정상 답변", "claude-sonnet-5", "   ", "stop_reason"),
    ],
)
def test_generation_result_rejects_blank_required_values(
    text: str,
    model: str,
    stop_reason: str | None,
    expected_message: str,
) -> None:
    """응답의 필수 텍스트와 식별값은 공백만으로 구성될 수 없다."""

    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        GenerationResult(
            text=text,
            model=model,
            usage=GenerationUsage(
                input_tokens=0,
                output_tokens=0,
            ),
            stop_reason=stop_reason,
        )
