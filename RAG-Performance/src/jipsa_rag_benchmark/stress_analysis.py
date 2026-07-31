"""단계형 Stress 결과를 요약하고 정상 최대·최초 실패 경계를 계산한다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from jipsa_rag_benchmark.models import RequestRecord, percentile
from jipsa_rag_benchmark.resource_sampler import ResourceSample
from jipsa_rag_benchmark.stress_models import (
    CapacityBoundary,
    StageMode,
    StageStatus,
    StageSummary,
    StopPolicy,
    StressOperation,
)


def summarize_stress_stage(
    *,
    sequence: int,
    stage_id: str,
    parent_stage_id: str,
    stage_name: str,
    mode: StageMode,
    operation: StressOperation,
    destructive: bool,
    started_epoch_seconds: float,
    completed_epoch_seconds: float,
    declared_concurrency: int,
    scheduled_request_count: int,
    submitted_request_count: int,
    records: Sequence[RequestRecord],
    samples: Sequence[ResourceSample],
    sla_seconds: float,
    stop_policy: StopPolicy,
    scheduler_backpressure_count: int,
    explicit_stop_reason: str | None = None,
) -> StageSummary:
    """한 단계의 요청·SLA·백분위수·자원 최대값과 상태를 계산한다."""

    elapsed_seconds = max(completed_epoch_seconds - started_epoch_seconds, 1e-9)
    successful = tuple(record for record in records if record.success)
    latencies = tuple(record.duration_ms for record in successful)
    sla_limit_ms = sla_seconds * 1000.0
    sla_success_count = sum(record.duration_ms <= sla_limit_ms for record in successful)
    slow_success_count = len(successful) - sla_success_count
    error_count = len(records) - len(successful)
    error_rate = error_count / len(records) if records else 0.0
    sla_success_rate = sla_success_count / len(records) if records else 0.0

    matching_samples = tuple(
        sample
        for sample in samples
        if started_epoch_seconds <= sample.epoch_seconds <= completed_epoch_seconds
    )
    system_memory_percent = _numeric_values(
        sample.system_memory_percent for sample in matching_samples
    )
    gpu_memory_percent = tuple(
        percentage
        for sample in matching_samples
        if (
            percentage := _percentage(
                sample.gpu_memory_used_bytes_sum,
                sample.gpu_memory_total_bytes_sum,
            )
        )
        is not None
    )
    target_rss = _numeric_values(sample.target_rss_bytes_sum for sample in matching_samples)
    target_vram = _numeric_values(
        sample.target_gpu_memory_used_bytes_sum for sample in matching_samples
    )
    target_threads = _numeric_values(sample.target_thread_count_sum for sample in matching_samples)
    target_handles = _numeric_values(sample.target_handle_count_sum for sample in matching_samples)

    first_error_type = next(
        (
            record.error_type or f"http_{record.status_code}"
            for record in records
            if not record.success
        ),
        None,
    )
    stop_reason = explicit_stop_reason or _policy_stop_reason(
        error_rate=error_rate,
        latency_p95_ms=percentile(latencies, 95.0),
        system_memory_percent_max=_max_or_none(system_memory_percent),
        gpu_memory_percent_max=_max_or_none(gpu_memory_percent),
        policy=stop_policy,
    )
    status = _stage_status(
        request_count=len(records),
        submitted_request_count=submitted_request_count,
        error_rate=error_rate,
        slow_success_count=slow_success_count,
        stop_reason=stop_reason,
        policy=stop_policy,
    )
    return StageSummary(
        sequence=sequence,
        stage_id=stage_id,
        parent_stage_id=parent_stage_id,
        stage_name=stage_name,
        mode=mode,
        operation=operation,
        destructive=destructive,
        started_at_utc=_utc_iso(started_epoch_seconds),
        completed_at_utc=_utc_iso(completed_epoch_seconds),
        elapsed_seconds=elapsed_seconds,
        declared_concurrency=declared_concurrency,
        scheduled_request_count=scheduled_request_count,
        submitted_request_count=submitted_request_count,
        request_count=len(records),
        success_count=len(successful),
        sla_success_count=sla_success_count,
        slow_success_count=slow_success_count,
        error_count=error_count,
        error_rate=error_rate,
        sla_success_rate=sla_success_rate,
        throughput_requests_per_second=len(successful) / elapsed_seconds,
        latency_mean_ms=(sum(latencies) / len(latencies)) if latencies else None,
        latency_min_ms=min(latencies) if latencies else None,
        latency_max_ms=max(latencies) if latencies else None,
        latency_p50_ms=percentile(latencies, 50.0),
        latency_p90_ms=percentile(latencies, 90.0),
        latency_p95_ms=percentile(latencies, 95.0),
        latency_p99_ms=percentile(latencies, 99.0),
        system_memory_percent_max=_max_or_none(system_memory_percent),
        gpu_memory_percent_max=_max_or_none(gpu_memory_percent),
        target_rss_bytes_max=max(target_rss, default=None),
        target_vram_bytes_max=max(target_vram, default=None),
        target_thread_count_max=max(target_threads, default=None),
        target_handle_count_max=max(target_handles, default=None),
        scheduler_backpressure_count=scheduler_backpressure_count,
        status=status,
        stop_triggered=stop_reason is not None,
        stop_reason=stop_reason,
        first_error_type=first_error_type,
        resource_sample_count=len(matching_samples),
    )


def analyze_capacity_boundaries(
    summaries: Sequence[StageSummary],
) -> tuple[CapacityBoundary, ...]:
    """Ramp·동시성 단계에서 연속 정상 범위와 최초 실패 동시성을 분리한다."""

    results: list[CapacityBoundary] = []
    operations: tuple[StressOperation, ...] = ("search", "ingest")
    for operation in operations:
        operation_summaries = tuple(
            summary
            for summary in summaries
            if summary.operation == operation and summary.declared_concurrency > 0
        )
        # 처리 한계의 주 근거는 동시성을 순서대로 증가시킨 Ramp Stage다. Burst·Soak·Chaos는
        # 서로 다른 도착 패턴과 지속시간을 사용하므로 Ramp 결과가 있을 때 섞으면 낮은
        # 동시성의 일시적 실패가 최초 한계로 오판될 수 있다. Ramp가 없는 사용자 정의
        # 계획에서만 다른 Traffic Stage를 보조 근거로 사용한다.
        ramp_summaries = tuple(summary for summary in operation_summaries if summary.mode == "ramp")
        source = ramp_summaries or tuple(
            summary
            for summary in operation_summaries
            if summary.mode in {"burst", "soak", "chaos", "batch", "interval"}
        )
        candidates = sorted(
            source,
            key=lambda value: (value.declared_concurrency, value.sequence),
        )
        if not candidates:
            continue

        normal_maximum: int | None = None
        first_failure: StageSummary | None = None
        for summary in candidates:
            if summary.status in {"failed", "stopped"}:
                first_failure = summary
                break
            normal_maximum = max(normal_maximum or 0, summary.declared_concurrency)

        results.append(
            CapacityBoundary(
                operation=operation,
                normal_maximum_concurrency=normal_maximum,
                first_failure_concurrency=(
                    first_failure.declared_concurrency if first_failure is not None else None
                ),
                first_failure_stage_id=(
                    first_failure.stage_id if first_failure is not None else None
                ),
                first_failure_reason=(
                    first_failure.stop_reason
                    or first_failure.first_error_type
                    or first_failure.status
                    if first_failure is not None
                    else None
                ),
                upper_bound_censored=first_failure is None,
                evidence_count=len(candidates),
            )
        )
    return tuple(results)


def _policy_stop_reason(
    *,
    error_rate: float,
    latency_p95_ms: float | None,
    system_memory_percent_max: float | None,
    gpu_memory_percent_max: float | None,
    policy: StopPolicy,
) -> str | None:
    if error_rate > policy.max_error_rate:
        return "error_rate_exceeded"
    if latency_p95_ms is not None and latency_p95_ms > policy.max_p95_ms:
        return "p95_latency_exceeded"
    if (
        system_memory_percent_max is not None
        and system_memory_percent_max >= policy.max_system_memory_percent
    ):
        return "system_memory_guard_triggered"
    if (
        gpu_memory_percent_max is not None
        and gpu_memory_percent_max >= policy.max_gpu_memory_percent
    ):
        return "gpu_memory_guard_triggered"
    return None


def _stage_status(
    *,
    request_count: int,
    submitted_request_count: int,
    error_rate: float,
    slow_success_count: int,
    stop_reason: str | None,
    policy: StopPolicy,
) -> StageStatus:
    if submitted_request_count == 0 and request_count == 0:
        return "skipped"
    if stop_reason is not None:
        if stop_reason in {
            "system_memory_guard_triggered",
            "gpu_memory_guard_triggered",
            "target_process_exited",
            "scheduler_submission_stopped",
            "client_memory_guard_triggered",
        }:
            return "stopped"
        return "failed"
    if error_rate > policy.max_error_rate:
        return "failed"
    if slow_success_count > 0 or error_rate > 0:
        return "degraded"
    return "passed"


def _numeric_values(values: Iterable[object]) -> tuple[float, ...]:
    """bool을 제외한 숫자만 float Tuple로 정규화한다."""

    normalized: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        normalized.append(float(value))
    return tuple(normalized)


def _max_or_none(values: Sequence[float]) -> float | None:
    """빈 Sequence는 ``None``, 값이 있으면 최대값을 반환한다."""

    return max(values) if values else None


def _percentage(used: int | None, total: int | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return (used / total) * 100.0


def _utc_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat(timespec="milliseconds")
