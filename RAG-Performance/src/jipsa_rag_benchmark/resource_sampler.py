"""별도 프로세스로 실행되는 Local RAG의 시스템·프로세스·GPU·Docker 자원을 수집한다.

측정 프로그램 자신과 RAG 대상 프로세스를 분리하기 위해 대상 PID와 그 자식 프로세스만
별도로 합산한다. 호스트 전체 값, RAG 프로세스 트리, NVIDIA GPU, TEI·Qdrant 컨테이너를
한 샘플에 함께 기록하여 어느 계층에서 자원이 증가했는지 비교할 수 있게 한다.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Iterable, Mapping, Sequence, TypedDict, cast

import psutil

_SIZE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<value>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>[A-Za-z]+)\s*$"
)
_BYTES_BY_UNIT: Final[Mapping[str, float]] = {
    "B": 1.0,
    "KB": 1_000.0,
    "MB": 1_000_000.0,
    "GB": 1_000_000_000.0,
    "TB": 1_000_000_000_000.0,
    "KIB": 1024.0,
    "MIB": 1024.0**2,
    "GIB": 1024.0**3,
    "TIB": 1024.0**4,
}


class _SystemMetrics(TypedDict):
    cpu_percent: float | None
    memory_used_bytes: int | None
    memory_total_bytes: int | None
    memory_percent: float | None
    disk_read_bytes: int | None
    disk_write_bytes: int | None
    network_received_bytes: int | None
    network_sent_bytes: int | None


class _ProcessTreeMetrics(TypedDict):
    pids: set[int]
    cpu_times_by_pid: dict[int, float]
    process_count: int
    rss_bytes_sum: int | None
    private_bytes_sum: int | None
    read_bytes_sum: int | None
    write_bytes_sum: int | None
    thread_count_sum: int | None
    handle_count_sum: int | None


class _GpuMetrics(TypedDict):
    utilization_percent_max: float | None
    memory_used_bytes_sum: int | None
    memory_total_bytes_sum: int | None
    temperature_c_max: float | None
    power_watts_sum: float | None
    target_memory_used_bytes_sum: int | None


class _ContainerMetrics(TypedDict):
    cpu_percent: float | None
    memory_used_bytes: int | None
    network_received_bytes: int | None
    network_sent_bytes: int | None
    block_read_bytes: int | None
    block_write_bytes: int | None


class _DockerMetrics(TypedDict):
    tei: _ContainerMetrics
    qdrant: _ContainerMetrics


@dataclass(frozen=True, slots=True)
class SampleContext:
    """현재 자원 샘플이 속한 논리적 측정 구간."""

    case_id: str = "idle"
    operation: str = "idle"
    phase: str = "idle"
    concurrency: int = 0


@dataclass(frozen=True, slots=True)
class ResourceSample:
    """한 시점에 수집한 호스트, RAG, GPU 및 컨테이너 자원 값."""

    timestamp_utc: str
    epoch_seconds: float
    case_id: str
    operation: str
    phase: str
    concurrency: int

    system_cpu_percent: float | None
    system_memory_used_bytes: int | None
    system_memory_total_bytes: int | None
    system_memory_percent: float | None
    system_disk_read_bytes: int | None
    system_disk_write_bytes: int | None
    system_network_received_bytes: int | None
    system_network_sent_bytes: int | None

    target_pid: int | None
    target_process_count: int
    target_cpu_percent_sum: float | None
    target_rss_bytes_sum: int | None
    target_private_bytes_sum: int | None
    target_read_bytes_sum: int | None
    target_write_bytes_sum: int | None
    target_thread_count_sum: int | None
    target_handle_count_sum: int | None

    gpu_utilization_percent_max: float | None
    gpu_memory_used_bytes_sum: int | None
    gpu_memory_total_bytes_sum: int | None
    gpu_temperature_c_max: float | None
    gpu_power_watts_sum: float | None
    target_gpu_memory_used_bytes_sum: int | None

    tei_cpu_percent: float | None
    tei_memory_used_bytes: int | None
    tei_network_received_bytes: int | None
    tei_network_sent_bytes: int | None
    tei_block_read_bytes: int | None
    tei_block_write_bytes: int | None

    qdrant_cpu_percent: float | None
    qdrant_memory_used_bytes: int | None
    qdrant_network_received_bytes: int | None
    qdrant_network_sent_bytes: int | None
    qdrant_block_read_bytes: int | None
    qdrant_block_write_bytes: int | None

    sample_duration_ms: float
    sampling_errors: str | None

    def to_dict(self) -> dict[str, object]:
        """JSONL과 CSV에 공통으로 사용할 평평한 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class HostIoSnapshot:
    """측정 구간 시작·종료 시점의 누적 호스트 Disk·Network 값."""

    timestamp_utc: str
    epoch_seconds: float
    network_received_bytes: int | None
    network_sent_bytes: int | None
    disk_read_bytes: int | None
    disk_write_bytes: int | None
    error: str | None = None

    def delta(self, previous: HostIoSnapshot) -> dict[str, int | None]:
        """카운터 재설정 또는 래핑으로 감소한 값은 ``None``으로 처리한다."""

        return {
            "network_received_bytes": _safe_counter_delta(
                previous.network_received_bytes,
                self.network_received_bytes,
            ),
            "network_sent_bytes": _safe_counter_delta(
                previous.network_sent_bytes,
                self.network_sent_bytes,
            ),
            "disk_read_bytes": _safe_counter_delta(
                previous.disk_read_bytes,
                self.disk_read_bytes,
            ),
            "disk_write_bytes": _safe_counter_delta(
                previous.disk_write_bytes,
                self.disk_write_bytes,
            ),
        }


class ResourceSampler:
    """백그라운드 Thread에서 일정 간격으로 자원을 관측한다."""

    def __init__(
        self,
        *,
        output_path: Path,
        sample_interval_seconds: float,
        docker_sample_interval_seconds: float,
        tei_container_name: str = "jipsa-embedding",
        qdrant_container_name: str = "jipsa-qdrant",
    ) -> None:
        if sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be greater than zero.")
        if docker_sample_interval_seconds <= 0:
            raise ValueError("docker_sample_interval_seconds must be greater than zero.")

        self._output_path = output_path
        self._sample_interval_seconds = sample_interval_seconds
        self._docker_sample_interval_seconds = docker_sample_interval_seconds
        self._tei_container_name = tei_container_name
        self._qdrant_container_name = qdrant_container_name

        self._context = SampleContext()
        self._context_lock = threading.Lock()
        self._samples: list[ResourceSample] = []
        self._samples_lock = threading.Lock()
        self._target_pid: int | None = None
        self._target_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_docker_sample_epoch = 0.0
        self._last_docker_metrics = _empty_container_metrics()
        self._previous_process_cpu_times: dict[int, float] = {}
        self._previous_process_sample_epoch: float | None = None

        # psutil의 첫 cpu_percent 호출은 기준 시점만 설정하므로 시작 전에 한 번 호출한다.
        psutil.cpu_percent(interval=None)

    @property
    def samples(self) -> tuple[ResourceSample, ...]:
        """현재까지 수집된 샘플의 불변 복사본을 반환한다."""

        with self._samples_lock:
            return tuple(self._samples)

    def set_target_pid(self, pid: int | None) -> None:
        """RAG 대상 프로세스 PID를 설정하거나 제거한다."""

        if pid is not None and pid <= 0:
            raise ValueError("pid must be greater than zero.")
        with self._target_lock:
            self._target_pid = pid

    def start(self) -> None:
        """샘플링 Thread를 한 번만 시작한다."""

        if self._thread is not None:
            raise RuntimeError("ResourceSampler has already been started.")
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path.write_text("", encoding="utf-8")
        self._thread = threading.Thread(
            target=self._run,
            name="jipsa-rag-performance-resource-sampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Thread를 종료하고 마지막 파일 기록이 끝날 때까지 기다린다."""

        self._stop_event.set()
        if self._thread is None:
            return
        self._thread.join(timeout=max(10.0, self._sample_interval_seconds * 4.0))
        if self._thread.is_alive():
            raise RuntimeError("Resource sampler did not stop within the timeout.")

    def set_context(
        self,
        *,
        case_id: str,
        operation: str,
        phase: str,
        concurrency: int,
    ) -> None:
        """이후 샘플에 적용할 논리적 측정 구간을 원자적으로 변경한다."""

        if concurrency < 0:
            raise ValueError("concurrency must be zero or greater.")
        with self._context_lock:
            self._context = SampleContext(
                case_id=case_id,
                operation=operation,
                phase=phase,
                concurrency=concurrency,
            )

    def reset_context(self) -> None:
        """측정 구간 종료 후 샘플 Context를 idle로 되돌린다."""

        with self._context_lock:
            self._context = SampleContext()

    def write_csv(self, path: Path) -> None:
        """현재 샘플 전체를 CSV로 저장한다."""

        rows = [sample.to_dict() for sample in self.samples]
        path.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    def _run(self) -> None:
        """중단 신호까지 샘플을 JSONL에 순차 기록한다."""

        while not self._stop_event.is_set():
            started = time.perf_counter()
            sample = self._collect_sample(started_at=started)
            serialized = json.dumps(sample.to_dict(), ensure_ascii=False, separators=(",", ":"))
            with self._output_path.open("a", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.write("\n")
            with self._samples_lock:
                self._samples.append(sample)

            elapsed = time.perf_counter() - started
            remaining = max(self._sample_interval_seconds - elapsed, 0.0)
            self._stop_event.wait(remaining)

    def _collect_sample(self, *, started_at: float) -> ResourceSample:
        now_epoch = time.time()
        errors: list[str] = []

        with self._context_lock:
            context = self._context
        with self._target_lock:
            target_pid = self._target_pid

        system = _read_system_metrics(errors)
        process = _read_process_tree_metrics(target_pid, errors)
        target_cpu_percent = self._calculate_process_cpu_percent(
            process["cpu_times_by_pid"],
            now_epoch,
        )
        gpu = _read_gpu_metrics(process["pids"], errors)

        if now_epoch - self._last_docker_sample_epoch >= self._docker_sample_interval_seconds:
            self._last_docker_metrics = _read_docker_metrics(
                self._tei_container_name,
                self._qdrant_container_name,
                errors,
            )
            self._last_docker_sample_epoch = now_epoch
        containers = self._last_docker_metrics

        return ResourceSample(
            timestamp_utc=_utc_iso(now_epoch),
            epoch_seconds=now_epoch,
            case_id=context.case_id,
            operation=context.operation,
            phase=context.phase,
            concurrency=context.concurrency,
            system_cpu_percent=system["cpu_percent"],
            system_memory_used_bytes=system["memory_used_bytes"],
            system_memory_total_bytes=system["memory_total_bytes"],
            system_memory_percent=system["memory_percent"],
            system_disk_read_bytes=system["disk_read_bytes"],
            system_disk_write_bytes=system["disk_write_bytes"],
            system_network_received_bytes=system["network_received_bytes"],
            system_network_sent_bytes=system["network_sent_bytes"],
            target_pid=target_pid,
            target_process_count=process["process_count"],
            target_cpu_percent_sum=target_cpu_percent,
            target_rss_bytes_sum=process["rss_bytes_sum"],
            target_private_bytes_sum=process["private_bytes_sum"],
            target_read_bytes_sum=process["read_bytes_sum"],
            target_write_bytes_sum=process["write_bytes_sum"],
            target_thread_count_sum=process["thread_count_sum"],
            target_handle_count_sum=process["handle_count_sum"],
            gpu_utilization_percent_max=gpu["utilization_percent_max"],
            gpu_memory_used_bytes_sum=gpu["memory_used_bytes_sum"],
            gpu_memory_total_bytes_sum=gpu["memory_total_bytes_sum"],
            gpu_temperature_c_max=gpu["temperature_c_max"],
            gpu_power_watts_sum=gpu["power_watts_sum"],
            target_gpu_memory_used_bytes_sum=gpu["target_memory_used_bytes_sum"],
            tei_cpu_percent=containers["tei"]["cpu_percent"],
            tei_memory_used_bytes=containers["tei"]["memory_used_bytes"],
            tei_network_received_bytes=containers["tei"]["network_received_bytes"],
            tei_network_sent_bytes=containers["tei"]["network_sent_bytes"],
            tei_block_read_bytes=containers["tei"]["block_read_bytes"],
            tei_block_write_bytes=containers["tei"]["block_write_bytes"],
            qdrant_cpu_percent=containers["qdrant"]["cpu_percent"],
            qdrant_memory_used_bytes=containers["qdrant"]["memory_used_bytes"],
            qdrant_network_received_bytes=containers["qdrant"]["network_received_bytes"],
            qdrant_network_sent_bytes=containers["qdrant"]["network_sent_bytes"],
            qdrant_block_read_bytes=containers["qdrant"]["block_read_bytes"],
            qdrant_block_write_bytes=containers["qdrant"]["block_write_bytes"],
            sample_duration_ms=(time.perf_counter() - started_at) * 1000.0,
            sampling_errors="; ".join(errors) if errors else None,
        )

    def _calculate_process_cpu_percent(
        self,
        raw_cpu_times: object,
        now_epoch: float,
    ) -> float | None:
        """PID별 누적 CPU 시간을 이전 샘플과 비교해 Process Tree 사용률을 계산한다.

        ``psutil.Process.cpu_percent()``는 Process 객체별 기준값을 보관하므로 매 샘플마다
        새 객체를 만들면 계속 0을 반환할 수 있다. 누적 user+system 시간을 직접 비교하여
        멀티코어 사용 시 100%를 넘을 수 있는 Process Tree 합계 의미를 보존한다.
        """

        if not isinstance(raw_cpu_times, dict):
            return None
        current = {
            int(pid): float(value)
            for pid, value in raw_cpu_times.items()
            if isinstance(pid, int) and isinstance(value, int | float)
        }
        previous_epoch = self._previous_process_sample_epoch
        previous = self._previous_process_cpu_times
        self._previous_process_cpu_times = current
        self._previous_process_sample_epoch = now_epoch
        if previous_epoch is None or now_epoch <= previous_epoch:
            return 0.0 if current else None

        cpu_delta = sum(
            max(cpu_time - previous[pid], 0.0)
            for pid, cpu_time in current.items()
            if pid in previous
        )
        return (cpu_delta / (now_epoch - previous_epoch)) * 100.0


def capture_host_io_snapshot() -> HostIoSnapshot:
    """호스트 누적 Disk·Network 카운터를 한 번 읽는다."""

    now = time.time()
    try:
        disk = psutil.disk_io_counters()
        network = psutil.net_io_counters()
        return HostIoSnapshot(
            timestamp_utc=_utc_iso(now),
            epoch_seconds=now,
            network_received_bytes=network.bytes_recv if network is not None else None,
            network_sent_bytes=network.bytes_sent if network is not None else None,
            disk_read_bytes=disk.read_bytes if disk is not None else None,
            disk_write_bytes=disk.write_bytes if disk is not None else None,
        )
    except (OSError, psutil.Error) as error:
        return HostIoSnapshot(
            timestamp_utc=_utc_iso(now),
            epoch_seconds=now,
            network_received_bytes=None,
            network_sent_bytes=None,
            disk_read_bytes=None,
            disk_write_bytes=None,
            error=type(error).__name__,
        )


def summarize_resource_samples(samples: Sequence[ResourceSample]) -> list[dict[str, object]]:
    """operation·phase·concurrency·case별 평균과 최대 자원을 계산한다."""

    groups: dict[tuple[str, str, int, str], list[ResourceSample]] = {}
    for sample in samples:
        if sample.operation == "idle":
            continue
        key = (sample.operation, sample.phase, sample.concurrency, sample.case_id)
        groups.setdefault(key, []).append(sample)

    rows: list[dict[str, object]] = []
    fields = (
        "system_cpu_percent",
        "system_memory_used_bytes",
        "target_cpu_percent_sum",
        "target_rss_bytes_sum",
        "target_private_bytes_sum",
        "target_gpu_memory_used_bytes_sum",
        "gpu_utilization_percent_max",
        "gpu_memory_used_bytes_sum",
        "tei_cpu_percent",
        "tei_memory_used_bytes",
        "qdrant_cpu_percent",
        "qdrant_memory_used_bytes",
    )

    for (operation, phase, concurrency, case_id), group in sorted(groups.items()):
        row: dict[str, object] = {
            "operation": operation,
            "phase": phase,
            "concurrency": concurrency,
            "case_id": case_id,
            "sample_count": len(group),
        }
        for field_name in fields:
            values = [
                float(value)
                for sample in group
                if (value := getattr(sample, field_name)) is not None
            ]
            row[f"{field_name}_mean"] = sum(values) / len(values) if values else None
            row[f"{field_name}_max"] = max(values) if values else None
        rows.append(row)
    return rows


def _read_system_metrics(errors: list[str]) -> _SystemMetrics:
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_io_counters()
        network = psutil.net_io_counters()
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_used_bytes": int(memory.used),
            "memory_total_bytes": int(memory.total),
            "memory_percent": float(memory.percent),
            "disk_read_bytes": int(disk.read_bytes) if disk is not None else None,
            "disk_write_bytes": int(disk.write_bytes) if disk is not None else None,
            "network_received_bytes": int(network.bytes_recv) if network is not None else None,
            "network_sent_bytes": int(network.bytes_sent) if network is not None else None,
        }
    except (OSError, psutil.Error) as error:
        errors.append(f"system:{type(error).__name__}")
        return {
            "cpu_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "memory_percent": None,
            "disk_read_bytes": None,
            "disk_write_bytes": None,
            "network_received_bytes": None,
            "network_sent_bytes": None,
        }


def _read_process_tree_metrics(
    target_pid: int | None,
    errors: list[str],
) -> _ProcessTreeMetrics:
    empty: _ProcessTreeMetrics = {
        "pids": set(),
        "process_count": 0,
        "cpu_times_by_pid": {},
        "rss_bytes_sum": None,
        "private_bytes_sum": None,
        "read_bytes_sum": None,
        "write_bytes_sum": None,
        "thread_count_sum": None,
        "handle_count_sum": None,
    }
    if target_pid is None:
        return empty

    try:
        root = psutil.Process(target_pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as error:
        errors.append(f"process-tree:{type(error).__name__}")
        return empty

    pids: set[int] = set()
    cpu_times_by_pid: dict[int, float] = {}
    rss_total = 0
    private_total = 0
    read_total = 0
    write_total = 0
    thread_total = 0
    handle_total = 0
    successful = 0

    for process in processes:
        try:
            with process.oneshot():
                pids.add(process.pid)
                cpu_times = process.cpu_times()
                cpu_times_by_pid[process.pid] = float(cpu_times.user + cpu_times.system)
                memory = process.memory_info()
                rss_total += int(memory.rss)
                private_total += int(getattr(memory, "private", getattr(memory, "vms", 0)))
                io = process.io_counters()
                read_total += int(io.read_bytes)
                write_total += int(io.write_bytes)
                thread_total += process.num_threads()
                if hasattr(process, "num_handles"):
                    handle_total += int(process.num_handles())
                successful += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue

    if successful == 0:
        errors.append("process-tree:no-readable-process")
        return empty

    return {
        "pids": pids,
        "process_count": successful,
        "cpu_times_by_pid": cpu_times_by_pid,
        "rss_bytes_sum": rss_total,
        "private_bytes_sum": private_total,
        "read_bytes_sum": read_total,
        "write_bytes_sum": write_total,
        "thread_count_sum": thread_total,
        "handle_count_sum": handle_total if handle_total > 0 else None,
    }


def _read_gpu_metrics(target_pids: set[int], errors: list[str]) -> _GpuMetrics:
    metrics: _GpuMetrics = {
        "utilization_percent_max": None,
        "memory_used_bytes_sum": None,
        "memory_total_bytes_sum": None,
        "temperature_c_max": None,
        "power_watts_sum": None,
        "target_memory_used_bytes_sum": None,
    }
    try:
        output = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        ).stdout
        rows = [line.strip() for line in output.splitlines() if line.strip()]
        parsed = [[_optional_float(value) for value in row.split(",")] for row in rows]
        if parsed:
            metrics["utilization_percent_max"] = _max_optional(row[0] for row in parsed)
            used_mib = _sum_optional(row[1] for row in parsed)
            total_mib = _sum_optional(row[2] for row in parsed)
            metrics["memory_used_bytes_sum"] = _mib_to_bytes(used_mib)
            metrics["memory_total_bytes_sum"] = _mib_to_bytes(total_mib)
            metrics["temperature_c_max"] = _max_optional(row[3] for row in parsed)
            metrics["power_watts_sum"] = _sum_optional(row[4] for row in parsed)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as error:
        errors.append(f"nvidia-smi:{type(error).__name__}")
        return metrics

    if not target_pids:
        return metrics

    try:
        process_output = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        ).stdout
        used_mib = 0.0
        matched = False
        for line in process_output.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
                memory = float(parts[1])
            except ValueError:
                continue
            if pid in target_pids:
                used_mib += memory
                matched = True
        metrics["target_memory_used_bytes_sum"] = _mib_to_bytes(used_mib) if matched else 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as error:
        errors.append(f"nvidia-smi-process:{type(error).__name__}")
    return metrics


def _read_docker_metrics(
    tei_name: str,
    qdrant_name: str,
    errors: list[str],
) -> _DockerMetrics:
    empty = _empty_container_metrics()
    try:
        output = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                tei_name,
                qdrant_name,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as error:
        errors.append(f"docker-stats:{type(error).__name__}")
        return empty

    result = _empty_container_metrics()
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or row.get("Container") or "")
        if name != tei_name and name != qdrant_name:
            continue

        network_rx, network_tx = _parse_size_pair(str(row.get("NetIO") or ""))
        block_read, block_write = _parse_size_pair(str(row.get("BlockIO") or ""))
        memory_used, _ = _parse_size_pair(str(row.get("MemUsage") or ""))
        container_metrics: _ContainerMetrics = {
            "cpu_percent": _parse_percent(row.get("CPUPerc")),
            "memory_used_bytes": memory_used,
            "network_received_bytes": network_rx,
            "network_sent_bytes": network_tx,
            "block_read_bytes": block_read,
            "block_write_bytes": block_write,
        }
        if name == tei_name:
            result["tei"] = container_metrics
        else:
            result["qdrant"] = container_metrics
    return result


def _empty_container_metrics() -> _DockerMetrics:
    def one() -> _ContainerMetrics:
        return {
            "cpu_percent": None,
            "memory_used_bytes": None,
            "network_received_bytes": None,
            "network_sent_bytes": None,
            "block_read_bytes": None,
            "block_write_bytes": None,
        }

    return {"tei": one(), "qdrant": one()}


def _parse_percent(value: object) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _parse_size_pair(value: str) -> tuple[int | None, int | None]:
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 2:
        return None, None
    return _parse_human_size(parts[0]), _parse_human_size(parts[1])


def _parse_human_size(value: str) -> int | None:
    """Docker의 decimal·IEC 크기 문자열을 byte로 변환한다."""

    match = _SIZE_PATTERN.fullmatch(value)
    if match is None:
        return None
    multiplier = _BYTES_BY_UNIT.get(match.group("unit").upper())
    if multiplier is None:
        return None
    return int(float(match.group("value")) * multiplier)


def _optional_float(value: str) -> float | None:
    normalized = value.strip()
    if not normalized or normalized.upper() in {"N/A", "[NOT SUPPORTED]", "NOT SUPPORTED"}:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def _sum_optional(values: Iterable[float | None]) -> float | None:
    normalized = [value for value in values if value is not None]
    return sum(normalized) if normalized else None


def _max_optional(values: Iterable[float | None]) -> float | None:
    normalized = [value for value in values if value is not None]
    return max(normalized) if normalized else None


def _mib_to_bytes(value: float | None) -> int | None:
    return int(value * 1024 * 1024) if value is not None else None


def _safe_counter_delta(previous: int | None, current: int | None) -> int | None:
    if previous is None or current is None or current < previous:
        return None
    return current - previous


def _utc_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat(timespec="milliseconds")
