"""Anthropic Claude Messages API를 이용한 비동기 텍스트 생성 클라이언트."""

import json
from typing import Final, cast

import anthropic
from anthropic.types import (
    Message,
    MessageParam,
    OutputConfigParam,
    TextBlock,
)

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

# 구조화 출력은 일반적으로 JSON Schema를 만족하지만 Claude가 요청을
# 거부하거나 최대 출력 토큰에 도달하면 스키마와 일치하지 않는 텍스트가
# 반환될 수 있다.
#
# 해당 종료 사유는 JSON 파싱 전에 명시적으로 차단한다.
_STRUCTURED_OUTPUT_INVALID_STOP_REASONS: Final[frozenset[str]] = frozenset(
    {
        "refusal",
        "max_tokens",
    }
)


class ClaudeGenerationClient:
    """Claude API 호출 세부 사항을 공급자 독립 생성 계약으로 감싼다.

    상위 서비스에는 Anthropic SDK의 요청·응답 타입을 노출하지 않는다.

    테스트에서는 ``client`` 매개변수로 대체 클라이언트를 주입할 수 있으므로
    실제 Claude API를 호출하지 않고 요청 구성, 응답 변환 및 예외 매핑을
    검증할 수 있다.

    ``GenerationRequest.output_schema``가 존재하면 Messages API의
    ``output_config.format``에 JSON Schema를 전달한다.

    Claude가 반환한 JSON 문자열은 이 계층에서 객체로 파싱하되, RAG 상태와
    출처 같은 도메인 규칙은 상위 서비스에서 다시 검증한다.
    """

    def __init__(
        self,
        settings: GenerationSettings,
        *,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        """생성 설정과 선택적인 테스트용 Anthropic 클라이언트를 받는다.

        Args:
            settings:
                API Key, 모델 ID, 최대 출력 토큰 수 및 요청 제한 시간을
                포함하는 Claude 생성 설정이다.
            client:
                단위 테스트 또는 상위 의존성 주입 컨테이너에서 제공하는
                ``AsyncAnthropic`` 인스턴스다.

                전달하지 않으면 설정으로 새 클라이언트를 만들고 이 객체가
                해당 클라이언트의 생명주기를 소유한다.
        """

        self._settings = settings
        self._closed = False

        if client is None:
            # SecretStr 원문은 SDK 생성 시점에만 꺼내며 객체 repr,
            # 예외 메시지 또는 로그 컨텍스트에 복사하지 않는다.
            self._client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                timeout=settings.anthropic_timeout_seconds,
            )
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False

    async def close(self) -> None:
        """내부에서 생성한 Anthropic 클라이언트의 연결 자원을 정리한다.

        외부에서 주입받은 클라이언트는 호출자가 생명주기를 소유하므로
        닫지 않는다.

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
        """Claude API에 비동기 생성 요청을 보내고 내부 결과로 변환한다.

        Anthropic SDK 예외는 이 메서드 경계에서 애플리케이션 전용
        예외로 변환한다.

        구조화 출력을 요청한 경우에는 공급자 응답의 JSON 파싱까지 이
        계층에서 완료하여 상위 서비스가 Anthropic 응답 형식에 의존하지
        않게 한다.
        """

        if self._closed:
            raise RuntimeError("ClaudeGenerationClient is closed.")

        try:
            response = await self._request_message(
                request=request,
            )
        except anthropic.AnthropicError as error:
            raise _map_anthropic_error(error) from error

        return _convert_message_to_result(
            response,
            expects_structured_output=request.output_schema is not None,
        )

    async def _request_message(
        self,
        *,
        request: GenerationRequest,
    ) -> Message:
        """내부 생성 요청을 Claude Messages API 형식으로 변환한다.

        ``output_schema``가 존재할 때만 ``output_config``를 전송한다.

        일반 텍스트 생성 요청에는 해당 필드를 완전히 생략하여 기존
        Claude 요청 계약을 유지한다.

        ``output_config``의 존재 여부를 먼저 분기한다. 이렇게 하면
        구조화 출력 분기에서 값이 ``None``이 아니라는 사실을 Mypy가
        정확하게 추론할 수 있다.

        Anthropic SDK의 ``messages.create``는 ``output_config``에
        ``OutputConfigParam`` 또는 SDK 내부의 ``Omit`` 타입만 허용한다.
        따라서 ``None`` 가능성이 있는 값을 직접 전달하지 않는다.
        """

        messages: list[MessageParam] = [
            {
                "role": "user",
                "content": request.user_prompt,
            }
        ]

        output_config = _build_output_config(
            request,
        )

        # =========================================================
        # 일반 텍스트 생성
        # =========================================================
        #
        # 구조화 출력 스키마가 없으면 output_config 자체를 요청 인자에서
        # 제외한다.
        #
        # None을 명시적으로 전달하는 것과 인자를 완전히 생략하는 것은
        # Anthropic SDK의 타입 계약에서 서로 다른 의미이므로 이 분기를
        # 별도로 유지한다.
        if output_config is None:
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

        # =========================================================
        # JSON Schema 구조화 출력 생성
        # =========================================================
        #
        # 위의 output_config is None 분기가 return으로 종료됐으므로
        # 이 위치에서 output_config의 타입은 OutputConfigParam으로
        # 확정된다.
        #
        # 따라서 Anthropic SDK가 요구하는 OutputConfigParam | Omit
        # 계약에 맞으며 Mypy도 None 가능성을 보고하지 않는다.
        if request.system_prompt is None:
            return await self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.anthropic_max_output_tokens,
                messages=messages,
                output_config=output_config,
            )

        return await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.anthropic_max_output_tokens,
            messages=messages,
            system=request.system_prompt,
            output_config=output_config,
        )


def _build_output_config(
    request: GenerationRequest,
) -> OutputConfigParam | None:
    """공급자 독립 JSON Schema를 Anthropic 출력 설정으로 변환한다.

    Args:
        request:
            선택적 ``output_schema``를 포함한 내부 생성 요청이다.

    Returns:
        Anthropic ``output_config`` 값 또는 일반 텍스트 요청일 때
        ``None``이다.
    """

    if request.output_schema is None:
        return None

    return {
        "format": {
            "type": "json_schema",
            "schema": dict(request.output_schema),
        }
    }


def _convert_message_to_result(
    response: Message,
    *,
    expects_structured_output: bool,
) -> GenerationResult:
    """Claude Message 응답을 공급자 독립 생성 결과로 변환한다.

    구조화 출력을 요청한 경우에는 텍스트 블록을 결합한 뒤 JSON 객체로
    역직렬화한다.

    파싱 실패, 객체가 아닌 JSON 루트, 거부 및 토큰 제한 종료는 원본
    응답을 노출하지 않는 ``InvalidGenerationResponseError``로
    변환한다.
    """

    # Claude 응답은 text 외에 thinking, tool_use 등 여러 content block을
    # 포함할 수 있다.
    #
    # 현재 생성 계약은 사용자에게 노출 가능한 TextBlock만 원래 순서대로
    # 이어 붙인다.
    #
    # 블록 사이에 임의의 공백이나 줄바꿈을 추가하면 모델이 생성한
    # Markdown, 코드 블록 또는 JSON 형식이 바뀔 수 있으므로 각 text 값을
    # 그대로 결합한다.
    generated_text = "".join(
        block.text
        for block in response.content
        if isinstance(
            block,
            TextBlock,
        )
    )

    structured_output: dict[str, object] | None = None

    if expects_structured_output:
        structured_output = _parse_structured_output(
            generated_text=generated_text,
            stop_reason=response.stop_reason,
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
            structured_output=structured_output,
        )
    except ValueError as error:
        # 빈 텍스트, 잘못된 모델 ID 또는 비정상 토큰 수처럼 내부 결과
        # 계약을 만족하지 못하는 공급자 응답을 일반 ValueError로
        # 노출하지 않는다.
        raise InvalidGenerationResponseError(
            provider=_ANTHROPIC_PROVIDER,
            reason=("response does not satisfy the internal generation result contract"),
        ) from error


def _parse_structured_output(
    *,
    generated_text: str,
    stop_reason: str | None,
) -> dict[str, object]:
    """Claude 구조화 출력 문자열을 JSON 객체로 안전하게 변환한다.

    응답 원문은 예외 메시지나 예외 속성에 포함하지 않는다.

    호출자는 안전하게 정의된 ``reason`` 값만 사용하여 실패 범주를
    진단할 수 있다.

    Args:
        generated_text:
            Claude TextBlock에서 결합한 JSON 문자열이다.
        stop_reason:
            Claude Messages API가 반환한 종료 사유다.

    Returns:
        문자열 Key를 가진 JSON 객체다.

    Raises:
        InvalidGenerationResponseError:
            거부, 출력 토큰 제한, JSON 파싱 실패 또는 객체가 아닌 JSON
            루트가 반환된 경우 발생한다.
    """

    if stop_reason in _STRUCTURED_OUTPUT_INVALID_STOP_REASONS:
        raise InvalidGenerationResponseError(
            provider=_ANTHROPIC_PROVIDER,
            reason=("structured output did not complete with a schema-valid response"),
        )

    try:
        parsed_output: object = json.loads(
            generated_text,
        )
    except json.JSONDecodeError as error:
        raise InvalidGenerationResponseError(
            provider=_ANTHROPIC_PROVIDER,
            reason="structured output is not valid JSON",
        ) from error

    if not isinstance(
        parsed_output,
        dict,
    ):
        raise InvalidGenerationResponseError(
            provider=_ANTHROPIC_PROVIDER,
            reason=("structured output root must be a JSON object"),
        )

    # JSON 객체 Key는 표준상 문자열이지만 json.loads의 반환 타입은
    # 구체적인 제네릭 정보를 제공하지 않는다.
    #
    # 런타임 타입 검증을 마친 뒤 내부 계약에 맞는 타입으로 한 번만
    # 명시적으로 변환한다.
    return cast(
        dict[str, object],
        parsed_output,
    )


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
    # OverloadedError로 구분한다.
    #
    # 두 오류 모두 공급자 측 일시 장애이므로 상위 서비스에는 동일한
    # 서버 오류 범주로 전달한다.
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
            reason=("Anthropic SDK response schema validation failed"),
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

    # 연결 실패 등 나머지 Anthropic SDK 예외도 원본 메시지를 외부로
    # 노출하지 않고 공급자 일반 오류로 정규화한다.
    return GenerationProviderError(
        "Generation provider request failed.",
        provider=_ANTHROPIC_PROVIDER,
    )
