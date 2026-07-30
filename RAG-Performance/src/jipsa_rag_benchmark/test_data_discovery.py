"""기존 Qdrant·Local RAG DB·Qdrant Snapshot에서 테스트 범위를 자동 선정한다.

선정 과정은 읽기 전용이다. Snapshot Source를 사용할 때도 운영 Collection에 복원하지 않고,
임시 Qdrant Container와 임시 Collection을 생성한 뒤 반드시 제거한다. 선택된 질문 원문과
청크 원문은 보고서에 기록하지 않는다.
"""

from __future__ import annotations

import base64
import os
import random
import re
import secrets
import shutil
import subprocess
import tarfile
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast
from uuid import uuid4

import httpx

from jipsa_rag_benchmark.rag_environment import RagEnvironmentSettings

DataSource = Literal["auto", "qdrant", "database", "snapshot"]

_DEFAULT_QUERY_POOL: Final[tuple[str, ...]] = (
    "선택한 문서의 핵심 내용을 알려줘",
    "문서에서 중요한 조건을 찾아줘",
    "문서에 명시된 수치와 근거를 찾아줘",
    "선택한 문서의 주요 절차를 정리해줘",
)
_MAX_DISCOVERY_POINTS: Final[int] = 4096
_QDRANT_SCROLL_PAGE_SIZE: Final[int] = 256
_CONTENT_QUERY_MAX_CHARS: Final[int] = 180
_CONTROL_CHARACTER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]+")
# 전각 느낌표와 물음표는 소스에서 혼동 가능한 글리프로 보이지 않도록 Unicode Escape로
# 표현한다. 정규식 엔진은 Escape를 실제 문장부호로 해석하므로 기존 분리 동작은 유지된다.
_SENTENCE_BOUNDARY_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?。\uFF01\uFF1F])\s+")


@dataclass(frozen=True, slots=True)
class DataCandidate:
    """Qdrant 또는 DB에서 읽은 하나의 검색 가능한 청크 후보."""

    user_idx: int
    file_idx: int
    content: str


@dataclass(frozen=True, slots=True)
class DiscoveredTestData:
    """외부 Search API에 전달할 자동 선정 사용자·파일·질문 범위."""

    source: DataSource
    user_idx: int
    file_idxs: tuple[int, ...]
    queries: tuple[str, ...]
    random_seed: int
    candidate_user_count: int
    candidate_file_count: int
    candidate_chunk_count: int
    source_detail: str
    fallback_errors: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        """청크·질문 원문 없이 선정 근거만 반환한다."""

        return {
            "source": self.source,
            "source_detail": self.source_detail,
            "user_idx": self.user_idx,
            "file_idxs": list(self.file_idxs),
            "query_count": len(self.queries),
            "random_seed": self.random_seed,
            "candidate_user_count": self.candidate_user_count,
            "candidate_file_count": self.candidate_file_count,
            "candidate_chunk_count": self.candidate_chunk_count,
            "fallback_errors": list(self.fallback_errors),
            "raw_content_recorded": False,
            "query_text_recorded": False,
        }


def discover_test_data(
    settings: RagEnvironmentSettings,
    *,
    source: DataSource,
    files_per_user: int,
    query_count: int,
    random_seed: int | None,
    snapshot_path: Path | None,
    snapshot_search_roots: Sequence[Path],
    qdrant_scan_limit: int = _MAX_DISCOVERY_POINTS,
) -> DiscoveredTestData:
    """Source 우선순위에 따라 기존 데이터에서 테스트 범위를 무작위 선정한다."""

    if files_per_user <= 0:
        raise ValueError("files_per_user must be positive.")
    if query_count <= 0:
        raise ValueError("query_count must be positive.")
    if qdrant_scan_limit <= 0:
        raise ValueError("qdrant_scan_limit must be positive.")

    seed = random_seed if random_seed is not None else secrets.randbits(63)
    fallback_errors: list[str] = []
    source_order = _source_order(source)
    for candidate_source in source_order:
        try:
            if candidate_source == "qdrant":
                candidates = _discover_from_qdrant(
                    url=settings.qdrant_url,
                    collection=settings.qdrant_collection,
                    api_key=settings.qdrant_api_key,
                    scan_limit=qdrant_scan_limit,
                )
                detail = _safe_origin(settings.qdrant_url)
            elif candidate_source == "database":
                candidates = _discover_from_database(
                    settings,
                    scan_limit=qdrant_scan_limit,
                )
                detail = settings.database_name or "Local RAG DB"
            else:
                resolved_snapshot = find_snapshot(
                    explicit_path=snapshot_path,
                    search_roots=snapshot_search_roots,
                    collection_name=settings.qdrant_collection,
                )
                candidates = _discover_from_snapshot(
                    resolved_snapshot,
                    collection_name=settings.qdrant_collection,
                    scan_limit=qdrant_scan_limit,
                )
                detail = resolved_snapshot.name

            return _select_test_data(
                candidates,
                source=candidate_source,
                source_detail=detail,
                files_per_user=files_per_user,
                query_count=query_count,
                random_seed=seed,
                fallback_errors=tuple(fallback_errors),
            )
        except Exception as error:
            fallback_errors.append(
                f"{candidate_source}:{type(error).__name__}:{_safe_error(error)}"
            )
            if source != "auto":
                raise

    joined = "; ".join(fallback_errors) or "no discovery source was attempted"
    raise RuntimeError(f"Existing RAG test data could not be discovered: {joined}")


def find_snapshot(
    *,
    explicit_path: Path | None,
    search_roots: Sequence[Path],
    collection_name: str,
) -> Path:
    """명시 경로 또는 검색 Root에서 Collection과 가장 가까운 최신 Snapshot을 찾는다."""

    if explicit_path is not None:
        resolved = explicit_path.expanduser().resolve()
        _validate_snapshot_file(resolved)
        return resolved

    candidates: list[Path] = []
    for root in search_roots:
        resolved_root = root.expanduser().resolve()
        if not resolved_root.exists():
            continue
        if resolved_root.is_file() and resolved_root.suffix.lower() == ".snapshot":
            candidates.append(resolved_root)
            continue
        if resolved_root.is_dir():
            candidates.extend(path for path in resolved_root.rglob("*.snapshot") if path.is_file())

    if not candidates:
        roots = ", ".join(str(path) for path in search_roots)
        raise FileNotFoundError(f"No Qdrant snapshot was found under: {roots}")

    normalized_collection = collection_name.lower()
    candidates.sort(
        key=lambda path: (
            normalized_collection in path.name.lower(),
            path.stat().st_mtime,
        ),
        reverse=True,
    )
    selected = candidates[0]
    _validate_snapshot_file(selected)
    return selected


def _source_order(source: DataSource) -> tuple[DataSource, ...]:
    if source == "auto":
        return ("qdrant", "database", "snapshot")
    return (source,)


def _discover_from_qdrant(
    *,
    url: str,
    collection: str,
    api_key: str | None,
    scan_limit: int,
) -> list[DataCandidate]:
    headers = {"api-key": api_key} if api_key else {}
    timeout = httpx.Timeout(15.0, connect=5.0)
    candidates: list[DataCandidate] = []
    next_offset: object | None = None

    with httpx.Client(
        base_url=url,
        headers=headers,
        timeout=timeout,
        trust_env=False,
    ) as client:
        collection_response = client.get(f"/collections/{collection}")
        collection_response.raise_for_status()

        while len(candidates) < scan_limit:
            body: dict[str, object] = {
                "limit": min(_QDRANT_SCROLL_PAGE_SIZE, scan_limit - len(candidates)),
                "with_payload": ["users_idx", "file_idx", "is_active", "content"],
                "with_vector": False,
                "filter": {
                    "must": [
                        {
                            "key": "is_active",
                            "match": {"value": True},
                        }
                    ]
                },
            }
            if next_offset is not None:
                body["offset"] = next_offset

            response = client.post(
                f"/collections/{collection}/points/scroll",
                json=body,
            )
            response.raise_for_status()
            root = response.json()
            result = root.get("result") if isinstance(root, dict) else None
            if not isinstance(result, dict):
                raise ValueError("Qdrant scroll response result is not an object.")
            points = result.get("points")
            if not isinstance(points, list):
                raise ValueError("Qdrant scroll response points is not an array.")

            for point in points:
                candidate = _candidate_from_qdrant_point(point)
                if candidate is not None:
                    candidates.append(candidate)
                    if len(candidates) >= scan_limit:
                        break

            next_offset = result.get("next_page_offset")
            if next_offset is None or not points:
                break

    if not candidates:
        raise LookupError("No active Qdrant payload with users_idx and file_idx was found.")
    return candidates


def _candidate_from_qdrant_point(point: object) -> DataCandidate | None:
    if not isinstance(point, dict):
        return None
    payload = point.get("payload")
    if not isinstance(payload, dict) or payload.get("is_active") is not True:
        return None
    user_idx = _positive_int(payload.get("users_idx"))
    file_idx = _positive_int(payload.get("file_idx"))
    if user_idx is None or file_idx is None:
        return None
    content = payload.get("content")
    return DataCandidate(
        user_idx=user_idx,
        file_idx=file_idx,
        content=content if isinstance(content, str) else "",
    )


def _discover_from_database(
    settings: RagEnvironmentSettings,
    *,
    scan_limit: int,
) -> list[DataCandidate]:
    executable = shutil.which("mariadb") or shutil.which("mysql")
    required = (
        settings.database_host,
        settings.database_port,
        settings.database_name,
        settings.database_user,
    )
    if executable is None or any(value is None for value in required):
        raise RuntimeError(
            "mariadb/mysql client or Local RAG database connection settings are unavailable."
        )

    sql = (
        "SELECT c.Users_IDX, c.File_IDX, "
        "TO_BASE64(LEFT(REPLACE(REPLACE(c.Content, '\\r', ' '), '\\n', ' '), 512)) "
        "FROM RAG_Chunk AS c "
        "INNER JOIN RAG_Document AS d "
        "ON d.RAG_Document_IDX = c.RAG_Document_IDX "
        "WHERE d.Deleted_At IS NULL "
        "AND d.Index_Status = 'INDEXED' "
        "AND d.Chunk_Count > 0 "
        f"ORDER BY RAND() LIMIT {min(scan_limit, _MAX_DISCOVERY_POINTS)};"
    )
    command = [
        executable,
        "--batch",
        "--raw",
        "--skip-column-names",
        "--connect-timeout=5",
        "--host",
        cast(str, settings.database_host),
        "--port",
        str(settings.database_port),
        "--user",
        cast(str, settings.database_user),
        cast(str, settings.database_name),
        "--execute",
        sql,
    ]
    environment = dict(os.environ)
    if settings.database_password:
        environment["MYSQL_PWD"] = settings.database_password

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Local RAG DB discovery failed: "
            f"{completed.stderr.strip()[:240] or 'unknown mysql client error'}"
        )

    candidates: list[DataCandidate] = []
    for line in completed.stdout.splitlines():
        columns = line.split("\t")
        if len(columns) != 3:
            continue
        user_idx = _positive_int_text(columns[0])
        file_idx = _positive_int_text(columns[1])
        if user_idx is None or file_idx is None:
            continue
        try:
            content = base64.b64decode(columns[2]).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            content = ""
        candidates.append(DataCandidate(user_idx, file_idx, content))

    if not candidates:
        raise LookupError("No indexed Local RAG DB chunk was found.")
    return candidates


def _discover_from_snapshot(
    snapshot_path: Path,
    *,
    collection_name: str,
    scan_limit: int,
) -> list[DataCandidate]:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("Docker is required to inspect a Qdrant snapshot safely.")

    container_name = f"jipsa-perf-snapshot-{uuid4().hex[:10]}"
    temporary_collection = f"jipsa_perf_snapshot_{uuid4().hex[:10]}"
    image = _resolve_qdrant_image(docker)
    started = False
    try:
        start = subprocess.run(
            [
                docker,
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--publish",
                "127.0.0.1::6333",
                image,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if start.returncode != 0:
            raise RuntimeError(
                f"Temporary Qdrant container failed to start: {start.stderr.strip()[:240]}"
            )
        started = True
        port = _resolve_container_port(docker, container_name)
        base_url = f"http://127.0.0.1:{port}"
        _wait_for_qdrant(base_url)

        with (
            snapshot_path.open("rb") as snapshot_file,
            httpx.Client(
                base_url=base_url,
                timeout=httpx.Timeout(600.0, connect=10.0),
                trust_env=False,
            ) as client,
        ):
            response = client.post(
                f"/collections/{temporary_collection}/snapshots/upload",
                params={"priority": "snapshot", "wait": "true"},
                files={
                    "snapshot": (
                        snapshot_path.name,
                        snapshot_file,
                        "application/octet-stream",
                    )
                },
            )
            response.raise_for_status()

        return _discover_from_qdrant(
            url=base_url,
            collection=temporary_collection,
            api_key=None,
            scan_limit=scan_limit,
        )
    finally:
        if started:
            subprocess.run(
                [docker, "rm", "--force", container_name],
                check=False,
                capture_output=True,
                timeout=30,
            )


def _resolve_qdrant_image(docker: str) -> str:
    """Snapshot과 호환될 가능성이 가장 높은 기존 Local Qdrant Image를 선택한다.

    먼저 프로젝트 Container ``jipsa-qdrant``가 사용한 정확한 Image를 조회한다. Container가
    없으면 이미 Local Docker에 존재하는 ``qdrant/qdrant`` Tag를 사용한다. ``latest``를
    암묵적으로 Pull하면 Snapshot 생성 버전과 달라질 수 있으므로 새 Image는 자동 다운로드하지
    않는다.
    """

    inspect = subprocess.run(
        [docker, "inspect", "--format", "{{.Config.Image}}", "jipsa-qdrant"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    container_image = inspect.stdout.strip()
    if container_image:
        return container_image

    images = subprocess.run(
        [
            docker,
            "image",
            "ls",
            "qdrant/qdrant",
            "--format",
            "{{.Repository}}:{{.Tag}}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    for image in images.stdout.splitlines():
        normalized = image.strip()
        if normalized and not normalized.endswith(":<none>"):
            return normalized

    raise RuntimeError(
        "No existing Local Qdrant Docker image was found for isolated snapshot inspection. "
        "Start the project Qdrant container once before using DataSource=snapshot."
    )


def _resolve_container_port(docker: str, container_name: str) -> int:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        result = subprocess.run(
            [docker, "port", container_name, "6333/tcp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        value = result.stdout.strip().splitlines()
        if value:
            match = re.search(r":(\d+)$", value[-1])
            if match:
                return int(match.group(1))
        time.sleep(0.25)
    raise TimeoutError("Temporary Qdrant host port was not assigned.")


def _wait_for_qdrant(base_url: str) -> None:
    deadline = time.monotonic() + 60.0
    with httpx.Client(base_url=base_url, timeout=2.0, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get("/readyz")
                if 200 <= response.status_code < 300:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    raise TimeoutError("Temporary Qdrant did not become ready within 60 seconds.")


def _validate_snapshot_file(path: Path) -> None:
    if not path.is_file() or path.suffix.lower() != ".snapshot":
        raise FileNotFoundError(f"Qdrant snapshot was not found: {path}")
    try:
        with tarfile.open(path, mode="r:*") as archive:
            names = set(archive.getnames())
    except tarfile.TarError as error:
        raise ValueError(f"Invalid Qdrant snapshot archive: {path.name}") from error
    required = {"version.info", "config.json"}
    if not required.issubset(names):
        raise ValueError(f"Snapshot {path.name} is missing required Qdrant collection metadata.")


def _select_test_data(
    candidates: Sequence[DataCandidate],
    *,
    source: DataSource,
    source_detail: str,
    files_per_user: int,
    query_count: int,
    random_seed: int,
    fallback_errors: tuple[str, ...],
) -> DiscoveredTestData:
    grouped: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for candidate in candidates:
        if candidate.content.strip():
            grouped[candidate.user_idx][candidate.file_idx].append(candidate.content)
        else:
            grouped[candidate.user_idx][candidate.file_idx]

    if not grouped:
        raise LookupError("No valid users_idx/file_idx pair was discovered.")

    eligible = [user_idx for user_idx, files in grouped.items() if len(files) >= files_per_user]
    if not eligible:
        maximum = max(len(files) for files in grouped.values())
        eligible = [user_idx for user_idx, files in grouped.items() if len(files) == maximum]

    rng = random.Random(random_seed)
    selected_user = rng.choice(sorted(eligible))
    available_files = sorted(grouped[selected_user])
    selected_count = min(files_per_user, len(available_files))
    selected_files = tuple(sorted(rng.sample(available_files, selected_count)))

    selected_queries: list[str] = []
    for file_idx in selected_files:
        content_pool = grouped[selected_user][file_idx]
        if content_pool:
            query = _query_from_content(rng.choice(content_pool))
            if query and query not in selected_queries:
                selected_queries.append(query)

    defaults = list(_DEFAULT_QUERY_POOL)
    rng.shuffle(defaults)
    for query in defaults:
        if query not in selected_queries:
            selected_queries.append(query)
        if len(selected_queries) >= query_count:
            break
    while len(selected_queries) < query_count:
        selected_queries.append(f"선택한 문서의 근거를 찾아줘 {len(selected_queries) + 1}")

    all_file_pairs = {(candidate.user_idx, candidate.file_idx) for candidate in candidates}
    return DiscoveredTestData(
        source=source,
        user_idx=selected_user,
        file_idxs=selected_files,
        queries=tuple(selected_queries[:query_count]),
        random_seed=random_seed,
        candidate_user_count=len(grouped),
        candidate_file_count=len(all_file_pairs),
        candidate_chunk_count=len(candidates),
        source_detail=source_detail,
        fallback_errors=fallback_errors,
    )


def _query_from_content(content: str) -> str:
    normalized = _CONTROL_CHARACTER_PATTERN.sub(" ", content)
    normalized = " ".join(normalized.split())
    if not normalized:
        return ""
    sentence = _SENTENCE_BOUNDARY_PATTERN.split(normalized, maxsplit=1)[0]
    candidate = sentence if len(sentence) >= 8 else normalized
    return candidate[:_CONTENT_QUERY_MAX_CHARS].rstrip()


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _positive_int_text(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _safe_error(error: BaseException) -> str:
    return " ".join(str(error).split())[:240] or type(error).__name__


def _safe_origin(url: str) -> str:
    parsed = httpx.URL(url)
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{parsed.host}{port}"


def public_candidates(candidates: Iterable[DataCandidate]) -> list[dict[str, int]]:
    """테스트에서 후보 식별자만 확인할 수 있도록 공개 형태로 변환한다."""

    return [{"user_idx": item.user_idx, "file_idx": item.file_idx} for item in candidates]
