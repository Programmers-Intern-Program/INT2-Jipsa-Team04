"""장시간 실행, 실패 조건과 처리 경계 결과를 순수 함수로 분석한다.

분석 함수는 서비스 설정을 바꾸지 않는다. 입력으로 받은 요청·자원 표본을 집계하고,
관측된 정상 최대값과 최초 실패값을 분리해 후속 리뷰용 결과만 생성한다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from jipsa_rag_benchmark.models import LevelSummary, RequestRecord, percentile
from jipsa_rag_benchmark.reliability_models import (
    BoundaryResult,
    DriftAssessment,
    FailureCategory,
    FailureEvent,
    ReliabilityPlan,
    SoakWindowSummary,
    finite_percent_change,
)
from jipsa_rag_benchmark.resource_sampler import ResourceSample


@dataclass(frozen=True, slots=True)
class _WindowBoundary:
    """한 Soak Window의 실제 요청 시작·종료 범위."""

    started_epoch_seconds: float
    completed_epoch_seconds: float
    started_at_utc: str
    completed_at_utc: str


_RESOURCE_FIELDS: tuple[str, ...] = (
    "target_rss_bytes_sum",
    "target_gpu_memory_used_bytes_sum",
    "target_thread_count_sum",
    "target_handle_count_sum",
)


def summarize_soak_windows(
    records: Sequence[RequestRecord],
    samples: Sequence[ResourceSample],
    *,
    case_prefix: str = "soak-window-",
) -> tuple[SoakWindowSummary, ...]:
    """Soak Window별 요청 지연과 자원 평균·최대·백분위수를 계산한다."""

    grouped_records: dict[str, list[RequestRecord]] = defaultdict(list)
    for record in records:
        if record.case_id.startswith(case_prefix):
            grouped_records[record.case_id].append(record)

    grouped_samples: dict[str, list[ResourceSample]] = defaultdict(list)
    for sample in samples:
        if sample.case_id.startswith(case_prefix):
            grouped_samples[sample.case_id].append(sample)

    summaries: list[SoakWindowSummary] = []
    for case_id in sorted(grouped_records, key=_window_sort_key):
        window_records = sorted(
            grouped_records[case_id],
            key=lambda value: value.started_epoch_seconds,
        )
        if not window_records:
            continue
        boundary = _request_boundary(window_records)
        window_samples = [
            sample
            for sample in grouped_samples.get(case_id, [])
            if boundary.started_epoch_seconds
            <= sample.epoch_seconds
            <= boundary.completed_epoch_seconds
        ]
        elapsed_seconds = max(
            boundary.completed_epoch_seconds - boundary.started_epoch_seconds,
            1e-9,
        )
        successful_durations = tuple(
            record.duration_ms for record in window_records if record.success
        )
        success_count = sum(record.success for record in window_records)
        error_count = len(window_records) - success_count

        rss = _sample_values(window_samples, "target_rss_bytes_sum")
        vram = _sample_values(window_samples, "target_gpu_memory_used_bytes_sum")
        threads = _sample_values(window_samples, "target_thread_count_sum")
        handles = _sample_values(window_samples, "target_handle_count_sum")

        summaries.append(
            SoakWindowSummary(
                window_index=_parse_window_index(case_id),
                case_id=case_id,
                started_at_utc=boundary.started_at_utc,
                completed_at_utc=boundary.completed_at_utc,
                elapsed_seconds=elapsed_seconds,
                request_count=len(window_records),
                success_count=success_count,
                error_count=error_count,
                error_rate=error_count / len(window_records),
                throughput_requests_per_second=success_count / elapsed_seconds,
                latency_mean_ms=_mean(successful_durations),
                latency_max_ms=max(successful_durations) if successful_durations else None,
                latency_p50_ms=percentile(successful_durations, 50.0),
                latency_p95_ms=percentile(successful_durations, 95.0),
                latency_p99_ms=percentile(successful_durations, 99.0),
                target_rss_mean_bytes=_mean(rss),
                target_rss_max_bytes=max(rss) if rss else None,
                target_rss_p50_bytes=percentile(rss, 50.0),
                target_rss_p95_bytes=percentile(rss, 95.0),
                target_rss_p99_bytes=percentile(rss, 99.0),
                target_vram_mean_bytes=_mean(vram),
                target_vram_max_bytes=max(vram) if vram else None,
                target_vram_p50_bytes=percentile(vram, 50.0),
                target_vram_p95_bytes=percentile(vram, 95.0),
                target_vram_p99_bytes=percentile(vram, 99.0),
                target_thread_mean=_mean(threads),
                target_thread_max=max(threads) if threads else None,
                target_handle_mean=_mean(handles),
                target_handle_max=max(handles) if handles else None,
                resource_sample_count=len(window_samples),
            )
        )
    return tuple(summaries)


def assess_soak_drift(
    windows: Sequence[SoakWindowSummary],
    *,
    plan: ReliabilityPlan,
) -> DriftAssessment:
    """첫 Window와 마지막 Window를 비교해 누수·성능 저하 후보를 표시한다."""

    if not windows:
        return DriftAssessment(
            window_count=0,
            first_window_index=None,
            last_window_index=None,
            latency_p95_growth_percent=None,
            target_rss_p95_growth_percent=None,
            target_vram_p95_growth_percent=None,
            thread_max_growth_count=None,
            handle_max_growth_count=None,
            latency_degradation_candidate=False,
            rss_leak_candidate=False,
            vram_leak_candidate=False,
            thread_leak_candidate=False,
            handle_leak_candidate=False,
            any_candidate=False,
            interpretation="Soak Window 결과가 없어 장시간 변화량을 계산하지 못했습니다.",
        )

    ordered = sorted(windows, key=lambda value: value.window_index)
    first = ordered[0]
    last = ordered[-1]
    latency_growth = finite_percent_change(first.latency_p95_ms, last.latency_p95_ms)
    rss_growth = finite_percent_change(
        first.target_rss_p95_bytes,
        last.target_rss_p95_bytes,
    )
    vram_growth = finite_percent_change(
        first.target_vram_p95_bytes,
        last.target_vram_p95_bytes,
    )
    thread_growth = _difference(first.target_thread_max, last.target_thread_max)
    handle_growth = _difference(first.target_handle_max, last.target_handle_max)

    latency_candidate = _at_least(
        latency_growth,
        plan.soak.latency_p95_growth_report_percent,
    )
    rss_candidate = _at_least(rss_growth, plan.soak.rss_p95_growth_report_percent)
    vram_candidate = _at_least(
        vram_growth,
        plan.soak.vram_p95_growth_report_percent,
    )
    thread_candidate = _at_least(
        thread_growth,
        float(plan.soak.thread_growth_report_count),
    )
    handle_candidate = _at_least(
        handle_growth,
        float(plan.soak.handle_growth_report_count),
    )
    any_candidate = any(
        (
            latency_candidate,
            rss_candidate,
            vram_candidate,
            thread_candidate,
            handle_candidate,
        )
    )

    interpretation = (
        "보고 기준을 넘은 변화가 있어 반복 실행 로그와 시간축 표본을 추가 검토해야 합니다."
        if any_candidate
        else "관측한 Window 범위에서는 설정한 보고 기준을 넘는 누수·성능 저하 후보가 없습니다."
    )
    return DriftAssessment(
        window_count=len(ordered),
        first_window_index=first.window_index,
        last_window_index=last.window_index,
        latency_p95_growth_percent=latency_growth,
        target_rss_p95_growth_percent=rss_growth,
        target_vram_p95_growth_percent=vram_growth,
        thread_max_growth_count=thread_growth,
        handle_max_growth_count=handle_growth,
        latency_degradation_candidate=latency_candidate,
        rss_leak_candidate=rss_candidate,
        vram_leak_candidate=vram_candidate,
        thread_leak_candidate=thread_candidate,
        handle_leak_candidate=handle_candidate,
        any_candidate=any_candidate,
        interpretation=interpretation,
    )


def analyze_boundaries(
    records: Sequence[RequestRecord],
    level_summaries: Sequence[LevelSummary],
    *,
    max_error_rate: float,
) -> tuple[BoundaryResult, ...]:
    """동시성·파일·이미지·청크 기준의 정상 최대와 최초 실패를 분리한다."""

    results: list[BoundaryResult] = []
    by_operation: dict[str, list[LevelSummary]] = defaultdict(list)
    for summary in level_summaries:
        if summary.phase == "concurrency":
            by_operation[summary.operation].append(summary)

    for operation, summaries in sorted(by_operation.items()):
        ordered = sorted(summaries, key=lambda value: value.concurrency)
        first_failure_index = next(
            (index for index, value in enumerate(ordered) if value.error_rate > max_error_rate),
            None,
        )
        first_failed = ordered[first_failure_index] if first_failure_index is not None else None
        # 실패 뒤의 우연한 성공 단계는 연속적인 정상 범위로 간주하지 않는다. 따라서
        # 정상 최대값은 최초 실패 전까지 허용 오류율을 충족한 단계에서만 선택한다.
        normal_prefix = (
            ordered[:first_failure_index] if first_failure_index is not None else ordered
        )
        normal = [value for value in normal_prefix if value.error_rate <= max_error_rate]
        results.append(
            BoundaryResult(
                dimension="concurrency",
                operation=operation,
                unit="requests",
                normal_maximum_value=(
                    max(value.concurrency for value in normal) if normal else None
                ),
                first_failure_value=(
                    first_failed.concurrency if first_failed is not None else None
                ),
                first_failure_reason=("error_rate_exceeded" if first_failed is not None else None),
                observed_upper_bound_censored=first_failed is None,
                evidence_count=len(ordered),
            )
        )

    results.extend(
        _numeric_record_boundary(
            records,
            dimension="fixture_size_bytes",
            unit="bytes",
            extractor=lambda record: record.fixture_size_bytes,
        )
    )
    results.extend(
        _numeric_record_boundary(
            records,
            dimension="declared_text_units",
            unit="text_units",
            extractor=lambda record: record.declared_text_units,
        )
    )
    results.extend(
        _numeric_record_boundary(
            records,
            dimension="declared_image_count",
            unit="images",
            extractor=lambda record: record.declared_image_count,
        )
    )
    results.extend(
        _numeric_record_boundary(
            records,
            dimension="chunk_count",
            unit="chunks",
            extractor=lambda record: record.chunk_count,
        )
    )
    return tuple(results)


def classify_passive_failures(
    records: Sequence[RequestRecord],
    *,
    target_log_text: str,
    timestamp_utc: str,
) -> tuple[FailureEvent, ...]:
    """실제 요청·대상 로그에서 Timeout/OOM/외부 실패 흔적을 별도로 분류한다."""

    events: list[FailureEvent] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        if record.success or record.error_type is None:
            continue
        category, condition = _classify_error_text(
            " ".join(
                value for value in (record.error_type, record.error_message) if value is not None
            )
        )
        if category is None:
            continue
        key = (category, record.request_id)
        if key in seen:
            continue
        seen.add(key)
        events.append(
            FailureEvent(
                event_id=f"passive-request-{record.request_id}",
                category=category,
                condition=condition,
                injected=False,
                expected=False,
                started_at_utc=record.started_at_utc,
                completed_at_utc=record.started_at_utc,
                duration_ms=record.duration_ms,
                outcome="observed",
                request_operation=record.operation,
                error_type=record.error_type,
                status_code=record.status_code,
                recovered=None,
                safe_probe=True,
                detail="요청 결과에서 자동 분류한 실제 실패입니다.",
            )
        )

    lower_log = target_log_text.lower()
    for token, raw_category, condition in (
        ("cuda out of memory", "oom", "cuda_oom"),
        ("outofmemoryerror", "oom", "process_oom"),
        ("memoryerror", "oom", "python_memory_error"),
        ("timed out", "timeout", "target_timeout"),
        ("connection refused", "external_service", "connection_refused"),
    ):
        if token not in lower_log:
            continue

        # 같은 함수 앞부분의 Optional category 변수와 타입이 합쳐지지 않도록 별도 이름으로
        # 수신한 뒤, 고정된 Literal 집합임을 명시적으로 좁힌다.
        log_category = cast(FailureCategory, raw_category)
        key = (log_category, f"log:{token}")
        if key in seen:
            continue
        seen.add(key)
        events.append(
            FailureEvent(
                event_id=f"passive-log-{condition}",
                category=log_category,
                condition=condition,
                injected=False,
                expected=False,
                started_at_utc=timestamp_utc,
                completed_at_utc=timestamp_utc,
                duration_ms=0.0,
                outcome="observed",
                error_type=condition,
                recovered=None,
                safe_probe=True,
                detail="target.log에서 민감한 원문을 복사하지 않고 오류 표식만 감지했습니다.",
            )
        )
    return tuple(events)


def _numeric_record_boundary(
    records: Sequence[RequestRecord],
    *,
    dimension: str,
    unit: str,
    extractor: Callable[[RequestRecord], int | None],
) -> list[BoundaryResult]:
    typed_extractor = extractor
    grouped: dict[str, list[tuple[int, RequestRecord]]] = defaultdict(list)
    for record in records:
        if record.operation != "ingest":
            continue
        value = typed_extractor(record)
        if value is None:
            continue
        operation = f"ingest:{record.file_type or 'unknown'}:{record.content_origin or 'unknown'}"
        grouped[operation].append((value, record))

    results: list[BoundaryResult] = []
    for operation, evidence in sorted(grouped.items()):
        ordered = sorted(evidence, key=lambda value: value[0])
        first_failure_index = next(
            (index for index, (_, record) in enumerate(ordered) if not record.success),
            None,
        )
        first_failed = ordered[first_failure_index] if first_failure_index is not None else None
        # 크기·이미지·청크 역시 최초 실패 이후의 성공 표본을 정상 연속 범위에 포함하지 않는다.
        normal_prefix = (
            ordered[:first_failure_index] if first_failure_index is not None else ordered
        )
        successful = [value for value, record in normal_prefix if record.success]
        results.append(
            BoundaryResult(
                dimension=dimension,
                operation=operation,
                unit=unit,
                normal_maximum_value=max(successful) if successful else None,
                first_failure_value=first_failed[0] if first_failed is not None else None,
                first_failure_reason=(
                    first_failed[1].error_type or "request_failed"
                    if first_failed is not None
                    else None
                ),
                observed_upper_bound_censored=first_failed is None,
                evidence_count=len(ordered),
            )
        )
    return results


def _request_boundary(records: Sequence[RequestRecord]) -> _WindowBoundary:
    first = min(records, key=lambda value: value.started_epoch_seconds)
    last = max(records, key=lambda value: value.completed_epoch_seconds)
    return _WindowBoundary(
        started_epoch_seconds=first.started_epoch_seconds,
        completed_epoch_seconds=last.completed_epoch_seconds,
        started_at_utc=first.started_at_utc,
        completed_at_utc=_iso_from_record(last),
    )


def _iso_from_record(record: RequestRecord) -> str:
    return datetime.fromtimestamp(
        record.completed_epoch_seconds,
        tz=UTC,
    ).isoformat(timespec="milliseconds")


def _sample_values(samples: Sequence[ResourceSample], field_name: str) -> tuple[float, ...]:
    if field_name not in _RESOURCE_FIELDS:
        raise ValueError(f"Unsupported resource field: {field_name}")
    return tuple(
        float(value) for sample in samples if (value := getattr(sample, field_name)) is not None
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _difference(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None:
        return None
    return current - previous


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _window_sort_key(case_id: str) -> tuple[int, str]:
    return _parse_window_index(case_id), case_id


def _parse_window_index(case_id: str) -> int:
    raw = case_id.rsplit("-", 1)[-1]
    try:
        return int(raw)
    except ValueError:
        return 0


def _classify_error_text(text: str) -> tuple[FailureCategory | None, str]:
    normalized = text.lower()
    if "timeout" in normalized or "timed out" in normalized:
        return "timeout", "request_timeout"
    if any(
        token in normalized for token in ("out of memory", "outofmemory", "memoryerror", "cuda oom")
    ):
        return "oom", "oom"
    if any(
        token in normalized
        for token in (
            "connecterror",
            "connection refused",
            "remoteprotocolerror",
            "service unavailable",
        )
    ):
        return "external_service", "external_service_failure"
    return None, "unclassified"
