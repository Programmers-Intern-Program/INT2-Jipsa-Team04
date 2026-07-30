"""단계형 Stress의 SLA·백분위수·자원 Guard·처리 경계 계산을 검증한다."""

from jipsa_rag_benchmark.models import RequestRecord
from jipsa_rag_benchmark.resource_sampler import ResourceSample
from jipsa_rag_benchmark.stress_analysis import (
    analyze_capacity_boundaries,
    summarize_stress_stage,
)
from jipsa_rag_benchmark.stress_models import StageStatus, StageSummary, StopPolicy


def test_stage_summary_calculates_sla_percentiles_and_resource_maximums() -> None:
    policy = StopPolicy(
        max_error_rate=0.25,
        max_p95_ms=5_000.0,
        max_system_memory_percent=95.0,
        max_gpu_memory_percent=98.0,
        stop_on_target_exit=True,
        consecutive_failed_stages=1,
    )
    records = (
        _record(index=1, duration_ms=100.0, success=True),
        _record(index=2, duration_ms=200.0, success=True),
        _record(index=3, duration_ms=4_000.0, success=True),
        _record(index=4, duration_ms=300.0, success=False, error_type="TimeoutException"),
    )
    samples = (
        _sample(epoch_seconds=100.5, memory_percent=60.0, gpu_used=1_000, gpu_total=10_000),
        _sample(epoch_seconds=101.5, memory_percent=70.0, gpu_used=2_000, gpu_total=10_000),
    )

    summary = summarize_stress_stage(
        sequence=1,
        stage_id="stage-1",
        parent_stage_id="stage-1",
        stage_name="테스트",
        mode="burst",
        operation="search",
        destructive=False,
        started_epoch_seconds=100.0,
        completed_epoch_seconds=102.0,
        declared_concurrency=4,
        scheduled_request_count=4,
        submitted_request_count=4,
        records=records,
        samples=samples,
        sla_seconds=3.0,
        stop_policy=policy,
        scheduler_backpressure_count=0,
    )

    assert summary.request_count == 4
    assert summary.success_count == 3
    assert summary.sla_success_count == 2
    assert summary.slow_success_count == 1
    assert summary.error_count == 1
    assert summary.error_rate == 0.25
    assert summary.latency_p50_ms == 200.0
    assert summary.latency_max_ms == 4_000.0
    assert summary.system_memory_percent_max == 70.0
    assert summary.gpu_memory_percent_max == 20.0
    assert summary.first_error_type == "TimeoutException"
    assert summary.status == "degraded"


def test_memory_guard_marks_stage_stopped() -> None:
    policy = StopPolicy(
        max_error_rate=0.10,
        max_p95_ms=5_000.0,
        max_system_memory_percent=90.0,
        max_gpu_memory_percent=95.0,
        stop_on_target_exit=True,
        consecutive_failed_stages=1,
    )
    summary = summarize_stress_stage(
        sequence=1,
        stage_id="memory-stop",
        parent_stage_id="memory-stop",
        stage_name="메모리 Guard",
        mode="soak",
        operation="search",
        destructive=False,
        started_epoch_seconds=100.0,
        completed_epoch_seconds=101.0,
        declared_concurrency=8,
        scheduled_request_count=1,
        submitted_request_count=1,
        records=(_record(index=1, duration_ms=100.0, success=True),),
        samples=(
            _sample(
                epoch_seconds=100.5,
                memory_percent=91.0,
                gpu_used=1_000,
                gpu_total=10_000,
            ),
        ),
        sla_seconds=3.0,
        stop_policy=policy,
        scheduler_backpressure_count=0,
    )

    assert summary.status == "stopped"
    assert summary.stop_reason == "system_memory_guard_triggered"


def test_capacity_boundary_separates_normal_maximum_and_first_failure() -> None:
    summaries = (
        _summary(sequence=1, concurrency=8, status="passed"),
        _summary(sequence=2, concurrency=16, status="degraded"),
        _summary(
            sequence=3,
            concurrency=32,
            status="failed",
            stop_reason="error_rate_exceeded",
        ),
    )

    boundaries = analyze_capacity_boundaries(summaries)

    assert len(boundaries) == 1
    boundary = boundaries[0]
    assert boundary.operation == "search"
    assert boundary.normal_maximum_concurrency == 16
    assert boundary.first_failure_concurrency == 32
    assert boundary.first_failure_reason == "error_rate_exceeded"
    assert boundary.upper_bound_censored is False


def _record(
    *,
    index: int,
    duration_ms: float,
    success: bool,
    error_type: str | None = None,
) -> RequestRecord:
    return RequestRecord(
        run_id="run",
        request_id=f"request-{index}",
        case_id="case",
        operation="search",
        phase="concurrency",
        concurrency=4,
        request_index=index,
        started_at_utc="2026-07-30T00:00:00+00:00",
        started_epoch_seconds=100.0,
        completed_epoch_seconds=100.0 + (duration_ms / 1000.0),
        duration_ms=duration_ms,
        status_code=200 if success else None,
        success=success,
        request_bytes=10,
        response_bytes=20 if success else 0,
        error_type=error_type,
    )


def _sample(
    *,
    epoch_seconds: float,
    memory_percent: float,
    gpu_used: int,
    gpu_total: int,
) -> ResourceSample:
    return ResourceSample(
        timestamp_utc="2026-07-30T00:00:00+00:00",
        epoch_seconds=epoch_seconds,
        case_id="case",
        operation="search",
        phase="concurrency",
        concurrency=4,
        system_cpu_percent=10.0,
        system_memory_used_bytes=1_000,
        system_memory_total_bytes=10_000,
        system_memory_percent=memory_percent,
        system_disk_read_bytes=0,
        system_disk_write_bytes=0,
        system_network_received_bytes=0,
        system_network_sent_bytes=0,
        target_pid=123,
        target_process_count=1,
        target_cpu_percent_sum=20.0,
        target_rss_bytes_sum=2_000,
        target_private_bytes_sum=2_000,
        target_read_bytes_sum=0,
        target_write_bytes_sum=0,
        target_thread_count_sum=4,
        target_handle_count_sum=10,
        gpu_utilization_percent_max=30.0,
        gpu_memory_used_bytes_sum=gpu_used,
        gpu_memory_total_bytes_sum=gpu_total,
        gpu_temperature_c_max=60.0,
        gpu_power_watts_sum=100.0,
        target_gpu_memory_used_bytes_sum=500,
        tei_cpu_percent=20.0,
        tei_memory_used_bytes=1_000,
        tei_network_received_bytes=0,
        tei_network_sent_bytes=0,
        tei_block_read_bytes=0,
        tei_block_write_bytes=0,
        qdrant_cpu_percent=5.0,
        qdrant_memory_used_bytes=500,
        qdrant_network_received_bytes=0,
        qdrant_network_sent_bytes=0,
        qdrant_block_read_bytes=0,
        qdrant_block_write_bytes=0,
        sample_duration_ms=1.0,
        sampling_errors=None,
    )


def _summary(
    *,
    sequence: int,
    concurrency: int,
    status: StageStatus,
    stop_reason: str | None = None,
) -> StageSummary:
    return StageSummary(
        sequence=sequence,
        stage_id=f"ramp-c{concurrency}",
        parent_stage_id="ramp",
        stage_name="Ramp",
        mode="ramp",
        operation="search",
        destructive=False,
        started_at_utc="2026-07-30T00:00:00+00:00",
        completed_at_utc="2026-07-30T00:00:01+00:00",
        elapsed_seconds=1.0,
        declared_concurrency=concurrency,
        scheduled_request_count=concurrency,
        submitted_request_count=concurrency,
        request_count=concurrency,
        success_count=concurrency if status != "failed" else 0,
        sla_success_count=concurrency if status != "failed" else 0,
        slow_success_count=0,
        error_count=0 if status != "failed" else concurrency,
        error_rate=0.0 if status != "failed" else 1.0,
        sla_success_rate=1.0 if status != "failed" else 0.0,
        throughput_requests_per_second=float(concurrency),
        latency_mean_ms=100.0,
        latency_min_ms=50.0,
        latency_max_ms=150.0,
        latency_p50_ms=100.0,
        latency_p90_ms=130.0,
        latency_p95_ms=140.0,
        latency_p99_ms=148.0,
        system_memory_percent_max=50.0,
        gpu_memory_percent_max=20.0,
        target_rss_bytes_max=1_000.0,
        target_vram_bytes_max=500.0,
        target_thread_count_max=4.0,
        target_handle_count_max=10.0,
        scheduler_backpressure_count=0,
        status=status,
        stop_triggered=stop_reason is not None,
        stop_reason=stop_reason,
        first_error_type=None,
        resource_sample_count=1,
    )
