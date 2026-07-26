"""Claude 호출 횟수, 토큰 예산 및 프로세스 동시성을 제한한다."""

import asyncio
import json
import threading
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from jipsa_rag.infrastructure.generation.client import GenerationClient
from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationBudgetExceededError,
    GenerationBudgetLimitType,
)
from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
)

# Messages API의 역할 구분, Content Block 및 구조화 출력 포맷에 필요한
# 소량의 고정 오버헤드를 입력 토큰 사전 추정에 포함한다.
_INPUT_TOKEN_ESTIMATE_OVERHEAD: int = 32


@dataclass(frozen=True, slots=True)
class GenerationLimitPolicy:
    """한 번의 RAG 답변이 사용할 수 있는 Claude 예산 정책."""

    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_output_tokens_per_call: int

    def __post_init__(self) -> None:
        """모든 예산이 양수인지 검증한다."""

        if self.max_calls <= 0:
            raise ValueError(
                "max_calls must be greater than zero."
            )

        if self.max_input_tokens <= 0:
            raise ValueError(
                "max_input_tokens must be greater than zero."
            )

        if self.max_output_tokens <= 0:
            raise ValueError(
                "max_output_tokens must be greater than zero."
            )

        if self.max_output_tokens_per_call <= 0:
            raise ValueError(
                "max_output_tokens_per_call must be greater than zero."
            )


@dataclass(frozen=True, slots=True)
class GenerationBudgetSnapshot:
    """민감한 원문 없이 확인할 수 있는 현재 생성 예산 사용량."""

    attempted_calls: int
    committed_input_tokens: int
    committed_output_tokens: int
    reserved_input_tokens: int
    reserved_output_tokens: int


@dataclass(frozen=True, slots=True)
class _GenerationReservation:
    """공급자 호출 전에 원자적으로 확보한 요청별 예산."""

    estimated_input_tokens: int
    output_token_limit: int


class GenerationConcurrencyLimiter:
    """동일 프로세스와 이벤트 루프에서 Claude 동시 호출 수를 제한한다.

    FastAPI 요청마다 생성 클라이언트 객체는 새로 만들어지지만, 이 제한기는
    프로세스 범위에서 공유된다. 따라서 서로 다른 HTTP 요청이 동시에 Claude를
    호출해도 설정된 최대 동시성보다 많은 공급자 요청이 실행되지 않는다.

    ``asyncio.Semaphore``는 이벤트 루프 경계를 넘겨 재사용하지 않는다.
    테스트 클라이언트처럼 하나의 프로세스가 여러 이벤트 루프를 순차적으로
    만들 수 있으므로 이벤트 루프별 Semaphore를 약한 참조로 관리한다.
    """

    def __init__(
        self,
        *,
        max_concurrency: int,
    ) -> None:
        """프로세스 전역 동시 호출 상한을 초기화한다."""

        if max_concurrency <= 0:
            raise ValueError(
                "max_concurrency must be greater than zero."
            )

        self._max_concurrency = max_concurrency
        self._registry_lock = threading.Lock()
        self._semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            asyncio.Semaphore,
        ] = weakref.WeakKeyDictionary()

    @property
    def max_concurrency(self) -> int:
        """설정된 최대 동시 호출 수를 반환한다."""

        return self._max_concurrency

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """현재 이벤트 루프의 Claude 호출 슬롯 하나를 점유한다."""

        semaphore = self._get_semaphore_for_running_loop()

        async with semaphore:
            yield

    def _get_semaphore_for_running_loop(
        self,
    ) -> asyncio.Semaphore:
        """현재 이벤트 루프에서 공유할 Semaphore를 반환한다."""

        loop = asyncio.get_running_loop()

        with self._registry_lock:
            semaphore = self._semaphores.get(
                loop,
            )

            if semaphore is None:
                semaphore = asyncio.Semaphore(
                    self._max_concurrency,
                )
                self._semaphores[loop] = semaphore

        return semaphore


_SHARED_CONCURRENCY_LIMITERS: dict[
    int,
    GenerationConcurrencyLimiter,
] = {}
_SHARED_CONCURRENCY_LIMITERS_LOCK = threading.Lock()


def get_shared_generation_concurrency_limiter(
    max_concurrency: int,
) -> GenerationConcurrencyLimiter:
    """같은 동시성 값에 대해 프로세스 범위 제한기 하나를 재사용한다."""

    if max_concurrency <= 0:
        raise ValueError(
            "max_concurrency must be greater than zero."
        )

    with _SHARED_CONCURRENCY_LIMITERS_LOCK:
        limiter = _SHARED_CONCURRENCY_LIMITERS.get(
            max_concurrency,
        )

        if limiter is None:
            limiter = GenerationConcurrencyLimiter(
                max_concurrency=max_concurrency,
            )
            _SHARED_CONCURRENCY_LIMITERS[max_concurrency] = limiter

    return limiter


class LimitedGenerationClient:
    """공급자 독립 생성 클라이언트에 요청 단위 예산을 적용한다.

    하나의 ``LimitedGenerationClient`` 인스턴스는 하나의 RAG 답변 요청에만
    사용한다.

    lookup은 최대 한 번, synthesis는 PDF별 부분 호출과 마지막 종합 호출을
    모두 같은 인스턴스를 통과하므로 호출 횟수와 누적 토큰을 요청 전체에서
    일관되게 제한할 수 있다.

    질문, 청크, 프롬프트, 구조화 출력 및 Claude 응답 원문은 카운터나 예외에
    저장하지 않는다.

    입력 토큰 사전 검사는 프롬프트의 UTF-8 바이트 수를 보수적인 상한
    추정치로 사용하고, 공급자 응답 이후에는 실제 usage 값으로 누적
    사용량을 확정한다.
    """

    def __init__(
        self,
        *,
        delegate: GenerationClient,
        policy: GenerationLimitPolicy,
        concurrency_limiter: GenerationConcurrencyLimiter,
    ) -> None:
        """실제 생성 클라이언트와 요청·프로세스 제한 정책을 주입받는다."""

        self._delegate = delegate
        self._policy = policy
        self._concurrency_limiter = concurrency_limiter
        self._budget_lock = asyncio.Lock()
        self._attempted_calls = 0
        self._committed_input_tokens = 0
        self._committed_output_tokens = 0
        self._reserved_input_tokens = 0
        self._reserved_output_tokens = 0

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """예산과 동시성 슬롯을 확보한 뒤 공급자 호출을 실행한다."""

        estimated_input_tokens = _estimate_input_tokens(
            request,
        )
        reservation = await self._reserve_budget(
            request=request,
            estimated_input_tokens=estimated_input_tokens,
        )

        # 남은 요청 전체 출력 예산이 단일 호출 설정보다 작으면 현재 호출의
        # max_tokens를 동적으로 낮춘다.
        #
        # Claude 공급자에 실제로 전달되는 값이므로 사후 검사만 하는 것보다
        # 초과 비용을 선제적으로 차단한다.
        limited_request = replace(
            request,
            max_output_tokens=reservation.output_token_limit,
        )

        try:
            async with self._concurrency_limiter.slot():
                result = await self._delegate.generate(
                    request=limited_request,
                )

        except BaseException:
            # 취소, 공급자 오류 및 예기치 않은 오류 모두 예약 토큰을 반환한다.
            #
            # 호출 횟수는 실제 공급자 시도로 소비된 상태를 유지한다.
            await self._release_reservation(
                reservation,
            )

            raise

        exceeded_limit = await self._commit_usage(
            reservation=reservation,
            result=result,
        )

        if exceeded_limit is not None:
            # 공급자가 반환한 실제 usage가 보수적 사전 추정을 넘어선
            # 경우에도 초과 결과를 외부 정상 답변으로 전달하지 않는다.
            raise GenerationBudgetExceededError(
                limit_type=exceeded_limit,
            )

        return result

    async def snapshot(
        self,
    ) -> GenerationBudgetSnapshot:
        """테스트와 안전한 진단에 사용할 숫자 카운터만 반환한다."""

        async with self._budget_lock:
            return GenerationBudgetSnapshot(
                attempted_calls=self._attempted_calls,
                committed_input_tokens=(
                    self._committed_input_tokens
                ),
                committed_output_tokens=(
                    self._committed_output_tokens
                ),
                reserved_input_tokens=(
                    self._reserved_input_tokens
                ),
                reserved_output_tokens=(
                    self._reserved_output_tokens
                ),
            )

    async def _reserve_budget(
        self,
        *,
        request: GenerationRequest,
        estimated_input_tokens: int,
    ) -> _GenerationReservation:
        """호출, 입력 토큰 및 출력 토큰 예산을 원자적으로 예약한다."""

        async with self._budget_lock:
            if self._attempted_calls >= self._policy.max_calls:
                raise GenerationBudgetExceededError(
                    limit_type="call_count",
                )

            remaining_input_tokens = (
                self._policy.max_input_tokens
                - (
                    self._committed_input_tokens
                    + self._reserved_input_tokens
                )
            )

            if estimated_input_tokens > remaining_input_tokens:
                raise GenerationBudgetExceededError(
                    limit_type="input_tokens",
                )

            remaining_output_tokens = (
                self._policy.max_output_tokens
                - (
                    self._committed_output_tokens
                    + self._reserved_output_tokens
                )
            )

            if remaining_output_tokens <= 0:
                raise GenerationBudgetExceededError(
                    limit_type="output_tokens",
                )

            requested_output_tokens = (
                request.max_output_tokens
                if request.max_output_tokens is not None
                else self._policy.max_output_tokens_per_call
            )
            output_token_limit = min(
                requested_output_tokens,
                self._policy.max_output_tokens_per_call,
                remaining_output_tokens,
            )

            if output_token_limit <= 0:
                raise GenerationBudgetExceededError(
                    limit_type="output_tokens",
                )

            self._attempted_calls += 1
            self._reserved_input_tokens += estimated_input_tokens
            self._reserved_output_tokens += output_token_limit

            return _GenerationReservation(
                estimated_input_tokens=estimated_input_tokens,
                output_token_limit=output_token_limit,
            )

    async def _release_reservation(
        self,
        reservation: _GenerationReservation,
    ) -> None:
        """실패한 공급자 호출이 확보했던 입력·출력 예약량을 반환한다."""

        async with self._budget_lock:
            self._reserved_input_tokens -= (
                reservation.estimated_input_tokens
            )
            self._reserved_output_tokens -= (
                reservation.output_token_limit
            )

    async def _commit_usage(
        self,
        *,
        reservation: _GenerationReservation,
        result: GenerationResult,
    ) -> GenerationBudgetLimitType | None:
        """예약량을 실제 Claude usage로 교체하고 초과 여부를 반환한다."""

        async with self._budget_lock:
            self._reserved_input_tokens -= (
                reservation.estimated_input_tokens
            )
            self._reserved_output_tokens -= (
                reservation.output_token_limit
            )
            self._committed_input_tokens += (
                result.usage.input_tokens
            )
            self._committed_output_tokens += (
                result.usage.output_tokens
            )

            if (
                self._committed_input_tokens
                > self._policy.max_input_tokens
            ):
                return "input_tokens"

            if (
                result.usage.output_tokens
                > reservation.output_token_limit
                or self._committed_output_tokens
                > self._policy.max_output_tokens
            ):
                return "output_tokens"

        return None


def _estimate_input_tokens(
    request: GenerationRequest,
) -> int:
    """프롬프트를 기록하지 않고 입력 토큰의 보수적인 상한을 계산한다.

    외부 tokenizer를 추가하거나 질문 원문을 별도 저장하지 않기 위해 UTF-8
    바이트 수를 토큰 상한 추정치로 사용한다.

    일반적인 Claude 토큰은 여러 바이트를 포함하므로 실제 토큰 수보다
    보수적으로 계산되는 방향이다.

    공급자 응답 후에는 ``GenerationUsage.input_tokens`` 실제 값으로 다시
    검증한다.
    """

    system_prompt = request.system_prompt or ""
    output_schema = ""

    if request.output_schema is not None:
        output_schema = json.dumps(
            dict(
                request.output_schema,
            ),
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
            sort_keys=True,
        )

    estimated_tokens = sum(
        len(
            value.encode(
                "utf-8",
            )
        )
        for value in (
            system_prompt,
            request.user_prompt,
            output_schema,
        )
    )

    return max(
        1,
        estimated_tokens + _INPUT_TOKEN_ESTIMATE_OVERHEAD,
    )