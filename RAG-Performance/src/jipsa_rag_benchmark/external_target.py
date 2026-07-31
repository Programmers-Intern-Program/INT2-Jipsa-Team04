"""외부 RAG 한계 테스트 대상 설정을 생성하고 검증한다.

기본 실행은 ``RAG/.env.local``에서 외부 Origin과 Token을 읽고, Qdrant·DB·Snapshot에서
자동 선정한 실제 ``Users_IDX``와 ``File_IDX``를 결합한다. 수동 JSON 설정은 재현 실험을
위한 호환 경로로만 유지한다.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx

from jipsa_rag_benchmark.rag_environment import RagEnvironmentSettings
from jipsa_rag_benchmark.test_data_discovery import DiscoveredTestData

TargetEnvironment = Literal["test", "staging", "production"]


@dataclass(frozen=True, slots=True)
class ExternalTargetConfig:
    """외부 RAG에 HTTP Black-box 부하를 보내기 위한 비밀정보 없는 설정."""

    schema_version: int
    target_base_url: str
    target_environment: TargetEnvironment
    health_path: str
    readiness_path: str | None
    search_path: str
    test_user_idx: int
    reference_file_idxs: tuple[int, ...]
    queries: tuple[str, ...]
    top_k: int
    score_threshold: float | None
    connect_timeout_seconds: float
    request_timeout_seconds: float
    selection_source: str = "configured"
    selection_seed: int | None = None
    selection_detail: str | None = None
    candidate_user_count: int | None = None
    candidate_file_count: int | None = None
    candidate_chunk_count: int | None = None

    @property
    def target_origin(self) -> str:
        """사용자 정보·Path·Query가 제거된 Origin만 반환한다."""

        parsed = urlsplit(self.target_base_url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{host}{port}"

    @property
    def target_host(self) -> str:
        """파괴적 실행 확인에 사용할 정규화 Host 이름을 반환한다."""

        return (urlsplit(self.target_base_url).hostname or "").lower()

    def to_public_dict(self) -> dict[str, object]:
        """보고서에 저장 가능한 설정을 반환하되 질문 원문은 개수로 대체한다."""

        values = asdict(self)
        values["target_base_url"] = self.target_origin
        values["reference_file_idxs"] = list(self.reference_file_idxs)
        values["query_count"] = len(self.queries)
        values.pop("queries", None)
        return values


def build_external_target_config(
    settings: RagEnvironmentSettings,
    discovered: DiscoveredTestData,
    *,
    top_k: int = 5,
    score_threshold: float | None = None,
    connect_timeout_seconds: float = 10.0,
    request_timeout_seconds: float = 60.0,
    allow_insecure_http: bool = False,
    allow_loopback_target: bool = False,
) -> ExternalTargetConfig:
    """RAG 환경과 자동 선정 데이터로 외부 Target 설정을 구성한다."""

    _validate_target_url(
        settings.external_base_url,
        allow_insecure_http=allow_insecure_http,
        allow_loopback_target=allow_loopback_target,
    )
    _validate_top_k(top_k)
    _validate_score_threshold(score_threshold)
    return ExternalTargetConfig(
        schema_version=1,
        target_base_url=settings.external_base_url,
        target_environment=settings.target_environment,
        health_path=settings.health_path,
        readiness_path=settings.readiness_path,
        search_path=settings.search_path,
        test_user_idx=discovered.user_idx,
        reference_file_idxs=discovered.file_idxs,
        queries=discovered.queries,
        top_k=top_k,
        score_threshold=score_threshold,
        connect_timeout_seconds=connect_timeout_seconds,
        request_timeout_seconds=request_timeout_seconds,
        selection_source=discovered.source,
        selection_seed=discovered.random_seed,
        selection_detail=discovered.source_detail,
        candidate_user_count=discovered.candidate_user_count,
        candidate_file_count=discovered.candidate_file_count,
        candidate_chunk_count=discovered.candidate_chunk_count,
    )


def validate_search_scope(
    target: ExternalTargetConfig,
    *,
    internal_token: str,
    verify_tls: bool,
) -> ExternalTargetConfig:
    """자동 선정 파일이 외부 Search API에서 실제 결과를 반환하는지 검증한다.

    각 File IDX에 대응하는 Query를 한 번씩 호출하고 ``result_count > 0``인 파일만 유지한다.
    검증 요청은 성능 측정 결과에 포함하지 않으며 질문·응답 본문도 저장하지 않는다.
    """

    timeout = httpx.Timeout(
        connect=target.connect_timeout_seconds,
        read=target.request_timeout_seconds,
        write=target.request_timeout_seconds,
        pool=target.request_timeout_seconds,
    )
    valid_files: list[int] = []
    valid_queries: list[str] = []
    headers = {
        "X-Internal-Token": internal_token,
        "User-Agent": "jipsa-rag-performance/scope-validation",
        "Accept": "application/json",
    }
    with httpx.Client(
        base_url=target.target_base_url,
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        verify=verify_tls,
    ) as client:
        for index, file_idx in enumerate(target.reference_file_idxs):
            query = target.queries[index % len(target.queries)]
            response = client.post(
                target.search_path,
                headers={"X-Request-ID": f"scope-validation-{index + 1}"},
                json={
                    "user_idx": target.test_user_idx,
                    "reference_file_idxs": [file_idx],
                    "query": query,
                    "top_k": target.top_k,
                    "score_threshold": target.score_threshold,
                },
            )
            if response.status_code == 401:
                raise PermissionError(
                    "RAG_INGEST_TOKEN from RAG/.env.local was rejected by the external RAG."
                )
            if not 200 <= response.status_code < 300:
                continue
            body = response.json()
            if not isinstance(body, dict) or body.get("success") is False:
                continue
            data = body.get("data")
            result_count = data.get("result_count") if isinstance(data, dict) else None
            # bool은 int의 하위 타입이므로 명시적으로 제외한다. 검색 결과가 실제 양의
            # 정수일 때만 File IDX와 대응 Query를 최종 부하 테스트 범위에 포함한다.
            if (
                isinstance(result_count, int)
                and not isinstance(result_count, bool)
                and result_count > 0
            ):
                valid_files.append(file_idx)
                valid_queries.append(query)

    if not valid_files:
        raise LookupError(
            "Automatically selected User/File values returned no external RAG search results. "
            "Check that the external RAG and local Qdrant/DB/Snapshot represent the same data."
        )

    remaining_queries = [query for query in target.queries if query not in valid_queries]
    return replace(
        target,
        reference_file_idxs=tuple(valid_files),
        queries=tuple(valid_queries + remaining_queries),
    )


def load_external_target_config(
    path: Path,
    *,
    allow_insecure_http: bool,
    allow_loopback_target: bool,
) -> ExternalTargetConfig:
    """수동 JSON 설정을 읽고 외부 대상·검색 범위·Timeout을 검증한다."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("External target config root must be an object.")

    schema_version = _positive_int(raw, "schema_version")
    if schema_version != 1:
        raise ValueError("external target schema_version must be 1.")

    target_base_url = _required_text(raw, "target_base_url").rstrip("/")
    _validate_target_url(
        target_base_url,
        allow_insecure_http=allow_insecure_http,
        allow_loopback_target=allow_loopback_target,
    )

    environment = _required_text(raw, "target_environment").lower()
    if environment not in {"test", "staging", "production"}:
        raise ValueError("target_environment must be test, staging, or production.")

    reference_file_idxs = _positive_int_tuple(raw, "reference_file_idxs")
    queries = _non_empty_text_tuple(raw, "queries")
    top_k = _positive_int(raw, "top_k")
    _validate_top_k(top_k)

    score_threshold_raw = raw.get("score_threshold")
    score_threshold: float | None
    if score_threshold_raw is None:
        score_threshold = None
    elif isinstance(score_threshold_raw, bool) or not isinstance(score_threshold_raw, int | float):
        raise ValueError("score_threshold must be a number or null.")
    else:
        score_threshold = float(score_threshold_raw)
    _validate_score_threshold(score_threshold)

    readiness_raw = raw.get("readiness_path")
    readiness_path = None if readiness_raw is None else _path_text(readiness_raw, "readiness_path")

    return ExternalTargetConfig(
        schema_version=schema_version,
        target_base_url=target_base_url,
        target_environment=cast(TargetEnvironment, environment),
        health_path=_path_text(raw.get("health_path"), "health_path"),
        readiness_path=readiness_path,
        search_path=_path_text(raw.get("search_path"), "search_path"),
        test_user_idx=_positive_int(raw, "test_user_idx"),
        reference_file_idxs=reference_file_idxs,
        queries=queries,
        top_k=top_k,
        score_threshold=score_threshold,
        connect_timeout_seconds=_positive_float(raw, "connect_timeout_seconds"),
        request_timeout_seconds=_positive_float(raw, "request_timeout_seconds"),
    )


def _validate_target_url(
    value: str,
    *,
    allow_insecure_http: bool,
    allow_loopback_target: bool,
) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("target_base_url must use http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("target_base_url must not contain user information.")
    if not parsed.hostname:
        raise ValueError("target_base_url must include a host.")
    if parsed.query or parsed.fragment:
        raise ValueError("target_base_url must not contain a query or fragment.")
    if parsed.path not in {"", "/"}:
        raise ValueError("target_base_url must be an origin without an API path.")
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ValueError(
            "External HTTP requires --allow-insecure-http. HTTPS is required by default."
        )
    if _is_loopback_host(parsed.hostname) and not allow_loopback_target:
        raise ValueError("Loopback target is blocked. This runner is for an external RAG endpoint.")


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_top_k(value: int) -> None:
    if not 1 <= value <= 20:
        raise ValueError("top_k must be between 1 and 20.")


def _validate_score_threshold(value: float | None) -> None:
    if value is not None and not -1.0 <= value <= 1.0:
        raise ValueError("score_threshold must be between -1.0 and 1.0 or null.")


def _required_text(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    return value.strip()


def _path_text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{key} must be an absolute API path beginning with '/'.")
    if "?" in value or "#" in value:
        raise ValueError(f"{key} must not contain a query or fragment.")
    return value


def _positive_int(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer.")
    return value


def _positive_float(raw: dict[str, object], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{key} must be a positive number.")
    return float(value)


def _positive_int_tuple(raw: dict[str, object], key: str) -> tuple[int, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty array.")
    normalized: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ValueError(f"{key} must contain only positive integers.")
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def _non_empty_text_tuple(raw: dict[str, object], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty array.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must contain only non-empty strings.")
        text = item.strip()
        if text not in normalized:
            normalized.append(text)
    return tuple(normalized)
