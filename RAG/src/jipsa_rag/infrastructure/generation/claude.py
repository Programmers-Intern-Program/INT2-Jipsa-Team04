"""Anthropic Claude Messages API를 이용한 비동기 텍스트 생성 클라이언트."""

from typing import Final

import anthropic
from anthropic.types import Message, MessageParam, TextBlock

from jipsa_rag.core.generation_config import GenerationSettings
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
    GenerationResult,
    GenerationUsage,
)

_ANTHROPIC_PROVIDER: Final[str] = "anthropic"


class ClaudeGenerationClient:
    """Claude API 호출 세부 사항을 공급자 독립 생성 계약으로 감싼다.

    상위 서비스에는 Anthropic SDK의 요청·응답 타입을 노출하지 않는다.
    테스트에서는 ``client`` 매개변수로 대체 클라이언트를 주입할 수 있으므로
    실제 Claude API를 호출하지 않고 요청 구성, 응답 변환 및 예외 매핑을 검증한다.
    """

    def __init__(
        self,
        settings: GenerationSettings,
        *,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        """생성 설정과 선택적인 테스트용 Anthropic 비동기 클라이언트를 받는다.

        Args:
            settings:
                API Key, 모델 ID, 최대 출력 토큰 수 및 요청 제한 시간을 포함하는
                Claude 생성 설정이다.
            client:
                단위 테스트 또는 상위 의존성 주입 컨테이너에서 제공하는
                ``AsyncAnthropic`` 인스턴스다. 전달하지 않으면 설정으로 새 클라이언트를
                만들고 이 객체가 해당 클라이언트의 생명주기를 소유한다.
        """

        self._settings = settings
        self._closed = False

        if client is None:
            # SecretStr 원문은 SDK 생성 시점에만 꺼내며 객체 repr, 예외 메시지 또는
            # 로그 컨텍스트에 복사하지 않는다.
            self._client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                timeout=settings.anthropic_timeout_seconds,
            )
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False

    async def close(self) -> None:
        """내부에서 생성한 Anthropic 비동기 클라이언트의 연결 자원을 정리한다.

        외부에서 주입받은 클라이언트는 호출자가 생명주기를 소유하므로 닫지 않는다.
        여러 번 호출해도 안전하도록 멱등적으로 처리한다.
        """

        if self._closed:
            return

        self._closed = True

        if self._owns_client:
            await self._client.close()

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """Claude API에 비동기 생성 요청을 보내고 내부 결과 모델로 변환한다.

        Anthropic SDK 예외는 이 메서드 경계에서 애플리케이션 전용 예외로 변환한다.
        따라서 상위 RAG 서비스는 공급자별 예외 클래스나 응답 구조를 알 필요가 없다.
        """

        if self._closed:
            raise RuntimeError("ClaudeGenerationClient is closed.")

        try:
            response = await self._request_message(
                request=request,
            )
        except anthropic.AnthropicError as error:
            raise _map_anthropic_error(error) from error

        return _convert_message_to_result(response)

    async def _request_message(
        self,
        *,
        request: GenerationRequest,
    ) -> Message:
        """내부 생성 요청을 Claude Messages API 요청 형식으로 변환한다."""

        messages: list[MessageParam] = [
            {
                "role": "user",
                "content": request.user_prompt,
            }
        ]

        # Messages API는 system 역할 메시지를 지원하지 않고 최상위 system
        # 매개변수를 사용한다. 시스템 프롬프트가 없을 때는 해당 필드를 요청에서
        # 완전히 생략하여 SDK의 기본 동작을 유지한다.
        if request.system_prompt is None:
            return await self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.anthropic_max_output_tokens,
                messages=messages,
            )

        return await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.anthropic_max_output_tokens,
            messages=messages,
            system=request.system_prompt,
        )


def _convert_message_to_result(
    response: Message,
) -> GenerationResult:
    """Claude Message 응답에서 사용자에게 노출할 텍스트와 사용량을 추출한다."""

    # Claude 응답은 text 외에 thinking, tool_use 등 여러 content block을
    # 포함할 수 있다. 현재 이슈는 일반 텍스트 생성만 다루므로 사용자에게
    # 노출 가능한 TextBlock만 원래 순서대로 이어 붙인다.
    #
    # 블록 사이에 임의의 공백이나 줄바꿈을 추가하면 모델이 생성한 Markdown 또는
    # 코드 블록 형식이 바뀔 수 있으므로 각 text 값을 그대로 결합한다.
    generated_text = "".join(
        block.text
        for block in response.content
        if isinstance(
            block,
            TextBlock,
        )
    )

    try:
        return GenerationResult(
            text=generated_text,
            model=response.model,
            usage=GenerationUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            stop_reason=response.stop_reason,
        )
    except ValueError as error:
        # 빈 텍스트, 잘못된 모델 ID 또는 비정상 토큰 수처럼 내부 결과 계약을
        # 만족하지 못하는 공급자 응답을 일반 ValueError로 노출하지 않는다.
        raise InvalidGenerationResponseError(
            provider=_ANTHROPIC_PROVIDER,
            reason="response does not satisfy the internal generation result contract",
        ) from error


def _map_anthropic_error(
    error: anthropic.AnthropicError,
) -> GenerationProviderError:
    """Anthropic SDK 예외를 안전한 애플리케이션 생성 예외로 변환한다."""

    if isinstance(
        error,
        anthropic.AuthenticationError,
    ):
        return GenerationAuthenticationError(
            "Generation provider authentication failed.",
            provider=_ANTHROPIC_PROVIDER,
            status_code=error.status_code,
            request_id=error.request_id,
        )

    if isinstance(
        error,
        anthropic.RateLimitError,
    ):
        return GenerationRateLimitError(
            "Generation provider rate limit exceeded.",
            provider=_ANTHROPIC_PROVIDER,
            status_code=error.status_code,
            request_id=error.request_id,
        )

    if isinstance(
        error,
        anthropic.APITimeoutError,
    ):
        return GenerationTimeoutError(
            "Generation provider request timed out.",
            provider=_ANTHROPIC_PROVIDER,
        )

    # Anthropic SDK는 일반 5xx를 InternalServerError로, 529 과부하를
    # OverloadedError로 구분한다. 두 오류 모두 공급자 측 일시 장애이므로
    # 상위 서비스에는 동일한 서버 오류 범주로 전달한다.
    if isinstance(
        error,
        (
            anthropic.InternalServerError,
            anthropic.OverloadedError,
        ),
    ):
        return GenerationServerError(
            "Generation provider server error.",
            provider=_ANTHROPIC_PROVIDER,
            status_code=error.status_code,
            request_id=error.request_id,
        )

    if isinstance(
        error,
        anthropic.APIResponseValidationError,
    ):
        return InvalidGenerationResponseError(
            provider=_ANTHROPIC_PROVIDER,
            reason="Anthropic SDK response schema validation failed",
            status_code=error.status_code,
            request_id=error.response.headers.get("request-id"),
        )

    if isinstance(
        error,
        anthropic.APIStatusError,
    ):
        return GenerationProviderError(
            "Generation provider rejected the request.",
            provider=_ANTHROPIC_PROVIDER,
            status_code=error.status_code,
            request_id=error.request_id,
        )

    # 연결 실패 등 나머지 Anthropic SDK 예외도 원본 메시지를 외부로 노출하지
    # 않고 공급자 일반 오류로 정규화한다.
    return GenerationProviderError(
        "Generation provider request failed.",
        provider=_ANTHROPIC_PROVIDER,
    )
