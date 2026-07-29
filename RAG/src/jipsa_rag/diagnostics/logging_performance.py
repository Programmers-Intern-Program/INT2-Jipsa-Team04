"""로그 개선 전후의 출력량과 로깅 오버헤드를 재현 가능한 방식으로 비교한다.

이 모듈은 실제 PDF 파싱, CUDA OCR, TEI 임베딩, MySQL 또는 Qdrant 처리를
실행하지 않는다. 동일한 파일 처리 작업에서 로깅 정책만 바꾸어 포맷팅과 출력에
추가되는 비용을 분리 측정한다. 따라서 결과는 전체 파일 처리 시간 자체가 아니라
로그 계층이 더하는 상대적 오버헤드와 출력량을 해석하는 데 사용해야 한다.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import TextIOBase
from statistics import median
from time import perf_counter_ns
from typing import Final, Literal

from jipsa_rag.core.logging import (
    SensitiveDataConsoleFormatter,
    SensitiveDataJsonFormatter,
)

BenchmarkMode = Literal[
    "legacy_json",
    "improved_console",
    "improved_json",
]

_REQUEST_ID: Final[str] = "11111111-1111-4111-8111-111111111111"
_SERVICE_NAME: Final[str] = "Jipsa RAG Service"
_ENVIRONMENT: Final[str] = "local"
_LOGGER_NAME: Final[str] = "jipsa_rag.diagnostics.logging_performance"
_SLOW_STAGE_THRESHOLD_MS: Final[float] = 5000.0

_JSON_LOG_FIELDS: Final[tuple[str, ...]] = (
    "asctime",
    "levelname",
    "name",
    "message",
    "request_id",
    "exc_info",
)


@dataclass(frozen=True, slots=True)
class LoggingWorkload:
    """한 번의 비교에서 재현할 요청 조합과 파일 처리 규모를 정의한다."""

    name: str
    file_processing_count: int
    health_check_count: int
    chunk_count: int = 64
    embedding_dim: int = 1024
    embedding_batch_size: int = 32

    def __post_init__(self) -> None:
        """음수 요청 수와 잘못된 임베딩 설정을 비교 입력에서 차단한다."""

        non_negative_fields = {
            "file_processing_count": self.file_processing_count,
            "health_check_count": self.health_check_count,
        }
        for field_name, value in non_negative_fields.items():
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be greater than or equal to zero.")

        positive_fields = {
            "chunk_count": self.chunk_count,
            "embedding_dim": self.embedding_dim,
            "embedding_batch_size": self.embedding_batch_size,
        }
        for field_name, value in positive_fields.items():
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_name} must be greater than zero.")


@dataclass(frozen=True, slots=True)
class LoggingBenchmarkSample:
    """단일 로깅 정책과 워크로드에서 측정한 출력량과 중앙값 시간이다."""

    workload_name: str
    mode: BenchmarkMode
    output_line_count: int
    output_byte_count: int
    output_write_count: int
    median_duration_ms: float


@dataclass(frozen=True, slots=True)
class LoggingComparisonReport:
    """비교 환경, 반복 조건과 모든 측정 결과를 하나의 보고서로 묶는다."""

    generated_at_utc: str
    python_version: str
    platform: str
    iterations: int
    rounds: int
    samples: tuple[LoggingBenchmarkSample, ...]

    def to_dict(self) -> dict[str, object]:
        """JSON 직렬화 가능한 일반 자료 구조로 변환한다."""

        return {
            "generated_at_utc": self.generated_at_utc,
            "python_version": self.python_version,
            "platform": self.platform,
            "iterations": self.iterations,
            "rounds": self.rounds,
            "samples": [asdict(sample) for sample in self.samples],
            "methodology": {
                "scope": "logging_overhead_only",
                "external_processing_included": False,
                "legacy_policy": (
                    "successful HTTP request start and completion at INFO; "
                    "successful health checks also at INFO"
                ),
                "improved_policy": (
                    "successful normal request completion at INFO; successful health checks "
                    "at DEBUG; six file-processing summary events at INFO"
                ),
            },
        }

    def to_markdown(self) -> str:
        """사람이 검토하기 쉬운 Markdown 비교 보고서를 생성한다."""

        lines = [
            "# RAG 로그 개선 전후 성능 및 출력량 비교",
            "",
            "## 측정 범위",
            "",
            "이 비교는 문서 파싱, CUDA OCR, TEI 임베딩, MySQL 및 Qdrant 처리 시간을 "
            "제외하고 로그 레코드 생성, 민감 정보 보호 Formatter 처리와 스트림 쓰기 "
            "비용만 측정합니다.",
            "",
            "- 개선 전: 정상 HTTP 요청의 시작과 완료를 모두 INFO로 기록",
            "- 개선 후: 정상 요청은 완료 중심, 정상 Health Check는 DEBUG로 기록",
            "- 개선 후 파일 처리: 다운로드부터 전체 완료까지 6개 단계 요약 로그 기록",
            "- 청크 원문, 질문, 벡터와 HTTP 본문은 측정 로그에 포함하지 않음",
            "",
            "## 실행 환경",
            "",
            f"- 생성 시각(UTC): `{self.generated_at_utc}`",
            f"- Python: `{self.python_version}`",
            f"- Platform: `{self.platform}`",
            f"- 반복 횟수: 라운드당 `{self.iterations}`회, 총 `{self.rounds}`라운드",
            "",
            "## 측정 결과",
            "",
            "| Workload | Mode | Lines | Bytes | Writes | Median ms/workload |",
            "|---|---:|---:|---:|---:|---:|",
        ]

        for sample in self.samples:
            lines.append(
                "| "
                f"{sample.workload_name} | {sample.mode} | "
                f"{sample.output_line_count} | {sample.output_byte_count} | "
                f"{sample.output_write_count} | {sample.median_duration_ms:.6f} |"
            )

        lines.extend(
            [
                "",
                "## 해석",
                "",
            ]
        )

        workload_names = tuple(dict.fromkeys(sample.workload_name for sample in self.samples))
        for workload_name in workload_names:
            grouped = {
                sample.mode: sample
                for sample in self.samples
                if sample.workload_name == workload_name
            }
            legacy = grouped["legacy_json"]
            improved_json = grouped["improved_json"]
            improved_console = grouped["improved_console"]
            line_change = _percentage_change(
                before=legacy.output_line_count,
                after=improved_json.output_line_count,
            )
            json_time_ratio = _safe_ratio(
                numerator=improved_json.median_duration_ms,
                denominator=legacy.median_duration_ms,
            )
            console_time_ratio = _safe_ratio(
                numerator=improved_console.median_duration_ms,
                denominator=legacy.median_duration_ms,
            )
            lines.extend(
                [
                    f"### `{workload_name}`",
                    "",
                    f"- JSON 기준 로그 행 변화: `{line_change:+.2f}%`",
                    f"- 개선 JSON/기존 JSON 시간 비율: `{json_time_ratio:.3f}x`",
                    f"- 개선 Console/기존 JSON 시간 비율: `{console_time_ratio:.3f}x`",
                    "",
                ]
            )

        lines.extend(
            [
                "파일 처리 단독 워크로드에서는 단계별 관측 정보를 추가했기 때문에 "
                "로그 행 수가 증가할 수 있습니다. 반대로 Health Check가 반복되는 혼합 "
                "워크로드에서는 정상 Health Check를 INFO에서 제외하여 전체 출력량이 "
                "감소합니다.",
                "",
                "시간 수치는 실행 장비와 부하에 따라 달라지므로 절대 성능 보증값으로 "
                "사용하지 않습니다. 실제 CUDA·DB·Qdrant E2E 처리 시간은 별도의 전체 "
                "파이프라인 테스트에서 확인해야 합니다.",
                "",
            ]
        )

        return "\n".join(lines)


class CountingTextStream(TextIOBase):
    """로그 문자열을 보관하지 않고 쓰기 횟수, 행 수와 UTF-8 바이트만 센다."""

    def __init__(self) -> None:
        """모든 누적 계수를 0으로 초기화한다."""

        super().__init__()
        self.write_count = 0
        self.line_count = 0
        self.byte_count = 0

    def write(self, value: str) -> int:
        """문자열을 저장하지 않고 출력량 계수만 증가시킨다."""

        self.write_count += 1
        self.line_count += value.count("\n")
        self.byte_count += len(value.encode("utf-8"))
        return len(value)

    def flush(self) -> None:
        """메모리 버퍼가 없으므로 표준 스트림 계약만 충족한다."""

    def reset_counts(self) -> None:
        """다음 라운드가 이전 출력량의 영향을 받지 않도록 계수를 초기화한다."""

        self.write_count = 0
        self.line_count = 0
        self.byte_count = 0


def measure_logging_scenario(
    *,
    mode: BenchmarkMode,
    workload: LoggingWorkload,
    iterations: int,
    rounds: int,
) -> LoggingBenchmarkSample:
    """단일 정책의 출력량 1회와 반복 실행 중앙 시간을 측정한다."""

    if isinstance(iterations, bool) or iterations <= 0:
        raise ValueError("iterations must be greater than zero.")
    if isinstance(rounds, bool) or rounds <= 0:
        raise ValueError("rounds must be greater than zero.")

    output_stream = CountingTextStream()
    output_logger = _create_benchmark_logger(
        mode=mode,
        stream=output_stream,
        suffix="output",
    )
    _emit_workload(
        logger=output_logger,
        mode=mode,
        workload=workload,
    )
    _flush_logger(output_logger)

    output_line_count = output_stream.line_count
    output_byte_count = output_stream.byte_count
    output_write_count = output_stream.write_count
    _close_logger(output_logger)

    timing_stream = CountingTextStream()
    timing_logger = _create_benchmark_logger(
        mode=mode,
        stream=timing_stream,
        suffix="timing",
    )

    # 최초 Formatter 경로와 정규식 캐시 준비 비용이 측정값을 과도하게 흔들지 않도록
    # 각 모드에서 작은 고정 횟수의 예열을 먼저 수행한다.
    for _ in range(3):
        _emit_workload(
            logger=timing_logger,
            mode=mode,
            workload=workload,
        )
    _flush_logger(timing_logger)
    timing_stream.reset_counts()

    elapsed_per_workload_ms: list[float] = []
    for _ in range(rounds):
        started_at_ns = perf_counter_ns()
        for _ in range(iterations):
            _emit_workload(
                logger=timing_logger,
                mode=mode,
                workload=workload,
            )
        _flush_logger(timing_logger)
        elapsed_ns = perf_counter_ns() - started_at_ns
        elapsed_per_workload_ms.append(elapsed_ns / iterations / 1_000_000)
        timing_stream.reset_counts()

    _close_logger(timing_logger)

    return LoggingBenchmarkSample(
        workload_name=workload.name,
        mode=mode,
        output_line_count=output_line_count,
        output_byte_count=output_byte_count,
        output_write_count=output_write_count,
        median_duration_ms=round(median(elapsed_per_workload_ms), 6),
    )


def run_logging_comparison(
    *,
    iterations: int = 1000,
    rounds: int = 7,
) -> LoggingComparisonReport:
    """파일 처리 단독 및 Health Check 혼합 워크로드를 모두 비교한다."""

    workloads = (
        LoggingWorkload(
            name="file_processing_only",
            file_processing_count=1,
            health_check_count=0,
        ),
        LoggingWorkload(
            name="file_processing_with_120_health_checks",
            file_processing_count=1,
            health_check_count=120,
        ),
    )
    modes: tuple[BenchmarkMode, ...] = (
        "legacy_json",
        "improved_console",
        "improved_json",
    )

    samples = tuple(
        measure_logging_scenario(
            mode=mode,
            workload=workload,
            iterations=iterations,
            rounds=rounds,
        )
        for workload in workloads
        for mode in modes
    )

    return LoggingComparisonReport(
        generated_at_utc=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        python_version=platform.python_version(),
        platform=platform.platform(),
        iterations=iterations,
        rounds=rounds,
        samples=samples,
    )


def _create_benchmark_logger(
    *,
    mode: BenchmarkMode,
    stream: CountingTextStream,
    suffix: str,
) -> logging.Logger:
    """선택한 정책의 Formatter만 연결한 전파 없는 독립 Logger를 생성한다."""

    logger = logging.Logger(f"{_LOGGER_NAME}.{mode}.{suffix}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    handler.setFormatter(_create_formatter(mode))
    logger.addHandler(handler)
    return logger


def _create_formatter(mode: BenchmarkMode) -> logging.Formatter:
    """기존 JSON 또는 개선 Console·JSON Formatter를 생성한다."""

    if mode == "improved_console":
        return SensitiveDataConsoleFormatter(
            service_name=_SERVICE_NAME,
            environment=_ENVIRONMENT,
        )

    return SensitiveDataJsonFormatter(
        _JSON_LOG_FIELDS,
        rename_fields={
            "asctime": "timestamp",
            "levelname": "level",
            "name": "logger",
        },
        static_fields={
            "service": _SERVICE_NAME,
            "environment": _ENVIRONMENT,
        },
    )


def _emit_workload(
    *,
    logger: logging.Logger,
    mode: BenchmarkMode,
    workload: LoggingWorkload,
) -> None:
    """동일한 요청 조합을 기존 또는 개선 정책으로 로그에 기록한다."""

    if mode == "legacy_json":
        _emit_legacy_workload(logger=logger, workload=workload)
        return

    _emit_improved_workload(logger=logger, workload=workload)


def _emit_legacy_workload(
    *,
    logger: logging.Logger,
    workload: LoggingWorkload,
) -> None:
    """개선 전의 요청 시작·완료 INFO 정책을 재현한다."""

    for health_index in range(workload.health_check_count):
        health_path = "/api/v1/health/live" if health_index % 2 == 0 else "/api/v1/health/ready"
        _emit_legacy_request_pair(
            logger=logger,
            method="GET",
            path=health_path,
        )

    for _ in range(workload.file_processing_count):
        _emit_legacy_request_pair(
            logger=logger,
            method="POST",
            path="/api/v1/files/process",
        )


def _emit_legacy_request_pair(
    *,
    logger: logging.Logger,
    method: str,
    path: str,
) -> None:
    """개선 전 미들웨어의 요청 시작과 완료 두 줄을 생성한다."""

    logger.info(
        "HTTP request started.",
        extra={
            "event": "http_request_started",
            "request_id": _REQUEST_ID,
            "method": method,
            "path": path,
        },
    )
    logger.info(
        "HTTP request completed.",
        extra={
            "event": "http_request_completed",
            "request_id": _REQUEST_ID,
            "method": method,
            "path": path,
            "status_code": 200,
            "duration_ms": 12.345,
        },
    )


def _emit_improved_workload(
    *,
    logger: logging.Logger,
    workload: LoggingWorkload,
) -> None:
    """완료 중심 요청 로그와 파일 처리 단계 요약 정책을 재현한다."""

    for health_index in range(workload.health_check_count):
        health_path = "/api/v1/health/live" if health_index % 2 == 0 else "/api/v1/health/ready"
        # 실제 미들웨어와 같이 INFO Logger에 DEBUG 레코드를 전달한다.
        # 출력은 발생하지 않지만 DEBUG 호출 경로의 작은 제어 비용은 측정에 포함한다.
        logger.debug(
            "HTTP request completed.",
            extra={
                "event": "http_request_completed",
                "request_id": _REQUEST_ID,
                "method": "GET",
                "path": health_path,
                "status_code": 200,
                "duration_ms": 1.234,
            },
        )

    for _ in range(workload.file_processing_count):
        _emit_improved_file_processing_summary(
            logger=logger,
            workload=workload,
        )
        logger.info(
            "HTTP request completed.",
            extra={
                "event": "http_request_completed",
                "request_id": _REQUEST_ID,
                "method": "POST",
                "path": "/api/v1/files/process",
                "status_code": 200,
                "duration_ms": 1200.0,
            },
        )


def _emit_improved_file_processing_summary(
    *,
    logger: logging.Logger,
    workload: LoggingWorkload,
) -> None:
    """청크 수와 무관하게 여섯 개의 파일 처리 완료 이벤트만 생성한다."""

    batch_count = (
        workload.chunk_count + workload.embedding_batch_size - 1
    ) // workload.embedding_batch_size
    common = {
        "request_id": _REQUEST_ID,
        "users_idx": 45,
        "file_idx": 123,
        "file_type": "pdf",
        "slow_stage_threshold_ms": _SLOW_STAGE_THRESHOLD_MS,
        "is_slow_stage": False,
    }

    stage_events = (
        (
            "File download completed.",
            "file_download_completed",
            {
                "stage": "download",
                "size_bytes": 4_194_304,
                "duration_ms": 130.0,
            },
        ),
        (
            "Document parsing and OCR phase completed.",
            "document_parsing_ocr_completed",
            {
                "stage": "parsing_ocr",
                "structure_unit_count": 12,
                "text_unit_count": 11,
                "duration_ms": 420.0,
            },
        ),
        (
            "Document chunking completed.",
            "document_chunking_completed",
            {
                "stage": "chunking",
                "chunk_count": workload.chunk_count,
                "duration_ms": 14.0,
            },
        ),
        (
            "Document embedding completed.",
            "document_embedding_completed",
            {
                "stage": "embedding",
                "chunk_count": workload.chunk_count,
                "batch_count": batch_count,
                "embedding_dim": workload.embedding_dim,
                "duration_ms": 510.0,
            },
        ),
        (
            "Local RAG DB and Qdrant indexing completed.",
            "file_indexing_completed",
            {
                "stage": "indexing",
                "rag_document_idx": 100,
                "rag_index_run_idx": 200,
                "chunk_count": workload.chunk_count,
                "duration_ms": 85.0,
            },
        ),
        (
            "File processing pipeline completed.",
            "file_processing_completed",
            {
                "stage": "file_processing",
                "success": True,
                "rag_document_idx": 100,
                "rag_index_run_idx": 200,
                "chunk_count": workload.chunk_count,
                "total_duration_ms": 1159.0,
            },
        ),
    )

    for message, event, event_extra in stage_events:
        logger.info(
            message,
            extra={
                **common,
                **event_extra,
                "event": event,
            },
        )


def _flush_logger(logger: logging.Logger) -> None:
    """Logger에 연결된 모든 Handler의 버퍼를 비운다."""

    for handler in logger.handlers:
        handler.flush()


def _close_logger(logger: logging.Logger) -> None:
    """측정이 끝난 Handler를 닫고 Logger 참조에서 제거한다."""

    for handler in tuple(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def _safe_ratio(*, numerator: float, denominator: float) -> float:
    """매우 빠른 측정에서 0으로 반올림된 분모를 안전하게 처리한다."""

    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _percentage_change(*, before: int, after: int) -> float:
    """개선 전 출력량을 기준으로 증감률을 계산한다."""

    if before == 0:
        return 0.0
    return (after - before) / before * 100.0


__all__ = [
    "BenchmarkMode",
    "CountingTextStream",
    "LoggingBenchmarkSample",
    "LoggingComparisonReport",
    "LoggingWorkload",
    "measure_logging_scenario",
    "run_logging_comparison",
]
