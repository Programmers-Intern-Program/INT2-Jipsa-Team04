"""독립 프로세스 방식의 Jipsa Local RAG 자원·처리 한계 측정 실행기.

측정 프로그램은 ``RAG`` 소스 트리 밖에서 실행되며 다음 프로세스를 분리한다.

- 부하 생성·보고서 작성: ``RAG-Performance`` 자체 uv 환경
- 측정 대상 FastAPI·EasyOCR: ``RAG`` uv 환경의 별도 Python 프로세스
- CUDA TEI·Qdrant: 기존 Docker Compose 컨테이너

서비스 설정이나 운영 제한값은 변경하지 않는다. 전용 사용자와 File_IDX, 합성 Fixture만
사용하여 현재 구현의 자원 사용량, 지연시간, 처리량과 최초 포화 후보를 측정한다.
"""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import os
import platform
import secrets
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TextIO, cast
from uuid import uuid4

import httpx
import psutil

from jipsa_rag_benchmark.dotenv_loader import (
    build_child_environment,
    get_required_secret,
    read_dotenv,
)
from jipsa_rag_benchmark.fixture_factory import FixtureFactory
from jipsa_rag_benchmark.models import (
    BenchmarkOperation,
    BenchmarkPhase,
    BenchmarkPlan,
    GeneratedFixture,
    LevelSummary,
    RequestRecord,
    SaturationCandidate,
    StageEvent,
    detect_saturation_candidate,
    load_benchmark_plan,
    summarize_level,
)
from jipsa_rag_benchmark.resource_sampler import (
    ResourceSample,
    ResourceSampler,
    capture_host_io_snapshot,
    summarize_resource_samples,
)

_STAGE_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "file_download_completed",
        "document_parsing_ocr_completed",
        "document_chunking_completed",
        "document_embedding_completed",
        "file_indexing_completed",
        "file_processing_completed",
    }
)
_SAFE_RAG_ENVIRONMENT_NAMES: Final[tuple[str, ...]] = (
    "JIPSA_RAG_APP_ENV",
    "JIPSA_RAG_EMBEDDING_PROVIDER",
    "JIPSA_RAG_EMBEDDING_BASE_URL",
    "JIPSA_RAG_EMBEDDING_MODEL",
    "JIPSA_RAG_EMBEDDING_DIM",
    "JIPSA_RAG_EMBEDDING_BATCH_SIZE",
    "JIPSA_RAG_VECTOR_DB_PROVIDER",
    "JIPSA_RAG_QDRANT_URL",
    "JIPSA_RAG_QDRANT_COLLECTION",
    "JIPSA_RAG_QDRANT_PREFER_GRPC",
    "JIPSA_RAG_IMAGE_EXTRACTION_ENABLED",
    "JIPSA_RAG_OCR_ENABLED",
    "JIPSA_RAG_OCR_GPU",
    "JIPSA_RAG_OCR_GPU_REQUIRED",
    "JIPSA_RAG_OCR_DEVICE",
    "JIPSA_RAG_OCR_MAX_CONCURRENCY",
    "JIPSA_RAG_CHUNK_SIZE_CHARS",
    "JIPSA_RAG_CHUNK_OVERLAP_CHARS",
    "JIPSA_RAG_INDEX_VERSION",
)


class TargetLogCollector:
    """대상 프로세스 stdout을 원본 파일로 보존하고 단계 이벤트를 추출한다."""

    def __init__(self, *, run_id: str, output_path: Path) -> None:
        self._run_id = run_id
        self._output_path = output_path
        self._events: list[StageEvent] = []
        self._events_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def events(self) -> tuple[StageEvent, ...]:
        """현재까지 수집된 단계 이벤트의 불변 복사본."""

        with self._events_lock:
            return tuple(self._events)

    def start(self, stream: TextIO) -> None:
        """대상 stdout을 읽는 Thread를 시작한다."""

        if self._thread is not None:
            raise RuntimeError("TargetLogCollector has already been started.")
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._read_stream,
            args=(stream,),
            name="jipsa-rag-performance-target-log",
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout_seconds: float = 30.0) -> None:
        """대상 종료 후 stdout Thread가 남은 출력을 모두 기록하도록 기다린다."""

        if self._thread is None:
            return
        self._thread.join(timeout=timeout_seconds)
        if self._thread.is_alive():
            raise RuntimeError("Target log collector did not stop within the timeout.")

    def _read_stream(self, stream: TextIO) -> None:
        with self._output_path.open("w", encoding="utf-8", newline="") as output:
            for line in stream:
                output.write(line)
                output.flush()
                event = self._parse_stage_event(line)
                if event is not None:
                    with self._events_lock:
                        self._events.append(event)

    def _parse_stage_event(self, line: str) -> StageEvent | None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        event = payload.get("event")
        if not isinstance(event, str) or event not in _STAGE_EVENTS:
            return None
        file_idx = _optional_positive_int(payload.get("file_idx"))
        duration_ms = _optional_float(
            payload.get("duration_ms", payload.get("total_duration_ms"))
        )
        if file_idx is None or duration_ms is None:
            return None

        timestamp = payload.get("timestamp")
        completed_epoch = _parse_timestamp(timestamp) or time.time()
        stage = payload.get("stage")
        file_type = payload.get("file_type")
        request_id = payload.get("request_id")
        return StageEvent(
            run_id=self._run_id,
            request_id=request_id if isinstance(request_id, str) else None,
            file_idx=file_idx,
            file_type=file_type if isinstance(file_type, str) else "unknown",
            stage=(
                stage
                if isinstance(stage, str) and stage
                else "file_processing" if event == "file_processing_completed" else event
            ),
            event=event,
            completed_at_utc=_utc_iso(completed_epoch),
            completed_epoch_seconds=completed_epoch,
            duration_ms=duration_ms,
            chunk_count=_optional_non_negative_int(payload.get("chunk_count")),
            structure_unit_count=_optional_non_negative_int(
                payload.get("structure_unit_count")
            ),
            text_unit_count=_optional_non_negative_int(payload.get("text_unit_count")),
            size_bytes=_optional_non_negative_int(payload.get("size_bytes")),
        )


class BenchmarkRunner:
    """인프라·대상 서버·부하·자원·보고서의 전체 실행 순서를 관리한다."""

    def __init__(
        self,
        *,
        plan: BenchmarkPlan,
        rag_root: Path,
        run_id: str,
        run_directory: Path,
        target_host: str,
        target_port: int,
        disable_answers: bool,
        keep_test_data: bool,
        keep_infrastructure_running: bool,
        preserve_running_infrastructure: bool,
    ) -> None:
        self._plan = plan
        self._rag_root = rag_root.resolve()
        self._run_id = run_id
        self._run_directory = run_directory.resolve()
        self._target_host = target_host
        self._target_port = target_port
        self._target_base_url = f"http://{target_host}:{target_port}"
        self._disable_answers = disable_answers
        self._keep_test_data = keep_test_data
        self._keep_infrastructure_running = keep_infrastructure_running
        self._preserve_running_infrastructure = preserve_running_infrastructure

        self._dotenv_path = self._rag_root / ".env.local"
        self._compose_path = self._rag_root / "infra/qdrant/compose.yaml"
        self._target_script = Path(__file__).with_name("target_server.py")
        self._fixture_factory = FixtureFactory(
            output_directory=self._run_directory / "fixtures",
            file_idx_start=plan.file_idx_start,
        )
        self._base_fixtures: tuple[GeneratedFixture, ...] = ()
        self._all_fixtures: list[GeneratedFixture] = []
        self._cold_warm_cases: dict[str, GeneratedFixture] = {}
        self._ingest_concurrency_cases: dict[int, tuple[GeneratedFixture, ...]] = {}

        self._environment = build_child_environment(read_dotenv(self._dotenv_path))
        self._ingest_token = get_required_secret(
            self._environment,
            "RAG_INGEST_TOKEN",
            "JIPSA_RAG_INGEST_TOKEN",
        )
        self._benchmark_token = secrets.token_urlsafe(48)

        self._request_lock = threading.Lock()
        self._request_counter = 0
        self._request_records: list[RequestRecord] = []
        self._level_summaries: list[LevelSummary] = []
        self._host_io_records: list[dict[str, object]] = []
        self._target_process: subprocess.Popen[str] | None = None
        self._target_log_collector = TargetLogCollector(
            run_id=run_id,
            output_path=self._run_directory / "target.log",
        )
        self._client: httpx.Client | None = None
        self._sampler = ResourceSampler(
            output_path=self._run_directory / "resource_samples.jsonl",
            sample_interval_seconds=plan.sample_interval_seconds,
            docker_sample_interval_seconds=plan.docker_sample_interval_seconds,
        )
        self._infrastructure_started = False

    def run(self) -> Path:
        """전체 측정을 실행하고 최종 Markdown 보고서 경로를 반환한다."""

        self._validate_preflight()
        self._run_directory.mkdir(parents=True, exist_ok=False)
        self._prepare_fixtures_and_cases()
        _write_json(self._run_directory / "benchmark_plan.resolved.json", asdict(self._plan))

        execution_error: BaseException | None = None
        self._sampler.start()
        try:
            self._start_infrastructure()
            self._start_target_process()
            environment = self._collect_environment()
            _write_json(self._run_directory / "environment.json", environment)

            with httpx.Client(
                base_url=self._target_base_url,
                headers={"X-Internal-Token": self._ingest_token},
                timeout=httpx.Timeout(self._plan.request_timeout_seconds),
                trust_env=False,
            ) as client:
                self._client = client
                self._run_cold_warm_ingest()
                self._run_format_coverage_and_scale_ingest()
                self._run_ingest_concurrency()
                self._run_search_measurements()
                self._run_answer_measurements()
        # 일반 실행 오류와 사용자의 Ctrl+C 중단에서도 이미 수집한 표본과 요청 결과를
        # 먼저 파일로 보존한 뒤 finally 정리 후 동일한 오류를 다시 전달한다.
        except (Exception, KeyboardInterrupt) as error:
            execution_error = error
        finally:
            self._client = None
            try:
                self._cleanup_and_stop_target()
            finally:
                try:
                    self._stop_infrastructure_if_owned()
                finally:
                    self._sampler.stop()

        environment_path = self._run_directory / "environment.json"
        environment = (
            cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
            if environment_path.is_file()
            else self._collect_environment(best_effort=True)
        )
        self._write_outputs(environment=environment, execution_error=execution_error)
        if execution_error is not None:
            raise execution_error
        return self._run_directory / "report.md"

    def _validate_preflight(self) -> None:
        """파일·명령·CUDA·토큰과 전용 ID 범위를 실제 실행 전에 확인한다."""

        required_files = (
            self._rag_root / "pyproject.toml",
            self._dotenv_path,
            self._compose_path,
            self._target_script,
        )
        missing = [str(path) for path in required_files if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"Required files are missing: {missing}")

        for command in ("git", "uv", "docker", "nvidia-smi"):
            if shutil.which(command) is None:
                raise RuntimeError(f"Required command is not available in PATH: {command}")

        if self._plan.test_user_idx <= 0 or self._plan.file_idx_start <= 0:
            raise ValueError("Performance test IDs must be positive integers.")
        if not 1 <= self._target_port <= 65535:
            raise ValueError("target_port must be between 1 and 65535.")
        if self._disable_answers:
            return

        api_key = self._environment.get("JIPSA_RAG_ANTHROPIC_API_KEY") or self._environment.get(
            "ANTHROPIC_API_KEY"
        )
        if self._plan.answers.enabled and not api_key:
            raise RuntimeError(
                "Answer measurement is enabled but the Anthropic API key is not configured."
            )

    def _prepare_fixtures_and_cases(self) -> None:
        """형식·OCR·크기·동시성에 필요한 모든 File_IDX를 대상 시작 전에 확정한다."""

        self._base_fixtures = self._fixture_factory.generate(self._plan)
        if not self._base_fixtures:
            raise RuntimeError("No performance fixtures were generated.")
        self._all_fixtures.extend(self._base_fixtures)

        text_source = self._find_fixture(content_origin="text", preferred_format="pdf")
        ocr_source = self._find_fixture(content_origin="ocr", preferred_format="pdf")
        for label, source in (
            ("cold_text", text_source),
            ("warm_text", text_source),
            ("cold_ocr", ocr_source),
            ("warm_ocr", ocr_source),
        ):
            clone = self._fixture_factory.allocate_clone(source, purpose=label, ordinal=1)
            self._cold_warm_cases[label] = clone
            self._all_fixtures.append(clone)

        for concurrency in self._plan.ingest.concurrency_levels:
            cases = tuple(
                self._fixture_factory.allocate_clone(
                    text_source,
                    purpose=f"ingest-concurrency-{concurrency}",
                    ordinal=index + 1,
                )
                for index in range(self._plan.ingest.requests_per_level)
            )
            self._ingest_concurrency_cases[concurrency] = cases
            self._all_fixtures.extend(cases)

        _write_json(
            self._run_directory / "all_owned_fixtures.json",
            {
                "schema_version": 1,
                "fixtures": [fixture.to_public_dict() for fixture in self._all_fixtures],
            },
        )

    def _start_infrastructure(self) -> None:
        """Cold Start 측정을 위해 Qdrant·TEI를 필요 시 정지 후 시작한다."""

        compose = self._compose_arguments()
        if not self._preserve_running_infrastructure:
            self._sampler.set_context(
                case_id="cold-start-infrastructure",
                operation="startup",
                phase="cold",
                concurrency=0,
            )
            _run_command(
                ["docker", *compose, "stop", "qdrant", "embedding"],
                cwd=self._rag_root,
                timeout_seconds=180.0,
                allow_failure=True,
            )

        _run_command(
            ["docker", *compose, "up", "-d", "qdrant", "embedding"],
            cwd=self._rag_root,
            timeout_seconds=1800.0,
        )
        self._infrastructure_started = True
        self._wait_http_ready(
            _safe_environment_url(
                self._environment,
                "JIPSA_RAG_QDRANT_URL",
                "http://127.0.0.1:6333",
            )
            + "/readyz",
            timeout_seconds=180.0,
        )
        embedding_base_url = _safe_environment_url(
            self._environment,
            "JIPSA_RAG_EMBEDDING_BASE_URL",
            "http://127.0.0.1:18081",
        )
        self._wait_any_http_ready(
            (f"{embedding_base_url}/health", f"{embedding_base_url}/info"),
            timeout_seconds=1200.0,
        )
        self._sampler.reset_context()

    def _start_target_process(self) -> None:
        """RAG uv 환경에서 별도 FastAPI 대상 프로세스를 시작하고 준비를 기다린다."""

        fixture_manifest = self._run_directory / "all_owned_fixtures.json"
        download_temp = self._run_directory / "download-temp"
        child_environment = build_child_environment(
            read_dotenv(self._dotenv_path),
            overrides={
                "JIPSA_RAG_APP_ENV": "test",
                "JIPSA_RAG_LOG_FORMAT": "json",
                "JIPSA_RAG_LOG_LEVEL": "INFO",
                "JIPSA_RAG_DEBUG": "false",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            },
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
        ]
        if self._keep_test_data:
            command.append("--keep-test-data")

        self._sampler.set_context(
            case_id="cold-start-rag-target",
            operation="startup",
            phase="cold",
            concurrency=0,
        )
        process = subprocess.Popen(
            command,
            cwd=self._rag_root,
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._target_process = process
        # uv Wrapper PID를 먼저 추적하면 Python Target이 준비되기 전 시작 구간도
        # 자식 Process Tree로 포함된다. Health 응답 후 실제 Python PID로 교체한다.
        self._sampler.set_target_pid(process.pid)
        if process.stdout is None:
            raise RuntimeError("Target process stdout pipe was not created.")
        self._target_log_collector.start(process.stdout)

        health = self._wait_target_ready(timeout_seconds=300.0)
        pid = _optional_positive_int(health.get("pid"))
        if pid is None:
            raise RuntimeError("Benchmark target health did not return a valid PID.")
        self._sampler.set_target_pid(pid)
        self._sampler.reset_context()

    def _cleanup_and_stop_target(self) -> None:
        process = self._target_process
        if process is None:
            return

        if process.poll() is None:
            try:
                with httpx.Client(
                    base_url=self._target_base_url,
                    headers={"X-Benchmark-Token": self._benchmark_token},
                    timeout=60.0,
                    trust_env=False,
                ) as client:
                    if not self._keep_test_data:
                        client.post(
                            "/__benchmark__/cleanup",
                            json={
                                "user_idx": self._plan.test_user_idx,
                                "file_idxs": [fixture.file_idx for fixture in self._all_fixtures],
                            },
                        ).raise_for_status()
                    client.post("/__benchmark__/shutdown").raise_for_status()
            except (httpx.HTTPError, OSError):
                # 대상이 이미 비정상 종료했거나 관리 요청을 받을 수 없는 상태라면 아래
                # wait/terminate 절차로 정리한다. 비밀 토큰이나 응답 본문은 기록하지 않는다.
                pass

        try:
            process.wait(timeout=60.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=20.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=20.0)
        finally:
            self._sampler.set_target_pid(None)
            self._target_log_collector.join()
            self._target_process = None

    def _stop_infrastructure_if_owned(self) -> None:
        if (
            not self._infrastructure_started
            or self._keep_infrastructure_running
            or self._preserve_running_infrastructure
        ):
            return
        _run_command(
            ["docker", *self._compose_arguments(), "stop", "qdrant", "embedding"],
            cwd=self._rag_root,
            timeout_seconds=180.0,
            allow_failure=True,
        )

    def _compose_arguments(self) -> list[str]:
        return [
            "compose",
            "--env-file",
            str(self._dotenv_path),
            "--file",
            str(self._compose_path),
        ]

    def _wait_target_ready(self, *, timeout_seconds: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            process = self._target_process
            if process is not None and process.poll() is not None:
                raise RuntimeError(
                    f"Benchmark target exited before readiness with code {process.returncode}."
                )
            try:
                response = httpx.get(
                    f"{self._target_base_url}/__benchmark__/health",
                    headers={"X-Benchmark-Token": self._benchmark_token},
                    timeout=5.0,
                    trust_env=False,
                )
                response.raise_for_status()
                body = response.json()
                if isinstance(body, dict) and body.get("success") is True:
                    return cast(dict[str, object], body)
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
            time.sleep(1.0)
        raise TimeoutError(
            "Benchmark target did not become ready within the timeout. "
            f"Last error type: {type(last_error).__name__ if last_error else 'none'}"
        )

    @staticmethod
    def _wait_http_ready(url: str, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url, timeout=5.0, trust_env=False)
                if 200 <= response.status_code < 300:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2.0)
        raise TimeoutError(f"Service did not become ready: {url}")

    @classmethod
    def _wait_any_http_ready(cls, urls: Sequence[str], *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for url in urls:
                try:
                    response = httpx.get(url, timeout=5.0, trust_env=False)
                    if 200 <= response.status_code < 300:
                        return
                except httpx.HTTPError:
                    continue
            time.sleep(3.0)
        raise TimeoutError(f"No service readiness endpoint succeeded: {list(urls)}")

    def _run_cold_warm_ingest(self) -> None:
        for label, phase in (
            ("cold_text", "cold"),
            ("warm_text", "warm"),
            ("cold_ocr", "cold"),
            ("warm_ocr", "warm"),
        ):
            fixture = self._cold_warm_cases[label]
            self._run_request_batch(
                case_id=label,
                operation="ingest",
                phase=cast(BenchmarkPhase, phase),
                concurrency=1,
                tasks=(
                    lambda fixture=fixture, phase=phase: self._ingest_request(
                        fixture,
                        phase=cast(BenchmarkPhase, phase),
                    ),
                ),
            )

    def _run_format_coverage_and_scale_ingest(self) -> None:
        for fixture in self._base_fixtures:
            phase: BenchmarkPhase = "scale" if fixture.group.startswith("scale") else "coverage"
            self._run_request_batch(
                case_id=fixture.case_id,
                operation="ingest",
                phase=phase,
                concurrency=1,
                tasks=(
                    lambda fixture=fixture, phase=phase: self._ingest_request(
                        fixture,
                        phase=phase,
                    ),
                ),
            )

    def _run_ingest_concurrency(self) -> None:
        for concurrency, fixtures in self._ingest_concurrency_cases.items():
            tasks = tuple(
                lambda fixture=fixture, concurrency=concurrency: self._ingest_request(
                    fixture,
                    phase="concurrency",
                    concurrency=concurrency,
                )
                for fixture in fixtures
            )
            self._run_request_batch(
                case_id=f"ingest-concurrency-{concurrency}",
                operation="ingest",
                phase="concurrency",
                concurrency=concurrency,
                tasks=tasks,
            )

    def _run_search_measurements(self) -> None:
        fixture = self._cold_warm_cases["warm_text"]
        payload = self._search_payload(fixture)
        self._run_request_batch(
            case_id="search-cold",
            operation="search",
            phase="cold",
            concurrency=1,
            tasks=(lambda: self._api_request("search-cold", "search", "cold", 1, payload),),
        )
        for _ in range(self._plan.warmup_requests):
            self._api_request("search-warmup", "search", "warm", 1, payload, store=False)
        self._run_request_batch(
            case_id="search-warm",
            operation="search",
            phase="warm",
            concurrency=1,
            tasks=(lambda: self._api_request("search-warm", "search", "warm", 1, payload),),
        )

        for concurrency in self._plan.search.load.concurrency_levels:
            tasks = tuple(
                lambda concurrency=concurrency: self._api_request(
                    f"search-concurrency-{concurrency}",
                    "search",
                    "concurrency",
                    concurrency,
                    payload,
                )
                for _ in range(self._plan.search.load.requests_per_level)
            )
            self._run_request_batch(
                case_id=f"search-concurrency-{concurrency}",
                operation="search",
                phase="concurrency",
                concurrency=concurrency,
                tasks=tasks,
            )

    def _run_answer_measurements(self) -> None:
        if self._disable_answers or not self._plan.answers.enabled:
            return

        lookup_fixture = self._cold_warm_cases["warm_text"]
        lookup_payload = self._answer_lookup_payload(lookup_fixture)
        synthesis_fixtures = self._synthesis_fixtures()
        synthesis_payload = self._answer_synthesis_payload(synthesis_fixtures)

        for operation, payload in (
            ("answer_lookup", lookup_payload),
            ("answer_synthesis", synthesis_payload),
        ):
            operation_value = cast(BenchmarkOperation, operation)
            self._run_request_batch(
                case_id=f"{operation}-cold",
                operation=operation_value,
                phase="cold",
                concurrency=1,
                tasks=(
                    lambda operation=operation_value, payload=payload: self._api_request(
                        f"{operation}-cold",
                        operation,
                        "cold",
                        1,
                        payload,
                    ),
                ),
            )
            for _ in range(self._plan.warmup_requests):
                self._api_request(
                    f"{operation}-warmup",
                    operation_value,
                    "warm",
                    1,
                    payload,
                    store=False,
                )
            self._run_request_batch(
                case_id=f"{operation}-warm",
                operation=operation_value,
                phase="warm",
                concurrency=1,
                tasks=(
                    lambda operation=operation_value, payload=payload: self._api_request(
                        f"{operation}-warm",
                        operation,
                        "warm",
                        1,
                        payload,
                    ),
                ),
            )

        self._run_answer_concurrency(
            operation="answer_lookup",
            payload=lookup_payload,
            levels=self._plan.answers.lookup.concurrency_levels,
            requests_per_level=self._plan.answers.lookup.requests_per_level,
        )
        self._run_answer_concurrency(
            operation="answer_synthesis",
            payload=synthesis_payload,
            levels=self._plan.answers.synthesis.concurrency_levels,
            requests_per_level=self._plan.answers.synthesis.requests_per_level,
        )

    def _run_answer_concurrency(
        self,
        *,
        operation: BenchmarkOperation,
        payload: dict[str, object],
        levels: Sequence[int],
        requests_per_level: int,
    ) -> None:
        for concurrency in levels:
            tasks = tuple(
                lambda concurrency=concurrency: self._api_request(
                    f"{operation}-concurrency-{concurrency}",
                    operation,
                    "concurrency",
                    concurrency,
                    payload,
                )
                for _ in range(requests_per_level)
            )
            self._run_request_batch(
                case_id=f"{operation}-concurrency-{concurrency}",
                operation=operation,
                phase="concurrency",
                concurrency=concurrency,
                tasks=tasks,
            )

    def _ingest_request(
        self,
        fixture: GeneratedFixture,
        *,
        phase: BenchmarkPhase,
        concurrency: int = 1,
    ) -> RequestRecord:
        return self._api_request(
            fixture.case_id,
            "ingest",
            phase,
            concurrency,
            fixture.to_manifest(user_idx=self._plan.test_user_idx),
            fixture=fixture,
        )

    def _api_request(
        self,
        case_id: str,
        operation: BenchmarkOperation,
        phase: BenchmarkPhase,
        concurrency: int,
        payload: dict[str, object],
        *,
        fixture: GeneratedFixture | None = None,
        store: bool = True,
    ) -> RequestRecord:
        client = self._required_client()
        path = {
            "ingest": "/api/v1/files/process",
            "search": "/api/v1/chunks/search",
            "answer_lookup": "/api/v1/rag/answers",
            "answer_synthesis": "/api/v1/rag/answers",
        }[operation]
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
        input_tokens: int | None = None
        output_tokens: int | None = None
        error_type: str | None = None
        error_message: str | None = None

        try:
            response = client.post(path, json=payload, headers={"X-Request-ID": request_id})
            status_code = response.status_code
            response_bytes = len(response.content)
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("API response root is not an object.")
            success = status_code == 200 and body.get("success") is True
            data = body.get("data")
            if isinstance(data, dict):
                if operation == "ingest":
                    chunk_count = _optional_non_negative_int(data.get("chunk_count"))
                elif operation == "search":
                    chunk_count = _optional_non_negative_int(data.get("result_count"))
                else:
                    usage = data.get("usage")
                    if isinstance(usage, dict):
                        input_tokens = _optional_non_negative_int(usage.get("input_tokens"))
                        output_tokens = _optional_non_negative_int(usage.get("output_tokens"))
            if not success:
                code = body.get("code")
                error_type = str(code)[:128] if code is not None else "api_error"
                error_message = "The API returned a non-success response."
        # 부하 요청 하나의 네트워크·직렬화 오류는 전체 Level 실행을 중단하지 않고
        # 실패 표본으로 기록해야 포화 지점의 실제 오류율을 계산할 수 있다.
        except Exception as error:
            error_type = type(error).__name__
            error_message = _safe_error_message(error)

        completed_epoch = time.time()
        record = RequestRecord(
            run_id=self._run_id,
            request_id=request_id,
            case_id=case_id,
            operation=operation,
            phase=phase,
            concurrency=concurrency,
            request_index=self._next_request_index(),
            started_at_utc=_utc_iso(started_epoch),
            started_epoch_seconds=started_epoch,
            completed_epoch_seconds=completed_epoch,
            duration_ms=(time.perf_counter() - started_perf) * 1000.0,
            status_code=status_code,
            success=success,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            file_idx=fixture.file_idx if fixture is not None else _first_file_idx(payload),
            file_type=fixture.file_type if fixture is not None else None,
            profile_name=fixture.profile_name if fixture is not None else None,
            content_origin=fixture.content_origin if fixture is not None else None,
            fixture_size_bytes=fixture.size_bytes if fixture is not None else None,
            declared_text_units=fixture.text_units if fixture is not None else None,
            declared_image_count=fixture.image_count if fixture is not None else None,
            chunk_count=chunk_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_type=error_type,
            error_message=error_message,
        )
        if store:
            with self._request_lock:
                self._request_records.append(record)
        return record

    def _run_request_batch(
        self,
        *,
        case_id: str,
        operation: BenchmarkOperation,
        phase: BenchmarkPhase,
        concurrency: int,
        tasks: Sequence[Callable[[], RequestRecord]],
    ) -> tuple[RequestRecord, ...]:
        if not tasks:
            raise ValueError("A benchmark batch requires at least one task.")
        if concurrency <= 0:
            raise ValueError("concurrency must be greater than zero.")

        self._sampler.set_context(
            case_id=case_id,
            operation=operation,
            phase=phase,
            concurrency=concurrency,
        )
        host_start = capture_host_io_snapshot()
        started = time.perf_counter()
        records: list[RequestRecord] = []
        try:
            if concurrency == 1:
                records = [task() for task in tasks]
            else:
                with ThreadPoolExecutor(
                    max_workers=concurrency,
                    thread_name_prefix=f"jipsa-{operation}-{concurrency}",
                ) as executor:
                    futures = [executor.submit(task) for task in tasks]
                    for future in as_completed(futures):
                        records.append(future.result())
        finally:
            elapsed_seconds = max(time.perf_counter() - started, 1e-9)
            host_end = capture_host_io_snapshot()
            self._sampler.reset_context()

        self._level_summaries.append(
            summarize_level(
                tuple(records),
                operation=operation,
                phase=phase,
                concurrency=concurrency,
                elapsed_seconds=elapsed_seconds,
            )
        )
        self._host_io_records.append(
            {
                "run_id": self._run_id,
                "case_id": case_id,
                "operation": operation,
                "phase": phase,
                "concurrency": concurrency,
                "started_at_utc": host_start.timestamp_utc,
                "completed_at_utc": host_end.timestamp_utc,
                "elapsed_seconds": elapsed_seconds,
                **host_end.delta(host_start),
                "start_error": host_start.error,
                "end_error": host_end.error,
            }
        )
        return tuple(records)

    def _search_payload(self, fixture: GeneratedFixture) -> dict[str, object]:
        return {
            "user_idx": self._plan.test_user_idx,
            "reference_file_idxs": [fixture.file_idx],
            "query": fixture.search_query,
            "top_k": self._plan.search.top_k,
            "score_threshold": self._plan.search.score_threshold,
        }

    def _answer_lookup_payload(self, fixture: GeneratedFixture) -> dict[str, object]:
        return {
            "user_idx": self._plan.test_user_idx,
            "reference_file_idxs": [fixture.file_idx],
            "query": (
                "문서에 명시된 format, profile, declared text units와 "
                "declared OCR images 값을 근거와 함께 알려줘"
            ),
            "top_k": self._plan.search.top_k,
            "score_threshold": self._plan.search.score_threshold,
        }

    def _answer_synthesis_payload(
        self,
        fixtures: Sequence[GeneratedFixture],
    ) -> dict[str, object]:
        return {
            "user_idx": self._plan.test_user_idx,
            "reference_file_idxs": [fixture.file_idx for fixture in fixtures],
            "query": (
                "선택한 두 문서의 format, profile, declared text units와 "
                "declared OCR images를 문서별로 비교하고 종합해줘"
            ),
            "top_k": self._plan.search.top_k,
            "score_threshold": self._plan.search.score_threshold,
        }

    def _synthesis_fixtures(self) -> tuple[GeneratedFixture, GeneratedFixture]:
        text_fixtures = [
            fixture
            for fixture in self._base_fixtures
            if fixture.content_origin == "text" and fixture.group == "format-coverage"
        ]
        if len(text_fixtures) < 2:
            raise RuntimeError("Synthesis measurement requires at least two text fixtures.")
        return text_fixtures[0], text_fixtures[1]

    def _find_fixture(self, *, content_origin: str, preferred_format: str) -> GeneratedFixture:
        preferred = next(
            (
                fixture
                for fixture in self._base_fixtures
                if fixture.content_origin == content_origin
                and fixture.file_type == preferred_format
            ),
            None,
        )
        if preferred is not None:
            return preferred
        fallback = next(
            (
                fixture
                for fixture in self._base_fixtures
                if fixture.content_origin == content_origin
            ),
            None,
        )
        if fallback is None:
            raise RuntimeError(f"No {content_origin} fixture is available.")
        return fallback

    def _required_client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError("Benchmark HTTP client is not active.")
        return self._client

    def _next_request_index(self) -> int:
        with self._request_lock:
            self._request_counter += 1
            return self._request_counter

    def _collect_environment(self, *, best_effort: bool = False) -> dict[str, object]:
        """RAG Git SHA, 하드웨어, CUDA, Docker와 안전한 설정을 기록한다."""

        def command(arguments: list[str], cwd: Path = self._rag_root) -> str | None:
            try:
                return _run_command(
                    arguments,
                    cwd=cwd,
                    timeout_seconds=120.0,
                    capture_output=True,
                ).strip()
            except Exception:
                if best_effort:
                    return None
                raise

        git_sha = command(["git", "rev-parse", "HEAD"])
        git_branch = command(["git", "branch", "--show-current"])
        git_status = command(["git", "status", "--porcelain"]) or ""
        memory = psutil.virtual_memory()
        nvidia = command(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
        rag_versions = command(
            [
                "uv",
                "run",
                "python",
                "-c",
                (
                    "import importlib.metadata as m,json,platform,torch;"
                    "names=['fastapi','torch','torchvision','easyocr','qdrant-client',"
                    "'sqlalchemy','anthropic','pymupdf','python-docx','python-pptx','openpyxl'];"
                    "payload={'python':platform.python_version(),"
                    "'torch_cuda':torch.version.cuda,"
                    "'cuda_available':torch.cuda.is_available(),"
                    "'packages':{n:m.version(n) for n in names}};"
                    "print(json.dumps(payload))"
                ),
            ]
        )
        try:
            parsed_rag_versions = json.loads(rag_versions) if rag_versions else None
        except json.JSONDecodeError:
            parsed_rag_versions = None

        package_versions: dict[str, str | None] = {}
        for name in ("httpx", "psutil", "pymupdf", "python-docx", "python-pptx", "openpyxl"):
            try:
                package_versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                package_versions[name] = None

        safe_settings = {
            name: self._environment.get(name)
            for name in _SAFE_RAG_ENVIRONMENT_NAMES
            if self._environment.get(name) is not None
        }
        return {
            "schema_version": 1,
            "run_id": self._run_id,
            "benchmark_name": self._plan.benchmark_name,
            "started_at_utc": _utc_iso(time.time()),
            "repository_root": str(self._rag_root.parent),
            "rag_root": str(self._rag_root),
            "rag_git_branch": git_branch,
            "rag_git_commit_sha": git_sha,
            "rag_git_worktree_dirty": bool(git_status),
            "rag_git_status_porcelain": git_status.splitlines(),
            "platform": platform.platform(),
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "physical_cpu_count": psutil.cpu_count(logical=False),
            "logical_cpu_count": psutil.cpu_count(logical=True),
            "total_memory_bytes": int(memory.total),
            "benchmark_python_version": platform.python_version(),
            "benchmark_python_executable": os.sys.executable,
            "benchmark_package_versions": package_versions,
            "rag_runtime_versions": parsed_rag_versions,
            "nvidia_smi_summary": nvidia.splitlines() if nvidia else [],
            "uv_version": command(["uv", "--version"]),
            "docker_version": command(
                ["docker", "version", "--format", "{{.Server.Version}}"]
            ),
            "docker_compose_version": command(["docker", "compose", "version", "--short"]),
            "safe_rag_settings": safe_settings,
            "target_base_url": self._target_base_url,
            "plan": asdict(self._plan),
        }

    def _write_outputs(
        self,
        *,
        environment: Mapping[str, object],
        execution_error: BaseException | None,
    ) -> None:
        requests = tuple(self._request_records)
        stages = self._target_log_collector.events
        samples = self._sampler.samples

        _write_csv(
            self._run_directory / "request_records.csv",
            [record.to_dict() for record in requests],
        )
        _write_csv(
            self._run_directory / "ingest_stage_events.csv",
            [event.to_dict() for event in stages],
        )
        _write_csv(
            self._run_directory / "level_summaries.csv",
            [summary.to_dict() for summary in self._level_summaries],
        )
        _write_csv(self._run_directory / "host_io_deltas.csv", self._host_io_records)
        self._sampler.write_csv(self._run_directory / "resource_samples.csv")

        resource_summaries = summarize_resource_samples(samples)
        stage_resources = _summarize_stage_resources(
            stages=stages,
            samples=samples,
            fixtures=self._all_fixtures,
        )
        _write_csv(self._run_directory / "resource_summaries.csv", resource_summaries)
        _write_csv(
            self._run_directory / "ingest_stage_resource_summary.csv",
            stage_resources,
        )

        saturation_candidates = _find_saturation_candidates(
            self._level_summaries,
            self._plan,
        )
        summary: dict[str, object] = {
            "schema_version": 1,
            "run_id": self._run_id,
            "benchmark_name": self._plan.benchmark_name,
            "rag_git_commit_sha": environment.get("rag_git_commit_sha"),
            "request_count": len(requests),
            "successful_request_count": sum(record.success for record in requests),
            "failed_request_count": sum(not record.success for record in requests),
            "stage_event_count": len(stages),
            "resource_sample_count": len(samples),
            "answers_measured": not self._disable_answers and self._plan.answers.enabled,
            "execution_error_type": type(execution_error).__name__ if execution_error else None,
            "level_summaries": [value.to_dict() for value in self._level_summaries],
            "saturation_candidates": [value.to_dict() for value in saturation_candidates],
            "transport_scope": {
                "api_execution": "Uvicorn HTTP in a separate RAG target process",
                "download": "HttpFileDownloader with local httpx2.MockTransport",
                "included": [
                    "FastAPI middleware and routing",
                    "download validation and temporary file I/O",
                    "PDF/DOCX/PPTX/XLSX/TXT parsers",
                    "CUDA EasyOCR",
                    "chunking",
                    "CUDA TEI",
                    "Local RAG DB",
                    "Qdrant",
                    "Claude when enabled",
                ],
                "excluded": ["AWS Backend", "S3 network latency"],
            },
        }
        _write_json(self._run_directory / "report.json", summary)
        _write_json(
            self._run_directory / "saturation_candidates.json",
            {
                "schema_version": 1,
                "run_id": self._run_id,
                "candidates": [value.to_dict() for value in saturation_candidates],
            },
        )
        report = _build_markdown_report(
            environment=environment,
            summary=summary,
            resource_summaries=resource_summaries,
            stage_resource_summaries=stage_resources,
            host_io_records=self._host_io_records,
        )
        (self._run_directory / "report.md").write_text(report, encoding="utf-8")


def _summarize_stage_resources(
    *,
    stages: Sequence[StageEvent],
    samples: Sequence[ResourceSample],
    fixtures: Sequence[GeneratedFixture],
) -> list[dict[str, object]]:
    fixtures_by_idx = {fixture.file_idx: fixture for fixture in fixtures}
    results: list[dict[str, object]] = []
    for event in stages:
        fixture = fixtures_by_idx.get(event.file_idx)
        matching = [
            sample
            for sample in samples
            if event.started_epoch_seconds <= sample.epoch_seconds <= event.completed_epoch_seconds
        ]
        row: dict[str, object] = {
            **event.to_dict(),
            "case_id": fixture.case_id if fixture else None,
            "profile_name": fixture.profile_name if fixture else None,
            "content_origin": fixture.content_origin if fixture else None,
            "fixture_size_bytes": fixture.size_bytes if fixture else None,
            "declared_image_count": fixture.image_count if fixture else None,
            "resource_sample_count": len(matching),
        }
        for field_name in (
            "target_cpu_percent_sum",
            "target_rss_bytes_sum",
            "target_gpu_memory_used_bytes_sum",
            "gpu_utilization_percent_max",
            "gpu_memory_used_bytes_sum",
            "tei_cpu_percent",
            "tei_memory_used_bytes",
            "qdrant_cpu_percent",
            "qdrant_memory_used_bytes",
        ):
            values = [
                float(value)
                for sample in matching
                if (value := getattr(sample, field_name)) is not None
            ]
            row[f"{field_name}_mean"] = sum(values) / len(values) if values else None
            row[f"{field_name}_max"] = max(values) if values else None
        results.append(row)
    return results


def _find_saturation_candidates(
    summaries: Sequence[LevelSummary],
    plan: BenchmarkPlan,
) -> tuple[SaturationCandidate, ...]:
    grouped: dict[BenchmarkOperation, list[LevelSummary]] = defaultdict(list)
    for summary in summaries:
        if summary.phase == "concurrency":
            grouped[summary.operation].append(summary)

    candidates: list[SaturationCandidate] = []
    for operation, values in grouped.items():
        candidate = detect_saturation_candidate(tuple(values), policy=plan.saturation)
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates)


def _build_markdown_report(
    *,
    environment: Mapping[str, object],
    summary: Mapping[str, object],
    resource_summaries: Sequence[Mapping[str, object]],
    stage_resource_summaries: Sequence[Mapping[str, object]],
    host_io_records: Sequence[Mapping[str, object]],
) -> str:
    lines = [
        "# Local RAG 자원 사용량 및 처리 한계 측정 보고서",
        "",
        "> 현재 구현을 측정한 결과이며 성능 개선, 운영 제한값 변경 또는 하드웨어 사양 "
        "확정은 포함하지 않습니다.",
        "",
        "## 실행 환경",
        "",
        f"- Run ID: `{summary.get('run_id')}`",
        f"- RAG Branch: `{environment.get('rag_git_branch')}`",
        f"- RAG Commit SHA: `{environment.get('rag_git_commit_sha')}`",
        f"- Worktree 변경 존재: `{environment.get('rag_git_worktree_dirty')}`",
        f"- OS: `{environment.get('platform')}`",
        f"- 전체 RAM: `{_format_bytes(environment.get('total_memory_bytes'))}`",
        f"- GPU: `{', '.join(cast(list[str], environment.get('nvidia_smi_summary', [])))}`",
        "",
        "## 측정 경계",
        "",
        "- 부하 생성기와 RAG FastAPI는 별도 Python 프로세스입니다.",
        "- API는 Uvicorn TCP/HTTP 경로로 호출합니다.",
        "- 합성 파일은 실제 `HttpFileDownloader`와 `MockTransport`를 거칩니다.",
        "- 포함: 다운로드 검증·임시 파일 I/O, 파서, CUDA EasyOCR, 청킹, CUDA TEI, "
        "Local RAG DB, Qdrant, 활성화 시 Claude",
        "- 제외: AWS Backend와 S3 실제 네트워크 지연",
        "",
        "## 요청 결과",
        "",
        f"- 전체 요청: {summary.get('request_count')}",
        f"- 성공 요청: {summary.get('successful_request_count')}",
        f"- 실패 요청: {summary.get('failed_request_count')}",
        f"- 자원 샘플: {summary.get('resource_sample_count')}",
        f"- 인제스트 단계 이벤트: {summary.get('stage_event_count')}",
        f"- 실행 오류: `{summary.get('execution_error_type') or '없음'}`",
        "",
        "### 동시성 단계",
        "",
        "| 작업 | 동시성 | 요청 | 성공 | 오류율 | 처리량(req/s) | p50(ms) | p95(ms) | p99(ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    raw_summaries = summary.get("level_summaries")
    if isinstance(raw_summaries, list):
        for raw in raw_summaries:
            if not isinstance(raw, Mapping) or raw.get("phase") != "concurrency":
                continue
            lines.append(
                "| {operation} | {concurrency} | {request_count} | {success_count} | "
                "{error_rate:.2%} | {throughput:.3f} | {p50} | {p95} | {p99} |".format(
                    operation=raw.get("operation"),
                    concurrency=raw.get("concurrency"),
                    request_count=raw.get("request_count"),
                    success_count=raw.get("success_count"),
                    error_rate=float(raw.get("error_rate") or 0.0),
                    throughput=float(raw.get("throughput_requests_per_second") or 0.0),
                    p50=_format_optional_number(raw.get("p50_ms")),
                    p95=_format_optional_number(raw.get("p95_ms")),
                    p99=_format_optional_number(raw.get("p99_ms")),
                )
            )

    lines.extend(["", "### 포화 후보", ""])
    candidates = summary.get("saturation_candidates")
    if isinstance(candidates, list) and candidates:
        for raw in candidates:
            if isinstance(raw, Mapping):
                lines.append(
                    f"- `{raw.get('operation')}` concurrency `{raw.get('concurrency')}`: "
                    f"`{raw.get('reason')}`"
                )
    else:
        lines.append("- 설정한 동시성 범위에서는 자동 포화 후보가 발견되지 않았습니다.")

    lines.extend(
        [
            "",
            "## Case별 자원 요약",
            "",
            "| Case | 작업 | 단계 | 동시성 | RAG CPU max(%) | RAG RAM max(MiB) | "
            "GPU max(%) | VRAM max(MiB) | TEI RAM max(MiB) | Qdrant RAM max(MiB) |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for value in resource_summaries:
        lines.append(
            "| {case_id} | {operation} | {phase} | {concurrency} | {cpu} | {ram} | "
            "{gpu} | {vram} | {tei} | {qdrant} |".format(
                case_id=value.get("case_id"),
                operation=value.get("operation"),
                phase=value.get("phase"),
                concurrency=value.get("concurrency"),
                cpu=_format_optional_number(value.get("target_cpu_percent_sum_max")),
                ram=_format_bytes_as_mib(value.get("target_rss_bytes_sum_max")),
                gpu=_format_optional_number(value.get("gpu_utilization_percent_max_max")),
                vram=_format_bytes_as_mib(value.get("gpu_memory_used_bytes_sum_max")),
                tei=_format_bytes_as_mib(value.get("tei_memory_used_bytes_max")),
                qdrant=_format_bytes_as_mib(value.get("qdrant_memory_used_bytes_max")),
            )
        )

    lines.extend(
        [
            "",
            "## 인제스트 단계",
            "",
            "| Case | 형식 | Origin | 파일 크기 | 이미지 | Stage | 시간(ms) | 청크 | "
            "RAG RAM max(MiB) | VRAM max(MiB) |",
            "|---|---|---|---:|---:|---|---:|---:|---:|---:|",
        ]
    )
    for value in stage_resource_summaries:
        lines.append(
            "| {case_id} | {file_type} | {origin} | {size} | {images} | {stage} | "
            "{duration} | {chunks} | {ram} | {vram} |".format(
                case_id=value.get("case_id"),
                file_type=value.get("file_type"),
                origin=value.get("content_origin"),
                size=_format_bytes(value.get("fixture_size_bytes")),
                images=value.get("declared_image_count") or 0,
                stage=value.get("stage"),
                duration=_format_optional_number(value.get("duration_ms")),
                chunks=value.get("chunk_count") or "-",
                ram=_format_bytes_as_mib(value.get("target_rss_bytes_sum_max")),
                vram=_format_bytes_as_mib(value.get("gpu_memory_used_bytes_sum_max")),
            )
        )

    lines.extend(
        [
            "",
            "## Host Disk/Network I/O",
            "",
            "| Case | 작업 | 동시성 | Network RX | Network TX | Disk Read | Disk Write |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for value in host_io_records:
        lines.append(
            "| {case_id} | {operation} | {concurrency} | {rx} | {tx} | {read} | {write} |".format(
                case_id=value.get("case_id"),
                operation=value.get("operation"),
                concurrency=value.get("concurrency"),
                rx=_format_bytes(value.get("network_received_bytes")),
                tx=_format_bytes(value.get("network_sent_bytes")),
                read=_format_bytes(value.get("disk_read_bytes")),
                write=_format_bytes(value.get("disk_write_bytes")),
            )
        )

    lines.extend(
        [
            "",
            "## 결과 파일",
            "",
            "- `environment.json`: RAG Git SHA와 하드웨어·CUDA·Docker 환경",
            "- `target.log`: RAG 대상 프로세스의 원본 JSON 로그",
            "- `request_records.csv`: 요청별 상태·지연·응답 크기·Claude 토큰",
            "- `level_summaries.csv`: 동시성 단계별 처리량과 p50·p95·p99",
            "- `resource_samples.csv`: 시간축 자원 원본",
            "- `resource_summaries.csv`: Case별 평균·최대 자원",
            "- `ingest_stage_events.csv`: 단계별 처리 시간",
            "- `ingest_stage_resource_summary.csv`: 단계 시간과 해당 구간 자원",
            "- `host_io_deltas.csv`: Case 경계의 Host Disk·Network 누적 차이",
            "- `saturation_candidates.json`: 최초 포화 후보",
            "- `report.json`: 기계 판독용 전체 요약",
            "",
            "## 해석 주의",
            "",
            "- 짧은 단계는 샘플 간격보다 먼저 끝나 단계 자원 샘플이 없을 수 있습니다.",
            "- GPU 전체 VRAM에는 CUDA EasyOCR과 TEI가 함께 포함될 수 있습니다.",
            "- Claude 응답 시간은 외부 API 상태의 영향을 받으므로 별도로 해석합니다.",
            "- 포화 후보는 관측 결과이며 운영 제한값을 자동 변경하지 않습니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    capture_output: bool = False,
    allow_failure: bool = False,
) -> str:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=capture_output,
        text=True,
        timeout=timeout_seconds,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {command[0]}"
        )
    if not capture_output:
        return ""
    return result.stdout or ""


def _safe_environment_url(
    environment: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    return (environment.get(name) or default).rstrip("/")


def _first_file_idx(payload: Mapping[str, object]) -> int | None:
    direct = _optional_positive_int(payload.get("file_idx"))
    if direct is not None:
        return direct
    values = payload.get("reference_file_idxs")
    if isinstance(values, list) and values:
        return _optional_positive_int(values[0])
    return None


def _safe_error_message(error: Exception) -> str:
    """토큰·URL·질의·응답 본문 없이 예외 유형 중심의 짧은 메시지를 만든다."""

    if isinstance(error, httpx.TimeoutException):
        return "HTTP request timed out."
    if isinstance(error, httpx.RequestError):
        return "HTTP request failed before a valid response was received."
    if isinstance(error, ValueError):
        return "The response did not match the expected JSON contract."
    return "The benchmark request failed."


def _parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


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


def _format_optional_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "-"
    return f"{float(value):.3f}"


def _format_bytes_as_mib(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "-"
    return f"{float(value) / (1024 * 1024):.2f}"


def _format_bytes(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "-"
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return "-"


def _utc_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat(timespec="milliseconds")


def _parse_arguments() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    repository_root = project_root.parent
    parser = argparse.ArgumentParser(
        description="Measure Local RAG resource usage and saturation without optimizing it."
    )
    parser.add_argument("--rag-root", type=Path, default=repository_root / "RAG")
    parser.add_argument(
        "--plan",
        type=Path,
        default=project_root / "configs/benchmark-plan.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=project_root / "artifacts",
    )
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, default=18077)
    parser.add_argument("--disable-answers", action="store_true")
    parser.add_argument("--keep-test-data", action="store_true")
    parser.add_argument("--keep-infrastructure-running", action="store_true")
    parser.add_argument("--preserve-running-infrastructure", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_arguments()
    plan = load_benchmark_plan(args.plan.resolve())
    run_id = f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    run_directory = args.output_root.resolve() / run_id
    runner = BenchmarkRunner(
        plan=plan,
        rag_root=args.rag_root,
        run_id=run_id,
        run_directory=run_directory,
        target_host=args.target_host,
        target_port=args.target_port,
        disable_answers=args.disable_answers,
        keep_test_data=args.keep_test_data,
        keep_infrastructure_running=args.keep_infrastructure_running,
        preserve_running_infrastructure=args.preserve_running_infrastructure,
    )
    report_path = runner.run()
    print(f"Benchmark report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
