"""Anthropic Claude 비동기 생성 클라이언트의 요청·응답·예외 변환을 테스트한다."""

from typing import cast

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock, Usage

from jipsa_rag.core.generation_config import GenerationSettings
from jipsa_rag.infrastructure.generation.claude import ClaudeGenerationClient
from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationAuthenticationError,
    GenerationProviderError,
    GenerationRateLimitError,
    GenerationServerError,
    GenerationTimeoutError,
    InvalidGenerationResponseError,
)
from jipsa_rag.infrastructure.generation.models import GenerationRequest

_TEST_API_KEY = "sk-ant-test-0123456789abcdef0123456789abcdef"
_TEST_MODEL = "claude-sonnet-5"
_TEST_REQUEST_ID = "req_test_0123456789"
_TEST_MAX_OUTPUT_TOKENS = 4096


class _FakeMessagesResource:
    """실제 네트워크 호출 없이 messages.create 결과 또는 예외를 제공한다."""

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
        """호출 인자를 기록한 뒤 준비된 응답을 반환하거나 예외를 발생시킨다."""

        self.calls.append(dict(kwargs))

        if self._error is not None:
            raise self._error

        if self._response is None:
            raise AssertionError("Fake response is not configured.")

        return self._response


class _FakeAsyncAnthropic:
    """ClaudeGenerationClient에 주입할 최소 Anthropic 클라이언트 대역."""

    def __init__(
        self,
        messages: _FakeMessagesResource,
    ) -> None:
        self.messages = messages

    async def close(self) -> None:
        """주입된 테스트 클라이언트는 생성 클라이언트가 직접 닫지 않는다."""


def _create_settings() -> GenerationSettings:
    """dotenv와 실제 Anthropic 자격 증명에 의존하지 않는 설정을 생성한다."""

    return GenerationSettings(
        generation_provider="anthropic",
        anthropic_api_key=_TEST_API_KEY,
        anthropic_model=_TEST_MODEL,
        anthropic_max_output_tokens=_TEST_MAX_OUTPUT_TOKENS,
        anthropic_timeout_seconds=1.0,
        _env_file=None,
    )


def _create_message(
    *,
    content: list[TextBlock] | None = None,
) -> Message:
    """정상 응답 변환 테스트에 사용할 Claude Message 객체를 생성한다."""

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
        stop_reason="end_turn",
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
    """Anthropic SDK 클라이언트 대신 테스트 대역을 주입한 생성 클라이언트를 만든다."""

    # 운영 코드는 공식 AsyncAnthropic 타입만 받는다. 테스트 대역은 동일하게
    # messages.create와 close를 제공하므로 테스트 경계에서만 명시적으로 캐스팅한다.
    return ClaudeGenerationClient(
        _create_settings(),
        client=cast(
            anthropic.AsyncAnthropic,
            _FakeAsyncAnthropic(messages),
        ),
    )


def _create_status_error(
    error_type: type[anthropic.APIStatusError],
    *,
    status_code: int,
) -> anthropic.APIStatusError:
    """요청 ID와 민감한 응답 본문을 포함한 Anthropic 상태 오류를 생성한다."""

    request = httpx.Request(
        method="POST",
        url="https://api.anthropic.test/v1/messages",
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
                "message": "sensitive provider response body",
                "type": "test_error",
            }
        },
    )


@pytest.mark.asyncio
async def test_generate_sends_prompts_and_converts_message_to_internal_result() -> None:
    """Claude 요청을 비동기로 보내고 텍스트·토큰 사용량을 내부 모델로 변환해야 한다."""

    messages = _FakeMessagesResource(
        response=_create_message(),
    )
    client = _create_client(messages)
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
            "max_tokens": _TEST_MAX_OUTPUT_TOKENS,
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


@pytest.mark.asyncio
async def test_generate_omits_system_parameter_when_system_prompt_is_absent() -> None:
    """시스템 프롬프트가 없으면 Messages API의 system 필드를 전송하지 않아야 한다."""

    messages = _FakeMessagesResource(
        response=_create_message(),
    )
    client = _create_client(messages)

    await client.generate(
        request=GenerationRequest(
            user_prompt="요약해줘.",
        )
    )

    assert len(messages.calls) == 1
    assert "system" not in messages.calls[0]


@pytest.mark.asyncio
async def test_generate_rejects_response_without_visible_text() -> None:
    """사용자에게 반환할 TextBlock이 없으면 잘못된 공급자 응답으로 처리해야 한다."""

    messages = _FakeMessagesResource(
        response=_create_message(
            content=[],
        ),
    )
    client = _create_client(messages)

    with pytest.raises(InvalidGenerationResponseError) as exception_info:
        await client.generate(
            request=GenerationRequest(
                user_prompt="질문",
            )
        )

    assert exception_info.value.provider == "anthropic"
    assert (
        exception_info.value.reason
        == "response does not satisfy the internal generation result contract"
    )


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
                    url="https://api.anthropic.test/v1/messages",
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
    """인증·요청 제한·타임아웃·서버 오류를 공급자 독립 예외로 변환해야 한다."""

    messages = _FakeMessagesResource(
        error=sdk_error,
    )
    client = _create_client(messages)

    with pytest.raises(expected_error_type) as exception_info:
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
