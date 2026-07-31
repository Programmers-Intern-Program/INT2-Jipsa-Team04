"""외부 RAG 단계형 부하·장시간·파괴적 극한 테스트 계획과 결과 모델을 정의한다.

모든 임계값은 외부 서비스 운영 설정을 바꾸는 값이 아니라 부하 생성기 자체의 중단·보고
기준이다. 내장 Profile은 외부 검색 API만 호출하며 Local Process, Docker, DB, VectorDB 또는
GPU를 직접 제어하지 않는다. ``destructive``는 높은 동시성과 Spike를 허용한다는 의미다.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, cast

StressOperation = Literal["search", "ingest"]
StageMode = Literal["burst", "interval", "batch", "ramp", "soak", "chaos", "fault_suite"]
FaultProbeName = Literal[
    "timeout",
    "controlled_oom",
    "embedding",
    "qdrant",
    "abnormal_termination",
]
StageStatus = Literal["passed", "degraded", "failed", "stopped", "skipped"]

_MAX_CONCURRENCY: Final[int] = 512
_MAX_STAGE_REQUESTS: Final[int] = 1_000_000
_ALLOWED_PROBES: Final[frozenset[str]] = frozenset(
    {
        "timeout",
        "controlled_oom",
        "embedding",
        "qdrant",
        "abnormal_termination",
    }
)
_BUILT_IN_PROFILES: Final[frozenset[str]] = frozenset(
    {"quick", "standard", "endurance", "destructive"}
)
_PRIMARY_TRAFFIC_MODES: Final[tuple[StageMode, ...]] = (
    "burst",
    "interval",
    "batch",
    "ramp",
    "chaos",
)


@dataclass(frozen=True, slots=True)
class StopPolicy:
    """각 단계와 전체 Suite를 중단할 관측 기준.

    값은 서비스의 운영 제한값을 바꾸지 않는다. 부하 생성기가 Host를 실제 OOM 상태로
    밀어 넣거나 이미 명백히 실패한 Target에 무한 요청을 보내는 일을 방지한다.
    """

    max_error_rate: float
    max_p95_ms: float
    max_system_memory_percent: float
    max_gpu_memory_percent: float
    stop_on_target_exit: bool
    consecutive_failed_stages: int


@dataclass(frozen=True, slots=True)
class BurstStage:
    """동일 시점에 고정 수의 요청을 제출하는 순간 폭주 단계."""

    stage_id: str
    name: str
    mode: Literal["burst"]
    operation: StressOperation
    enabled: bool
    destructive: bool
    total_requests: int
    concurrency: int
    cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class IntervalStage:
    """일정 간격으로 요청을 제출해 지속 유입과 Queue 누적을 확인하는 단계."""

    stage_id: str
    name: str
    mode: Literal["interval"]
    operation: StressOperation
    enabled: bool
    destructive: bool
    total_requests: int
    concurrency: int
    interval_seconds: float
    cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class BatchStage:
    """일정 간격마다 요청 묶음을 제출하는 파도형 단계."""

    stage_id: str
    name: str
    mode: Literal["batch"]
    operation: StressOperation
    enabled: bool
    destructive: bool
    total_requests: int
    batch_size: int
    max_workers: int
    interval_seconds: float
    cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class RampStage:
    """동시성을 단계적으로 증가시키며 정상 최대와 최초 실패를 찾는 단계."""

    stage_id: str
    name: str
    mode: Literal["ramp"]
    operation: StressOperation
    enabled: bool
    destructive: bool
    start_concurrency: int
    step_concurrency: int
    max_concurrency: int
    requests_per_worker: int
    wave_interval_seconds: float
    cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class SoakStage:
    """고정 동시성을 여러 Window에 걸쳐 반복하는 장시간 안정성 단계."""

    stage_id: str
    name: str
    mode: Literal["soak"]
    operation: Literal["search"]
    enabled: bool
    destructive: bool
    duration_seconds: float
    window_seconds: float
    concurrency: int
    max_requests: int
    cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class ChaosStage:
    """평상시 TPS와 주기적 Spike를 혼합해 피크 복구 능력을 측정하는 단계."""

    stage_id: str
    name: str
    mode: Literal["chaos"]
    operation: Literal["search"]
    enabled: bool
    destructive: bool
    duration_seconds: float
    baseline_tps: int
    spike_size: int
    spike_interval_seconds: float
    max_workers: int
    max_pending_multiplier: int
    cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class FaultSuiteStage:
    """Timeout·OOM·외부 서비스·Target 종료 Probe를 순서대로 실행하는 단계."""

    stage_id: str
    name: str
    mode: Literal["fault_suite"]
    operation: Literal["search"]
    enabled: bool
    destructive: bool
    probes: tuple[FaultProbeName, ...]
    cooldown_seconds: float


type StressStage = (
    BurstStage | IntervalStage | BatchStage | RampStage | SoakStage | ChaosStage | FaultSuiteStage
)


@dataclass(frozen=True, slots=True)
class StressSuitePlan:
    """단기·표준·장시간·파괴적 프로필을 공통 형식으로 표현한다."""

    schema_version: int
    suite_name: str
    profile: str
    description: str
    destructive: bool
    requires_explicit_confirmation: bool
    sla_seconds: float
    max_total_requests: int
    stop_policy: StopPolicy
    stages: tuple[StressStage, ...]

    def to_dict(self) -> dict[str, object]:
        """JSON 직렬화 가능한 전체 계획을 반환한다."""

        return cast(dict[str, object], asdict(self))

    @property
    def enabled_stages(self) -> tuple[StressStage, ...]:
        """실제로 실행할 단계만 원래 순서로 반환한다."""

        return tuple(stage for stage in self.stages if stage.enabled)

    @property
    def maximum_declared_concurrency(self) -> int:
        """계획에서 선언한 최대 Worker·동시성 값을 반환한다."""

        values: list[int] = []
        for stage in self.enabled_stages:
            if isinstance(stage, BurstStage | IntervalStage):
                values.append(stage.concurrency)
            elif isinstance(stage, BatchStage | ChaosStage):
                values.append(stage.max_workers)
            elif isinstance(stage, RampStage):
                values.append(stage.max_concurrency)
            elif isinstance(stage, SoakStage):
                values.append(stage.concurrency)
        return max(values, default=0)

    @property
    def estimated_minimum_duration_seconds(self) -> float:
        """요청 처리 대기시간을 제외한 최소 스케줄 시간을 근사한다."""

        total = 0.0
        for stage in self.enabled_stages:
            if isinstance(stage, IntervalStage):
                total += max(stage.total_requests - 1, 0) * stage.interval_seconds
            elif isinstance(stage, BatchStage):
                batches = math.ceil(stage.total_requests / stage.batch_size)
                total += max(batches - 1, 0) * stage.interval_seconds
            elif isinstance(stage, RampStage):
                waves = (
                    (stage.max_concurrency - stage.start_concurrency) // stage.step_concurrency
                ) + 1
                total += max(waves - 1, 0) * stage.wave_interval_seconds
            elif isinstance(stage, SoakStage | ChaosStage):
                total += stage.duration_seconds
            total += stage.cooldown_seconds
        return total


@dataclass(frozen=True, slots=True)
class StageSummary:
    """한 단계 또는 Ramp·Soak 하위 Wave의 요청·지연·자원 결과."""

    sequence: int
    stage_id: str
    parent_stage_id: str
    stage_name: str
    mode: StageMode
    operation: StressOperation
    destructive: bool
    started_at_utc: str
    completed_at_utc: str
    elapsed_seconds: float
    declared_concurrency: int
    scheduled_request_count: int
    submitted_request_count: int
    request_count: int
    success_count: int
    sla_success_count: int
    slow_success_count: int
    error_count: int
    error_rate: float
    sla_success_rate: float
    throughput_requests_per_second: float
    latency_mean_ms: float | None
    latency_min_ms: float | None
    latency_max_ms: float | None
    latency_p50_ms: float | None
    latency_p90_ms: float | None
    latency_p95_ms: float | None
    latency_p99_ms: float | None
    system_memory_percent_max: float | None
    gpu_memory_percent_max: float | None
    target_rss_bytes_max: float | None
    target_vram_bytes_max: float | None
    target_thread_count_max: float | None
    target_handle_count_max: float | None
    scheduler_backpressure_count: int
    status: StageStatus
    stop_triggered: bool
    stop_reason: str | None
    first_error_type: str | None
    resource_sample_count: int

    def to_dict(self) -> dict[str, object]:
        """CSV·JSON 공통 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class CapacityBoundary:
    """동시성 기준 정상 최대 관측값과 최초 실패 관측값."""

    operation: StressOperation
    normal_maximum_concurrency: int | None
    first_failure_concurrency: int | None
    first_failure_stage_id: str | None
    first_failure_reason: str | None
    upper_bound_censored: bool
    evidence_count: int

    def to_dict(self) -> dict[str, object]:
        """CSV·JSON 공통 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


def load_stress_suite_plan(path: Path) -> StressSuitePlan:
    """UTF-8 JSON 계획을 읽고 안전 범위·단계별 필수 필드를 검증한다."""

    raw = _object(json.loads(path.read_text(encoding="utf-8")), "stress suite plan")
    schema_version = _positive_int(raw, "schema_version")
    if schema_version != 1:
        raise ValueError(f"Unsupported stress suite schema_version: {schema_version}")

    stop_raw = _object(raw.get("stop_policy"), "stop_policy")
    stop_policy = StopPolicy(
        max_error_rate=_bounded_float(
            stop_raw,
            "max_error_rate",
            minimum=0.0,
            maximum=1.0,
        ),
        max_p95_ms=_positive_float(stop_raw, "max_p95_ms"),
        max_system_memory_percent=_bounded_float(
            stop_raw,
            "max_system_memory_percent",
            minimum=1.0,
            maximum=99.0,
        ),
        max_gpu_memory_percent=_bounded_float(
            stop_raw,
            "max_gpu_memory_percent",
            minimum=1.0,
            maximum=99.5,
        ),
        stop_on_target_exit=_bool(stop_raw, "stop_on_target_exit"),
        consecutive_failed_stages=_bounded_int(
            stop_raw,
            "consecutive_failed_stages",
            minimum=1,
            maximum=100,
        ),
    )

    stages_raw = _objects(raw, "stages")
    stages = tuple(_parse_stage(value) for value in stages_raw)
    if not stages:
        raise ValueError("stages must contain at least one stage.")

    stage_ids = tuple(stage.stage_id for stage in stages)
    if len(stage_ids) != len(set(stage_ids)):
        raise ValueError("stage_id values must be unique.")

    destructive = _bool(raw, "destructive")
    requires_confirmation = _bool(raw, "requires_explicit_confirmation")
    destructive_stages = tuple(stage for stage in stages if stage.enabled and stage.destructive)
    if destructive_stages and not destructive:
        raise ValueError("A suite containing destructive stages must set destructive=true.")
    if destructive and not requires_confirmation:
        raise ValueError("A destructive suite must require explicit confirmation.")

    enabled_fault_indexes = [
        index
        for index, stage in enumerate(stages)
        if stage.enabled and isinstance(stage, FaultSuiteStage)
    ]
    if enabled_fault_indexes:
        last_enabled_index = max(index for index, stage in enumerate(stages) if stage.enabled)
        fault_index = enabled_fault_indexes[-1]
        fault_stage = cast(FaultSuiteStage, stages[fault_index])
        if "abnormal_termination" in fault_stage.probes and fault_index != last_enabled_index:
            raise ValueError(
                "A fault_suite containing abnormal_termination must be the last enabled stage."
            )

    profile = _non_empty_str(raw, "profile")
    enabled_modes = tuple(stage.mode for stage in stages if stage.enabled)
    if profile in _BUILT_IN_PROFILES and enabled_modes[:5] != _PRIMARY_TRAFFIC_MODES:
        raise ValueError(
            "Built-in profiles must start with the five traffic modes in order: "
            "burst, interval, batch, ramp, chaos."
        )
    if profile in _BUILT_IN_PROFILES:
        unsupported = tuple(
            stage.stage_id
            for stage in stages
            if stage.enabled and (stage.operation != "search" or isinstance(stage, FaultSuiteStage))
        )
        if unsupported:
            raise ValueError(
                "Built-in external profiles must contain search-only traffic stages. "
                f"Unsupported stages: {unsupported}"
            )

    plan = StressSuitePlan(
        schema_version=schema_version,
        suite_name=_non_empty_str(raw, "suite_name"),
        profile=profile,
        description=_non_empty_str(raw, "description"),
        destructive=destructive,
        requires_explicit_confirmation=requires_confirmation,
        sla_seconds=_positive_float(raw, "sla_seconds"),
        max_total_requests=_bounded_int(
            raw,
            "max_total_requests",
            minimum=1,
            maximum=_MAX_STAGE_REQUESTS,
        ),
        stop_policy=stop_policy,
        stages=stages,
    )

    declared_requests = _declared_request_upper_bound(plan.enabled_stages)
    if declared_requests > plan.max_total_requests:
        raise ValueError(
            "Declared request upper bound exceeds max_total_requests: "
            f"{declared_requests} > {plan.max_total_requests}"
        )
    if plan.maximum_declared_concurrency > _MAX_CONCURRENCY:
        raise ValueError(f"Declared concurrency exceeds the hard safety cap {_MAX_CONCURRENCY}.")
    return plan


def _parse_stage(raw: dict[str, object]) -> StressStage:
    """한 Stage JSON을 mode별 불변 Model로 변환한다.

    공통 필드를 ``dict[str, object]``로 만든 뒤 ``**`` 확장하면 strict Mypy가 각
    Dataclass 생성자 인자의 구체 타입을 복원할 수 없다. 공통 값을 먼저 정확한 타입으로
    읽고 각 생성자에 명시적으로 전달하여 계획 파일 오류와 타입 오류를 같은 경계에서
    차단한다.
    """

    stage_id = _non_empty_str(raw, "stage_id")
    name = _non_empty_str(raw, "name")
    enabled = _bool(raw, "enabled")
    destructive = _bool(raw, "destructive")
    cooldown_seconds = _non_negative_float(raw, "cooldown_seconds")
    mode = _non_empty_str(raw, "mode")
    operation = _operation(raw)

    if mode == "burst":
        return BurstStage(
            stage_id=stage_id,
            name=name,
            enabled=enabled,
            destructive=destructive,
            cooldown_seconds=cooldown_seconds,
            mode="burst",
            operation=operation,
            total_requests=_bounded_int(
                raw,
                "total_requests",
                minimum=1,
                maximum=_MAX_STAGE_REQUESTS,
            ),
            concurrency=_bounded_int(
                raw,
                "concurrency",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
        )
    if mode == "interval":
        return IntervalStage(
            stage_id=stage_id,
            name=name,
            enabled=enabled,
            destructive=destructive,
            cooldown_seconds=cooldown_seconds,
            mode="interval",
            operation=operation,
            total_requests=_bounded_int(
                raw,
                "total_requests",
                minimum=1,
                maximum=_MAX_STAGE_REQUESTS,
            ),
            concurrency=_bounded_int(
                raw,
                "concurrency",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            interval_seconds=_positive_float(raw, "interval_seconds"),
        )
    if mode == "batch":
        return BatchStage(
            stage_id=stage_id,
            name=name,
            enabled=enabled,
            destructive=destructive,
            cooldown_seconds=cooldown_seconds,
            mode="batch",
            operation=operation,
            total_requests=_bounded_int(
                raw,
                "total_requests",
                minimum=1,
                maximum=_MAX_STAGE_REQUESTS,
            ),
            batch_size=_bounded_int(
                raw,
                "batch_size",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            max_workers=_bounded_int(
                raw,
                "max_workers",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            interval_seconds=_positive_float(raw, "interval_seconds"),
        )
    if mode == "ramp":
        ramp_stage = RampStage(
            stage_id=stage_id,
            name=name,
            enabled=enabled,
            destructive=destructive,
            cooldown_seconds=cooldown_seconds,
            mode="ramp",
            operation=operation,
            start_concurrency=_bounded_int(
                raw,
                "start_concurrency",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            step_concurrency=_bounded_int(
                raw,
                "step_concurrency",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            max_concurrency=_bounded_int(
                raw,
                "max_concurrency",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            requests_per_worker=_bounded_int(
                raw,
                "requests_per_worker",
                minimum=1,
                maximum=10_000,
            ),
            wave_interval_seconds=_non_negative_float(raw, "wave_interval_seconds"),
        )
        if ramp_stage.start_concurrency > ramp_stage.max_concurrency:
            raise ValueError(f"{ramp_stage.stage_id}: start_concurrency exceeds max_concurrency.")
        return ramp_stage
    if mode == "soak":
        _require_search_operation(raw, mode)
        soak_stage = SoakStage(
            stage_id=stage_id,
            name=name,
            enabled=enabled,
            destructive=destructive,
            cooldown_seconds=cooldown_seconds,
            mode="soak",
            operation="search",
            duration_seconds=_positive_float(raw, "duration_seconds"),
            window_seconds=_positive_float(raw, "window_seconds"),
            concurrency=_bounded_int(
                raw,
                "concurrency",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            max_requests=_bounded_int(
                raw,
                "max_requests",
                minimum=0,
                maximum=_MAX_STAGE_REQUESTS,
            ),
        )
        if soak_stage.window_seconds > soak_stage.duration_seconds:
            raise ValueError(f"{soak_stage.stage_id}: window_seconds exceeds duration_seconds.")
        return soak_stage
    if mode == "chaos":
        _require_search_operation(raw, mode)
        return ChaosStage(
            stage_id=stage_id,
            name=name,
            enabled=enabled,
            destructive=destructive,
            cooldown_seconds=cooldown_seconds,
            mode="chaos",
            operation="search",
            duration_seconds=_positive_float(raw, "duration_seconds"),
            baseline_tps=_bounded_int(
                raw,
                "baseline_tps",
                minimum=1,
                maximum=10_000,
            ),
            spike_size=_bounded_int(
                raw,
                "spike_size",
                minimum=1,
                maximum=_MAX_STAGE_REQUESTS,
            ),
            spike_interval_seconds=_positive_float(raw, "spike_interval_seconds"),
            max_workers=_bounded_int(
                raw,
                "max_workers",
                minimum=1,
                maximum=_MAX_CONCURRENCY,
            ),
            max_pending_multiplier=_bounded_int(
                raw,
                "max_pending_multiplier",
                minimum=1,
                maximum=100,
            ),
        )
    if mode == "fault_suite":
        _require_search_operation(raw, mode)
        raw_probes = _strings(raw, "probes")
        if not raw_probes:
            raise ValueError("fault_suite.probes must not be empty.")
        if len(raw_probes) != len(set(raw_probes)):
            raise ValueError("fault_suite.probes must not contain duplicates.")
        invalid = set(raw_probes) - _ALLOWED_PROBES
        if invalid:
            raise ValueError(f"Unsupported fault probes: {sorted(invalid)}")
        return FaultSuiteStage(
            stage_id=stage_id,
            name=name,
            enabled=enabled,
            destructive=destructive,
            cooldown_seconds=cooldown_seconds,
            mode="fault_suite",
            operation="search",
            probes=cast(tuple[FaultProbeName, ...], raw_probes),
        )
    raise ValueError(f"Unsupported stage mode: {mode}")


def _declared_request_upper_bound(stages: tuple[StressStage, ...]) -> int:
    total = 0
    for stage in stages:
        if isinstance(stage, BurstStage | IntervalStage | BatchStage):
            total += stage.total_requests
        elif isinstance(stage, RampStage):
            total += sum(
                concurrency * stage.requests_per_worker
                for concurrency in range(
                    stage.start_concurrency,
                    stage.max_concurrency + 1,
                    stage.step_concurrency,
                )
            )
        elif isinstance(stage, SoakStage):
            total += stage.max_requests
        elif isinstance(stage, ChaosStage):
            spike_count = math.floor(stage.duration_seconds / stage.spike_interval_seconds)
            total += math.ceil(stage.duration_seconds) * stage.baseline_tps
            total += spike_count * stage.spike_size
    return total


def _require_search_operation(raw: dict[str, object], mode: str) -> None:
    operation = _operation(raw)
    if operation != "search":
        raise ValueError(f"{mode} stages currently support operation=search only.")


def _operation(raw: dict[str, object]) -> StressOperation:
    value = _non_empty_str(raw, "operation")
    if value not in {"search", "ingest"}:
        raise ValueError(f"Unsupported stress operation: {value}")
    return cast(StressOperation, value)


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object with string keys.")
    return cast(dict[str, object], value)


def _objects(mapping: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array.")
    return tuple(_object(item, f"{key} item") for item in cast(list[object], value))


def _strings(mapping: dict[str, object], key: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array.")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must contain only non-empty strings.")
        result.append(item.strip())
    return tuple(result)


def _non_empty_str(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value.strip()


def _bool(mapping: dict[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean.")
    return value


def _positive_int(mapping: dict[str, object], key: str) -> int:
    return _bounded_int(mapping, key, minimum=1, maximum=2**63 - 1)


def _bounded_int(
    mapping: dict[str, object],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}.")
    return value


def _positive_float(mapping: dict[str, object], key: str) -> float:
    value = _finite_float(mapping.get(key), key)
    if value <= 0:
        raise ValueError(f"{key} must be greater than zero.")
    return value


def _non_negative_float(mapping: dict[str, object], key: str) -> float:
    value = _finite_float(mapping.get(key), key)
    if value < 0:
        raise ValueError(f"{key} must be zero or greater.")
    return value


def _bounded_float(
    mapping: dict[str, object],
    key: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = _finite_float(mapping.get(key), key)
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}.")
    return value


def _finite_float(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number.")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{key} must be finite.")
    return normalized
