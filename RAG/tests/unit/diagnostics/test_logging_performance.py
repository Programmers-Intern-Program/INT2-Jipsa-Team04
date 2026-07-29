"""로그 개선 전후 비교 도구의 출력량 계약과 절대 오버헤드 상한을 검증한다."""

from __future__ import annotations

from jipsa_rag.diagnostics.logging_performance import (
    LoggingWorkload,
    measure_logging_scenario,
    run_logging_comparison,
)


def test_file_processing_only_output_line_contract() -> None:
    """파일 처리 단독에서는 기존 2줄, 개선 후 단계 포함 7줄이어야 한다."""

    workload = LoggingWorkload(
        name="file_processing_only",
        file_processing_count=1,
        health_check_count=0,
        chunk_count=64,
    )

    legacy = measure_logging_scenario(
        mode="legacy_json",
        workload=workload,
        iterations=1,
        rounds=1,
    )
    improved_console = measure_logging_scenario(
        mode="improved_console",
        workload=workload,
        iterations=1,
        rounds=1,
    )
    improved_json = measure_logging_scenario(
        mode="improved_json",
        workload=workload,
        iterations=1,
        rounds=1,
    )

    assert legacy.output_line_count == 2
    assert improved_console.output_line_count == 7
    assert improved_json.output_line_count == 7


def test_improved_info_output_does_not_scale_with_chunk_count() -> None:
    """청크 수가 1개에서 10,000개로 늘어도 로그 행 수는 증가하지 않아야 한다."""

    small = measure_logging_scenario(
        mode="improved_json",
        workload=LoggingWorkload(
            name="small",
            file_processing_count=1,
            health_check_count=0,
            chunk_count=1,
        ),
        iterations=1,
        rounds=1,
    )
    large = measure_logging_scenario(
        mode="improved_json",
        workload=LoggingWorkload(
            name="large",
            file_processing_count=1,
            health_check_count=0,
            chunk_count=10_000,
        ),
        iterations=1,
        rounds=1,
    )

    assert small.output_line_count == 7
    assert large.output_line_count == 7

    # 숫자 자릿수 차이 외에는 출력량이 청크 수에 비례해 증가하지 않아야 한다.
    assert abs(large.output_byte_count - small.output_byte_count) < 100


def test_health_check_mixed_workload_reduces_total_output_volume() -> None:
    """정상 Health Check가 반복되면 개선 정책의 전체 출력량이 더 작아야 한다."""

    workload = LoggingWorkload(
        name="mixed",
        file_processing_count=1,
        health_check_count=120,
    )

    legacy = measure_logging_scenario(
        mode="legacy_json",
        workload=workload,
        iterations=1,
        rounds=1,
    )
    improved = measure_logging_scenario(
        mode="improved_json",
        workload=workload,
        iterations=1,
        rounds=1,
    )

    assert legacy.output_line_count == 242
    assert improved.output_line_count == 7
    assert improved.output_byte_count < legacy.output_byte_count


def test_improved_logging_overhead_stays_below_absolute_microbenchmark_budget() -> None:
    """일곱 줄 요약 로그의 중앙 처리 시간이 워크로드당 50ms를 넘지 않아야 한다.

    상대 비율은 기존 2줄과 개선 7줄의 행 수 차이에 영향을 받으므로 실패 기준으로
    사용하지 않는다. 대신 일반 CI에서도 충분히 여유 있는 절대 상한을 적용하여 원문,
    벡터 또는 대형 payload 직렬화가 실수로 추가되는 심각한 회귀만 차단한다.
    """

    report = run_logging_comparison(
        iterations=25,
        rounds=3,
    )
    improved_samples = [
        sample for sample in report.samples if sample.mode in {"improved_console", "improved_json"}
    ]

    assert improved_samples
    assert all(sample.median_duration_ms < 50.0 for sample in improved_samples)
