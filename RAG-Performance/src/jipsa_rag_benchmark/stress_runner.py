"""외부 RAG Endpoint를 대상으로 단계형 한계 테스트를 실행한다.

이 실행기는 더 이상 Local RAG Process, Docker, TEI, Qdrant 또는 Local DB를 시작·중단하지
않는다. 지정된 외부 Origin에 HTTP 요청만 전송하는 Black-box 부하 생성기다.

모든 내장 Profile은 다음 1~5단계를 동일한 순서로 실행한다.

1. Burst: 순간 동시 폭주
2. Interval: 짧은 간격 릴레이
3. Batch: 그룹 Wave
4. Ramp: 동시성 증가와 정상 최대·최초 실패 경계
5. Chaos: 기준 TPS와 주기적 Spike

Standard·Endurance·Destructive는 추가 Soak 단계를 통해 장시간 지연 Drift와 복구 여부를
관측한다. 외부 대상에 대한 Container 중단, Process Kill, MemoryError 주입, 데이터 삭제는
수행하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

import httpx
import psutil

from jipsa_rag_benchmark.external_target import (
    ExternalTargetConfig,
    build_external_target_config,
    load_external_target_config,
    validate_search_scope,
)
from jipsa_rag_benchmark.models import BenchmarkPhase, RequestRecord
from jipsa_rag_benchmark.rag_environment import load_rag_environment
from jipsa_rag_benchmark.stress_analysis import (
    analyze_capacity_boundaries,
    summarize_stress_stage,
)
from jipsa_rag_benchmark.stress_models import (
    BatchStage,
    BurstStage,
    CapacityBoundary,
    ChaosStage,
    FaultSuiteStage,
    IntervalStage,
    RampStage,
    SoakStage,
    StageSummary,
    StressStage,
    StressSuitePlan,
    load_stress_suite_plan,
)
from jipsa_rag_benchmark.test_data_discovery import (
    DataSource,
    discover_test_data,
)
from jipsa_rag_benchmark.verification_readme import (
    build_readme_verification_record,
    update_readme_verification,
)

_TOKEN_ENV: Final[str] = "JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN"
_USER_AGENT: Final[str] = "jipsa-rag-performance/external-stress"
_REQUEST_PHASE: Final[BenchmarkPhase] = "concurrency"


class ProgressReporter:
    """콘솔 출력과 UTF-8 ``progress.log`` 기록을 직렬화한다."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def emit(self, message: str) -> None:
        """UTC 시각을 붙여 Console과 파일에 같은 내용을 남긴다."""

        line = f"[{_utc_iso(time.time())}] {message}"
        with self._lock:
            print(line, flush=True)
            with self._path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(line)
                stream.write("\n")


class ExternalStagedStressRunner:
    """하나의 외부 RAG Origin에 계획된 HTTP Traffic Stage를 실행한다."""

    def __init__(
        self,
        *,
        target: ExternalTargetConfig,
        stress_plan: StressSuitePlan,
        run_id: str,
        run_directory: Path,
        internal_token: str,
        allow_destructive: bool,
        allow_production_target: bool,
        confirm_target_host: str | None,
        verify_tls: bool,
        execution_command: str,
    ) -> None:
        self._target = target
        self._stress_plan = stress_plan
        self._run_id = run_id
        self._run_directory = run_directory
        self._internal_token = internal_token
        self._allow_destructive = allow_destructive
        self._allow_production_target = allow_production_target
        self._confirm_target_host = confirm_target_host
        self._verify_tls = verify_tls
        self._execution_command = execution_command

        self._progress: ProgressReporter | None = None
        self._client: httpx.Client | None = None
        self._request_index = 0
        self._request_lock = threading.Lock()
        self._request_records: list[RequestRecord] = []
        self._stage_summaries: list[StageSummary] = []
        self._health_checks: list[dict[str, object]] = []
        self._suite_stop_reason: str | None = None

    @property
    def stage_summaries(self) -> tuple[StageSummary, ...]:
        """완료된 Stage Summary를 실행 순서대로 반환한다."""

        return tuple(self._stage_summaries)

    @property
    def health_checks(self) -> tuple[dict[str, object], ...]:
        """실행 전·단계 후·실행 후 Health Check 결과를 반환한다."""

        return tuple(self._health_checks)

    def run(self) -> Path:
        """사전 검증, 단계 실행, 결과 생성과 README 갱신용 자료 생성을 수행한다."""

        self._validate_preflight()
        self._run_directory.mkdir(parents=True, exist_ok=False)
        self._progress = ProgressReporter(self._run_directory / "progress.log")
        self._write_static_inputs()
        self._emit(
            "외부 Stress Suite 시작: "
            f"profile={self._stress_plan.profile}, "
            f"target={self._target.target_origin}, "
            f"destructive={self._stress_plan.destructive}"
        )

        execution_error: BaseException | None = None
        connection_limit = max(20, self._stress_plan.maximum_declared_concurrency)
        timeout = httpx.Timeout(
            connect=self._target.connect_timeout_seconds,
            read=self._target.request_timeout_seconds,
            write=self._target.request_timeout_seconds,
            pool=self._target.request_timeout_seconds,
        )
        headers = {
            "X-Internal-Token": self._internal_token,
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        }

        try:
            with httpx.Client(
                base_url=self._target.target_base_url,
                headers=headers,
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=connection_limit,
                    max_keepalive_connections=min(connection_limit, 128),
                ),
                follow_redirects=False,
                trust_env=False,
                verify=self._verify_tls,
            ) as client:
                self._client = client
                self._require_health("preflight")
                self._run_stages()
                self._require_health("postflight")
        except (Exception, KeyboardInterrupt) as error:
            execution_error = error
            self._emit(f"외부 Stress Suite 실행 오류: {type(error).__name__}")
        finally:
            self._client = None

        report_path = self._write_outputs(execution_error=execution_error)
        self._emit(
            "외부 Stress Suite 종료: "
            f"stages={len(self._stage_summaries)}, "
            f"requests={len(self._request_records)}, "
            f"error={type(execution_error).__name__ if execution_error else 'none'}"
        )
        if execution_error is not None:
            raise execution_error
        return report_path

    def _validate_preflight(self) -> None:
        """외부 Target, Profile 승인과 외부 모드에서 금지된 Stage를 검사한다."""

        if not self._internal_token.strip():
            raise ValueError(f"{_TOKEN_ENV} is required for the external search API.")
        if self._stress_plan.destructive and not self._allow_destructive:
            raise PermissionError("Destructive profile requires --allow-destructive for every run.")
        if self._stress_plan.destructive:
            confirmed = (self._confirm_target_host or "").strip().lower()
            if confirmed != self._target.target_host:
                raise PermissionError(
                    "Destructive profile requires --confirm-target-host to exactly match "
                    "the configured external host."
                )
        if (
            self._target.target_environment == "production"
            and self._stress_plan.destructive
            and not self._allow_production_target
        ):
            raise PermissionError(
                "Destructive traffic against production requires --allow-production-target."
            )

        for stage in self._stress_plan.enabled_stages:
            if stage.operation != "search":
                raise ValueError(
                    "External staged stress supports search-only plans. "
                    f"Unsupported operation: {stage.operation}"
                )
            if isinstance(stage, FaultSuiteStage):
                raise ValueError(
                    "fault_suite is local-only and is forbidden for an external target."
                )

    def _write_static_inputs(self) -> None:
        _write_json(
            self._run_directory / "external_target.resolved.json",
            self._target.to_public_dict(),
        )
        _write_json(
            self._run_directory / "stress_plan.resolved.json",
            self._stress_plan.to_dict(),
        )
        (self._run_directory / "execution_command.txt").write_text(
            f"{self._execution_command}\n",
            encoding="utf-8",
        )
        _write_json(
            self._run_directory / "environment.json",
            {
                "schema_version": 1,
                "execution_mode": "external_http_black_box",
                "target_origin": self._target.target_origin,
                "target_environment": self._target.target_environment,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "client_cpu_count": os.cpu_count(),
                "client_memory_total_bytes": psutil.virtual_memory().total,
                "verify_tls": self._verify_tls,
                "query_count": len(self._target.queries),
                "reference_file_count": len(self._target.reference_file_idxs),
                "internal_token_recorded": False,
                "local_rag_started": False,
                "local_docker_controlled": False,
                "local_database_touched": False,
            },
        )

    def _run_stages(self) -> None:
        enabled = self._stress_plan.enabled_stages
        consecutive_failed = 0
        for index, stage in enumerate(enabled, start=1):
            if self._suite_stop_reason is not None:
                self._emit(
                    f"[{index}/{len(enabled)}] {stage.stage_id} 생략: {self._suite_stop_reason}"
                )
                break

            self._emit(
                f"[{index}/{len(enabled)}] 시작: {stage.stage_id} | "
                f"{stage.name} | mode={stage.mode}"
            )
            produced = self._dispatch_stage(stage)
            latest = produced[-1] if produced else None
            if latest is None or latest.status in {"failed", "stopped"}:
                consecutive_failed += 1
            else:
                consecutive_failed = 0

            if latest is not None:
                self._emit(
                    f"[{index}/{len(enabled)}] 완료: {latest.stage_id} | "
                    f"status={latest.status} requests={latest.request_count} "
                    f"error_rate={latest.error_rate:.2%} "
                    f"p95={_format_number(latest.latency_p95_ms)}ms "
                    f"throughput={latest.throughput_requests_per_second:.2f}req/s"
                )

            health = self._record_health(f"after:{stage.stage_id}")
            if health.get("success") is not True:
                self._suite_stop_reason = f"external_health_failed:{stage.stage_id}"
                self._emit(f"전체 Suite 조기 중단: {self._suite_stop_reason}")
            if consecutive_failed >= self._stress_plan.stop_policy.consecutive_failed_stages:
                self._suite_stop_reason = (
                    "consecutive_failed_stages_exceeded:"
                    f"{self._stress_plan.stop_policy.consecutive_failed_stages}"
                )
                self._emit(f"전체 Suite 조기 중단: {self._suite_stop_reason}")

            if stage.cooldown_seconds > 0:
                self._emit(f"Cooldown {stage.cooldown_seconds:.1f}s")
                time.sleep(stage.cooldown_seconds)

    def _dispatch_stage(self, stage: StressStage) -> tuple[StageSummary, ...]:
        if isinstance(stage, BurstStage):
            return (self._run_burst(stage),)
        if isinstance(stage, IntervalStage):
            return (self._run_interval(stage),)
        if isinstance(stage, BatchStage):
            return (self._run_batch(stage),)
        if isinstance(stage, RampStage):
            return self._run_ramp(stage)
        if isinstance(stage, SoakStage):
            return (self._run_soak(stage),)
        if isinstance(stage, ChaosStage):
            return (self._run_chaos(stage),)
        raise TypeError(f"Unsupported external stress stage: {type(stage).__name__}")

    def _run_burst(self, stage: BurstStage) -> StageSummary:
        tasks = tuple(
            partial(self._search_request, stage.stage_id, stage.concurrency)
            for _ in range(stage.total_requests)
        )
        return self._run_fixed_batch(
            stage=stage,
            child_stage_id=stage.stage_id,
            child_stage_name=stage.name,
            concurrency=stage.concurrency,
            scheduled_request_count=stage.total_requests,
            tasks=tasks,
        )

    def _run_interval(self, stage: IntervalStage) -> StageSummary:
        started_epoch = time.time()
        records: list[RequestRecord] = []
        futures: set[Future[RequestRecord]] = set()
        backpressure_count = 0
        with ThreadPoolExecutor(
            max_workers=stage.concurrency,
            thread_name_prefix="external-interval",
        ) as executor:
            for _ in range(stage.total_requests):
                if self._client_memory_guard_triggered():
                    break
                futures.add(
                    executor.submit(
                        self._search_request,
                        stage.stage_id,
                        stage.concurrency,
                    )
                )
                if len(futures) >= stage.concurrency * 4:
                    completed, futures = wait(futures, return_when=FIRST_COMPLETED)
                    records.extend(future.result() for future in completed)
                    backpressure_count += 1
                time.sleep(stage.interval_seconds)
            for future in futures:
                records.append(future.result())
        return self._summarize(
            stage=stage,
            child_stage_id=stage.stage_id,
            child_stage_name=stage.name,
            declared_concurrency=stage.concurrency,
            scheduled_request_count=stage.total_requests,
            records=records,
            started_epoch=started_epoch,
            scheduler_backpressure_count=backpressure_count,
        )

    def _run_batch(self, stage: BatchStage) -> StageSummary:
        started_epoch = time.time()
        records: list[RequestRecord] = []
        submitted = 0
        with ThreadPoolExecutor(
            max_workers=stage.max_workers,
            thread_name_prefix="external-batch",
        ) as executor:
            while submitted < stage.total_requests:
                if self._client_memory_guard_triggered():
                    break
                current = min(stage.batch_size, stage.total_requests - submitted)
                futures = tuple(
                    executor.submit(
                        self._search_request,
                        stage.stage_id,
                        stage.max_workers,
                    )
                    for _ in range(current)
                )
                records.extend(future.result() for future in futures)
                submitted += current
                if submitted < stage.total_requests:
                    time.sleep(stage.interval_seconds)
        return self._summarize(
            stage=stage,
            child_stage_id=stage.stage_id,
            child_stage_name=stage.name,
            declared_concurrency=stage.max_workers,
            scheduled_request_count=stage.total_requests,
            records=records,
            started_epoch=started_epoch,
            scheduler_backpressure_count=0,
        )

    def _run_ramp(self, stage: RampStage) -> tuple[StageSummary, ...]:
        summaries: list[StageSummary] = []
        concurrency = stage.start_concurrency
        while concurrency <= stage.max_concurrency:
            count = concurrency * stage.requests_per_worker
            child_id = f"{stage.stage_id}-c{concurrency}"
            tasks = tuple(
                partial(self._search_request, child_id, concurrency) for _ in range(count)
            )
            summary = self._run_fixed_batch(
                stage=stage,
                child_stage_id=child_id,
                child_stage_name=f"{stage.name} / concurrency {concurrency}",
                concurrency=concurrency,
                scheduled_request_count=count,
                tasks=tasks,
            )
            summaries.append(summary)
            if summary.status in {"failed", "stopped"}:
                break
            concurrency += stage.step_concurrency
            if concurrency <= stage.max_concurrency:
                time.sleep(stage.wave_interval_seconds)
        return tuple(summaries)

    def _run_soak(self, stage: SoakStage) -> StageSummary:
        """설정된 시간 동안 동시 부하를 유지하고 요청 상한은 비상 안전장치로만 사용한다.

        Soak의 주 종료 조건은 ``duration_seconds``다. ``max_requests``가 너무 낮아 목표
        시간 전에 먼저 소진되면 성공으로 오판하지 않고 명시적인 중단 사유를 기록한다.
        이렇게 하면 문서에 2분 Soak라고 표시하면서 실제로는 수십 초만 실행되는 회귀를
        결과 파일과 자동 README 검증 기록에서 즉시 식별할 수 있다.
        """

        started_epoch = time.time()
        deadline = started_epoch + stage.duration_seconds
        records: list[RequestRecord] = []
        submitted = 0
        backpressure_count = 0
        futures: set[Future[RequestRecord]] = set()

        # max_requests=0은 요청 수 제한을 비활성화하고 duration_seconds를 유일한 정상
        # 종료 기준으로 사용한다. Quick·Standard·Endurance·Destructive가 처리량 차이로
        # 목표 시간보다 먼저 끝나는 회귀를 방지하면서도 고정 동시성은 그대로 유지한다.
        request_cap_enabled = stage.max_requests > 0
        with ThreadPoolExecutor(
            max_workers=stage.concurrency,
            thread_name_prefix="external-soak",
        ) as executor:
            while time.time() < deadline and (
                not request_cap_enabled or submitted < stage.max_requests
            ):
                if self._client_memory_guard_triggered():
                    break
                while (
                    len(futures) < stage.concurrency
                    and (not request_cap_enabled or submitted < stage.max_requests)
                    and time.time() < deadline
                ):
                    futures.add(
                        executor.submit(
                            self._search_request,
                            stage.stage_id,
                            stage.concurrency,
                        )
                    )
                    submitted += 1
                if not futures:
                    break
                completed, futures = wait(
                    futures,
                    timeout=min(1.0, max(0.0, deadline - time.time())),
                    return_when=FIRST_COMPLETED,
                )
                if not completed:
                    backpressure_count += 1
                    continue
                records.extend(future.result() for future in completed)

            # 진행 중 요청은 모두 회수한다. Stage 종료 시점에 Future를 버리면 실제 서버에
            # 전송된 요청 수와 보고서 요청 수가 달라져 처리량·오류율이 왜곡될 수 있다.
            scheduling_completed_epoch = time.time()
            for future in futures:
                records.append(future.result())

        remaining_seconds = deadline - scheduling_completed_epoch
        request_cap_reached_early = (
            request_cap_enabled and submitted >= stage.max_requests and remaining_seconds > 0.5
        )
        explicit_stop_reason = None
        if request_cap_reached_early:
            explicit_stop_reason = "soak_max_requests_reached_before_duration"
            self._emit(
                "Soak 요청 안전 상한이 목표 시간보다 먼저 소진되었습니다: "
                f"submitted={submitted}, remaining={remaining_seconds:.2f}s"
            )

        return self._summarize(
            stage=stage,
            child_stage_id=stage.stage_id,
            child_stage_name=stage.name,
            declared_concurrency=stage.concurrency,
            # 시간 기반 Stage에서는 실제로 Scheduler가 제출한 수가 계획된 요청 수다.
            # max_requests는 비상 상한이므로 Scheduled 열에 그대로 쓰지 않는다.
            scheduled_request_count=submitted,
            records=records,
            started_epoch=started_epoch,
            scheduler_backpressure_count=backpressure_count,
            explicit_stop_reason=explicit_stop_reason,
        )

    def _run_chaos(self, stage: ChaosStage) -> StageSummary:
        started_epoch = time.time()
        deadline = started_epoch + stage.duration_seconds
        last_spike = started_epoch
        records: list[RequestRecord] = []
        futures: set[Future[RequestRecord]] = set()
        submitted = 0
        backpressure_count = 0
        pending_limit = stage.max_workers * stage.max_pending_multiplier

        with ThreadPoolExecutor(
            max_workers=stage.max_workers,
            thread_name_prefix="external-chaos",
        ) as executor:
            while time.time() < deadline:
                loop_started = time.perf_counter()
                if self._client_memory_guard_triggered():
                    break

                request_count = stage.baseline_tps
                if time.time() - last_spike >= stage.spike_interval_seconds:
                    request_count += stage.spike_size
                    last_spike = time.time()
                    self._emit(f"Chaos Spike 제출: +{stage.spike_size}, pending={len(futures)}")

                for _ in range(request_count):
                    if len(futures) >= pending_limit:
                        completed, futures = wait(
                            futures,
                            return_when=FIRST_COMPLETED,
                        )
                        records.extend(future.result() for future in completed)
                        backpressure_count += 1
                    futures.add(
                        executor.submit(
                            self._search_request,
                            stage.stage_id,
                            stage.max_workers,
                        )
                    )
                    submitted += 1
                    if submitted >= self._stress_plan.max_total_requests:
                        break
                if submitted >= self._stress_plan.max_total_requests:
                    break

                completed = {future for future in futures if future.done()}
                futures.difference_update(completed)
                records.extend(future.result() for future in completed)
                remaining = 1.0 - (time.perf_counter() - loop_started)
                if remaining > 0:
                    time.sleep(remaining)

            for future in futures:
                records.append(future.result())

        return self._summarize(
            stage=stage,
            child_stage_id=stage.stage_id,
            child_stage_name=stage.name,
            declared_concurrency=stage.max_workers,
            scheduled_request_count=submitted,
            records=records,
            started_epoch=started_epoch,
            scheduler_backpressure_count=backpressure_count,
        )

    def _run_fixed_batch(
        self,
        *,
        stage: StressStage,
        child_stage_id: str,
        child_stage_name: str,
        concurrency: int,
        scheduled_request_count: int,
        tasks: Sequence[Callable[[], RequestRecord]],
    ) -> StageSummary:
        started_epoch = time.time()
        records: list[RequestRecord] = []
        with ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="external-fixed",
        ) as executor:
            futures = tuple(executor.submit(task) for task in tasks)
            records.extend(future.result() for future in futures)
        return self._summarize(
            stage=stage,
            child_stage_id=child_stage_id,
            child_stage_name=child_stage_name,
            declared_concurrency=concurrency,
            scheduled_request_count=scheduled_request_count,
            records=records,
            started_epoch=started_epoch,
            scheduler_backpressure_count=0,
        )

    def _summarize(
        self,
        *,
        stage: StressStage,
        child_stage_id: str,
        child_stage_name: str,
        declared_concurrency: int,
        scheduled_request_count: int,
        records: Sequence[RequestRecord],
        started_epoch: float,
        scheduler_backpressure_count: int,
        explicit_stop_reason: str | None = None,
    ) -> StageSummary:
        memory_stop_reason = (
            "client_memory_guard_triggered" if self._client_memory_guard_triggered() else None
        )
        effective_stop_reason = explicit_stop_reason or memory_stop_reason
        summary = summarize_stress_stage(
            sequence=len(self._stage_summaries) + 1,
            stage_id=child_stage_id,
            parent_stage_id=stage.stage_id,
            stage_name=child_stage_name,
            mode=stage.mode,
            operation=stage.operation,
            destructive=stage.destructive,
            started_epoch_seconds=started_epoch,
            completed_epoch_seconds=time.time(),
            declared_concurrency=declared_concurrency,
            scheduled_request_count=scheduled_request_count,
            submitted_request_count=len(records),
            records=records,
            samples=(),
            sla_seconds=self._stress_plan.sla_seconds,
            stop_policy=self._stress_plan.stop_policy,
            scheduler_backpressure_count=scheduler_backpressure_count,
            explicit_stop_reason=effective_stop_reason,
        )
        self._stage_summaries.append(summary)
        return summary

    def _search_request(self, case_id: str, concurrency: int) -> RequestRecord:
        client = self._required_client()
        request_index = self._next_request_index()
        query = self._target.queries[(request_index - 1) % len(self._target.queries)]
        file_idx = self._target.reference_file_idxs[
            (request_index - 1) % len(self._target.reference_file_idxs)
        ]
        if request_index % 5 == 0:
            reference_file_idxs = list(self._target.reference_file_idxs)
        else:
            reference_file_idxs = [file_idx]
        payload: dict[str, object] = {
            "user_idx": self._target.test_user_idx,
            "reference_file_idxs": reference_file_idxs,
            "query": query,
            "top_k": self._target.top_k,
            "score_threshold": self._target.score_threshold,
        }
        request_id = str(uuid4())
        request_bytes = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        started_epoch = time.time()
        started_perf = time.perf_counter()
        status_code: int | None = None
        response_bytes = 0
        success = False
        chunk_count: int | None = None
        error_type: str | None = None
        error_message: str | None = None

        try:
            response = client.post(
                self._target.search_path,
                json=payload,
                headers={"X-Request-ID": request_id},
            )
            status_code = response.status_code
            response_bytes = len(response.content)
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("API response root is not an object.")
            success_flag = body.get("success")
            success = 200 <= status_code < 300 and success_flag is not False
            data = body.get("data")
            if isinstance(data, dict):
                chunk_count = _optional_non_negative_int(data.get("result_count"))
            if not success:
                code = body.get("code")
                error_type = str(code)[:128] if code is not None else f"http_{status_code}"
                error_message = "The external RAG returned a non-success response."
        except Exception as error:
            error_type = type(error).__name__
            error_message = _safe_error_message(error)

        completed_epoch = time.time()
        record = RequestRecord(
            run_id=self._run_id,
            request_id=request_id,
            case_id=case_id,
            operation="search",
            phase=_REQUEST_PHASE,
            concurrency=concurrency,
            request_index=request_index,
            started_at_utc=_utc_iso(started_epoch),
            started_epoch_seconds=started_epoch,
            completed_epoch_seconds=completed_epoch,
            duration_ms=(time.perf_counter() - started_perf) * 1000.0,
            status_code=status_code,
            success=success,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            file_idx=file_idx,
            chunk_count=chunk_count,
            error_type=error_type,
            error_message=error_message,
        )
        with self._request_lock:
            self._request_records.append(record)
        return record

    def _required_client(self) -> httpx.Client:
        client = self._client
        if client is None:
            raise RuntimeError("External HTTP client is not initialized.")
        return client

    def _next_request_index(self) -> int:
        with self._request_lock:
            self._request_index += 1
            return self._request_index

    def _client_memory_guard_triggered(self) -> bool:
        current = psutil.virtual_memory().percent
        threshold = self._stress_plan.stop_policy.max_system_memory_percent
        triggered = current >= threshold
        if triggered:
            self._emit(
                "Load-generator memory guard triggered: "
                f"current={current:.1f}% threshold={threshold:.1f}%"
            )
        return triggered

    def _record_health(self, label: str) -> dict[str, object]:
        client = self._required_client()
        started = time.perf_counter()
        status_code: int | None = None
        success = False
        error_type: str | None = None
        try:
            response = client.get(self._target.health_path)
            status_code = response.status_code
            success = 200 <= status_code < 300
        except Exception as error:
            error_type = type(error).__name__
        record: dict[str, object] = {
            "label": label,
            "checked_at_utc": _utc_iso(time.time()),
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "status_code": status_code,
            "success": success,
            "error_type": error_type,
        }
        self._health_checks.append(record)
        return record

    def _require_health(self, label: str) -> None:
        record = self._record_health(label)
        if record["success"] is not True:
            raise RuntimeError(f"External RAG health check failed: {label}")
        if self._target.readiness_path is None:
            return
        client = self._required_client()
        response = client.get(self._target.readiness_path)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"External RAG readiness check failed: {label}")

    def _write_outputs(self, *, execution_error: BaseException | None) -> Path:
        summaries = tuple(self._stage_summaries)
        boundaries = analyze_capacity_boundaries(summaries)
        request_rows = [record.to_dict() for record in self._request_records]
        summary_rows = [summary.to_dict() for summary in summaries]
        boundary_rows = [boundary.to_dict() for boundary in boundaries]

        _write_json(
            self._run_directory / "requests.json",
            {"schema_version": 1, "records": request_rows},
        )
        _write_csv(self._run_directory / "requests.csv", request_rows)
        _write_json(
            self._run_directory / "stage_summaries.json",
            {"schema_version": 1, "records": summary_rows},
        )
        _write_csv(self._run_directory / "stage_summaries.csv", summary_rows)
        _write_json(
            self._run_directory / "capacity_boundaries.json",
            {"schema_version": 1, "records": boundary_rows},
        )
        _write_csv(self._run_directory / "capacity_boundaries.csv", boundary_rows)
        _write_json(
            self._run_directory / "health_checks.json",
            {"schema_version": 1, "records": list(self._health_checks)},
        )

        summary = _campaign_summary(
            target=self._target,
            plan=self._stress_plan,
            run_id=self._run_id,
            summaries=summaries,
            boundaries=boundaries,
            health_checks=self._health_checks,
            execution_error=execution_error,
        )
        _write_json(self._run_directory / "report.json", summary)
        report_markdown = _render_markdown_report(
            target=self._target,
            plan=self._stress_plan,
            summary=summary,
            summaries=summaries,
            boundaries=boundaries,
        )
        report_html = _render_html_report(
            target=self._target,
            plan=self._stress_plan,
            summary=summary,
            summaries=summaries,
            boundaries=boundaries,
        )
        markdown_path = self._run_directory / "report.md"
        html_path = self._run_directory / "report.html"
        markdown_path.write_text(report_markdown, encoding="utf-8")
        html_path.write_text(report_html, encoding="utf-8")
        return markdown_path

    def _emit(self, message: str) -> None:
        reporter = self._progress
        if reporter is not None:
            reporter.emit(message)


class ExternalStagedStressCampaign:
    """외부 Runner 실행과 프로젝트 README 검증 기록 갱신을 묶는다."""

    def __init__(
        self,
        *,
        target: ExternalTargetConfig,
        stress_plan: StressSuitePlan,
        campaign_directory: Path,
        internal_token: str,
        allow_destructive: bool,
        allow_production_target: bool,
        confirm_target_host: str | None,
        verify_tls: bool,
        execution_command: str,
        readme_markdown_path: Path,
        readme_html_path: Path,
        update_readme: bool,
        quality_gate_skipped: bool,
    ) -> None:
        self._target = target
        self._stress_plan = stress_plan
        self._campaign_directory = campaign_directory
        self._internal_token = internal_token
        self._allow_destructive = allow_destructive
        self._allow_production_target = allow_production_target
        self._confirm_target_host = confirm_target_host
        self._verify_tls = verify_tls
        self._execution_command = execution_command
        self._readme_markdown_path = readme_markdown_path
        self._readme_html_path = readme_html_path
        self._update_readme = update_readme
        self._quality_gate_skipped = quality_gate_skipped

    def run(self) -> Path:
        self._campaign_directory.mkdir(parents=True, exist_ok=False)
        runner = ExternalStagedStressRunner(
            target=self._target,
            stress_plan=self._stress_plan,
            run_id=self._campaign_directory.name,
            run_directory=self._campaign_directory / "external-stress",
            internal_token=self._internal_token,
            allow_destructive=self._allow_destructive,
            allow_production_target=self._allow_production_target,
            confirm_target_host=self._confirm_target_host,
            verify_tls=self._verify_tls,
            execution_command=self._execution_command,
        )
        execution_error: BaseException | None = None
        report_path: Path | None = None
        try:
            report_path = runner.run()
        except (Exception, KeyboardInterrupt) as error:
            execution_error = error

        summaries = runner.stage_summaries
        boundaries = analyze_capacity_boundaries(summaries)
        health_checks = runner.health_checks
        preflight_health = next(
            (item for item in health_checks if item.get("label") == "preflight"),
            None,
        )
        postflight_health = next(
            (item for item in health_checks if item.get("label") == "postflight"),
            None,
        )
        completed_at = _utc_iso(time.time())
        stress_markdown = self._campaign_directory / "external-stress/report.md"
        stress_html = self._campaign_directory / "external-stress/report.html"
        campaign_markdown = self._campaign_directory / "report.md"
        campaign_html = self._campaign_directory / "report.html"

        campaign_summary: dict[str, object] = {
            "schema_version": 1,
            "run_id": self._campaign_directory.name,
            "execution_mode": "external_http_black_box",
            "target_origin": self._target.target_origin,
            "target_environment": self._target.target_environment,
            "selection_source": self._target.selection_source,
            "selection_seed": self._target.selection_seed,
            "selected_user_idx": self._target.test_user_idx,
            "selected_file_count": len(self._target.reference_file_idxs),
            "profile": self._stress_plan.profile,
            "destructive": self._stress_plan.destructive,
            "quality_gate_skipped": self._quality_gate_skipped,
            "preflight_health_passed": bool(
                preflight_health and preflight_health.get("success") is True
            ),
            "postflight_health_passed": bool(
                postflight_health and postflight_health.get("success") is True
            ),
            "local_rag_touched": False,
            "local_docker_touched": False,
            "local_database_touched": False,
            "stage_summaries": [summary.to_dict() for summary in summaries],
            "capacity_boundaries": [boundary.to_dict() for boundary in boundaries],
            "execution_error_type": (
                type(execution_error).__name__ if execution_error is not None else None
            ),
            "completed_at_utc": completed_at,
        }
        _write_json(self._campaign_directory / "report.json", campaign_summary)
        campaign_markdown.write_text(
            _render_campaign_markdown(campaign_summary),
            encoding="utf-8",
        )
        campaign_html.write_text(
            _render_campaign_html(campaign_summary),
            encoding="utf-8",
        )

        if self._update_readme:
            record = build_readme_verification_record(
                run_id=self._campaign_directory.name,
                profile=self._stress_plan.profile,
                destructive=self._stress_plan.destructive,
                completed_at_utc=completed_at,
                execution_error_type=(
                    type(execution_error).__name__ if execution_error is not None else None
                ),
                quality_gate_skipped=self._quality_gate_skipped,
                execution_mode="external_http_black_box",
                target_origin=self._target.target_origin,
                target_environment=self._target.target_environment,
                selection_source=self._target.selection_source,
                selection_seed=self._target.selection_seed,
                selected_user_idx=self._target.test_user_idx,
                selected_file_count=len(self._target.reference_file_idxs),
                preflight_health_passed=bool(
                    preflight_health and preflight_health.get("success") is True
                ),
                postflight_health_passed=bool(
                    postflight_health and postflight_health.get("success") is True
                ),
                local_rag_touched=False,
                stage_summaries=summaries,
                capacity_boundaries=boundaries,
                report_markdown=_relative_link(
                    self._readme_markdown_path.parent,
                    campaign_markdown,
                ),
                report_html=_relative_link(
                    self._readme_html_path.parent,
                    campaign_html,
                ),
                stress_report_markdown=_relative_link(
                    self._readme_markdown_path.parent,
                    stress_markdown,
                ),
                stress_report_html=_relative_link(
                    self._readme_html_path.parent,
                    stress_html,
                ),
            )
            update_readme_verification(
                markdown_path=self._readme_markdown_path,
                html_path=self._readme_html_path,
                record=record,
            )

        if execution_error is not None:
            raise execution_error
        if report_path is None:
            raise RuntimeError("External stress report was not created.")
        return campaign_markdown


def _campaign_summary(
    *,
    target: ExternalTargetConfig,
    plan: StressSuitePlan,
    run_id: str,
    summaries: Sequence[StageSummary],
    boundaries: Sequence[CapacityBoundary],
    health_checks: Sequence[Mapping[str, object]],
    execution_error: BaseException | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "execution_mode": "external_http_black_box",
        "target_origin": target.target_origin,
        "target_environment": target.target_environment,
        "profile": plan.profile,
        "destructive": plan.destructive,
        "stage_count": len(summaries),
        "request_count": sum(item.request_count for item in summaries),
        "success_count": sum(item.success_count for item in summaries),
        "error_count": sum(item.error_count for item in summaries),
        "health_checks": list(health_checks),
        "capacity_boundaries": [boundary.to_dict() for boundary in boundaries],
        "execution_error_type": (
            type(execution_error).__name__ if execution_error is not None else None
        ),
        "completed_at_utc": _utc_iso(time.time()),
        "server_resource_metrics_available": False,
        "client_resource_guard": "system_memory_percent",
    }


def _render_markdown_report(
    *,
    target: ExternalTargetConfig,
    plan: StressSuitePlan,
    summary: Mapping[str, object],
    summaries: Sequence[StageSummary],
    boundaries: Sequence[CapacityBoundary],
) -> str:
    lines = [
        "# Jipsa 외부 RAG 단계형 한계 테스트 보고서",
        "",
        f"> 대상: `{target.target_origin}`  ",
        f"> 환경: `{target.target_environment}`  ",
        f"> Profile: `{plan.profile}`  ",
        "> 측정 방식: 외부 HTTP Black-box — Local RAG, Docker, DB는 제어하지 않음",
        "",
        "## 요약",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| Stage | {summary['stage_count']} |",
        f"| 요청 | {summary['request_count']} |",
        f"| 성공 | {summary['success_count']} |",
        f"| 실패 | {summary['error_count']} |",
        f"| 실행 오류 | {summary['execution_error_type'] or '없음'} |",
        "",
        "## 단계별 결과",
        "",
        "| Stage | Mode | 동시성 | 요청 | 오류율 | 처리량 req/s | p95 ms | 상태 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in summaries:
        lines.append(
            f"| `{item.stage_id}` | {item.mode} | {item.declared_concurrency} | "
            f"{item.request_count} | {item.error_rate:.2%} | "
            f"{item.throughput_requests_per_second:.2f} | "
            f"{_format_number(item.latency_p95_ms)} | {item.status} |"
        )
    lines.extend(
        [
            "",
            "## 처리 한계",
            "",
            "| 작업 | 정상 최대 동시성 | 최초 실패 동시성 | 해석 |",
            "|---|---:|---:|---|",
        ]
    )
    if boundaries:
        for boundary in boundaries:
            reason = (
                "계획 상한까지 실패 없음"
                if boundary.upper_bound_censored
                else boundary.first_failure_reason or "실패 경계 관측"
            )
            lines.append(
                f"| {boundary.operation} | "
                f"{_nullable(boundary.normal_maximum_concurrency)} | "
                f"{_nullable(boundary.first_failure_concurrency)} | {reason} |"
            )
    else:
        lines.append("| - | - | - | Ramp 근거 없음 |")
    lines.extend(
        [
            "",
            "## 해석 주의",
            "",
            "- 외부 Black-box 측정이므로 서버 CPU, RAM, GPU, VRAM은 이 보고서에 포함되지 않습니다.",
            "- 정상 최대는 Plan에서 실제로 시험한 상한 안의 관측값입니다.",
            "- 네트워크, TLS, Reverse Proxy, Gateway와 RAG 처리가 합쳐진 End-to-End 지연입니다.",
            "- Token, 질문 원문과 응답 본문은 결과에 저장하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_html_report(
    *,
    target: ExternalTargetConfig,
    plan: StressSuitePlan,
    summary: Mapping[str, object],
    summaries: Sequence[StageSummary],
    boundaries: Sequence[CapacityBoundary],
) -> str:
    rows = "".join(
        "<tr>"
        f"<td><code>{_escape(item.stage_id)}</code></td>"
        f"<td>{item.mode}</td>"
        f"<td>{item.declared_concurrency}</td>"
        f"<td>{item.request_count}</td>"
        f"<td>{item.error_rate:.2%}</td>"
        f"<td>{item.throughput_requests_per_second:.2f}</td>"
        f"<td>{_format_number(item.latency_p95_ms)}</td>"
        f"<td>{item.status}</td>"
        "</tr>"
        for item in summaries
    )
    boundary_rows = (
        "".join(
            "<tr>"
            f"<td>{boundary.operation}</td>"
            f"<td>{_nullable(boundary.normal_maximum_concurrency)}</td>"
            f"<td>{_nullable(boundary.first_failure_concurrency)}</td>"
            f"<td>{_escape(boundary.first_failure_reason or '계획 상한까지 실패 없음')}</td>"
            "</tr>"
            for boundary in boundaries
        )
        or "<tr><td>-</td><td>-</td><td>-</td><td>Ramp 근거 없음</td></tr>"
    )
    graph_bars = "".join(
        "<div class='bar-row'>"
        f"<span>{_escape(item.stage_id)}</span>"
        "<div class='bar-track'>"
        f"<i style='width:{min(item.error_rate * 100.0, 100.0):.2f}%'></i>"
        "</div>"
        f"<strong>{item.error_rate:.2%}</strong>"
        "</div>"
        for item in summaries
    )
    return f"""<!doctype html>
<html lang='ko'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Jipsa 외부 RAG Stress Report</title>
  <style>
    :root {{ color-scheme: light dark; --card:#fff; --bg:#eef3fb; --text:#11203a;
      --muted:#5f6f86; --line:#cdd8e9; --brand:#174ea6; --danger:#ba1a1a; }}
    @media (prefers-color-scheme:dark) {{ :root {{ --card:#101b2c; --bg:#08111f;
      --text:#eaf1ff; --muted:#a9b8ce; --line:#30445f; --brand:#8ab4ff; }} }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--text);
      font-family:Inter,"Noto Sans KR",system-ui,sans-serif; line-height:1.65; }}
    main {{ max-width:1180px; margin:0 auto; padding:32px 18px 70px; }}
    header,section {{ background:var(--card); border:1px solid var(--line); border-radius:18px;
      padding:26px; margin-bottom:18px; }} h1,h2 {{ letter-spacing:-.03em; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:8px; }} .chip {{ border:1px solid var(--line);
      border-radius:999px; padding:5px 10px; color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
    .metric {{ border:1px solid var(--line); border-radius:14px; padding:16px; }}
    .metric strong {{ display:block; color:var(--brand); font-size:1.8rem; }}
    .table {{ overflow:auto; }} table {{ width:100%; border-collapse:collapse; min-width:780px; }}
    th,td {{ border-bottom:1px solid var(--line); padding:10px; text-align:left; }}
    th {{ color:var(--muted); }} code {{ font-family:"Cascadia Code",monospace; }}
    .bar-row {{ display:grid; grid-template-columns:180px 1fr 76px; align-items:center;
      gap:12px; margin:9px 0; }} .bar-track {{ height:12px; background:var(--bg);
      border-radius:999px; overflow:hidden; }} .bar-track i {{ display:block; height:100%;
      background:var(--danger); }}
    @media(max-width:760px) {{
      .metrics {{ grid-template-columns:1fr 1fr; }}
      .bar-row {{ grid-template-columns:110px 1fr 60px; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>Jipsa 외부 RAG 단계형 한계 테스트</h1>
    <p>Local Process를 실행하지 않고 외부 Origin에 HTTP 요청을 보낸 Black-box 결과입니다.</p>
    <div class='meta'>
      <span class='chip'>{_escape(target.target_origin)}</span>
      <span class='chip'>{_escape(target.target_environment)}</span>
      <span class='chip'>{_escape(plan.profile)}</span>
    </div>
  </header>
  <section>
    <h2>요약</h2>
    <div class='metrics'>
      <div class='metric'><strong>{summary["stage_count"]}</strong><span>Stages</span></div>
      <div class='metric'><strong>{summary["request_count"]}</strong><span>Requests</span></div>
      <div class='metric'><strong>{summary["success_count"]}</strong><span>Success</span></div>
      <div class='metric'><strong>{summary["error_count"]}</strong><span>Errors</span></div>
    </div>
  </section>
  <section>
    <h2>단계별 오류율</h2>
    {graph_bars}
  </section>
  <section>
    <h2>단계별 결과</h2>
    <div class='table'><table><thead><tr><th>Stage</th><th>Mode</th><th>Concurrency</th>
      <th>Requests</th><th>Error</th><th>Throughput</th><th>p95 ms</th><th>Status</th>
    </tr></thead><tbody>{rows}</tbody></table></div>
  </section>
  <section>
    <h2>처리 한계</h2>
    <div class='table'><table><thead><tr><th>Operation</th><th>Normal maximum</th>
      <th>First failure</th><th>Interpretation</th></tr></thead>
      <tbody>{boundary_rows}</tbody></table></div>
  </section>
  <section>
    <h2>측정 경계</h2>
    <ul>
      <li>외부 Network, TLS, Proxy, Gateway와 RAG 처리 지연을 함께 측정합니다.</li>
      <li>서버 CPU, RAM, GPU, VRAM은 외부 Black-box에서 측정하지 않습니다.</li>
      <li>Local RAG, Docker, TEI, Qdrant와 Local DB를 제어하지 않습니다.</li>
      <li>Token, 질문 원문과 응답 본문은 결과 파일에 저장하지 않습니다.</li>
    </ul>
  </section>
</main>
</body>
</html>
"""


def _render_campaign_markdown(summary: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "# 외부 RAG Stress Campaign",
            "",
            f"- 실행 방식: `{summary['execution_mode']}`",
            f"- 대상: `{summary['target_origin']}`",
            f"- 환경: `{summary['target_environment']}`",
            f"- 데이터 Source: `{summary['selection_source']}`",
            f"- 선정 Seed: `{summary['selection_seed'] or 'configured'}`",
            f"- 선정 User IDX: `{summary['selected_user_idx']}`",
            f"- 선정 File 수: `{summary['selected_file_count']}`",
            f"- Profile: `{summary['profile']}`",
            f"- 사전 Health: `{summary['preflight_health_passed']}`",
            f"- 사후 Health: `{summary['postflight_health_passed']}`",
            f"- Local RAG 접근: `{summary['local_rag_touched']}`",
            f"- 실행 오류: `{summary['execution_error_type'] or '없음'}`",
            "",
            "상세 수치는 `external-stress/report.md`와 `external-stress/report.html`을 확인합니다.",
            "",
        ]
    )


def _render_campaign_html(summary: Mapping[str, object]) -> str:
    return f"""<!doctype html>
<html lang='ko'><head><meta charset='utf-8'><meta name='viewport'
content='width=device-width,initial-scale=1'><title>External RAG Stress Campaign</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 18px;
line-height:1.7}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;
padding:9px;text-align:left}}code{{font-family:monospace}}</style></head><body>
<h1>외부 RAG Stress Campaign</h1><table><tbody>
<tr><th>실행 방식</th><td><code>{_escape_object(summary["execution_mode"])}</code></td></tr>
<tr><th>대상</th><td><code>{_escape_object(summary["target_origin"])}</code></td></tr>
<tr><th>환경</th><td><code>{_escape_object(summary["target_environment"])}</code></td></tr>
<tr><th>데이터 Source</th><td><code>{_escape_object(summary["selection_source"])}</code></td></tr>
<tr><th>선정 Seed</th><td><code>{
        _escape_object(summary["selection_seed"] or "configured")
    }</code></td></tr>
<tr><th>선정 User IDX</th><td><code>{_escape_object(summary["selected_user_idx"])}</code></td></tr>
<tr><th>선정 File 수</th><td><code>{_escape_object(summary["selected_file_count"])}</code></td></tr>
<tr><th>Profile</th><td><code>{_escape_object(summary["profile"])}</code></td></tr>
<tr><th>사전 Health</th><td>{summary["preflight_health_passed"]}</td></tr>
<tr><th>사후 Health</th><td>{summary["postflight_health_passed"]}</td></tr>
<tr><th>Local RAG 접근</th><td>{summary["local_rag_touched"]}</td></tr>
<tr><th>실행 오류</th><td>{_escape_object(summary["execution_error_type"] or "없음")}</td></tr>
</tbody></table><p><a href='external-stress/report.html'>상세 HTML 보고서</a></p>
</body></html>"""


def _parse_arguments() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    repository_root = project_root.parent
    parser = argparse.ArgumentParser(
        description=(
            "Run staged limit tests against an external RAG HTTP endpoint while "
            "loading target and test data from the existing RAG environment."
        )
    )
    parser.add_argument(
        "--rag-env-file",
        type=Path,
        default=repository_root / "RAG/.env.local",
    )
    parser.add_argument(
        "--target-config",
        type=Path,
        default=None,
        help="Optional manual target JSON. Automatic environment discovery is the default.",
    )
    parser.add_argument(
        "--data-source",
        choices=("auto", "qdrant", "database", "snapshot"),
        default="auto",
    )
    parser.add_argument("--snapshot-path", type=Path, default=None)
    parser.add_argument(
        "--snapshot-search-root",
        action="append",
        type=Path,
        default=[],
    )
    parser.add_argument("--files-per-user", type=int, default=2)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--random-seed", type=int, default=None)
    parser.add_argument("--qdrant-scan-limit", type=int, default=4096)
    parser.add_argument(
        "--stress-plan",
        type=Path,
        default=project_root / "configs/stress-plan-quick.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=project_root / "artifacts/external-stress",
    )
    parser.add_argument("--allow-destructive", action="store_true")
    parser.add_argument("--allow-production-target", action="store_true")
    parser.add_argument("--confirm-target-host")
    parser.add_argument("--allow-insecure-http", action="store_true")
    parser.add_argument("--allow-loopback-target", action="store_true")
    parser.add_argument("--disable-tls-verification", action="store_true")
    parser.add_argument("--execution-command", default="")
    parser.add_argument(
        "--readme-markdown",
        type=Path,
        default=project_root / "README.md",
    )
    parser.add_argument(
        "--readme-html",
        type=Path,
        default=project_root / "README.html",
    )
    parser.add_argument("--skip-readme-update", action="store_true")
    parser.add_argument("--quality-gate-skipped", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_arguments()
    project_root = Path(__file__).resolve().parents[2]
    repository_root = project_root.parent
    settings = load_rag_environment(args.rag_env_file.resolve())

    # 사용자가 Token을 다시 입력하지 않도록 기존 RAG 환경의 RAG_INGEST_TOKEN을 현재
    # Process에만 주입한다. 값은 명령 문자열과 결과 파일에 기록하지 않는다.
    os.environ[_TOKEN_ENV] = settings.ingest_token
    internal_token = settings.ingest_token
    verify_tls = not args.disable_tls_verification

    if args.target_config is not None:
        target = load_external_target_config(
            args.target_config.resolve(),
            allow_insecure_http=args.allow_insecure_http,
            allow_loopback_target=args.allow_loopback_target,
        )
    else:
        default_snapshot_roots = (
            project_root / "snapshots",
            repository_root,
            settings.source_path.parent,
        )
        snapshot_roots = tuple(args.snapshot_search_root) or default_snapshot_roots
        discovered = discover_test_data(
            settings,
            source=cast(DataSource, args.data_source),
            files_per_user=args.files_per_user,
            query_count=args.query_count,
            random_seed=args.random_seed,
            snapshot_path=args.snapshot_path,
            snapshot_search_roots=snapshot_roots,
            qdrant_scan_limit=args.qdrant_scan_limit,
        )
        target = build_external_target_config(
            settings,
            discovered,
            allow_insecure_http=args.allow_insecure_http,
            allow_loopback_target=args.allow_loopback_target,
        )
        target = validate_search_scope(
            target,
            internal_token=internal_token,
            verify_tls=verify_tls,
        )

    stress_plan = load_stress_suite_plan(args.stress_plan.resolve())
    run_id = f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    campaign_directory = args.output_root.resolve() / run_id
    execution_command = args.execution_command.strip() or (
        "uv run jipsa-rag-stress "
        f"--rag-env-file '{args.rag_env_file.resolve()}' "
        f"--data-source '{args.data_source}' "
        f"--stress-plan '{args.stress_plan.resolve()}'"
    )
    campaign = ExternalStagedStressCampaign(
        target=target,
        stress_plan=stress_plan,
        campaign_directory=campaign_directory,
        internal_token=internal_token,
        allow_destructive=args.allow_destructive,
        allow_production_target=args.allow_production_target,
        confirm_target_host=args.confirm_target_host,
        verify_tls=verify_tls,
        execution_command=execution_command,
        readme_markdown_path=args.readme_markdown.resolve(),
        readme_html_path=args.readme_html.resolve(),
        update_readme=not args.skip_readme_update,
        quality_gate_skipped=args.quality_gate_skipped,
    )
    report_path = campaign.run()
    print(f"External staged stress campaign report: {report_path}")
    return 0


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
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


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_error_message(error: BaseException) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    return (message or type(error).__name__)[:256]


def _format_number(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _nullable(value: object) -> str:
    return "-" if value is None else str(value)


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _escape_object(value: object) -> str:
    """HTML Escape 전에 보고서 값을 명시적으로 문자열화한다."""

    return _escape(str(value))


def _relative_link(base_directory: Path, target: Path) -> str:
    return Path(os.path.relpath(target, start=base_directory)).as_posix()


def _utc_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat(timespec="milliseconds")


if __name__ == "__main__":
    raise SystemExit(main())
