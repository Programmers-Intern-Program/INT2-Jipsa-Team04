"""공급자 독립 생성 요청 및 응답 모델을 테스트한다."""

import pytest

from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
)


def test_generation_request_preserves_prompt_formatting() -> None:
    """프롬프트 검증 후에도 줄바꿈과 들여쓰기를 유지해야 한다."""

    user_prompt = "\n질문:\n  계약 해지 조건을 알려줘.\n"
    system_prompt = "\n문서 근거만 사용해 답변한다.\n"

    request = GenerationRequest(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )

    assert request.user_prompt == user_prompt
    assert request.system_prompt == system_prompt
    assert request.output_schema is None


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
    """사용자 프롬프트가 비어 있거나 공백만 있으면 거부한다."""

    with pytest.raises(
        ValueError,
        match="user_prompt",
    ):
        GenerationRequest(
            user_prompt=user_prompt,
        )


def test_generation_request_rejects_blank_system_prompt_when_provided() -> None:
    """명시적으로 전달된 시스템 프롬프트는 공백일 수 없다."""

    with pytest.raises(
        ValueError,
        match="system_prompt",
    ):
        GenerationRequest(
            user_prompt="정상 질문",
            system_prompt="   ",
        )


def test_generation_request_copies_structured_output_schema() -> None:
    """원본 스키마를 바꿔도 이미 생성된 요청은 변경되지 않아야 한다."""

    properties: dict[str, object] = {
        "answer": {
            "type": "string",
        }
    }
    output_schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "required": ["answer"],
        "additionalProperties": False,
    }

    request = GenerationRequest(
        user_prompt="정상 질문",
        output_schema=output_schema,
    )

    # 요청 생성 후 호출자가 원본 최상위 dict와 중첩 dict를 수정하는
    # 상황을 재현한다.
    #
    # GenerationRequest는 생성 시 전체 구조를 깊은 복사해야 한다.
    output_schema["type"] = "array"
    properties["answer"] = {
        "type": "integer",
    }

    assert request.output_schema is not None
    assert request.output_schema["type"] == "object"

    copied_properties = request.output_schema["properties"]

    assert isinstance(
        copied_properties,
        dict,
    )
    assert copied_properties["answer"] == {
        "type": "string",
    }


def test_generation_request_rejects_empty_output_schema() -> None:
    """구조화 출력을 요청하면서 빈 JSON Schema를 전달할 수 없다."""

    with pytest.raises(
        ValueError,
        match="output_schema",
    ):
        GenerationRequest(
            user_prompt="정상 질문",
            output_schema={},
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
    """생성 텍스트는 보존하고 식별값만 정규화해야 한다."""

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
    assert result.structured_output is None


def test_generation_result_copies_structured_output() -> None:
    """구조화 출력 원본 변경이 생성 결과에 반영되지 않아야 한다."""

    cited_source_ids = ["SOURCE-1"]
    structured_output: dict[str, object] = {
        "status": "answered",
        "answer": ("문서 근거 답변입니다. [SOURCE-1]"),
        "cited_source_ids": (cited_source_ids),
    }

    result = GenerationResult(
        text=(
            '{"status":"answered","answer":'
            '"문서 근거 답변입니다. [SOURCE-1]",'
            '"cited_source_ids":["SOURCE-1"]}'
        ),
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=10,
            output_tokens=5,
        ),
        stop_reason="end_turn",
        structured_output=structured_output,
    )

    structured_output["status"] = "insufficient_evidence"
    cited_source_ids.append(
        "SOURCE-2",
    )

    assert result.structured_output is not None
    assert result.structured_output["status"] == "answered"
    assert result.structured_output["cited_source_ids"] == ["SOURCE-1"]


@pytest.mark.parametrize(
    (
        "text",
        "model",
        "stop_reason",
        "expected_message",
    ),
    [
        (
            "   ",
            "claude-sonnet-5",
            None,
            "text",
        ),
        (
            "정상 답변",
            "   ",
            None,
            "model",
        ),
        (
            "정상 답변",
            "claude-sonnet-5",
            "   ",
            "stop_reason",
        ),
    ],
)
def test_generation_result_rejects_blank_required_values(
    text: str,
    model: str,
    stop_reason: str | None,
    expected_message: str,
) -> None:
    """응답 필수 텍스트와 식별값은 공백일 수 없다."""

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
