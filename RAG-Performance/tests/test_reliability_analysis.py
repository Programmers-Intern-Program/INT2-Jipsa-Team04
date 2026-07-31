"""Soak Window, Drift와 정상 최대·최초 실패 분석을 검증한다."""

from jipsa_rag_benchmark.models import LevelSummary, RequestRecord
from jipsa_rag_benchmark.reliability_analysis import (
    analyze_boundaries,
    summarize_soak_windows,
)
from jipsa_rag_benchmark.resource_sampler import ResourceSample


def _record(case_id: str, index: int, duration_ms: float, *, success: bool = True) -> RequestRecord:
    started = float(index)
    return RequestRecord(
        run_id="run",
        request_id=f"request-{index}",
        case_id=case_id,
        operation="search",
        phase="concurrency",
        concurrency=2,
        request_index=index,
        started_at_utc="2026-07-30T00:00:00+00:00",
        started_epoch_seconds=started,
        completed_epoch_seconds=started + (duration_ms / 1000.0),
        duration_ms=duration_ms,
        status_code=200 if success else 503,
        success=success,
        request_bytes=10,
        response_bytes=20,
        error_type=None if success else "ServiceUnavailable",
    )


def _sample(case_id: str, epoch: float, rss: int, vram: int) -> ResourceSample:
    return ResourceSample(
        timestamp_utc="2026-07-30T00:00:00+00:00",
        epoch_seconds=epoch,
        case_id=case_id,
        operation="search",
        phase="concurrency",
        concurrency=2,
        system_cpu_percent=10.0,
        system_memory_used_bytes=rss,
        system_memory_total_bytes=16 * 1024**3,
        system_memory_percent=10.0,
        system_disk_read_bytes=0,
        system_disk_write_bytes=0,
        system_network_received_bytes=0,
        system_network_sent_bytes=0,
        target_pid=1,
        target_process_count=1,
        target_cpu_percent_sum=10.0,
        target_rss_bytes_sum=rss,
        target_private_bytes_sum=rss,
        target_read_bytes_sum=0,
        target_write_bytes_sum=0,
        target_thread_count_sum=10,
        target_handle_count_sum=20,
        gpu_utilization_percent_max=10.0,
        gpu_memory_used_bytes_sum=vram,
        gpu_memory_total_bytes_sum=8 * 1024**3,
        gpu_temperature_c_max=40.0,
        gpu_power_watts_sum=20.0,
        target_gpu_memory_used_bytes_sum=vram,
        tei_cpu_percent=1.0,
        tei_memory_used_bytes=1,
        tei_network_received_bytes=0,
        tei_network_sent_bytes=0,
        tei_block_read_bytes=0,
        tei_block_write_bytes=0,
        qdrant_cpu_percent=1.0,
        qdrant_memory_used_bytes=1,
        qdrant_network_received_bytes=0,
        qdrant_network_sent_bytes=0,
        qdrant_block_read_bytes=0,
        qdrant_block_write_bytes=0,
        sample_duration_ms=1.0,
        sampling_errors=None,
    )


def test_soak_windows_include_mean_max_and_percentiles() -> None:
    records = (
        _record("soak-window-0001", 1, 10.0),
        _record("soak-window-0001", 2, 30.0),
    )
    samples = (
        _sample("soak-window-0001", 1.0, 100, 200),
        _sample("soak-window-0001", 2.0, 300, 400),
    )

    windows = summarize_soak_windows(records, samples)

    assert len(windows) == 1
    assert windows[0].latency_mean_ms == 20.0
    assert windows[0].latency_max_ms == 30.0
    assert windows[0].latency_p50_ms == 20.0
    assert windows[0].target_rss_max_bytes == 300


def test_boundary_analysis_separates_normal_maximum_and_first_failure() -> None:
    summaries = (
        LevelSummary(
            operation="search",
            phase="concurrency",
            concurrency=1,
            request_count=10,
            success_count=10,
            error_count=0,
            error_rate=0.0,
            elapsed_seconds=1.0,
            throughput_requests_per_second=10.0,
            mean_ms=10.0,
            p50_ms=10.0,
            p95_ms=10.0,
            p99_ms=10.0,
            min_ms=10.0,
            max_ms=10.0,
            total_request_bytes=0,
            total_response_bytes=0,
            total_input_tokens=0,
            total_output_tokens=0,
        ),
        LevelSummary(
            operation="search",
            phase="concurrency",
            concurrency=4,
            request_count=10,
            success_count=8,
            error_count=2,
            error_rate=0.2,
            elapsed_seconds=1.0,
            throughput_requests_per_second=8.0,
            mean_ms=20.0,
            p50_ms=20.0,
            p95_ms=30.0,
            p99_ms=40.0,
            min_ms=10.0,
            max_ms=50.0,
            total_request_bytes=0,
            total_response_bytes=0,
            total_input_tokens=0,
            total_output_tokens=0,
        ),
    )

    boundaries = analyze_boundaries((), summaries, max_error_rate=0.01)
    concurrency = next(value for value in boundaries if value.dimension == "concurrency")

    assert concurrency.normal_maximum_value == 1
    assert concurrency.first_failure_value == 4
    assert concurrency.observed_upper_bound_censored is False


def test_boundary_normal_maximum_stops_before_first_failure() -> None:
    summaries = (
        LevelSummary(
            operation="search",
            phase="concurrency",
            concurrency=1,
            request_count=10,
            success_count=10,
            error_count=0,
            error_rate=0.0,
            elapsed_seconds=1.0,
            throughput_requests_per_second=10.0,
            mean_ms=10.0,
            p50_ms=10.0,
            p95_ms=10.0,
            p99_ms=10.0,
            min_ms=10.0,
            max_ms=10.0,
            total_request_bytes=0,
            total_response_bytes=0,
            total_input_tokens=0,
            total_output_tokens=0,
        ),
        LevelSummary(
            operation="search",
            phase="concurrency",
            concurrency=2,
            request_count=10,
            success_count=5,
            error_count=5,
            error_rate=0.5,
            elapsed_seconds=1.0,
            throughput_requests_per_second=5.0,
            mean_ms=20.0,
            p50_ms=20.0,
            p95_ms=20.0,
            p99_ms=20.0,
            min_ms=20.0,
            max_ms=20.0,
            total_request_bytes=0,
            total_response_bytes=0,
            total_input_tokens=0,
            total_output_tokens=0,
        ),
        LevelSummary(
            operation="search",
            phase="concurrency",
            concurrency=4,
            request_count=10,
            success_count=10,
            error_count=0,
            error_rate=0.0,
            elapsed_seconds=1.0,
            throughput_requests_per_second=10.0,
            mean_ms=30.0,
            p50_ms=30.0,
            p95_ms=30.0,
            p99_ms=30.0,
            min_ms=30.0,
            max_ms=30.0,
            total_request_bytes=0,
            total_response_bytes=0,
            total_input_tokens=0,
            total_output_tokens=0,
        ),
    )

    boundaries = analyze_boundaries((), summaries, max_error_rate=0.01)
    concurrency = next(value for value in boundaries if value.dimension == "concurrency")

    assert concurrency.normal_maximum_value == 1
    assert concurrency.first_failure_value == 2
