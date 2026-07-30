"""Issue #159의 장시간 안정성·실패 경계·정리 검증 캠페인을 실행한다.

이 실행기는 기존 ``BenchmarkRunner``의 형식·OCR·동시성 기준선 측정을 재사용하고,
별도의 신뢰성 Session에서 다음 항목을 추가한다.

- 시간 Window 기반 장시간 반복 검색과 자원 Drift 분석
- 안전한 Timeout·MemoryError 기록 Probe
- TEI·Qdrant 중단/복구와 요청 실패 조건 기록
- 실제 RAG Target Process Tree 비정상 종료와 후속 Cleanup-only 검증
- 실행별 Qdrant Collection, 전용 Users_IDX·File_IDX와 Local DB Row 격리 확인
- 캠페인 전 인프라 상태 복원과 RAG Source/운영 설정 비변경 확인

성능 개선, 운영 제한값 변경, 모델 교체와 인덱스 튜닝은 수행하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

import httpx
import psutil

from jipsa_rag_benchmark.dotenv_loader import build_child_environment, read_dotenv
from jipsa_rag_benchmark.models import (
    BenchmarkOperation,
    BenchmarkPhase,
    BenchmarkPlan,
    GeneratedFixture,
    LevelSummary,
    RequestRecord,
    load_benchmark_plan,
    summarize_level,
)
from jipsa_rag_benchmark.reliability_analysis import (
    analyze_boundaries,
    assess_soak_drift,
    classify_passive_failures,
    summarize_soak_windows,
)
from jipsa_rag_benchmark.reliability_models import (
    BoundaryResult,
    ContainerLifecycleState,
    ContainerStateRecord,
    FailureCategory,
    FailureEvent,
    FailureOutcome,
    ReliabilityPlan,
    SoakWindowSummary,
    load_reliability_plan,
    normalize_container_state,
)
from jipsa_rag_benchmark.resource_sampler import capture_host_io_snapshot
from jipsa_rag_benchmark.runner import BenchmarkRunner

_CONTAINER_BY_SERVICE: Final[Mapping[str, str]] = {
    "embedding": "jipsa-embedding",
    "qdrant": "jipsa-qdrant",
}
_SAFE_COLLECTION_COMPONENT: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_-]+")


class InfrastructureStateManager:
    """캠페인 전 Qdrant·TEI 상태를 저장하고 종료 시 동일 상태로 복원한다."""

    def __init__(self, *, rag_root: Path, dotenv_path: Path, compose_path: Path) -> None:
        self._rag_root = rag_root
        self._dotenv_path = dotenv_path
        self._compose_path = compose_path
        self._initial: dict[str, ContainerLifecycleState] = {}

    def capture_initial(self) -> dict[str, ContainerLifecycleState]:
        """대상 Container별 최초 상태를 한 번만 기록한다."""

        if self._initial:
            raise RuntimeError("Initial infrastructure state was already captured.")
        self._initial = {
            service: self.inspect(container_name)
            for service, container_name in _CONTAINER_BY_SERVICE.items()
        }
        return dict(self._initial)

    def inspect(self, container_name: str) -> ContainerLifecycleState:
        """Docker inspect 결과를 running/stopped/absent 등 제한된 값으로 반환한다."""

        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
            cwd=self._rag_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30.0,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            lower = f"{result.stdout}\n{result.stderr}".lower()
            if "no such object" in lower or "no such container" in lower:
                return "absent"
            return "unknown"
        return normalize_container_state(result.stdout)

    def restore(self) -> tuple[ContainerStateRecord, ...]:
        """각 Service를 최초 running/stopped/absent 상태로 복원하고 재검증한다."""

        records: list[ContainerStateRecord] = []
        for service, container_name in _CONTAINER_BY_SERVICE.items():
            initial = self._initial.get(service, "unknown")
            before = self.inspect(container_name)
            action = "none"
            error_type: str | None = None
            try:
                if initial == "running":
                    action = "docker compose up -d"
                    self._compose("up", "-d", service)
                elif initial == "absent":
                    action = "docker compose rm -s -f"
                    self._compose("rm", "-s", "-f", service, allow_failure=True)
                elif initial in {
                    "stopped",
                    "exited",
                    "created",
                    "paused",
                    "restarting",
                    "dead",
                }:
                    action = "docker compose stop"
                    self._compose("stop", service, allow_failure=True)
                else:
                    action = "state unknown; no destructive restore"
            except Exception as error:
                error_type = type(error).__name__
            final = self.inspect(container_name)
            restored = _state_matches(initial, final)
            records.append(
                ContainerStateRecord(
                    service=service,
                    container_name=container_name,
                    initial_state=initial,
                    before_restore_state=before,
                    final_state=final,
                    restore_action=action,
                    restored=restored,
                    error_type=error_type,
                )
            )
        return tuple(records)

    def compose(self, *arguments: str, allow_failure: bool = False) -> None:
        """신뢰성 Probe가 사용할 Compose 명령을 동일 Env/Compose 파일로 실행한다."""

        self._compose(*arguments, allow_failure=allow_failure)

    def _compose(self, *arguments: str, allow_failure: bool = False) -> None:
        command = [
            "docker",
            "compose",
            "--env-file",
            str(self._dotenv_path),
            "--file",
            str(self._compose_path),
            *arguments,
        ]
        result = subprocess.run(
            command,
            cwd=self._rag_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=1800.0,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 and not allow_failure:
            raise RuntimeError(f"Docker Compose command failed with exit code {result.returncode}.")


class ScopeGuard:
    """RAG Source와 운영 설정 파일이 측정 중 바뀌지 않았는지 Hash로 검증한다."""

    def __init__(
        self,
        *,
        repository_root: Path,
        protected_paths: Sequence[str],
        forbidden_environment_overrides: Sequence[str],
    ) -> None:
        self._repository_root = repository_root.resolve()
        self._protected_paths = tuple(protected_paths)
        self._forbidden = frozenset(forbidden_environment_overrides)
        self._before: dict[str, str] = {}

    def capture_before(self) -> dict[str, str]:
        """보호 경로의 내용 Hash를 측정 시작 전에 기록한다."""

        if self._before:
            raise RuntimeError("ScopeGuard before snapshot was already captured.")
        self._before = self._fingerprints()
        return dict(self._before)

    def evaluate(self, *, runtime_overrides: Sequence[str]) -> dict[str, object]:
        """Hash·Git 변경 경로·Runtime Override를 종합해 비변경 여부를 반환한다."""

        after = self._fingerprints()
        changed_fingerprints = sorted(
            path for path in self._protected_paths if self._before.get(path) != after.get(path)
        )
        changed_files = _git_changed_files(self._repository_root)
        protected_git_changes = sorted(
            file_name
            for file_name in changed_files
            if any(
                file_name == protected or file_name.startswith(f"{protected.rstrip('/')}/")
                for protected in self._protected_paths
            )
        )
        forbidden_runtime = sorted(set(runtime_overrides) & self._forbidden)
        passed = not changed_fingerprints and not protected_git_changes and not forbidden_runtime
        return {
            "schema_version": 1,
            "protected_paths": list(self._protected_paths),
            "before_fingerprints": self._before,
            "after_fingerprints": after,
            "changed_protected_fingerprints": changed_fingerprints,
            "git_changed_files_against_main": changed_files,
            "protected_git_changes": protected_git_changes,
            "runtime_overrides": list(runtime_overrides),
            "forbidden_runtime_overrides": forbidden_runtime,
            "performance_optimization_or_limit_change_detected": bool(
                changed_fingerprints or protected_git_changes or forbidden_runtime
            ),
            "passed": passed,
        }

    def _fingerprints(self) -> dict[str, str]:
        return {
            relative: _hash_path(self._repository_root / relative)
            for relative in self._protected_paths
        }


class ReliabilityBenchmarkRunner(BenchmarkRunner):
    """한 RAG Target Session에서 Soak·Failure Probe·Abnormal Exit를 수행한다."""

    def __init__(
        self,
        *,
        plan: BenchmarkPlan,
        reliability_plan: ReliabilityPlan,
        rag_root: Path,
        run_id: str,
        run_directory: Path,
        target_host: str,
        target_port: int,
        collection_name: str,
        execution_command: str,
        infrastructure: InfrastructureStateManager,
        baseline_directory: Path | None,
    ) -> None:
        super().__init__(
            plan=plan,
            rag_root=rag_root,
            run_id=run_id,
            run_directory=run_directory,
            target_host=target_host,
            target_port=target_port,
            disable_answers=True,
            keep_test_data=False,
            keep_infrastructure_running=True,
            preserve_running_infrastructure=True,
        )
        self._reliability_plan = reliability_plan
        self._collection_name = collection_name
        self._execution_command = execution_command
        self._infrastructure_manager = infrastructure
        self._baseline_directory = (
            baseline_directory.resolve() if baseline_directory is not None else None
        )
        self._failure_events: list[FailureEvent] = []
        self._isolation_snapshots: list[dict[str, object]] = []
        self._cleanup_verification: dict[str, object] | None = None
        self._abnormal_termination_executed = False

    def run_reliability(self) -> Path:
        """신뢰성 Session을 실행하고 상세 Markdown 보고서 경로를 반환한다."""

        self._validate_reliability_preflight()
        self._validate_preflight()
        self._run_directory.mkdir(parents=True, exist_ok=False)
        self._prepare_fixtures_and_cases()
        _write_json(
            self._run_directory / "reliability_plan.resolved.json",
            self._reliability_plan.to_dict(),
        )
        (self._run_directory / "execution_command.txt").write_text(
            f"{self._execution_command}\n",
            encoding="utf-8",
        )

        environment: dict[str, object] = {}
        execution_error: BaseException | None = None
        self._sampler.start()
        try:
            self._start_infrastructure()
            with _benchmark_environment(
                collection_name=self._collection_name,
                collection_prefix=self._reliability_plan.isolation.qdrant_collection_prefix,
                database_name=self._reliability_plan.isolation.database_name_override,
            ):
                self._start_target_process()

            environment = self._collect_environment()
            environment["benchmark_qdrant_collection"] = self._collection_name
            environment["benchmark_database_name_override"] = (
                self._reliability_plan.isolation.database_name_override
            )
            environment["execution_command"] = self._execution_command
            _write_json(self._run_directory / "environment.json", environment)

            with httpx.Client(
                base_url=self._target_base_url,
                headers={"X-Internal-Token": self._ingest_token},
                timeout=httpx.Timeout(self._plan.request_timeout_seconds),
                trust_env=False,
            ) as client:
                self._client = client
                self._capture_isolation("target_ready")
                seed = self._cold_warm_cases["warm_text"]
                self._run_request_batch(
                    case_id="reliability-seed-ingest",
                    operation="ingest",
                    phase="warm",
                    concurrency=1,
                    tasks=(lambda: self._ingest_request(seed, phase="warm"),),
                )
                self._capture_isolation("seed_ingested")
                self._run_soak(seed)
                self._probe_timeout()
                self._probe_controlled_oom()
                self._probe_external_services(seed)
                self._capture_isolation("before_abnormal_termination")
                self._probe_abnormal_termination()
        except (Exception, KeyboardInterrupt) as error:
            execution_error = error
        finally:
            self._client = None
            try:
                self._cleanup_and_stop_target()
            except Exception as cleanup_error:
                if execution_error is None:
                    execution_error = cleanup_error
            try:
                self._sampler.stop()
            except Exception as sampler_error:
                if execution_error is None:
                    execution_error = sampler_error

        # Graceful 종료와 비정상 종료 모두 동일한 one-shot Target을 사용해 DB Row 0,
        # 전용 Collection 부재, 임시 파일 0을 실제 저장소에서 재검증한다. 외부 Service
        # Probe 복구가 부분 실패했더라도 Cleanup을 먼저 시도할 수 있도록 두 Service를 Ready로
        # 만든다. 캠페인 최상위 finally가 이후 최초 running/stopped/absent 상태로 복원한다.
        try:
            self._ensure_cleanup_infrastructure_ready()
            self._cleanup_verification = self._run_cleanup_only()
        except Exception as cleanup_error:
            if execution_error is None:
                execution_error = cleanup_error
            self._cleanup_verification = {
                "schema_version": 1,
                "success": False,
                "error_type": type(cleanup_error).__name__,
            }

        if not environment:
            environment = self._collect_environment(best_effort=True)
            environment["benchmark_qdrant_collection"] = self._collection_name
            environment["execution_command"] = self._execution_command
            _write_json(self._run_directory / "environment.json", environment)

        self._write_outputs(environment=environment, execution_error=execution_error)
        self._write_reliability_outputs(
            environment=environment,
            execution_error=execution_error,
        )
        if execution_error is not None:
            raise execution_error
        return self._run_directory / "report.md"

    def _validate_reliability_preflight(self) -> None:
        """전용 ID·Collection과 안전한 Fault Probe 범위를 실제 실행 전에 확인한다."""

        isolation = self._reliability_plan.isolation
        if self._plan.test_user_idx < isolation.test_user_idx_min:
            raise ValueError("Benchmark test_user_idx is outside the reliability isolation range.")
        if self._plan.file_idx_start < isolation.file_idx_min:
            raise ValueError("Benchmark file_idx_start is outside the reliability isolation range.")
        if not self._collection_name.startswith(isolation.qdrant_collection_prefix):
            raise ValueError("Reliability collection is outside the owned prefix.")
        if self._reliability_plan.failure_probes.oom.bounded_allocation_mib > 256:
            raise ValueError("Controlled OOM allocation exceeds the safety limit.")

    def _capture_isolation(self, label: str) -> None:
        """관리 API에서 비밀값 없는 DB·Collection·ID 범위와 현재 Row/Point 수를 기록한다."""

        started = time.perf_counter()
        with httpx.Client(
            base_url=self._target_base_url,
            headers={"X-Benchmark-Token": self._benchmark_token},
            timeout=60.0,
            trust_env=False,
        ) as client:
            response = client.get("/__benchmark__/isolation")
            response.raise_for_status()
            body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ValueError("Benchmark isolation response did not contain an object.")
        self._isolation_snapshots.append(
            {
                "label": label,
                "captured_at_utc": _utc_iso(time.time()),
                "duration_ms": (time.perf_counter() - started) * 1000.0,
                **cast(dict[str, object], data),
            }
        )

    def _run_soak(self, fixture: GeneratedFixture) -> None:
        """고정 동시성 검색을 Window 단위로 반복해 장시간 변화량을 관측한다."""

        soak = self._reliability_plan.soak
        if not soak.enabled:
            return
        payload = self._search_payload(fixture)
        campaign_started = time.monotonic()
        total_requests = 0
        window_index = 0

        while time.monotonic() - campaign_started < soak.duration_seconds:
            if soak.max_requests > 0 and total_requests >= soak.max_requests:
                break
            window_index += 1
            case_id = f"soak-window-{window_index:04d}"
            remaining_duration = soak.duration_seconds - (time.monotonic() - campaign_started)
            if remaining_duration <= 0:
                break
            window_duration = min(soak.window_seconds, remaining_duration)
            generated = self._run_single_soak_window(
                case_id=case_id,
                concurrency=soak.concurrency,
                duration_seconds=window_duration,
                payload=payload,
                total_requests=total_requests,
                max_requests=soak.max_requests,
            )
            total_requests += generated
            if generated == 0:
                break
            if self._target_process is not None and self._target_process.poll() is not None:
                self._failure_events.append(
                    _event_now(
                        event_id=f"soak-target-exit-{window_index}",
                        category="abnormal_termination",
                        condition="target_exited_during_soak",
                        injected=False,
                        expected=False,
                        outcome="observed",
                        error_type="TargetProcessExited",
                        recovered=False,
                        detail="장시간 반복 중 Target Wrapper Process 종료를 감지했습니다.",
                    )
                )
                break
            if soak.cooldown_seconds > 0:
                time.sleep(soak.cooldown_seconds)

    def _run_single_soak_window(
        self,
        *,
        case_id: str,
        concurrency: int,
        duration_seconds: float,
        payload: dict[str, object],
        total_requests: int,
        max_requests: int,
    ) -> int:
        """한 Window 동안 동시 요청 Wave를 반복하고 Level Summary·Host I/O를 저장한다."""

        self._sampler.set_context(
            case_id=case_id,
            operation="search",
            phase="concurrency",
            concurrency=concurrency,
        )
        host_start = capture_host_io_snapshot()
        started = time.perf_counter()
        deadline = time.monotonic() + duration_seconds
        records: list[RequestRecord] = []
        consecutive_failed_waves = 0
        try:
            with ThreadPoolExecutor(
                max_workers=concurrency,
                thread_name_prefix=f"jipsa-soak-{concurrency}",
            ) as executor:
                while time.monotonic() < deadline:
                    remaining_limit = (
                        max_requests - (total_requests + len(records))
                        if max_requests > 0
                        else concurrency
                    )
                    if max_requests > 0 and remaining_limit <= 0:
                        break
                    wave_size = min(concurrency, remaining_limit)
                    futures = [
                        executor.submit(
                            self._api_request,
                            case_id,
                            "search",
                            "concurrency",
                            concurrency,
                            payload,
                        )
                        for _ in range(wave_size)
                    ]
                    wave: list[RequestRecord] = []
                    for future in as_completed(futures):
                        record = future.result()
                        records.append(record)
                        wave.append(record)
                    if wave and all(not record.success for record in wave):
                        consecutive_failed_waves += 1
                    else:
                        consecutive_failed_waves = 0
                    # 외부 장애가 장시간 지속되는 경우 실패 요청을 무한히 생성하지 않는다.
                    if consecutive_failed_waves >= 3:
                        break
        finally:
            elapsed_seconds = max(time.perf_counter() - started, 1e-9)
            host_end = capture_host_io_snapshot()
            self._sampler.reset_context()

        if records:
            self._level_summaries.append(
                summarize_level(
                    tuple(records),
                    operation="search",
                    phase="concurrency",
                    concurrency=concurrency,
                    elapsed_seconds=elapsed_seconds,
                )
            )
        self._host_io_records.append(
            {
                "run_id": self._run_id,
                "case_id": case_id,
                "operation": "search",
                "phase": "concurrency",
                "concurrency": concurrency,
                "started_at_utc": host_start.timestamp_utc,
                "completed_at_utc": host_end.timestamp_utc,
                "elapsed_seconds": elapsed_seconds,
                **host_end.delta(host_start),
                "start_error": host_start.error,
                "end_error": host_end.error,
            }
        )
        return len(records)

    def _probe_timeout(self) -> None:
        """관리 API 지연보다 짧은 Client Timeout을 사용해 Timeout 분류를 기록한다."""

        plan = self._reliability_plan.failure_probes.timeout
        if not plan.enabled:
            return
        started_epoch = time.time()
        started = time.perf_counter()
        status_code: int | None = None
        error_type: str | None = None
        observed = False
        try:
            with httpx.Client(
                base_url=self._target_base_url,
                headers={"X-Benchmark-Token": self._benchmark_token},
                timeout=plan.client_timeout_seconds,
                trust_env=False,
            ) as client:
                response = client.post(
                    "/__benchmark__/fault/delay",
                    json={"delay_seconds": plan.delay_seconds},
                )
                status_code = response.status_code
        except httpx.TimeoutException as error:
            observed = True
            error_type = type(error).__name__
        except httpx.HTTPError as error:
            error_type = type(error).__name__

        # 지연 Coroutine이 끝난 뒤 Health를 확인해 Timeout이 서비스 장애로 번지지 않았는지 기록한다.
        time.sleep(max(plan.delay_seconds - plan.client_timeout_seconds, 0.0) + 0.2)
        recovered = self._management_health_succeeds(timeout_seconds=10.0)
        self._failure_events.append(
            FailureEvent(
                event_id="probe-timeout",
                category="timeout",
                condition="client_request_timeout",
                injected=True,
                expected=True,
                started_at_utc=_utc_iso(started_epoch),
                completed_at_utc=_utc_iso(time.time()),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                outcome=("expected_failure_observed" if observed else "unexpected_success"),
                request_operation="benchmark_delay",
                error_type=error_type,
                status_code=status_code,
                recovered=recovered,
                safe_probe=True,
                detail=(
                    "Loopback 관리 요청만 지연했으며 RAG 운영 Timeout 설정은 변경하지 않았습니다."
                ),
            )
        )

    def _probe_controlled_oom(self) -> None:
        """최대 256 MiB Worker가 MemoryError로 종료되는 안전한 OOM 기록 경로를 검증한다."""

        plan = self._reliability_plan.failure_probes.oom
        if not plan.enabled:
            return
        started_epoch = time.time()
        started = time.perf_counter()
        status_code: int | None = None
        error_type: str | None = None
        observed = False
        try:
            with httpx.Client(
                base_url=self._target_base_url,
                headers={"X-Benchmark-Token": self._benchmark_token},
                timeout=60.0,
                trust_env=False,
            ) as client:
                response = client.post(
                    "/__benchmark__/fault/controlled-oom",
                    json={"bounded_allocation_mib": plan.bounded_allocation_mib},
                )
                status_code = response.status_code
                body = response.json()
                data = body.get("data") if isinstance(body, dict) else None
                observed = (
                    isinstance(data, dict)
                    and data.get("oom_observed") is True
                    and data.get("safe_probe") is True
                )
        except (httpx.HTTPError, ValueError) as error:
            error_type = type(error).__name__

        recovered = self._management_health_succeeds(timeout_seconds=10.0)
        self._failure_events.append(
            FailureEvent(
                event_id="probe-controlled-oom",
                category="oom",
                condition="controlled_worker_memory_error",
                injected=True,
                expected=True,
                started_at_utc=_utc_iso(started_epoch),
                completed_at_utc=_utc_iso(time.time()),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                outcome=("expected_failure_observed" if observed else "probe_failed"),
                request_operation="controlled_oom",
                error_type=error_type,
                status_code=status_code,
                recovered=recovered,
                safe_probe=True,
                detail=(
                    f"별도 Worker가 최대 {plan.bounded_allocation_mib} MiB만 할당한 뒤 "
                    "의도적으로 MemoryError를 반환했습니다. 실제 CUDA/Host OOM은 주입하지 않습니다."
                ),
            )
        )

    def _probe_external_services(self, fixture: GeneratedFixture) -> None:
        """TEI·Qdrant를 한 번씩 정지하고 실패·복구 요청을 기록한다."""

        plan = self._reliability_plan.failure_probes.external_services
        if not plan.enabled:
            return
        payload = self._search_payload(fixture)
        for service in plan.services:
            started_epoch = time.time()
            started = time.perf_counter()
            failure_status: int | None = None
            failure_error: str | None = None
            expected_failure = False
            probe_started = False
            recovered = False
            try:
                self._infrastructure_manager.compose("stop", service)
                probe_started = True
                time.sleep(1.0)
                success, failure_status, failure_error = self._probe_search(
                    payload,
                    timeout_seconds=plan.request_timeout_seconds,
                )
                expected_failure = not success
            except Exception as probe_error:
                failure_error = type(probe_error).__name__
            finally:
                try:
                    self._infrastructure_manager.compose("up", "-d", service)
                    self._wait_external_service(
                        service,
                        timeout_seconds=plan.recovery_timeout_seconds,
                    )
                    success, _, recovery_error = self._probe_search(
                        payload,
                        timeout_seconds=plan.request_timeout_seconds,
                    )
                    recovered = success
                    if not success and failure_error is None:
                        failure_error = recovery_error
                except Exception as recovery_error:
                    if failure_error is None:
                        failure_error = type(recovery_error).__name__
                    recovered = False

            if not probe_started:
                outcome: FailureOutcome = "probe_failed"
            elif expected_failure:
                outcome = "expected_failure_observed"
            else:
                outcome = "unexpected_success"
            self._failure_events.append(
                FailureEvent(
                    event_id=f"probe-external-{service}",
                    category="external_service",
                    condition=f"{service}_unavailable",
                    injected=probe_started,
                    expected=True,
                    started_at_utc=_utc_iso(started_epoch),
                    completed_at_utc=_utc_iso(time.time()),
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                    service=service,
                    request_operation="search",
                    error_type=failure_error,
                    status_code=failure_status,
                    recovered=recovered,
                    safe_probe=True,
                    detail=(
                        "Docker Compose Service를 한 번 정지한 뒤 즉시 재기동하고 Ready/검색을 "
                        "재검증했습니다. 캠페인 종료 시 최초 인프라 상태를 다시 복원합니다."
                    ),
                )
            )

    def _probe_abnormal_termination(self) -> None:
        """실제 RAG Target Process Tree를 종료하고 Health 불가 상태를 기록한다."""

        plan = self._reliability_plan.failure_probes.abnormal_termination
        if not plan.enabled:
            return
        started_epoch = time.time()
        started = time.perf_counter()
        error_type: str | None = None
        observed = False
        try:
            target_pid = self._target_pid_from_health()
            _terminate_process_tree(target_pid)
            observed = not self._management_health_succeeds(timeout_seconds=5.0)
            self._abnormal_termination_executed = True
        except Exception as error:
            error_type = type(error).__name__

        self._failure_events.append(
            FailureEvent(
                event_id="probe-abnormal-termination",
                category="abnormal_termination",
                condition="target_process_tree_terminated",
                injected=True,
                expected=True,
                started_at_utc=_utc_iso(started_epoch),
                completed_at_utc=_utc_iso(time.time()),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                outcome=("expected_failure_observed" if observed else "probe_failed"),
                request_operation="health",
                error_type=error_type,
                recovered=None,
                safe_probe=True,
                detail=(
                    "성능 측정 전용 Target Process Tree만 종료했습니다. 이후 cleanup-only "
                    "프로세스로 DB·Collection·임시 파일 정리를 별도 검증합니다."
                ),
            )
        )

    def _probe_search(
        self,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> tuple[bool, int | None, str | None]:
        """외부 서비스 Probe용 짧은 제한시간 검색을 수행하고 안전한 결과만 반환한다."""

        try:
            with httpx.Client(
                base_url=self._target_base_url,
                headers={"X-Internal-Token": self._ingest_token},
                timeout=timeout_seconds,
                trust_env=False,
            ) as client:
                response = client.post("/api/v1/chunks/search", json=payload)
                body = response.json()
                success = (
                    response.status_code == 200
                    and isinstance(body, dict)
                    and body.get("success") is True
                )
                return success, response.status_code, None if success else "ApiFailure"
        except Exception as error:
            return False, None, type(error).__name__

    def _wait_external_service(self, service: str, *, timeout_seconds: float) -> None:
        if service == "qdrant":
            qdrant_url = (
                self._environment.get("JIPSA_RAG_QDRANT_URL") or "http://127.0.0.1:6333"
            ).rstrip("/")
            self._wait_http_ready(f"{qdrant_url}/readyz", timeout_seconds=timeout_seconds)
            return
        embedding_url = (
            self._environment.get("JIPSA_RAG_EMBEDDING_BASE_URL") or "http://127.0.0.1:18081"
        ).rstrip("/")
        self._wait_any_http_ready(
            (f"{embedding_url}/health", f"{embedding_url}/info"),
            timeout_seconds=timeout_seconds,
        )

    def _target_pid_from_health(self) -> int:
        with httpx.Client(
            base_url=self._target_base_url,
            headers={"X-Benchmark-Token": self._benchmark_token},
            timeout=10.0,
            trust_env=False,
        ) as client:
            response = client.get("/__benchmark__/health")
            response.raise_for_status()
            body = response.json()
        pid = body.get("pid") if isinstance(body, dict) else None
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Benchmark health did not return a valid PID.")
        return pid

    def _management_health_succeeds(self, *, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                self._target_pid_from_health()
                return True
            except Exception:
                time.sleep(0.25)
        return False

    def _ensure_cleanup_infrastructure_ready(self) -> None:
        """Cleanup-only 실행 전에 Qdrant·TEI를 일시적으로 Ready 상태로 보장한다."""

        self._infrastructure_manager.compose("up", "-d", "qdrant", "embedding")
        recovery_timeout = (
            self._reliability_plan.failure_probes.external_services.recovery_timeout_seconds
        )
        self._wait_external_service("qdrant", timeout_seconds=recovery_timeout)
        self._wait_external_service("embedding", timeout_seconds=recovery_timeout)

    def _run_cleanup_only(self) -> dict[str, object]:
        """RAG uv 환경의 one-shot Target으로 실제 DB·Qdrant·Temp 정리를 재검증한다."""

        verification_path = self._run_directory / "cleanup_verification.json"
        fixture_manifest = self._run_directory / "all_owned_fixtures.json"
        download_temp = self._run_directory / "download-temp"
        overrides: dict[str, str | None] = {
            "JIPSA_RAG_APP_ENV": "test",
            "JIPSA_RAG_LOG_FORMAT": "json",
            "JIPSA_RAG_LOG_LEVEL": "INFO",
            "JIPSA_RAG_DEBUG": "false",
            "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION": self._collection_name,
            "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION_PREFIX": (
                self._reliability_plan.isolation.qdrant_collection_prefix
            ),
            "JIPSA_RAG_BENCHMARK_DATABASE_NAME": (
                self._reliability_plan.isolation.database_name_override
            ),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        environment = build_child_environment(
            read_dotenv(self._dotenv_path),
            overrides=overrides,
        )
        command = [
            "uv",
            "run",
            "python",
            str(self._target_script),
            "--rag-root",
            str(self._rag_root),
            "--fixture-manifest",
            str(fixture_manifest),
            "--host",
            self._target_host,
            "--port",
            str(self._target_port),
            "--benchmark-token",
            self._benchmark_token,
            "--test-user-idx",
            str(self._plan.test_user_idx),
            "--download-temp-directory",
            str(download_temp),
            "--cleanup-only",
            "--verification-output",
            str(verification_path),
        ]
        result = subprocess.run(
            command,
            cwd=self._rag_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=600.0,
            encoding="utf-8",
            errors="replace",
        )
        if not verification_path.is_file():
            raise RuntimeError(
                f"Cleanup-only verification file was not created. Exit code: {result.returncode}"
            )
        payload = json.loads(verification_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Cleanup verification root must be an object.")
        payload["cleanup_process_exit_code"] = result.returncode
        payload["target_process_stopped"] = not self._management_health_succeeds(
            timeout_seconds=1.0
        )
        _write_json(verification_path, payload)
        return cast(dict[str, object], payload)

    def _write_reliability_outputs(
        self,
        *,
        environment: Mapping[str, object],
        execution_error: BaseException | None,
    ) -> None:
        """기존 Benchmark 원본 결과 위에 Issue #159의 나머지 분석 파일을 생성한다."""

        # 기존 Runner의 report 파일은 원본 기준선 보고서로 보존한다.
        standard_json = self._run_directory / "report.json"
        standard_markdown = self._run_directory / "report.md"
        if standard_json.is_file():
            standard_json.replace(self._run_directory / "benchmark_report.json")
        if standard_markdown.is_file():
            standard_markdown.replace(self._run_directory / "benchmark_report.md")

        records = tuple(self._request_records)
        samples = self._sampler.samples
        windows = summarize_soak_windows(records, samples)
        drift = assess_soak_drift(windows, plan=self._reliability_plan)
        baseline_records = _load_request_records_csv(
            self._baseline_directory / "request_records.csv"
            if self._baseline_directory is not None
            else None
        )
        baseline_levels = _load_level_summaries_csv(
            self._baseline_directory / "level_summaries.csv"
            if self._baseline_directory is not None
            else None
        )
        analysis_records = baseline_records + records
        analysis_levels = baseline_levels + tuple(self._level_summaries)
        boundaries = analyze_boundaries(
            analysis_records,
            analysis_levels,
            max_error_rate=self._plan.saturation.max_error_rate,
        )
        target_log_path = self._run_directory / "target.log"
        target_log_text = (
            target_log_path.read_text(encoding="utf-8", errors="replace")
            if target_log_path.is_file()
            else ""
        )
        passive = classify_passive_failures(
            records,
            target_log_text=target_log_text,
            timestamp_utc=_utc_iso(time.time()),
        )
        failure_events = tuple(self._failure_events) + passive

        _write_csv(
            self._run_directory / "soak_windows.csv",
            [value.to_dict() for value in windows],
        )
        _write_json(
            self._run_directory / "soak_windows.json",
            {"schema_version": 1, "windows": [value.to_dict() for value in windows]},
        )
        _write_csv(self._run_directory / "soak_drift.csv", [drift.to_dict()])
        _write_json(self._run_directory / "soak_drift.json", drift.to_dict())
        _write_csv(
            self._run_directory / "failure_events.csv",
            [value.to_dict() for value in failure_events],
        )
        _write_json(
            self._run_directory / "failure_events.json",
            {
                "schema_version": 1,
                "events": [value.to_dict() for value in failure_events],
            },
        )
        _write_csv(
            self._run_directory / "boundary_analysis.csv",
            [value.to_dict() for value in boundaries],
        )
        _write_json(
            self._run_directory / "boundary_analysis.json",
            {
                "schema_version": 1,
                "boundaries": [value.to_dict() for value in boundaries],
            },
        )
        _write_csv(
            self._run_directory / "isolation_verification.csv",
            self._isolation_snapshots,
        )
        _write_json(
            self._run_directory / "isolation_verification.json",
            {
                "schema_version": 1,
                "snapshots": self._isolation_snapshots,
            },
        )

        cleanup_checks = _cleanup_check_rows(self._cleanup_verification)
        _write_csv(self._run_directory / "cleanup_checks.csv", cleanup_checks)

        summary: dict[str, object] = {
            "schema_version": 1,
            "run_id": self._run_id,
            "campaign_name": self._reliability_plan.campaign_name,
            "rag_git_commit_sha": environment.get("rag_git_commit_sha"),
            "qdrant_collection": self._collection_name,
            "database_name_override": self._reliability_plan.isolation.database_name_override,
            "soak_window_count": len(windows),
            "soak_request_count": sum(value.request_count for value in windows),
            "soak_drift": drift.to_dict(),
            "failure_event_count": len(failure_events),
            "failure_events": [value.to_dict() for value in failure_events],
            "boundary_results": [value.to_dict() for value in boundaries],
            "boundary_includes_baseline": bool(baseline_records or baseline_levels),
            "isolation_snapshots": self._isolation_snapshots,
            "cleanup_verification": self._cleanup_verification,
            "abnormal_termination_executed": self._abnormal_termination_executed,
            "execution_error_type": type(execution_error).__name__ if execution_error else None,
            "scope_statement": {
                "performance_optimization": False,
                "operational_limit_change": False,
                "model_change": False,
                "index_tuning": False,
            },
        }
        _write_json(self._run_directory / "report.json", summary)
        (self._run_directory / "report.md").write_text(
            _build_reliability_report(
                environment=environment,
                summary=summary,
                windows=windows,
                drift=drift.to_dict(),
                failure_events=failure_events,
                boundaries=boundaries,
                cleanup_checks=cleanup_checks,
                execution_command=self._execution_command,
            ),
            encoding="utf-8",
        )


class ReliabilityCampaign:
    """기준선·신뢰성 Session·인프라 복원·Scope Guard를 하나의 Run으로 묶는다."""

    def __init__(
        self,
        *,
        benchmark_plan: BenchmarkPlan,
        reliability_plan: ReliabilityPlan,
        rag_root: Path,
        campaign_directory: Path,
        target_host: str,
        target_port: int,
        disable_answers: bool,
        skip_baseline: bool,
        execution_command: str,
    ) -> None:
        self._benchmark_plan = benchmark_plan
        self._reliability_plan = reliability_plan
        self._rag_root = rag_root.resolve()
        self._campaign_directory = campaign_directory.resolve()
        self._target_host = target_host
        self._target_port = target_port
        self._disable_answers = disable_answers
        self._skip_baseline = skip_baseline
        self._execution_command = execution_command
        self._repository_root = self._rag_root.parent
        self._infrastructure = InfrastructureStateManager(
            rag_root=self._rag_root,
            dotenv_path=self._rag_root / ".env.local",
            compose_path=self._rag_root / "infra/qdrant/compose.yaml",
        )
        self._scope_guard = ScopeGuard(
            repository_root=self._repository_root,
            protected_paths=reliability_plan.scope_guard.protected_repository_paths,
            forbidden_environment_overrides=(
                reliability_plan.scope_guard.forbidden_environment_overrides
            ),
        )

    def run(self) -> Path:
        """전체 캠페인을 실행하고 최상위 Markdown 보고서를 반환한다."""

        self._campaign_directory.mkdir(parents=True, exist_ok=False)
        (self._campaign_directory / "execution_command.txt").write_text(
            f"{self._execution_command}\n",
            encoding="utf-8",
        )
        _write_json(
            self._campaign_directory / "reliability_plan.resolved.json",
            self._reliability_plan.to_dict(),
        )
        self._scope_guard.capture_before()
        initial_states = self._infrastructure.capture_initial()
        _write_json(
            self._campaign_directory / "infrastructure_initial_state.json",
            initial_states,
        )

        baseline_report: Path | None = None
        reliability_report: Path | None = None
        baseline_cleanup: dict[str, object] | None = None
        execution_error: BaseException | None = None
        infrastructure_records: tuple[ContainerStateRecord, ...] = ()
        try:
            if not self._skip_baseline:
                baseline_collection = _make_collection_name(
                    self._reliability_plan.isolation.qdrant_collection_prefix,
                    f"{self._campaign_directory.name}-baseline",
                )
                baseline = BenchmarkRunner(
                    plan=self._benchmark_plan,
                    rag_root=self._rag_root,
                    run_id=f"{self._campaign_directory.name}-baseline",
                    run_directory=self._campaign_directory / "baseline",
                    target_host=self._target_host,
                    target_port=self._target_port,
                    disable_answers=self._disable_answers,
                    keep_test_data=False,
                    keep_infrastructure_running=True,
                    preserve_running_infrastructure=True,
                )
                with _benchmark_environment(
                    collection_name=baseline_collection,
                    collection_prefix=(self._reliability_plan.isolation.qdrant_collection_prefix),
                    database_name=self._reliability_plan.isolation.database_name_override,
                ):
                    baseline_report = baseline.run()
                baseline_cleanup = _run_cleanup_only_for_runner(
                    runner=baseline,
                    collection_name=baseline_collection,
                    reliability_plan=self._reliability_plan,
                )

            reliability_collection = _make_collection_name(
                self._reliability_plan.isolation.qdrant_collection_prefix,
                f"{self._campaign_directory.name}-reliability",
            )
            reliability = ReliabilityBenchmarkRunner(
                plan=self._benchmark_plan,
                reliability_plan=self._reliability_plan,
                rag_root=self._rag_root,
                run_id=f"{self._campaign_directory.name}-reliability",
                run_directory=self._campaign_directory / "reliability",
                target_host=self._target_host,
                target_port=self._target_port,
                collection_name=reliability_collection,
                execution_command=self._execution_command,
                infrastructure=self._infrastructure,
                baseline_directory=(
                    self._campaign_directory / "baseline" if baseline_report is not None else None
                ),
            )
            reliability_report = reliability.run_reliability()
        except (Exception, KeyboardInterrupt) as error:
            execution_error = error
        finally:
            if self._reliability_plan.cleanup.restore_infrastructure:
                infrastructure_records = self._infrastructure.restore()
            else:
                infrastructure_records = tuple(
                    ContainerStateRecord(
                        service=service,
                        container_name=container,
                        initial_state=state,
                        before_restore_state=self._infrastructure.inspect(container),
                        final_state=self._infrastructure.inspect(container),
                        restore_action="restore disabled by plan",
                        restored=False,
                    )
                    for service, container in _CONTAINER_BY_SERVICE.items()
                    for state in (initial_states[service],)
                )

        runtime_overrides = [
            "JIPSA_RAG_APP_ENV",
            "JIPSA_RAG_LOG_FORMAT",
            "JIPSA_RAG_LOG_LEVEL",
            "JIPSA_RAG_DEBUG",
            "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION",
            "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION_PREFIX",
        ]
        if self._reliability_plan.isolation.database_name_override is not None:
            runtime_overrides.append("JIPSA_RAG_BENCHMARK_DATABASE_NAME")
        scope_guard = self._scope_guard.evaluate(runtime_overrides=runtime_overrides)

        _write_csv(
            self._campaign_directory / "infrastructure_state.csv",
            [record.to_dict() for record in infrastructure_records],
        )
        _write_json(
            self._campaign_directory / "infrastructure_state.json",
            {
                "schema_version": 1,
                "records": [record.to_dict() for record in infrastructure_records],
            },
        )
        _write_json(self._campaign_directory / "scope_guard.json", scope_guard)
        if baseline_cleanup is not None:
            _write_json(
                self._campaign_directory / "baseline_cleanup_verification.json",
                baseline_cleanup,
            )

        campaign_summary: dict[str, object] = {
            "schema_version": 1,
            "campaign_name": self._reliability_plan.campaign_name,
            "campaign_directory": str(self._campaign_directory),
            "baseline_executed": not self._skip_baseline,
            "baseline_report": _relative_or_none(
                baseline_report,
                self._campaign_directory,
            ),
            "reliability_report": _relative_or_none(
                reliability_report,
                self._campaign_directory,
            ),
            "baseline_cleanup_verification": baseline_cleanup,
            "infrastructure_restored": all(record.restored for record in infrastructure_records),
            "infrastructure_records": [record.to_dict() for record in infrastructure_records],
            "scope_guard": scope_guard,
            "execution_error_type": type(execution_error).__name__ if execution_error else None,
            "completed_at_utc": _utc_iso(time.time()),
        }
        _write_json(self._campaign_directory / "report.json", campaign_summary)
        (self._campaign_directory / "report.md").write_text(
            _build_campaign_report(
                campaign_summary=campaign_summary,
                execution_command=self._execution_command,
            ),
            encoding="utf-8",
        )
        if execution_error is not None:
            raise execution_error
        return self._campaign_directory / "report.md"


def _load_request_records_csv(path: Path | None) -> tuple[RequestRecord, ...]:
    """기준선 CSV를 Boundary 분석용 RequestRecord로 안전하게 복원한다."""

    if path is None or not path.is_file():
        return ()
    records: list[RequestRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            operation = cast(BenchmarkOperation, _required_csv(row, "operation"))
            phase = cast(BenchmarkPhase, _required_csv(row, "phase"))
            records.append(
                RequestRecord(
                    run_id=_required_csv(row, "run_id"),
                    request_id=_required_csv(row, "request_id"),
                    case_id=_required_csv(row, "case_id"),
                    operation=operation,
                    phase=phase,
                    concurrency=_required_csv_int(row, "concurrency"),
                    request_index=_required_csv_int(row, "request_index"),
                    started_at_utc=_required_csv(row, "started_at_utc"),
                    started_epoch_seconds=_required_csv_float(
                        row,
                        "started_epoch_seconds",
                    ),
                    completed_epoch_seconds=_required_csv_float(
                        row,
                        "completed_epoch_seconds",
                    ),
                    duration_ms=_required_csv_float(row, "duration_ms"),
                    status_code=_optional_csv_int(row.get("status_code")),
                    success=_csv_bool(row.get("success")),
                    request_bytes=_required_csv_int(row, "request_bytes"),
                    response_bytes=_required_csv_int(row, "response_bytes"),
                    file_idx=_optional_csv_int(row.get("file_idx")),
                    file_type=_optional_csv_text(row.get("file_type")),
                    profile_name=_optional_csv_text(row.get("profile_name")),
                    content_origin=_optional_csv_text(row.get("content_origin")),
                    fixture_size_bytes=_optional_csv_int(row.get("fixture_size_bytes")),
                    declared_text_units=_optional_csv_int(row.get("declared_text_units")),
                    declared_image_count=_optional_csv_int(row.get("declared_image_count")),
                    chunk_count=_optional_csv_int(row.get("chunk_count")),
                    input_tokens=_optional_csv_int(row.get("input_tokens")),
                    output_tokens=_optional_csv_int(row.get("output_tokens")),
                    error_type=_optional_csv_text(row.get("error_type")),
                    error_message=_optional_csv_text(row.get("error_message")),
                )
            )
    return tuple(records)


def _load_level_summaries_csv(path: Path | None) -> tuple[LevelSummary, ...]:
    """기준선 CSV를 동시성 정상 최대·최초 실패 분석용 모델로 복원한다."""

    if path is None or not path.is_file():
        return ()
    values: list[LevelSummary] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            values.append(
                LevelSummary(
                    operation=cast(
                        BenchmarkOperation,
                        _required_csv(row, "operation"),
                    ),
                    phase=cast(BenchmarkPhase, _required_csv(row, "phase")),
                    concurrency=_required_csv_int(row, "concurrency"),
                    request_count=_required_csv_int(row, "request_count"),
                    success_count=_required_csv_int(row, "success_count"),
                    error_count=_required_csv_int(row, "error_count"),
                    error_rate=_required_csv_float(row, "error_rate"),
                    elapsed_seconds=_required_csv_float(row, "elapsed_seconds"),
                    throughput_requests_per_second=_required_csv_float(
                        row,
                        "throughput_requests_per_second",
                    ),
                    mean_ms=_optional_csv_float(row.get("mean_ms")),
                    p50_ms=_optional_csv_float(row.get("p50_ms")),
                    p95_ms=_optional_csv_float(row.get("p95_ms")),
                    p99_ms=_optional_csv_float(row.get("p99_ms")),
                    min_ms=_optional_csv_float(row.get("min_ms")),
                    max_ms=_optional_csv_float(row.get("max_ms")),
                    total_request_bytes=_required_csv_int(
                        row,
                        "total_request_bytes",
                    ),
                    total_response_bytes=_required_csv_int(
                        row,
                        "total_response_bytes",
                    ),
                    total_input_tokens=_required_csv_int(
                        row,
                        "total_input_tokens",
                    ),
                    total_output_tokens=_required_csv_int(
                        row,
                        "total_output_tokens",
                    ),
                )
            )
    return tuple(values)


def _required_csv(row: Mapping[str, str | None], key: str) -> str:
    value = row.get(key)
    if value is None or not value.strip():
        raise ValueError(f"CSV field is required: {key}")
    return value.strip()


def _required_csv_int(row: Mapping[str, str | None], key: str) -> int:
    return int(_required_csv(row, key))


def _required_csv_float(row: Mapping[str, str | None], key: str) -> float:
    return float(_required_csv(row, key))


def _optional_csv_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def _optional_csv_int(value: str | None) -> int | None:
    normalized = _optional_csv_text(value)
    return int(normalized) if normalized is not None else None


def _optional_csv_float(value: str | None) -> float | None:
    normalized = _optional_csv_text(value)
    return float(normalized) if normalized is not None else None


def _csv_bool(value: str | None) -> bool:
    normalized = _optional_csv_text(value)
    if normalized is None:
        raise ValueError("CSV boolean field is required.")
    if normalized.lower() == "true":
        return True
    if normalized.lower() == "false":
        return False
    raise ValueError(f"Invalid CSV boolean value: {normalized}")


def _run_cleanup_only_for_runner(
    *,
    runner: BenchmarkRunner,
    collection_name: str,
    reliability_plan: ReliabilityPlan,
) -> dict[str, object]:
    """기존 기준선 Runner가 종료한 뒤 동일 Collection·ID 범위를 one-shot으로 검증한다."""

    verification_path = runner._run_directory / "cleanup_verification.json"
    overrides: dict[str, str | None] = {
        "JIPSA_RAG_APP_ENV": "test",
        "JIPSA_RAG_LOG_FORMAT": "json",
        "JIPSA_RAG_LOG_LEVEL": "INFO",
        "JIPSA_RAG_DEBUG": "false",
        "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION": collection_name,
        "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION_PREFIX": (
            reliability_plan.isolation.qdrant_collection_prefix
        ),
        "JIPSA_RAG_BENCHMARK_DATABASE_NAME": (reliability_plan.isolation.database_name_override),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    environment = build_child_environment(
        read_dotenv(runner._dotenv_path),
        overrides=overrides,
    )
    command = [
        "uv",
        "run",
        "python",
        str(runner._target_script),
        "--rag-root",
        str(runner._rag_root),
        "--fixture-manifest",
        str(runner._run_directory / "all_owned_fixtures.json"),
        "--host",
        runner._target_host,
        "--port",
        str(runner._target_port),
        "--benchmark-token",
        runner._benchmark_token,
        "--test-user-idx",
        str(runner._plan.test_user_idx),
        "--download-temp-directory",
        str(runner._run_directory / "download-temp"),
        "--cleanup-only",
        "--verification-output",
        str(verification_path),
    ]
    result = subprocess.run(
        command,
        cwd=runner._rag_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=600.0,
        encoding="utf-8",
        errors="replace",
    )
    if not verification_path.is_file():
        raise RuntimeError("Baseline cleanup-only verification file was not created.")
    payload = json.loads(verification_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Baseline cleanup verification root must be an object.")
    payload["cleanup_process_exit_code"] = result.returncode
    _write_json(verification_path, payload)
    return cast(dict[str, object], payload)


@contextmanager
def _benchmark_environment(
    *,
    collection_name: str,
    collection_prefix: str,
    database_name: str | None,
) -> Iterator[None]:
    """Target 자식 프로세스에만 전달할 격리 설정을 임시 환경으로 주입한다."""

    values: dict[str, str | None] = {
        "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION": collection_name,
        "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION_PREFIX": collection_prefix,
        "JIPSA_RAG_BENCHMARK_DATABASE_NAME": database_name,
    }
    previous = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _terminate_process_tree(pid: int) -> None:
    """측정 전용 Target PID와 모든 자식만 종료하고 다른 Local RAG Process는 건드리지 않는다."""

    root = psutil.Process(pid)
    processes = [*root.children(recursive=True), root]
    for process in reversed(processes):
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(processes, timeout=5.0)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs(alive, timeout=5.0)


def _make_collection_name(prefix: str, value: str) -> str:
    component = _SAFE_COLLECTION_COMPONENT.sub("_", value).strip("_-").lower()
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    maximum_component = max(1, 255 - len(prefix) - len(digest) - 1)
    return f"{prefix}{component[:maximum_component]}_{digest}"


def _state_matches(
    initial: ContainerLifecycleState,
    final: ContainerLifecycleState,
) -> bool:
    if initial == "running":
        return final == "running"
    if initial == "absent":
        return final == "absent"
    if initial == "unknown":
        return False
    # 최초에 존재하던 비실행 Container는 종료 상태 계열이면 복원된 것으로 본다.
    # Container 자체가 사라진 ``absent``는 복원 성공으로 오인하지 않는다.
    return final in {
        "stopped",
        "exited",
        "created",
        "paused",
        "restarting",
        "dead",
    }


def _hash_path(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        digest.update(b"<missing>")
        return digest.hexdigest()
    if path.is_file():
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for child in sorted(value for value in path.rglob("*") if value.is_file()):
        relative = child.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(child.read_bytes())
    return digest.hexdigest()


def _git_changed_files(repository_root: Path) -> list[str]:
    """main 대비 Commit, staged, unstaged와 untracked 변경 경로를 합산한다."""

    commands = (
        ["git", "diff", "--name-only", "main...HEAD"],
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    changed: set[str] = set()
    for arguments in commands:
        result = subprocess.run(
            arguments,
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=60.0,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            continue
        changed.update(
            line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()
        )
    return sorted(changed)


def _event_now(
    *,
    event_id: str,
    category: FailureCategory,
    condition: str,
    injected: bool,
    expected: bool,
    outcome: FailureOutcome,
    error_type: str | None,
    recovered: bool | None,
    detail: str,
) -> FailureEvent:
    timestamp = _utc_iso(time.time())
    return FailureEvent(
        event_id=event_id,
        category=category,
        condition=condition,
        injected=injected,
        expected=expected,
        started_at_utc=timestamp,
        completed_at_utc=timestamp,
        duration_ms=0.0,
        outcome=outcome,
        error_type=error_type,
        recovered=recovered,
        safe_probe=True,
        detail=detail,
    )


def _cleanup_check_rows(value: Mapping[str, object] | None) -> list[dict[str, object]]:
    if value is None:
        return [
            {
                "check": "cleanup_verification_created",
                "passed": False,
                "observed": None,
            }
        ]
    result = value.get("result")
    result_mapping = result if isinstance(result, Mapping) else {}
    rows = [
        {
            "check": "cleanup_process_exit_code_zero",
            "passed": value.get("cleanup_process_exit_code") == 0,
            "observed": value.get("cleanup_process_exit_code"),
        },
        {
            "check": "database_rows_zero",
            "passed": result_mapping.get("database_rows_zero") is True,
            "observed": result_mapping.get("database_rows_zero"),
        },
        {
            "check": "qdrant_collection_absent",
            "passed": result_mapping.get("qdrant_collection_absent") is True,
            "observed": result_mapping.get("qdrant_collection_absent"),
        },
        {
            "check": "temp_files_zero",
            "passed": result_mapping.get("temp_files_zero") is True,
            "observed": result_mapping.get("temp_files_zero"),
        },
        {
            "check": "target_process_stopped",
            "passed": value.get("target_process_stopped", True) is True,
            "observed": value.get("target_process_stopped", True),
        },
    ]
    return rows


def _build_reliability_report(
    *,
    environment: Mapping[str, object],
    summary: Mapping[str, object],
    windows: Sequence[SoakWindowSummary],
    drift: Mapping[str, object],
    failure_events: Sequence[FailureEvent],
    boundaries: Sequence[BoundaryResult],
    cleanup_checks: Sequence[Mapping[str, object]],
    execution_command: str,
) -> str:
    lines = [
        "# Local RAG 장시간 안정성 및 실패 경계 측정 보고서",
        "",
        "> 현재 구현을 관측한 결과입니다. 성능 개선, 운영 제한값 변경, 모델 교체, "
        "Qdrant 튜닝은 포함하지 않습니다.",
        "",
        "## 실행 조건과 명령어",
        "",
        f"- Run ID: `{summary.get('run_id')}`",
        f"- RAG Branch: `{environment.get('rag_git_branch')}`",
        f"- RAG Commit SHA: `{environment.get('rag_git_commit_sha')}`",
        f"- OS: `{environment.get('platform')}`",
        f"- GPU: `{environment.get('nvidia_smi_summary')}`",
        f"- Qdrant Collection: `{summary.get('qdrant_collection')}`",
        f"- Test DB Override: `{summary.get('database_name_override') or '미사용'}`",
        "",
        "```powershell",
        execution_command,
        "```",
        "",
        "## 장시간 반복 실행",
        "",
        f"- Window 수: {summary.get('soak_window_count')}",
        f"- 검색 요청 수: {summary.get('soak_request_count')}",
        f"- 누수·성능 저하 후보: `{drift.get('any_candidate')}`",
        f"- 해석: {drift.get('interpretation')}",
        "",
        "| Window | 요청 | 성공 | 오류율 | 처리량(req/s) | 평균(ms) | 최대(ms) | "
        "p50 | p95 | p99 | RSS p95(MiB) | VRAM p95(MiB) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for window in windows:
        # 보고서의 숫자 표현은 원본 dataclass 값을 변경하지 않고 출력 시점에만 정규화한다.
        error_rate = float(window.error_rate)
        throughput = float(window.throughput_requests_per_second)
        lines.append(
            f"| {window.window_index} | {window.request_count} | "
            f"{window.success_count} | {error_rate:.2%} | {throughput:.3f} | "
            f"{_format_number(window.latency_mean_ms)} | "
            f"{_format_number(window.latency_max_ms)} | "
            f"{_format_number(window.latency_p50_ms)} | "
            f"{_format_number(window.latency_p95_ms)} | "
            f"{_format_number(window.latency_p99_ms)} | "
            f"{_format_mib(window.target_rss_p95_bytes)} | "
            f"{_format_mib(window.target_vram_p95_bytes)} |"
        )

    lines.extend(
        [
            "",
            "## Timeout·OOM·외부 서비스·비정상 종료",
            "",
            "| 분류 | 조건 | 주입 | 결과 | 오류 | 복구 | 안전 Probe |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for event in failure_events:
        lines.append(
            f"| {event.category} | {event.condition} | {event.injected} | "
            f"{event.outcome} | {event.error_type or '-'} | {event.recovered} | "
            f"{event.safe_probe} |"
        )

    lines.extend(
        [
            "",
            "## 정상 처리 최대 범위와 최초 실패",
            "",
            "| 차원 | 작업 | 정상 최대 | 최초 실패 | 실패 이유 | 상한 미도달 |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    for boundary in boundaries:
        lines.append(
            f"| {boundary.dimension} | {boundary.operation} | "
            f"{_format_nullable(boundary.normal_maximum_value)} | "
            f"{_format_nullable(boundary.first_failure_value)} | "
            f"{boundary.first_failure_reason or '-'} | "
            f"{boundary.observed_upper_bound_censored} |"
        )

    lines.extend(
        [
            "",
            "## 데이터·프로세스 정리",
            "",
            "| 확인 항목 | 통과 | 관측값 |",
            "|---|---|---|",
        ]
    )
    for cleanup_check in cleanup_checks:
        lines.append(
            f"| {cleanup_check.get('check')} | {cleanup_check.get('passed')} | "
            f"{cleanup_check.get('observed')} |"
        )

    lines.extend(
        [
            "",
            "## 결과 파일",
            "",
            "- `benchmark_report.json` / `benchmark_report.md`: 기존 Runner 원본 요약",
            "- `soak_windows.csv` / `.json`: Window별 평균·최대·p50·p95·p99",
            "- `soak_drift.csv` / `.json`: 첫·마지막 Window 자원·지연 변화",
            "- `failure_events.csv` / `.json`: Timeout·OOM·외부 실패·비정상 종료",
            "- `boundary_analysis.csv` / `.json`: 정상 최대와 최초 실패 분리",
            "- `isolation_verification.csv` / `.json`: Test ID·DB Row·Collection 확인",
            "- `cleanup_verification.json` / `cleanup_checks.csv`: 종료 정리 재검증",
            "- `request_records.csv`, `resource_samples.csv`, `target.log`: 원본 근거",
            "",
            "## 해석 주의",
            "",
            "- 실제 CUDA/Host OOM은 시스템 안전을 위해 의도적으로 만들지 않습니다. "
            "실제 발생 OOM은 요청·로그에서 수동 감지 항목으로 별도 기록합니다.",
            "- 누수 후보는 보고 기준을 넘은 변화이며 메모리 누수를 확정하는 판정이 아닙니다.",
            "- 최초 실패가 없으면 `상한 미도달=True`이며 정상 최대값은 관측 범위의 상한입니다.",
            "- 외부 서비스 Probe는 전용 캠페인 중 한 Service씩 정지하고 즉시 복구합니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_campaign_report(
    *,
    campaign_summary: Mapping[str, object],
    execution_command: str,
) -> str:
    scope = campaign_summary.get("scope_guard")
    scope_mapping = scope if isinstance(scope, Mapping) else {}
    lines = [
        "# Issue #159 성능·신뢰성 측정 캠페인 보고서",
        "",
        "## 실행",
        "",
        "```powershell",
        execution_command,
        "```",
        "",
        f"- 기준선 실행: `{campaign_summary.get('baseline_executed')}`",
        f"- 기준선 보고서: `{campaign_summary.get('baseline_report')}`",
        f"- 신뢰성 보고서: `{campaign_summary.get('reliability_report')}`",
        f"- 실행 오류: `{campaign_summary.get('execution_error_type') or '없음'}`",
        "",
        "## 인프라·범위 검증",
        "",
        f"- 최초 인프라 상태 복원: `{campaign_summary.get('infrastructure_restored')}`",
        f"- RAG 보호 경로·운영 제한 비변경: `{scope_mapping.get('passed')}`",
        f"- 성능 개선/제한 변경 감지: "
        f"`{scope_mapping.get('performance_optimization_or_limit_change_detected')}`",
        "",
        "상세 수치와 실패 조건은 `reliability/report.md`, 기계 판독 결과는 "
        "각 CSV·JSON 파일을 확인합니다.",
        "",
    ]
    return "\n".join(lines)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _format_nullable(value: object) -> str:
    """0을 결측값으로 오인하지 않고 Markdown 표 문자열로 변환한다."""

    return "-" if value is None else str(value)


def _format_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "-"
    return f"{float(value):.3f}"


def _format_mib(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "-"
    return f"{float(value) / (1024 * 1024):.2f}"


def _relative_or_none(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _utc_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat(timespec="milliseconds")


def _parse_arguments() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    repository_root = project_root.parent
    parser = argparse.ArgumentParser(
        description="Run Issue #159 baseline, soak, fault, isolation, and cleanup verification."
    )
    parser.add_argument("--rag-root", type=Path, default=repository_root / "RAG")
    parser.add_argument(
        "--benchmark-plan",
        type=Path,
        default=project_root / "configs/benchmark-plan.json",
    )
    parser.add_argument(
        "--reliability-plan",
        type=Path,
        default=project_root / "configs/reliability-plan.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=project_root / "artifacts/reliability",
    )
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=18077)
    parser.add_argument("--disable-answers", action="store_true")
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--execution-command", default="uv run jipsa-rag-reliability")
    return parser.parse_args()


def main() -> int:
    args = _parse_arguments()
    benchmark_plan = load_benchmark_plan(args.benchmark_plan.resolve())
    reliability_plan = load_reliability_plan(args.reliability_plan.resolve())
    campaign_id = f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    campaign_directory = args.output_root.resolve() / campaign_id
    campaign = ReliabilityCampaign(
        benchmark_plan=benchmark_plan,
        reliability_plan=reliability_plan,
        rag_root=args.rag_root,
        campaign_directory=campaign_directory,
        target_host=args.target_host,
        target_port=args.target_port,
        disable_answers=args.disable_answers,
        skip_baseline=args.skip_baseline,
        execution_command=args.execution_command,
    )
    report_path = campaign.run()
    print(f"Reliability campaign report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
