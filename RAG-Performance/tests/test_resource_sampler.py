"""자원 측정 단위 변환과 누적 Counter 차이를 검증한다."""

from pathlib import Path

from jipsa_rag_benchmark.resource_sampler import (
    HostIoSnapshot,
    ResourceSampler,
    _parse_human_size,
)


def test_parse_human_size_supports_decimal_and_iec_units() -> None:
    assert _parse_human_size("512B") == 512
    assert _parse_human_size("1.5MB") == 1_500_000
    assert _parse_human_size("1.5MiB") == 1_572_864
    assert _parse_human_size("unsupported") is None


def test_host_io_delta_rejects_reset_counters() -> None:
    previous = HostIoSnapshot(
        timestamp_utc="2026-07-30T00:00:00+00:00",
        epoch_seconds=1.0,
        network_received_bytes=100,
        network_sent_bytes=200,
        disk_read_bytes=300,
        disk_write_bytes=400,
    )
    current = HostIoSnapshot(
        timestamp_utc="2026-07-30T00:00:01+00:00",
        epoch_seconds=2.0,
        network_received_bytes=160,
        network_sent_bytes=150,
        disk_read_bytes=390,
        disk_write_bytes=500,
    )

    assert current.delta(previous) == {
        "network_received_bytes": 60,
        "network_sent_bytes": None,
        "disk_read_bytes": 90,
        "disk_write_bytes": 100,
    }


def test_process_cpu_percent_uses_pid_time_delta() -> None:
    """새 Process 객체 생성과 무관하게 누적 CPU 시간 차이로 사용률을 계산한다."""

    sampler = ResourceSampler(
        output_path=Path("unused.jsonl"),
        sample_interval_seconds=1.0,
        docker_sample_interval_seconds=2.0,
    )

    assert sampler._calculate_process_cpu_percent({10: 1.0}, 100.0) == 0.0
    assert sampler._calculate_process_cpu_percent({10: 1.5}, 101.0) == 50.0
