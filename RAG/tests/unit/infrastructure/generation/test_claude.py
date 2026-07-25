"""Anthropic Claude 비동기 생성 클라이언트를 테스트한다."""

import json
from typing import cast

import anthropic
import httpx
import pytest
from anthropic.types import (
    Message,
    StopReason,
    TextBlock,
    Usage,
)

from jipsa_rag.core.generation_config import (
    GenerationSettings,
)
from jipsa_rag.infrastructure.generation.claude import (
    ClaudeGenerationClient,
)
from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationAuthenticationError,
    GenerationProviderError,
    GenerationRateLimitError,
    GenerationServerError,
    GenerationTimeoutError,
    InvalidGenerationResponseError,
)
from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
)

_TEST_API_KEY = "sk-ant-test-0123456789abcdef0123456789abcdef"
_TEST_MODEL = "claude-sonnet-5"
_TEST_REQUEST_ID = "req_test_0123456789"
_TEST_MAX_OUTPUT_TOKENS = 4096

_TEST_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "answered",
                "insufficient_evidence",
            ],
        },
        "answer": {
            "type": "string",
        },
        "cited_source_ids": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": [
        "status",
        "answer",
        "cited_source_ids",
    ],
    "additionalProperties": False,
}


class _FakeMessagesResource:
    """messages.create 결과 또는 예외를 제공하는 테스트 대역."""

    def __init__(
        self,
        *,
        response: Message | None = None,
        error: anthropic.AnthropicError | None = None,
    ) -> None:
        if (response is None) == (error is None):
            raise ValueError("Exactly one of response or error must be provided.")

        self._response = response
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def create(
        self,
        **kwargs: object,
    ) -> Message:
        """호출 인자를 기록하고 응답 또는 예외를 반환한다."""

        self.calls.append(
            dict(kwargs),
        )

        if self._error is not None:
            raise self._error

        if self._response is None:
            raise AssertionError("Fake response is not configured.")

        return self._response


class _FakeAsyncAnthropic:
    """ClaudeGenerationClient에 주입할 최소 Anthropic 대역."""

    def __init__(
        self,
        messages: _FakeMessagesResource,
    ) -> None:
        self.messages = messages

    async def close(self) -> None:
        """주입된 클라이언트는 생성 클라이언트가 닫지 않는다."""


def _create_settings() -> GenerationSettings:
    """실제 Anthropic 자격 증명에 의존하지 않는 설정을 생성한다."""

    return GenerationSettings(
        generation_provider="anthropic",
        anthropic_api_key=_TEST_API_KEY,
        anthropic_model=_TEST_MODEL,
        anthropic_max_output_tokens=(_TEST_MAX_OUTPUT_TOKENS),
        anthropic_timeout_seconds=1.0,
        _env_file=None,
    )


def _create_message(
    *,
    content: list[TextBlock] | None = None,
    stop_reason: (StopReason | None) = "end_turn",
) -> Message:
    """정상 및 비정상 변환 테스트에 사용할 Message를 생성한다."""

    return Message(
        id="msg_test_0123456789",
        content=(
            content
            if content is not None
            else [
                TextBlock(
                    text="첫 번째 문단",
                    type="text",
                ),
                TextBlock(
                    text="\n두 번째 문단",
                    type="text",
                ),
            ]
        ),
        model=_TEST_MODEL,
        role="assistant",
        stop_reason=stop_reason,
        stop_sequence=None,
        type="message",
        usage=Usage(
            input_tokens=120,
            output_tokens=30,
        ),
    )


def _create_client(
    messages: _FakeMessagesResource,
) -> ClaudeGenerationClient:
    """Anthropic SDK 대신 테스트 대역을 주입한다."""

    # 운영 코드는 공식 AsyncAnthropic 타입만 받는다.
    #
    # 테스트 대역은 동일하게 messages.create와 close를 제공하므로
    # 테스트 경계에서만 명시적으로 캐스팅한다.
    return ClaudeGenerationClient(
        _create_settings(),
        client=cast(
            anthropic.AsyncAnthropic,
            _FakeAsyncAnthropic(
                messages,
            ),
        ),
    )


def _create_status_error(
    error_type: type[anthropic.APIStatusError],
    *,
    status_code: int,
) -> anthropic.APIStatusError:
    """민감한 응답 본문을 포함한 Anthropic 오류를 생성한다."""

    request = httpx.Request(
        method="POST",
        url=("https://api.anthropic.test/v1/messages"),
    )
    response = httpx.Response(
        status_code=status_code,
        request=request,
        headers={
            "request-id": _TEST_REQUEST_ID,
        },
    )

    return error_type(
        "sensitive Anthropic SDK error message",
        response=response,
        body={
            "error": {
                "message": ("sensitive provider response body"),
                "type": "test_error",
            }
        },
    )


@pytest.mark.asyncio
async def test_generate_sends_prompts_and_converts_message_to_internal_result() -> None:
    """일반 텍스트 요청과 응답을 내부 모델로 변환해야 한다."""

    messages = _FakeMessagesResource(
        response=_create_message(),
    )
    client = _create_client(
        messages,
    )
    user_prompt = "문서에서 계약 해지 조건을 알려줘."
    system_prompt = "제공된 문서 근거만 사용해 답변한다."

    result = await client.generate(
        request=GenerationRequest(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )
    )

    assert messages.calls == [
        {
            "model": _TEST_MODEL,
            "max_tokens": (_TEST_MAX_OUTPUT_TOKENS),
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
            "system": system_prompt,
        }
    ]
    assert result.text == "첫 번째 문단\n두 번째 문단"
    assert result.model == _TEST_MODEL
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 30
    assert result.usage.total_tokens == 150
    assert result.stop_reason == "end_turn"
    assert result.structured_output is None


@pytest.mark.asyncio
async def test_generate_sends_output_config_and_parses_structured_output() -> None:
    """JSON Schema를 전송하고 구조화 JSON 객체를 파싱해야 한다."""

    structured_payload: dict[str, object] = {
        "status": "answered",
        "answer": ("문서 근거 답변입니다. [SOURCE-1]"),
        "cited_source_ids": [
            "SOURCE-1",
        ],
    }
    generated_json = json.dumps(
        structured_payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    messages = _FakeMessagesResource(
        response=_create_message(
            content=[
                TextBlock(
                    text=generated_json,
                    type="text",
                )
            ],
        ),
    )
    client = _create_client(
        messages,
    )
    user_prompt = "문서 근거로 답변해줘."
    system_prompt = "구조화된 RAG 답변을 반환한다."

    result = await client.generate(
        request=GenerationRequest(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            output_schema=(_TEST_OUTPUT_SCHEMA),
        )
    )

    assert messages.calls == [
        {
            "model": _TEST_MODEL,
            "max_tokens": (_TEST_MAX_OUTPUT_TOKENS),
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
            "system": system_prompt,
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": (_TEST_OUTPUT_SCHEMA),
                }
            },
        }
    ]

    assert result.text == generated_json
    assert result.structured_output == structured_payload
    assert result.model == _TEST_MODEL
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_generate_omits_system_parameter_when_system_prompt_is_absent() -> None:
    """시스템 프롬프트가 없으면 system 필드를 보내지 않아야 한다."""

    messages = _FakeMessagesResource(
        response=_create_message(),
    )
    client = _create_client(
        messages,
    )

    await client.generate(
        request=GenerationRequest(
            user_prompt="요약해줘.",
        )
    )

    assert len(messages.calls) == 1
    assert "system" not in messages.calls[0]
    assert "output_config" not in messages.calls[0]


@pytest.mark.asyncio
async def test_generate_sends_output_config_without_system_prompt() -> None:
    """시스템 프롬프트가 없어도 출력 설정은 전송해야 한다."""

    structured_payload: dict[str, object] = {
        "status": "insufficient_evidence",
        "answer": ("제공된 문서 근거만으로는 답변할 수 없습니다."),
        "cited_source_ids": [],
    }

    messages = _FakeMessagesResource(
        response=_create_message(
            content=[
                TextBlock(
                    text=json.dumps(
                        structured_payload,
                        ensure_ascii=False,
                    ),
                    type="text",
                )
            ],
        ),
    )
    client = _create_client(
        messages,
    )

    await client.generate(
        request=GenerationRequest(
            user_prompt="요약해줘.",
            output_schema=(_TEST_OUTPUT_SCHEMA),
        )
    )

    assert len(messages.calls) == 1
    assert "system" not in messages.calls[0]
    assert messages.calls[0]["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": (_TEST_OUTPUT_SCHEMA),
        }
    }


@pytest.mark.asyncio
async def test_generate_rejects_response_without_visible_text() -> None:
    """TextBlock이 없으면 잘못된 공급자 응답으로 처리해야 한다."""

    messages = _FakeMessagesResource(
        response=_create_message(
            content=[],
        ),
    )
    client = _create_client(
        messages,
    )

    with pytest.raises(
        InvalidGenerationResponseError,
    ) as exception_info:
        await client.generate(
            request=GenerationRequest(
                user_prompt="질문",
            )
        )

    assert exception_info.value.provider == "anthropic"
    assert exception_info.value.reason == (
        "response does not satisfy the internal generation result contract"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "generated_text",
        "stop_reason",
        "expected_reason",
    ),
    [
        pytest.param(
            "not-json",
            "end_turn",
            ("structured output is not valid JSON"),
            id="invalid-json",
        ),
        pytest.param(
            "[]",
            "end_turn",
            ("structured output root must be a JSON object"),
            id="non-object-root",
        ),
        pytest.param(
            "요청을 처리할 수 없습니다.",
            "refusal",
            ("structured output did not complete with a schema-valid response"),
            id="refusal",
        ),
        pytest.param(
            '{"status":"answered"',
            "max_tokens",
            ("structured output did not complete with a schema-valid response"),
            id="max-tokens",
        ),
    ],
)
async def test_generate_rejects_invalid_structured_output_without_exposing_text(
    generated_text: str,
    stop_reason: StopReason,
    expected_reason: str,
) -> None:
    """잘못된 구조화 출력은 원문 비노출 오류로 변환해야 한다."""

    messages = _FakeMessagesResource(
        response=_create_message(
            content=[
                TextBlock(
                    text=generated_text,
                    type="text",
                )
            ],
            stop_reason=stop_reason,
        ),
    )
    client = _create_client(
        messages,
    )

    with pytest.raises(
        InvalidGenerationResponseError,
    ) as exception_info:
        await client.generate(
            request=GenerationRequest(
                user_prompt="질문",
                output_schema=(_TEST_OUTPUT_SCHEMA),
            )
        )

    assert exception_info.value.provider == "anthropic"
    assert exception_info.value.reason == expected_reason
    assert generated_text not in str(exception_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "sdk_error",
        "expected_error_type",
        "expected_status_code",
    ),
    [
        (
            _create_status_error(
                anthropic.AuthenticationError,
                status_code=401,
            ),
            GenerationAuthenticationError,
            401,
        ),
        (
            _create_status_error(
                anthropic.RateLimitError,
                status_code=429,
            ),
            GenerationRateLimitError,
            429,
        ),
        (
            anthropic.APITimeoutError(
                httpx.Request(
                    method="POST",
                    url=("https://api.anthropic.test/v1/messages"),
                )
            ),
            GenerationTimeoutError,
            None,
        ),
        (
            _create_status_error(
                anthropic.InternalServerError,
                status_code=500,
            ),
            GenerationServerError,
            500,
        ),
        (
            _create_status_error(
                anthropic.OverloadedError,
                status_code=529,
            ),
            GenerationServerError,
            529,
        ),
    ],
)
async def test_generate_maps_anthropic_errors_to_application_errors(
    sdk_error: anthropic.AnthropicError,
    expected_error_type: type[GenerationProviderError],
    expected_status_code: int | None,
) -> None:
    """Anthropic 오류를 공급자 독립 예외로 변환해야 한다."""

    messages = _FakeMessagesResource(
        error=sdk_error,
    )
    client = _create_client(
        messages,
    )

    with pytest.raises(
        expected_error_type,
    ) as exception_info:
        await client.generate(
            request=GenerationRequest(
                user_prompt="질문",
            )
        )

    assert exception_info.value.provider == "anthropic"
    assert exception_info.value.status_code == expected_status_code
    assert "sensitive Anthropic SDK error message" not in str(exception_info.value)
    assert "sensitive provider response body" not in str(exception_info.value)

    if expected_status_code is None:
        assert exception_info.value.request_id is None
    else:
        assert exception_info.value.request_id == _TEST_REQUEST_ID
