"""Issue #159 성능 측정 계획과 결과 모델을 정의한다.

이 모듈은 서비스의 성능을 개선하거나 운영 제한값을 변경하지 않는다. 측정 입력을
엄격하게 검증하고, 실행 중 수집한 요청·단계·자원 결과를 일관된 JSON/CSV 구조로
정규화하는 역할만 담당한다.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, cast

SupportedFormat = Literal["pdf", "docx", "pptx", "xlsx", "txt"]
ContentOrigin = Literal["text", "ocr"]
BenchmarkOperation = Literal[
    "ingest",
    "search",
    "answer_lookup",
    "answer_synthesis",
]
BenchmarkPhase = Literal["cold", "warm", "coverage", "scale", "concurrency"]

_SUPPORTED_FORMATS: Final[frozenset[str]] = frozenset({"pdf", "docx", "pptx", "xlsx", "txt"})


@dataclass(frozen=True, slots=True)
class FixtureProfile:
    """하나의 합성 문서 크기·이미지 조건을 정의한다."""

    name: str
    text_units: int
    repetitions_per_unit: int
    image_count: int
    ocr_only: bool

    @property
    def content_origin(self) -> ContentOrigin:
        """프로필이 일반 텍스트 또는 OCR 중심인지 반환한다."""

        return "ocr" if self.ocr_only else "text"


@dataclass(frozen=True, slots=True)
class FixtureMatrixEntry:
    """형식과 프로필의 조합을 하나의 측정 그룹으로 묶는다."""

    group: str
    formats: tuple[SupportedFormat, ...]
    profiles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoadPlan:
    """한 API 또는 인제스트 부하 단계의 동시성 계획."""

    concurrency_levels: tuple[int, ...]
    requests_per_level: int


@dataclass(frozen=True, slots=True)
class AnswerLoadPlan:
    """Claude 비용을 고려해 lookup과 synthesis 부하를 분리한다."""

    enabled: bool
    lookup: LoadPlan
    synthesis: LoadPlan


@dataclass(frozen=True, slots=True)
class SearchPlan:
    """청크 검색 API 측정 입력."""

    top_k: int
    score_threshold: float | None
    load: LoadPlan


@dataclass(frozen=True, slots=True)
class SaturationPolicy:
    """측정 결과에서 포화 후보를 표시하기 위한 판정 규칙.

    이 값은 서비스 설정을 변경하거나 테스트를 실패시키는 임계값이 아니다. 이전
    동시성 단계와 비교했을 때 처리량 증가가 멈추고 지연시간이 커지는 지점을 보고서에
    표시하기 위한 분석 규칙이다.
    """

    max_error_rate: float
    throughput_gain_floor_percent: float
    p95_growth_trigger_percent: float


@dataclass(frozen=True, slots=True)
class BenchmarkPlan:
    """Issue #159 전체 성능 측정 계획."""

    schema_version: int
    benchmark_name: str
    test_user_idx: int
    file_idx_start: int
    sample_interval_seconds: float
    docker_sample_interval_seconds: float
    request_timeout_seconds: float
    warmup_requests: int
    fixture_profiles: tuple[FixtureProfile, ...]
    fixture_matrix: tuple[FixtureMatrixEntry, ...]
    ingest: LoadPlan
    search: SearchPlan
    answers: AnswerLoadPlan
    saturation: SaturationPolicy

    @property
    def profiles_by_name(self) -> dict[str, FixtureProfile]:
        """프로필 이름으로 빠르게 조회할 수 있는 독립 사전을 반환한다."""

        return {profile.name: profile for profile in self.fixture_profiles}


@dataclass(frozen=True, slots=True)
class GeneratedFixture:
    """실제 생성된 합성 문서와 API 요청에 필요한 메타데이터."""

    case_id: str
    group: str
    file_idx: int
    file_type: SupportedFormat
    profile_name: str
    content_origin: ContentOrigin
    path: Path
    content_type: str
    text_units: int
    image_count: int
    search_query: str
    answer_fact: str

    @property
    def file_name(self) -> str:
        """Local RAG 외부 계약에 전달할 표시 파일명."""

        return self.path.name

    @property
    def size_bytes(self) -> int:
        """생성 완료된 파일의 실제 크기."""

        return self.path.stat().st_size

    @property
    def download_url(self) -> str:
        """MockTransport 전용 HTTPS Presigned URL 형태를 반환한다."""

        return (
            f"https://files.performance.invalid/issue-159/{self.file_idx}/{self.file_name}"
            f"?X-Amz-Signature=performance-{self.file_idx}"
        )

    def clone(self, *, case_id: str, file_idx: int, group: str) -> GeneratedFixture:
        """동일한 원본 바이트를 다른 File_IDX로 처리할 부하용 Case를 만든다."""

        return GeneratedFixture(
            case_id=case_id,
            group=group,
            file_idx=file_idx,
            file_type=self.file_type,
            profile_name=self.profile_name,
            content_origin=self.content_origin,
            path=self.path,
            content_type=self.content_type,
            text_units=self.text_units,
            image_count=self.image_count,
            search_query=self.search_query,
            answer_fact=self.answer_fact,
        )

    def to_manifest(self, *, user_idx: int) -> dict[str, object]:
        """POST /api/v1/files/process 요청 본문을 생성한다."""

        return {
            "file_idx": self.file_idx,
            "user_idx": user_idx,
            "folder_idx": None,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "download_url": self.download_url,
            "url_expires_in": 3600,
        }

    def to_public_dict(self) -> dict[str, object]:
        """경로를 문자열로 바꿔 JSON 직렬화 가능한 Fixture 정보를 반환한다."""

        value = asdict(self)
        value["path"] = str(self.path)
        value["file_name"] = self.file_name
        value["size_bytes"] = self.size_bytes
        return value


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """단일 HTTP 요청의 시간·상태·응답 메타데이터."""

    run_id: str
    request_id: str
    case_id: str
    operation: BenchmarkOperation
    phase: BenchmarkPhase
    concurrency: int
    request_index: int
    started_at_utc: str
    started_epoch_seconds: float
    completed_epoch_seconds: float
    duration_ms: float
    status_code: int | None
    success: bool
    request_bytes: int
    response_bytes: int
    file_idx: int | None = None
    file_type: str | None = None
    profile_name: str | None = None
    content_origin: str | None = None
    fixture_size_bytes: int | None = None
    declared_text_units: int | None = None
    declared_image_count: int | None = None
    chunk_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, object]:
        """CSV와 JSON에 공통으로 사용할 평평한 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class StageEvent:
    """기존 구조화 로그에서 수집한 인제스트 단계 완료 이벤트."""

    run_id: str
    request_id: str | None
    file_idx: int
    file_type: str
    stage: str
    event: str
    completed_at_utc: str
    completed_epoch_seconds: float
    duration_ms: float
    chunk_count: int | None = None
    structure_unit_count: int | None = None
    text_unit_count: int | None = None
    size_bytes: int | None = None

    @property
    def started_epoch_seconds(self) -> float:
        """완료 시각과 duration_ms로 단계 시작 시각을 근사한다."""

        return self.completed_epoch_seconds - (self.duration_ms / 1000.0)

    def to_dict(self) -> dict[str, object]:
        """CSV와 JSON 직렬화용 사전을 반환한다."""

        value = cast(dict[str, object], asdict(self))
        value["started_epoch_seconds"] = self.started_epoch_seconds
        return value


@dataclass(frozen=True, slots=True)
class LevelSummary:
    """동일 operation·phase·concurrency 요청 묶음의 통계."""

    operation: BenchmarkOperation
    phase: BenchmarkPhase
    concurrency: int
    request_count: int
    success_count: int
    error_count: int
    error_rate: float
    elapsed_seconds: float
    throughput_requests_per_second: float
    mean_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    min_ms: float | None
    max_ms: float | None
    total_request_bytes: int
    total_response_bytes: int
    total_input_tokens: int
    total_output_tokens: int

    def to_dict(self) -> dict[str, object]:
        """JSON 직렬화 가능한 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class SaturationCandidate:
    """동시성 단계 비교에서 발견한 포화 또는 실패 후보."""

    operation: BenchmarkOperation
    concurrency: int
    reason: str
    previous_concurrency: int | None
    throughput_gain_percent: float | None
    p95_growth_percent: float | None
    error_rate: float

    def to_dict(self) -> dict[str, object]:
        """JSON 직렬화 가능한 사전을 반환한다."""

        return cast(dict[str, object], asdict(self))


def load_benchmark_plan(path: Path) -> BenchmarkPlan:
    """UTF-8 JSON 계획을 읽고 모든 범위·참조 계약을 검증한다."""

    raw = _object(json.loads(path.read_text(encoding="utf-8")), "benchmark plan")

    schema_version = _positive_int(raw, "schema_version")
    if schema_version != 1:
        raise ValueError(f"Unsupported benchmark plan schema_version: {schema_version}")

    profiles = tuple(
        _parse_fixture_profile(value)
        for value in _objects(raw, "fixture_profiles")
    )
    if not profiles:
        raise ValueError("fixture_profiles must contain at least one profile.")

    profile_names = tuple(profile.name for profile in profiles)
    if len(profile_names) != len(set(profile_names)):
        raise ValueError("fixture profile names must be unique.")

    matrix = tuple(
        _parse_fixture_matrix_entry(value)
        for value in _objects(raw, "fixture_matrix")
    )
    if not matrix:
        raise ValueError("fixture_matrix must contain at least one entry.")

    known_profiles = frozenset(profile_names)
    for entry in matrix:
        unknown_profiles = set(entry.profiles) - known_profiles
        if unknown_profiles:
            raise ValueError(
                f"fixture_matrix[{entry.group}] references unknown profiles: "
                f"{sorted(unknown_profiles)}"
            )
        for profile_name in entry.profiles:
            profile = next(item for item in profiles if item.name == profile_name)
            if profile.ocr_only and "txt" in entry.formats:
                raise ValueError("TXT does not support OCR fixture profiles.")

    ingest_raw = _object(raw.get("ingest"), "ingest")
    search_raw = _object(raw.get("search"), "search")
    answers_raw = _object(raw.get("answers"), "answers")
    saturation_raw = _object(raw.get("saturation"), "saturation")

    score_threshold_raw = search_raw.get("score_threshold")
    score_threshold = _optional_finite_float(score_threshold_raw, "score_threshold")
    if score_threshold is not None and not -1.0 <= score_threshold <= 1.0:
        raise ValueError("search.score_threshold must be between -1.0 and 1.0.")

    return BenchmarkPlan(
        schema_version=schema_version,
        benchmark_name=_non_empty_str(raw, "benchmark_name"),
        test_user_idx=_positive_int(raw, "test_user_idx"),
        file_idx_start=_positive_int(raw, "file_idx_start"),
        sample_interval_seconds=_positive_float(raw, "sample_interval_seconds"),
        docker_sample_interval_seconds=_positive_float(
            raw,
            "docker_sample_interval_seconds",
        ),
        request_timeout_seconds=_positive_float(raw, "request_timeout_seconds"),
        warmup_requests=_non_negative_int(raw, "warmup_requests"),
        fixture_profiles=profiles,
        fixture_matrix=matrix,
        ingest=_parse_load_plan(ingest_raw, "ingest"),
        search=SearchPlan(
            top_k=_bounded_int(search_raw, "top_k", minimum=1, maximum=20),
            score_threshold=score_threshold,
            load=_parse_load_plan(
                _object(search_raw.get("load"), "search.load"),
                "search.load",
            ),
        ),
        answers=AnswerLoadPlan(
            enabled=_bool(answers_raw, "enabled"),
            lookup=_parse_load_plan(
                _object(answers_raw.get("lookup"), "answers.lookup"),
                "answers.lookup",
            ),
            synthesis=_parse_load_plan(
                _object(answers_raw.get("synthesis"), "answers.synthesis"),
                "answers.synthesis",
            ),
        ),
        saturation=SaturationPolicy(
            max_error_rate=_bounded_float(
                saturation_raw,
                "max_error_rate",
                minimum=0.0,
                maximum=1.0,
            ),
            throughput_gain_floor_percent=_non_negative_float(
                saturation_raw,
                "throughput_gain_floor_percent",
            ),
            p95_growth_trigger_percent=_non_negative_float(
                saturation_raw,
                "p95_growth_trigger_percent",
            ),
        ),
    )


def percentile(values: tuple[float, ...], percentile_value: float) -> float | None:
    """정렬된 인접 값 사이를 선형 보간하여 지정 백분위수를 계산한다."""

    if not values:
        return None
    if not 0.0 <= percentile_value <= 100.0:
        raise ValueError("percentile_value must be between 0 and 100.")

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * (percentile_value / 100.0)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]

    fraction = position - lower_index
    return ordered[lower_index] + (
        (ordered[upper_index] - ordered[lower_index]) * fraction
    )


def summarize_level(
    records: tuple[RequestRecord, ...],
    *,
    operation: BenchmarkOperation,
    phase: BenchmarkPhase,
    concurrency: int,
    elapsed_seconds: float,
) -> LevelSummary:
    """한 동시성 단계의 요청 지연·처리량·오류·토큰 통계를 계산한다."""

    if elapsed_seconds <= 0:
        raise ValueError("elapsed_seconds must be greater than zero.")

    durations = tuple(record.duration_ms for record in records if record.success)
    success_count = sum(record.success for record in records)
    error_count = len(records) - success_count

    return LevelSummary(
        operation=operation,
        phase=phase,
        concurrency=concurrency,
        request_count=len(records),
        success_count=success_count,
        error_count=error_count,
        error_rate=(error_count / len(records)) if records else 0.0,
        elapsed_seconds=elapsed_seconds,
        throughput_requests_per_second=(success_count / elapsed_seconds),
        mean_ms=(sum(durations) / len(durations)) if durations else None,
        p50_ms=percentile(durations, 50.0),
        p95_ms=percentile(durations, 95.0),
        p99_ms=percentile(durations, 99.0),
        min_ms=min(durations) if durations else None,
        max_ms=max(durations) if durations else None,
        total_request_bytes=sum(record.request_bytes for record in records),
        total_response_bytes=sum(record.response_bytes for record in records),
        total_input_tokens=sum(record.input_tokens or 0 for record in records),
        total_output_tokens=sum(record.output_tokens or 0 for record in records),
    )


def detect_saturation_candidate(
    summaries: tuple[LevelSummary, ...],
    *,
    policy: SaturationPolicy,
) -> SaturationCandidate | None:
    """오류율 또는 처리량 정체와 p95 증가가 처음 나타난 단계를 반환한다."""

    ordered = sorted(summaries, key=lambda item: item.concurrency)
    previous: LevelSummary | None = None

    for current in ordered:
        if current.error_rate > policy.max_error_rate:
            return SaturationCandidate(
                operation=current.operation,
                concurrency=current.concurrency,
                reason="error_rate_exceeded",
                previous_concurrency=(previous.concurrency if previous is not None else None),
                throughput_gain_percent=None,
                p95_growth_percent=None,
                error_rate=current.error_rate,
            )

        if previous is None:
            previous = current
            continue

        throughput_gain = _percent_change(
            previous.throughput_requests_per_second,
            current.throughput_requests_per_second,
        )
        p95_growth = _percent_change(previous.p95_ms, current.p95_ms)

        if (
            throughput_gain is not None
            and p95_growth is not None
            and throughput_gain <= policy.throughput_gain_floor_percent
            and p95_growth >= policy.p95_growth_trigger_percent
        ):
            return SaturationCandidate(
                operation=current.operation,
                concurrency=current.concurrency,
                reason="throughput_plateau_with_latency_growth",
                previous_concurrency=previous.concurrency,
                throughput_gain_percent=throughput_gain,
                p95_growth_percent=p95_growth,
                error_rate=current.error_rate,
            )

        previous = current

    return None


def _percent_change(previous: float | None, current: float | None) -> float | None:
    """0으로 나누지 않도록 방어하며 이전 값 대비 증감률을 반환한다."""

    if previous is None or current is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def _parse_fixture_profile(raw: dict[str, object]) -> FixtureProfile:
    profile = FixtureProfile(
        name=_non_empty_str(raw, "name"),
        text_units=_non_negative_int(raw, "text_units"),
        repetitions_per_unit=_non_negative_int(raw, "repetitions_per_unit"),
        image_count=_non_negative_int(raw, "image_count"),
        ocr_only=_bool(raw, "ocr_only"),
    )
    if profile.ocr_only:
        if profile.image_count <= 0:
            raise ValueError(f"OCR profile {profile.name} requires image_count > 0.")
        if profile.text_units != 0:
            raise ValueError(f"OCR-only profile {profile.name} requires text_units == 0.")
    elif profile.text_units <= 0:
        raise ValueError(f"Text profile {profile.name} requires text_units > 0.")
    return profile


def _parse_fixture_matrix_entry(raw: dict[str, object]) -> FixtureMatrixEntry:
    group = _non_empty_str(raw, "group")
    formats_raw = _strings(raw, "formats")
    profiles = _strings(raw, "profiles")

    if not formats_raw or not profiles:
        raise ValueError(f"fixture_matrix[{group}] requires formats and profiles.")
    if len(formats_raw) != len(set(formats_raw)):
        raise ValueError(f"fixture_matrix[{group}].formats must not contain duplicates.")
    if len(profiles) != len(set(profiles)):
        raise ValueError(f"fixture_matrix[{group}].profiles must not contain duplicates.")

    invalid_formats = set(formats_raw) - _SUPPORTED_FORMATS
    if invalid_formats:
        raise ValueError(f"Unsupported fixture formats: {sorted(invalid_formats)}")

    return FixtureMatrixEntry(
        group=group,
        formats=cast(tuple[SupportedFormat, ...], formats_raw),
        profiles=profiles,
    )


def _parse_load_plan(raw: dict[str, object], label: str) -> LoadPlan:
    levels = _ints(raw, "concurrency_levels")
    if not levels:
        raise ValueError(f"{label}.concurrency_levels must not be empty.")
    if any(level <= 0 for level in levels):
        raise ValueError(f"{label}.concurrency_levels must contain positive integers.")
    if tuple(sorted(set(levels))) != levels:
        raise ValueError(
            f"{label}.concurrency_levels must be unique and strictly increasing."
        )

    requests_per_level = _positive_int(raw, "requests_per_level")
    if requests_per_level < max(levels):
        raise ValueError(
            f"{label}.requests_per_level must be at least the maximum concurrency level."
        )

    return LoadPlan(
        concurrency_levels=levels,
        requests_per_level=requests_per_level,
    )


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object with string keys.")
    return cast(dict[str, object], value)


def _objects(mapping: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array.")
    return tuple(_object(item, f"{key} item") for item in cast(list[object], value))


def _non_empty_str(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
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


def _ints(mapping: dict[str, object], key: str) -> tuple[int, ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array.")
    result: list[int] = []
    for item in cast(list[object], value):
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{key} must contain only integers.")
        result.append(item)
    return tuple(result)


def _bool(mapping: dict[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean.")
    return value


def _positive_int(mapping: dict[str, object], key: str) -> int:
    return _bounded_int(mapping, key, minimum=1, maximum=2**63 - 1)


def _non_negative_int(mapping: dict[str, object], key: str) -> int:
    return _bounded_int(mapping, key, minimum=0, maximum=2**63 - 1)


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


def _bounded_float(
    mapping: dict[str, object],
    key: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = _finite_float(mapping.get(key), key)
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}.")
    return value


def _optional_finite_float(value: object, key: str) -> float | None:
    if value is None:
        return None
    return _finite_float(value, key)


def _finite_float(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number.")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{key} must be finite.")
    return normalized
