"""Claude 호출 횟수, 토큰 예산 및 프로세스 동시성 제한을 검증한다."""

import asyncio
import logging

import pytest

from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationBudgetExceededError,
)
from jipsa_rag.infrastructure.generation.limited import (
    GenerationConcurrencyLimiter,
    GenerationLimitPolicy,
    LimitedGenerationClient,
)
from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
)


def _result(
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> GenerationResult:
    """결정적인 생성 결과를 만든다."""

    return GenerationResult(
        text="테스트 답변 [SOURCE-1]",
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        stop_reason="end_turn",
    )


class _RecordingGenerationClient:
    """전달받은 요청을 기록하고 준비된 결과를 순서대로 반환한다."""

    def __init__(
        self,
        results: tuple[GenerationResult, ...],
    ) -> None:
        """호출 결과와 기록 목록을 초기화한다."""

        self._results = results
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """요청을 기록한 뒤 같은 순번의 결과를 반환한다."""

        call_index = len(
            self.calls,
        )
        self.calls.append(
            request,
        )

        if call_index >= len(
            self._results,
        ):
            raise AssertionError("Unexpected generation call.")

        return self._results[call_index]


class _BlockingGenerationClient:
    """동시 실행 수를 기록하고 해제 신호까지 공급자 호출을 대기시킨다."""

    def __init__(self) -> None:
        """동시 실행 카운터와 이벤트를 초기화한다."""

        self.active_count = 0
        self.max_active_count = 0
        self._state_lock = asyncio.Lock()
        self.two_calls_started = asyncio.Event()
        self.release_calls = asyncio.Event()

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """현재 활성 호출 수를 기록하고 테스트 해제 신호를 기다린다."""

        del request

        async with self._state_lock:
            self.active_count += 1
            self.max_active_count = max(
                self.max_active_count,
                self.active_count,
            )

            if self.active_count == 2:
                self.two_calls_started.set()

        try:
            await self.release_calls.wait()

            return _result()

        finally:
            async with self._state_lock:
                self.active_count -= 1


def _policy(
    *,
    max_calls: int = 3,
    max_input_tokens: int = 10_000,
    max_output_tokens: int = 100,
    max_output_tokens_per_call: int = 50,
) -> GenerationLimitPolicy:
    """테스트별로 조정할 수 있는 생성 제한 정책을 반환한다."""

    return GenerationLimitPolicy(
        max_calls=max_calls,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_output_tokens_per_call=max_output_tokens_per_call,
    )


def _request(
    *,
    prompt: str = "안전한 테스트 프롬프트",
) -> GenerationRequest:
    """구조화 출력 없이 사용할 테스트 생성 요청을 만든다."""

    return GenerationRequest(
        system_prompt="테스트 시스템 프롬프트",
        user_prompt=prompt,
    )


@pytest.mark.asyncio
async def test_limited_client_blocks_calls_above_request_budget() -> None:
    """허용 호출 횟수를 소비한 뒤에는 공급자를 추가 호출하지 않아야 한다."""

    delegate = _RecordingGenerationClient((_result(),))
    client = LimitedGenerationClient(
        delegate=delegate,
        policy=_policy(
            max_calls=1,
        ),
        concurrency_limiter=GenerationConcurrencyLimiter(
            max_concurrency=1,
        ),
    )

    await client.generate(
        request=_request(),
    )

    with pytest.raises(
        GenerationBudgetExceededError,
    ) as exc_info:
        await client.generate(
            request=_request(),
        )

    assert exc_info.value.limit_type == "call_count"
    assert len(delegate.calls) == 1


@pytest.mark.asyncio
async def test_limited_client_blocks_large_prompt_before_provider_call() -> None:
    """입력 예산을 넘는 프롬프트는 Claude 호출 전에 차단해야 한다."""

    delegate = _RecordingGenerationClient((_result(),))
    client = LimitedGenerationClient(
        delegate=delegate,
        policy=_policy(
            max_input_tokens=64,
        ),
        concurrency_limiter=GenerationConcurrencyLimiter(
            max_concurrency=1,
        ),
    )

    with pytest.raises(
        GenerationBudgetExceededError,
    ) as exc_info:
        await client.generate(
            request=_request(
                prompt="입력-예산-초과" * 100,
            )
        )

    assert exc_info.value.limit_type == "input_tokens"
    assert delegate.calls == []


@pytest.mark.asyncio
async def test_limited_client_reduces_each_call_to_remaining_output_budget() -> None:
    """남은 누적 출력 예산보다 큰 max_tokens를 전달하지 않아야 한다."""

    delegate = _RecordingGenerationClient(
        (
            _result(
                output_tokens=6,
            ),
            _result(
                output_tokens=4,
            ),
        )
    )
    client = LimitedGenerationClient(
        delegate=delegate,
        policy=_policy(
            max_calls=3,
            max_output_tokens=10,
            max_output_tokens_per_call=8,
        ),
        concurrency_limiter=GenerationConcurrencyLimiter(
            max_concurrency=1,
        ),
    )

    await client.generate(
        request=_request(),
    )
    await client.generate(
        request=_request(),
    )

    assert delegate.calls[0].max_output_tokens == 8
    assert delegate.calls[1].max_output_tokens == 4

    with pytest.raises(
        GenerationBudgetExceededError,
    ) as exc_info:
        await client.generate(
            request=_request(),
        )

    assert exc_info.value.limit_type == "output_tokens"
    assert len(delegate.calls) == 2


@pytest.mark.asyncio
async def test_shared_concurrency_limiter_caps_distinct_request_clients() -> None:
    """서로 다른 RAG 요청 클라이언트도 프로세스 동시성 상한을 공유한다."""

    delegate = _BlockingGenerationClient()
    concurrency_limiter = GenerationConcurrencyLimiter(
        max_concurrency=2,
    )
    clients = tuple(
        LimitedGenerationClient(
            delegate=delegate,
            policy=_policy(
                max_calls=1,
            ),
            concurrency_limiter=concurrency_limiter,
        )
        for _ in range(3)
    )

    tasks = tuple(
        asyncio.create_task(
            client.generate(
                request=_request(
                    prompt=f"동시성 테스트 요청 {index}",
                )
            )
        )
        for index, client in enumerate(
            clients,
        )
    )

    await asyncio.wait_for(
        delegate.two_calls_started.wait(),
        timeout=1.0,
    )

    # 세 번째 요청이 이미 생성됐더라도 공급자 내부에서 동시에 실행되는
    # 호출은 설정값인 두 개를 넘지 않는다.
    assert delegate.max_active_count == 2

    delegate.release_calls.set()

    await asyncio.gather(
        *tasks,
    )

    assert delegate.max_active_count == 2
    assert delegate.active_count == 0


@pytest.mark.asyncio
async def test_budget_error_does_not_expose_prompt_in_logs_or_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """예산 차단 시 질문과 프롬프트를 로그·예외에 남기지 않아야 한다."""

    secret_prompt = "QUESTION-SECRET-DO-NOT-LOG"
    delegate = _RecordingGenerationClient((_result(),))
    client = LimitedGenerationClient(
        delegate=delegate,
        policy=_policy(
            max_input_tokens=1,
        ),
        concurrency_limiter=GenerationConcurrencyLimiter(
            max_concurrency=1,
        ),
    )

    caplog.set_level(
        logging.DEBUG,
    )

    with pytest.raises(
        GenerationBudgetExceededError,
    ) as exc_info:
        await client.generate(
            request=_request(
                prompt=secret_prompt,
            )
        )

    assert secret_prompt not in str(
        exc_info.value,
    )
    assert secret_prompt not in caplog.text
    assert delegate.calls == []
