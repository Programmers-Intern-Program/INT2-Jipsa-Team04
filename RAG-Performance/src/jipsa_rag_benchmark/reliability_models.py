"""Issue #159의 장시간 안정성·실패 경계 측정 계획과 결과 모델을 정의한다.

이 모듈의 임계값은 서비스 설정이나 운영 제한값이 아니다. 장시간 측정 결과에서
자원 증가와 지연시간 악화를 사람이 검토하기 쉽게 표시하기 위한 보고 기준만 담는다.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, cast

ExternalServiceName = Literal["embedding", "qdrant"]
FailureCategory = Literal[
    "timeout",
    "oom",
    "external_service",
    "abnormal_termination",
    "passive_observation",
]
FailureOutcome = Literal[
    "expected_failure_observed",
    "unexpected_success",
    "probe_failed",
    "recovered",
    "recovery_failed",
    "observed",
]
ContainerLifecycleState = Literal[
    "running",
    "stopped",
    "exited",
    "created",
    "paused",
    "restarting",
    "dead",
    "absent",
    "unknown",
]

_ALLOWED_CONTAINER_STATES: Final[frozenset[str]] = frozenset(
    {
        "running",
        "stopped",
        "exited",
        "created",
        "paused",
        "restarting",
        "dead",
        "absent",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class SoakPlan:
    """고정 동시성으로 반복 검색하는 장시간 측정 계획."""

    enabled: bool
    duration_seconds: float
    window_seconds: float
    concurrency: int
    max_requests: int
    cooldown_seconds: float
    latency_p95_growth_report_percent: float
    rss_p95_growth_report_percent: float
    vram_p95_growth_report_percent: float
    thread_growth_report_count: int
    handle_growth_report_count: int


@dataclass(frozen=True, slots=True)
class TimeoutProbePlan:
    """클라이언트 제한시간이 의도대로 기록되는지 확인하는 안전한 지연 Probe."""

    enabled: bool
    delay_seconds: float
    client_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class OomProbePlan:
    """실제 Host/GPU 고갈 없이 별도 Worker의 MemoryError 기록 경로를 확인한다."""

    enabled: bool
    mode: Literal["controlled_worker"]
    bounded_allocation_mib: int


@dataclass(frozen=True, slots=True)
class ExternalServiceProbePlan:
    """TEI·Qdrant 중단과 복구 시 요청 실패 조건을 기록하는 계획."""

    enabled: bool
    services: tuple[ExternalServiceName, ...]
    request_timeout_seconds: float
    recovery_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class AbnormalTerminationProbePlan:
    """대상 RAG Process Tree 비정상 종료와 후속 정리 검증 계획."""

    enabled: bool
    restart_for_cleanup: bool


@dataclass(frozen=True, slots=True)
class FailureProbePlan:
    """Issue #159에서 요구한 실패 조건별 Probe 묶음."""

    timeout: TimeoutProbePlan
    oom: OomProbePlan
    external_services: ExternalServiceProbePlan
    abnormal_termination: AbnormalTerminationProbePlan


@dataclass(frozen=True, slots=True)
class IsolationPlan:
    """운영 데이터와 분리할 전용 ID·Collection·선택적 DB 설정."""

    qdrant_collection_prefix: str
    test_user_idx_min: int
    file_idx_min: int
    require_test_app_env: bool
    database_name_override: str | None


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    """실행 종료 후 확인할 데이터·프로세스·인프라 정리 계약."""

    restore_infrastructure: bool
    verify_database_rows_zero: bool
    verify_qdrant_collection_absent: bool
    verify_temp_files_zero: bool
    verify_target_process_stopped: bool


@dataclass(frozen=True, slots=True)
class ScopeGuardPlan:
    """측정 이슈에 포함되면 안 되는 운영 설정 Override 목록."""

    forbidden_environment_overrides: tuple[str, ...]
    protected_repository_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReliabilityPlan:
    """Issue #159의 나머지 9개 TODO를 한 번에 검증하는 전체 계획."""

    schema_version: int
    campaign_name: str
    soak: SoakPlan
    failure_probes: FailureProbePlan
    isolation: IsolationPlan
    cleanup: CleanupPlan
    scope_guard: ScopeGuardPlan

    def to_dict(self) -> dict[str, object]:
        """JSON 직렬화 가능한 계획 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class FailureEvent:
    """주입하거나 수동 감지한 단일 실패·복구 이벤트."""

    event_id: str
    category: FailureCategory
    condition: str
    injected: bool
    expected: bool
    started_at_utc: str
    completed_at_utc: str
    duration_ms: float
    outcome: FailureOutcome
    service: str | None = None
    request_operation: str | None = None
    error_type: str | None = None
    status_code: int | None = None
    recovered: bool | None = None
    safe_probe: bool = True
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        """CSV·JSON 공통 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class SoakWindowSummary:
    """장시간 실행의 한 시간축 Window에 대한 요청·자원 통계."""

    window_index: int
    case_id: str
    started_at_utc: str
    completed_at_utc: str
    elapsed_seconds: float
    request_count: int
    success_count: int
    error_count: int
    error_rate: float
    throughput_requests_per_second: float
    latency_mean_ms: float | None
    latency_max_ms: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_p99_ms: float | None
    target_rss_mean_bytes: float | None
    target_rss_max_bytes: float | None
    target_rss_p50_bytes: float | None
    target_rss_p95_bytes: float | None
    target_rss_p99_bytes: float | None
    target_vram_mean_bytes: float | None
    target_vram_max_bytes: float | None
    target_vram_p50_bytes: float | None
    target_vram_p95_bytes: float | None
    target_vram_p99_bytes: float | None
    target_thread_mean: float | None
    target_thread_max: float | None
    target_handle_mean: float | None
    target_handle_max: float | None
    resource_sample_count: int

    def to_dict(self) -> dict[str, object]:
        """CSV·JSON 공통 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class DriftAssessment:
    """첫 구간과 마지막 구간을 비교한 자원 누수·성능 저하 보고 결과."""

    window_count: int
    first_window_index: int | None
    last_window_index: int | None
    latency_p95_growth_percent: float | None
    target_rss_p95_growth_percent: float | None
    target_vram_p95_growth_percent: float | None
    thread_max_growth_count: float | None
    handle_max_growth_count: float | None
    latency_degradation_candidate: bool
    rss_leak_candidate: bool
    vram_leak_candidate: bool
    thread_leak_candidate: bool
    handle_leak_candidate: bool
    any_candidate: bool
    interpretation: str

    def to_dict(self) -> dict[str, object]:
        """JSON·CSV 공통 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class BoundaryResult:
    """정상 처리 최대 관측값과 최초 실패 관측값을 분리한 결과."""

    dimension: str
    operation: str
    unit: str
    normal_maximum_value: float | int | None
    first_failure_value: float | int | None
    first_failure_reason: str | None
    observed_upper_bound_censored: bool
    evidence_count: int

    def to_dict(self) -> dict[str, object]:
        """CSV·JSON 공통 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class ContainerStateRecord:
    """Docker Container의 캠페인 전·후 상태와 복원 결과."""

    service: str
    container_name: str
    initial_state: ContainerLifecycleState
    before_restore_state: ContainerLifecycleState
    final_state: ContainerLifecycleState
    restore_action: str
    restored: bool
    error_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        """CSV·JSON 공통 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


def load_reliability_plan(path: Path) -> ReliabilityPlan:
    """UTF-8 JSON 계획을 읽고 장시간·실패·격리·정리 계약을 검증한다."""

    raw = _object(json.loads(path.read_text(encoding="utf-8")), "reliability plan")
    schema_version = _positive_int(raw, "schema_version")
    if schema_version != 1:
        raise ValueError(f"Unsupported reliability schema_version: {schema_version}")

    soak_raw = _object(raw.get("soak"), "soak")
    failure_raw = _object(raw.get("failure_probes"), "failure_probes")
    timeout_raw = _object(failure_raw.get("timeout"), "failure_probes.timeout")
    oom_raw = _object(failure_raw.get("oom"), "failure_probes.oom")
    external_raw = _object(
        failure_raw.get("external_services"),
        "failure_probes.external_services",
    )
    abnormal_raw = _object(
        failure_raw.get("abnormal_termination"),
        "failure_probes.abnormal_termination",
    )
    isolation_raw = _object(raw.get("isolation"), "isolation")
    cleanup_raw = _object(raw.get("cleanup"), "cleanup")
    guard_raw = _object(raw.get("scope_guard"), "scope_guard")

    duration_seconds = _positive_float(soak_raw, "duration_seconds")
    window_seconds = _positive_float(soak_raw, "window_seconds")
    if window_seconds > duration_seconds:
        raise ValueError("soak.window_seconds must not exceed soak.duration_seconds.")

    timeout_delay = _positive_float(timeout_raw, "delay_seconds")
    timeout_client = _positive_float(timeout_raw, "client_timeout_seconds")
    if timeout_client >= timeout_delay:
        raise ValueError(
            "failure_probes.timeout.client_timeout_seconds must be shorter than delay_seconds."
        )

    mode = _non_empty_str(oom_raw, "mode")
    if mode != "controlled_worker":
        raise ValueError("failure_probes.oom.mode must be controlled_worker.")

    external_services_raw = _strings(external_raw, "services")
    invalid_services = set(external_services_raw) - {"embedding", "qdrant"}
    if invalid_services:
        raise ValueError(f"Unsupported external service probes: {sorted(invalid_services)}")
    if len(external_services_raw) != len(set(external_services_raw)):
        raise ValueError("failure_probes.external_services.services must be unique.")
    if _bool(external_raw, "enabled") and not external_services_raw:
        raise ValueError(
            "failure_probes.external_services.services must not be empty when enabled."
        )
    if _bool(abnormal_raw, "enabled") and not _bool(
        abnormal_raw,
        "restart_for_cleanup",
    ):
        raise ValueError(
            "abnormal termination requires restart_for_cleanup for deterministic cleanup."
        )
    if not _bool(isolation_raw, "require_test_app_env"):
        raise ValueError("isolation.require_test_app_env must be true.")

    collection_prefix = _non_empty_str(isolation_raw, "qdrant_collection_prefix")
    if not collection_prefix.startswith("rag_benchmark_issue_159_"):
        raise ValueError(
            "isolation.qdrant_collection_prefix must start with rag_benchmark_issue_159_."
        )
    if any(not (character.isalnum() or character in {"_", "-"}) for character in collection_prefix):
        raise ValueError("isolation.qdrant_collection_prefix contains an unsafe character.")

    database_name_override = _optional_non_empty_str(
        isolation_raw,
        "database_name_override",
    )
    if database_name_override is not None and any(
        not (character.isalnum() or character == "_") for character in database_name_override
    ):
        raise ValueError(
            "isolation.database_name_override must contain only letters, digits, or '_'."
        )

    forbidden = _strings(guard_raw, "forbidden_environment_overrides")
    protected_paths = _strings(guard_raw, "protected_repository_paths")
    if not forbidden or not protected_paths:
        raise ValueError("scope_guard lists must not be empty.")

    return ReliabilityPlan(
        schema_version=schema_version,
        campaign_name=_non_empty_str(raw, "campaign_name"),
        soak=SoakPlan(
            enabled=_bool(soak_raw, "enabled"),
            duration_seconds=duration_seconds,
            window_seconds=window_seconds,
            concurrency=_positive_int(soak_raw, "concurrency"),
            max_requests=_non_negative_int(soak_raw, "max_requests"),
            cooldown_seconds=_non_negative_float(soak_raw, "cooldown_seconds"),
            latency_p95_growth_report_percent=_non_negative_float(
                soak_raw,
                "latency_p95_growth_report_percent",
            ),
            rss_p95_growth_report_percent=_non_negative_float(
                soak_raw,
                "rss_p95_growth_report_percent",
            ),
            vram_p95_growth_report_percent=_non_negative_float(
                soak_raw,
                "vram_p95_growth_report_percent",
            ),
            thread_growth_report_count=_non_negative_int(
                soak_raw,
                "thread_growth_report_count",
            ),
            handle_growth_report_count=_non_negative_int(
                soak_raw,
                "handle_growth_report_count",
            ),
        ),
        failure_probes=FailureProbePlan(
            timeout=TimeoutProbePlan(
                enabled=_bool(timeout_raw, "enabled"),
                delay_seconds=timeout_delay,
                client_timeout_seconds=timeout_client,
            ),
            oom=OomProbePlan(
                enabled=_bool(oom_raw, "enabled"),
                mode="controlled_worker",
                bounded_allocation_mib=_bounded_int(
                    oom_raw,
                    "bounded_allocation_mib",
                    minimum=1,
                    maximum=256,
                ),
            ),
            external_services=ExternalServiceProbePlan(
                enabled=_bool(external_raw, "enabled"),
                services=cast(tuple[ExternalServiceName, ...], external_services_raw),
                request_timeout_seconds=_positive_float(
                    external_raw,
                    "request_timeout_seconds",
                ),
                recovery_timeout_seconds=_positive_float(
                    external_raw,
                    "recovery_timeout_seconds",
                ),
            ),
            abnormal_termination=AbnormalTerminationProbePlan(
                enabled=_bool(abnormal_raw, "enabled"),
                restart_for_cleanup=_bool(abnormal_raw, "restart_for_cleanup"),
            ),
        ),
        isolation=IsolationPlan(
            qdrant_collection_prefix=collection_prefix,
            test_user_idx_min=_positive_int(isolation_raw, "test_user_idx_min"),
            file_idx_min=_positive_int(isolation_raw, "file_idx_min"),
            require_test_app_env=_bool(isolation_raw, "require_test_app_env"),
            database_name_override=database_name_override,
        ),
        cleanup=CleanupPlan(
            restore_infrastructure=_bool(cleanup_raw, "restore_infrastructure"),
            verify_database_rows_zero=_bool(cleanup_raw, "verify_database_rows_zero"),
            verify_qdrant_collection_absent=_bool(
                cleanup_raw,
                "verify_qdrant_collection_absent",
            ),
            verify_temp_files_zero=_bool(cleanup_raw, "verify_temp_files_zero"),
            verify_target_process_stopped=_bool(
                cleanup_raw,
                "verify_target_process_stopped",
            ),
        ),
        scope_guard=ScopeGuardPlan(
            forbidden_environment_overrides=forbidden,
            protected_repository_paths=protected_paths,
        ),
    )


def normalize_container_state(value: str | None) -> ContainerLifecycleState:
    """Docker가 반환한 상태 문자열을 보고 모델의 제한된 값으로 정규화한다."""

    if value is None:
        return "unknown"
    normalized = value.strip().lower()
    if normalized in _ALLOWED_CONTAINER_STATES:
        return cast(ContainerLifecycleState, normalized)
    return "unknown"


def finite_percent_change(previous: float | None, current: float | None) -> float | None:
    """0 또는 결측값을 방어하면서 이전 값 대비 증감률을 계산한다."""

    if previous is None or current is None or previous == 0:
        return None
    result = ((current - previous) / previous) * 100.0
    return result if math.isfinite(result) else None


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object with string keys.")
    return cast(dict[str, object], value)


def _non_empty_str(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value.strip()


def _optional_non_empty_str(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be null or a non-empty string.")
    return value.strip()


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


def _bool(mapping: dict[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean.")
    return value


def _positive_int(mapping: dict[str, object], key: str) -> int:
    return _bounded_int(mapping, key, minimum=1, maximum=2**31 - 1)


def _non_negative_int(mapping: dict[str, object], key: str) -> int:
    return _bounded_int(mapping, key, minimum=0, maximum=2**31 - 1)


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


def _finite_float(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number.")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{key} must be finite.")
    return normalized
