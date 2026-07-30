"""측정 계획, 백분위수와 포화 후보 계산의 결정적 계약을 검증한다."""

from pathlib import Path

from jipsa_rag_benchmark.models import (
    LevelSummary,
    SaturationPolicy,
    detect_saturation_candidate,
    load_benchmark_plan,
    percentile,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PLAN = _PROJECT_ROOT / "configs/benchmark-plan.json"


def test_default_plan_covers_requested_formats_origins_and_api_modes() -> None:
    plan = load_benchmark_plan(_DEFAULT_PLAN)
    formats = {
        file_type
        for matrix_entry in plan.fixture_matrix
        for file_type in matrix_entry.formats
    }
    origins = {profile.content_origin for profile in plan.fixture_profiles}

    assert formats == {"pdf", "docx", "pptx", "xlsx", "txt"}
    assert origins == {"text", "ocr"}
    assert plan.ingest.concurrency_levels == (1, 2, 4)
    assert plan.search.load.concurrency_levels == (1, 2, 4, 8, 16)
    assert plan.answers.enabled is True


def test_percentile_uses_linear_interpolation() -> None:
    values = (10.0, 20.0, 30.0, 40.0)

    assert percentile(values, 50.0) == 25.0
    assert percentile(values, 95.0) == 38.5
    assert percentile((), 95.0) is None


def test_saturation_marks_first_plateau_with_latency_growth() -> None:
    summaries = (
        _summary(concurrency=1, throughput=10.0, p95_ms=100.0),
        _summary(concurrency=2, throughput=10.4, p95_ms=125.0),
        _summary(concurrency=4, throughput=10.5, p95_ms=150.0),
    )
    candidate = detect_saturation_candidate(
        summaries,
        policy=SaturationPolicy(
            max_error_rate=0.01,
            throughput_gain_floor_percent=5.0,
            p95_growth_trigger_percent=20.0,
        ),
    )

    assert candidate is not None
    assert candidate.concurrency == 2
    assert candidate.previous_concurrency == 1
    assert candidate.reason == "throughput_plateau_with_latency_growth"


def _summary(*, concurrency: int, throughput: float, p95_ms: float) -> LevelSummary:
    return LevelSummary(
        operation="search",
        phase="concurrency",
        concurrency=concurrency,
        request_count=16,
        success_count=16,
        error_count=0,
        error_rate=0.0,
        elapsed_seconds=16.0 / throughput,
        throughput_requests_per_second=throughput,
        mean_ms=p95_ms,
        p50_ms=p95_ms,
        p95_ms=p95_ms,
        p99_ms=p95_ms,
        min_ms=p95_ms,
        max_ms=p95_ms,
        total_request_bytes=0,
        total_response_bytes=0,
        total_input_tokens=0,
        total_output_tokens=0,
    )
