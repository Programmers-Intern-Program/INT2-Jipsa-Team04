"""성능·신뢰성 측정 전용 Local RAG 대상 프로세스를 실행한다.

이 파일은 ``RAG-Performance``의 Python 환경이 아니라 측정 대상 ``RAG``의
``uv run python``으로 실행된다. 실제 RAG 의존성과 소스 코드를 사용하면서 부하 생성기,
자원 수집기, Markdown 보고서 작성기는 별도 프로세스로 격리한다.

안전 계약
---------
- AWS Backend와 실제 S3 네트워크는 측정하지 않는다.
- ``JIPSA_RAG_APP_ENV=test``를 강제한다.
- Issue #159 전용 Users_IDX·File_IDX만 정리한다.
- 실행별 고유 Qdrant Collection만 사용하고 종료 시 Collection 전체를 삭제한다.
- 실제 Host/GPU OOM을 의도적으로 만들지 않는다. OOM Probe는 최대 256 MiB의 별도 Worker가
  의도적으로 ``MemoryError``를 반환하는 제어된 기록 경로만 검증한다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

_DOWNLOAD_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/issue-159/(?P<file_idx>[1-9][0-9]*)/(?P<file_name>[^/]+)$"
)
_SAFE_COLLECTION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_SAFE_DATABASE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_BENCHMARK_HEADER: Final[str] = "X-Benchmark-Token"
_DEFAULT_COLLECTION_PREFIX: Final[str] = "rag_benchmark_issue_159_"
_MIN_TEST_USER_IDX: Final[int] = 159_000
_MIN_TEST_FILE_IDX: Final[int] = 1_590_000
_OWNED_TABLES: Final[tuple[str, ...]] = (
    "RAG_Index_Run",
    "RAG_Chunk",
    "RAG_Document",
)
_CONTROLLED_OOM_EXIT_CODE: Final[int] = 86


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated Jipsa RAG benchmark target.")
    parser.add_argument("--rag-root", type=Path, required=True)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--benchmark-token", required=True)
    parser.add_argument("--test-user-idx", type=int, required=True)
    parser.add_argument("--keep-test-data", action="store_true")
    parser.add_argument("--download-temp-directory", type=Path, required=True)
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--verification-output", type=Path)
    return parser.parse_args()


def _prepare_import_path(rag_root: Path) -> None:
    """측정 대상 RAG의 src-layout을 현재 프로세스 import 경로에 추가한다."""

    source_root = rag_root / "src"
    if not (source_root / "jipsa_rag").is_dir():
        raise FileNotFoundError(f"RAG source package does not exist: {source_root}")
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))


def _load_fixture_manifest(path: Path) -> dict[int, dict[str, object]]:
    """생성 Fixture Manifest를 읽고 File_IDX·파일 경로 계약을 검증한다."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Fixture manifest schema_version must be 1.")
    values = raw.get("fixtures")
    if not isinstance(values, list):
        raise ValueError("Fixture manifest fixtures must be an array.")

    result: dict[int, dict[str, object]] = {}
    for item in cast(list[object], values):
        if not isinstance(item, dict):
            raise ValueError("Each fixture manifest item must be an object.")
        fixture = cast(dict[str, object], item)
        file_idx = fixture.get("file_idx")
        path_value = fixture.get("path")
        content_type = fixture.get("content_type")
        file_name = fixture.get("file_name")
        if isinstance(file_idx, bool) or not isinstance(file_idx, int) or file_idx <= 0:
            raise ValueError("fixture.file_idx must be a positive integer.")
        if not isinstance(path_value, str) or not path_value:
            raise ValueError("fixture.path must be a non-empty string.")
        if not isinstance(content_type, str) or not content_type:
            raise ValueError("fixture.content_type must be a non-empty string.")
        if not isinstance(file_name, str) or not file_name:
            raise ValueError("fixture.file_name must be a non-empty string.")
        fixture_path = Path(path_value).resolve()
        if not fixture_path.is_file():
            raise FileNotFoundError(f"Fixture file does not exist: {fixture_path}")
        if file_idx in result:
            raise ValueError(f"Duplicate fixture file_idx: {file_idx}")
        normalized = dict(fixture)
        normalized["path"] = str(fixture_path)
        result[file_idx] = normalized
    return result


def _resolve_benchmark_collection(
    *,
    benchmark_token: str,
    explicit_collection: str | None,
    prefix: str,
) -> str:
    """실행별 고유하고 삭제 가능한 Qdrant Collection 이름을 확정한다."""

    if not prefix.startswith(_DEFAULT_COLLECTION_PREFIX):
        raise ValueError(
            f"Benchmark collection prefix must start with {_DEFAULT_COLLECTION_PREFIX}."
        )
    if _SAFE_COLLECTION_PATTERN.fullmatch(prefix) is None:
        raise ValueError("Benchmark collection prefix contains an unsafe character.")

    if explicit_collection is not None and explicit_collection.strip():
        collection = explicit_collection.strip()
    else:
        digest = hashlib.sha256(benchmark_token.encode("utf-8")).hexdigest()[:20]
        collection = f"{prefix}{digest}"

    if _SAFE_COLLECTION_PATTERN.fullmatch(collection) is None:
        raise ValueError("Benchmark Qdrant collection name is invalid.")
    if not collection.startswith(prefix):
        raise ValueError("Benchmark Qdrant collection is outside the owned prefix.")
    return collection


def _validate_owned_ids(test_user_idx: int, file_idxs: Sequence[int]) -> tuple[int, ...]:
    """Issue #159 전용 ID 범위를 벗어난 데이터 삭제를 차단한다."""

    if test_user_idx < _MIN_TEST_USER_IDX:
        raise ValueError(f"test_user_idx must be at least {_MIN_TEST_USER_IDX}.")
    normalized = tuple(dict.fromkeys(file_idxs))
    if not normalized:
        raise ValueError("At least one owned File_IDX is required.")
    if any(value < _MIN_TEST_FILE_IDX for value in normalized):
        raise ValueError(f"Every File_IDX must be at least {_MIN_TEST_FILE_IDX}.")
    return normalized


def _resolve_database_override(value: str | None) -> str | None:
    """선택적 테스트 DB 이름 Override를 SQL 식별자 안전 범위로 제한한다."""

    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if _SAFE_DATABASE_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Benchmark database override contains an unsafe character.")
    return normalized


def _emit_event(event: str, **fields: object) -> None:
    """부하 생성기가 읽을 수 있는 한 줄 JSON 생명주기 이벤트를 출력한다."""

    payload = {"event": event, "component": "benchmark-target", **fields}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _count_temp_files(directory: Path) -> int:
    """측정 실행이 소유한 임시 경로의 일반 파일 개수를 반환한다."""

    if not directory.exists():
        return 0
    return sum(1 for path in directory.rglob("*") if path.is_file())


def _clear_owned_temp_directory(directory: Path) -> None:
    """실행별 전용 임시 디렉터리만 제거하고 빈 디렉터리로 다시 만든다."""

    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)


def _controlled_oom_worker(bounded_allocation_mib: int) -> dict[str, object]:
    """실제 자원 고갈 없이 별도 Python Worker의 MemoryError 분류 경로를 확인한다."""

    if not 1 <= bounded_allocation_mib <= 256:
        raise ValueError("bounded_allocation_mib must be between 1 and 256.")

    script = (
        "import sys\n"
        "try:\n"
        f"    payload = bytearray({bounded_allocation_mib} * 1024 * 1024)\n"
        "    assert len(payload) > 0\n"
        "    raise MemoryError('controlled benchmark probe')\n"
        "except MemoryError:\n"
        f"    raise SystemExit({_CONTROLLED_OOM_EXIT_CODE})\n"
    )
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "mode": "controlled_worker",
        "safe_probe": True,
        "bounded_allocation_mib": bounded_allocation_mib,
        "worker_exit_code": result.returncode,
        "oom_observed": result.returncode == _CONTROLLED_OOM_EXIT_CODE,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
    }


def main() -> int:
    args = _parse_arguments()
    rag_root = args.rag_root.resolve()
    fixture_manifest_path = args.fixture_manifest.resolve()
    download_temp_directory = args.download_temp_directory.resolve()
    verification_output = (
        args.verification_output.resolve() if args.verification_output is not None else None
    )

    _prepare_import_path(rag_root)
    fixtures = _load_fixture_manifest(fixture_manifest_path)
    owned_file_idxs = _validate_owned_ids(args.test_user_idx, tuple(sorted(fixtures)))

    collection_prefix = os.getenv(
        "JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION_PREFIX",
        _DEFAULT_COLLECTION_PREFIX,
    )
    collection = _resolve_benchmark_collection(
        benchmark_token=args.benchmark_token,
        explicit_collection=os.getenv("JIPSA_RAG_BENCHMARK_QDRANT_COLLECTION"),
        prefix=collection_prefix,
    )
    database_override = _resolve_database_override(os.getenv("JIPSA_RAG_BENCHMARK_DATABASE_NAME"))

    # RAG 설정 객체가 import되는 시점 전에 환경을 확정한다. 성능 측정 Target은 실제
    # 운영 데이터를 정리할 수 없도록 test 프로필과 실행별 Collection을 강제한다.
    os.environ["JIPSA_RAG_APP_ENV"] = "test"
    os.environ["JIPSA_RAG_LOG_FORMAT"] = "json"
    os.environ["JIPSA_RAG_LOG_LEVEL"] = "INFO"
    os.environ["JIPSA_RAG_DEBUG"] = "false"
    os.environ["JIPSA_RAG_QDRANT_COLLECTION"] = collection
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if database_override is not None:
        os.environ["JIPSA_RAG_DATABASE_NAME"] = database_override

    # 아래 import는 측정 대상 RAG의 uv 환경과 환경 변수가 준비된 뒤 수행한다.
    import httpx2  # type: ignore[import-not-found]
    import uvicorn  # type: ignore[import-not-found]
    from fastapi import Header, HTTPException  # type: ignore[import-not-found]
    from jipsa_rag.core.config import get_settings  # type: ignore[import-not-found]
    from qdrant_client import AsyncQdrantClient, models  # type: ignore[import-not-found]
    from sqlalchemy import bindparam, text  # type: ignore[import-not-found]
    from sqlalchemy.ext.asyncio import create_async_engine  # type: ignore[import-not-found]

    settings = get_settings()
    if settings.app_env != "test":
        raise RuntimeError("Benchmark target cleanup is allowed only in test environment.")
    if settings.qdrant_collection != collection:
        raise RuntimeError("Benchmark Qdrant collection override was not applied.")
    if not settings.qdrant_collection.startswith(collection_prefix):
        raise RuntimeError("Refusing to use a non-benchmark Qdrant collection.")
    if database_override is not None and settings.database_name != database_override:
        raise RuntimeError("Benchmark database name override was not applied.")

    def qdrant_filter() -> object:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="users_idx",
                    match=models.MatchValue(value=args.test_user_idx),
                ),
                models.FieldCondition(
                    key="file_idx",
                    match=models.MatchAny(any=list(owned_file_idxs)),
                ),
            ]
        )

    async def count_database_rows() -> dict[str, int]:
        """전용 Users_IDX·File_IDX에 해당하는 Local RAG DB Row만 집계한다."""

        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        parameters = {
            "users_idx": args.test_user_idx,
            "file_idxs": owned_file_idxs,
        }
        counts: dict[str, int] = {}
        try:
            async with engine.connect() as connection:
                for table_name in _OWNED_TABLES:
                    statement = text(
                        f"""
                        SELECT COUNT(*)
                        FROM `{table_name}`
                        WHERE `Users_IDX` = :users_idx
                          AND `File_IDX` IN :file_idxs
                        """
                    ).bindparams(bindparam("file_idxs", expanding=True))
                    result = await connection.execute(statement, parameters)
                    counts[table_name] = int(result.scalar_one())
        finally:
            await engine.dispose()
        return counts

    async def count_qdrant_points() -> dict[str, object]:
        """전용 Collection 존재 여부와 소유 Point 수를 집계한다."""

        api_key = (
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        )
        qdrant = AsyncQdrantClient(
            url=settings.qdrant_url,
            grpc_port=settings.qdrant_grpc_port,
            prefer_grpc=settings.qdrant_prefer_grpc,
            api_key=api_key,
            timeout=settings.qdrant_timeout_seconds,
        )
        try:
            exists = await qdrant.collection_exists(settings.qdrant_collection)
            if not exists:
                return {"collection_exists": False, "owned_point_count": 0}
            result = await qdrant.count(
                collection_name=settings.qdrant_collection,
                count_filter=cast(object, qdrant_filter()),
                exact=True,
            )
            return {
                "collection_exists": True,
                "owned_point_count": int(result.count),
            }
        finally:
            await qdrant.close()

    async def inspect_owned_state() -> dict[str, object]:
        """비밀값 없이 DB·Qdrant·임시 경로의 격리 상태를 반환한다."""

        database_counts = await count_database_rows()
        qdrant_state = await count_qdrant_points()
        return {
            "app_env": settings.app_env,
            "database_name": settings.database_name,
            "database_override_used": database_override is not None,
            "test_user_idx": args.test_user_idx,
            "owned_file_idx_min": min(owned_file_idxs),
            "owned_file_idx_max": max(owned_file_idxs),
            "owned_file_count": len(owned_file_idxs),
            "qdrant_collection": settings.qdrant_collection,
            "qdrant_collection_owned": settings.qdrant_collection.startswith(collection_prefix),
            "database_row_counts": database_counts,
            "database_row_total": sum(database_counts.values()),
            **qdrant_state,
            "temp_file_count": _count_temp_files(download_temp_directory),
        }

    async def cleanup_test_data() -> dict[str, object]:
        """전용 Qdrant Collection, Local DB Row와 임시 파일을 정확히 정리한다."""

        before = await inspect_owned_state()

        # 실행별 Collection 이름을 사용하므로 Point Filter 삭제보다 Collection 전체 삭제가
        # 더 강한 정리 보장을 제공한다. Prefix 검증을 통과하지 않으면 절대 삭제하지 않는다.
        if not settings.qdrant_collection.startswith(collection_prefix):
            raise RuntimeError("Refusing to delete a non-benchmark Qdrant collection.")
        api_key = (
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        )
        qdrant = AsyncQdrantClient(
            url=settings.qdrant_url,
            grpc_port=settings.qdrant_grpc_port,
            prefer_grpc=settings.qdrant_prefer_grpc,
            api_key=api_key,
            timeout=settings.qdrant_timeout_seconds,
        )
        try:
            if await qdrant.collection_exists(settings.qdrant_collection):
                await qdrant.delete_collection(collection_name=settings.qdrant_collection)
        finally:
            await qdrant.close()

        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        parameters = {
            "users_idx": args.test_user_idx,
            "file_idxs": owned_file_idxs,
        }
        try:
            async with engine.begin() as connection:
                for table_name in _OWNED_TABLES:
                    statement = text(
                        f"""
                        DELETE FROM `{table_name}`
                        WHERE `Users_IDX` = :users_idx
                          AND `File_IDX` IN :file_idxs
                        """
                    ).bindparams(bindparam("file_idxs", expanding=True))
                    await connection.execute(statement, parameters)
        finally:
            await engine.dispose()

        _clear_owned_temp_directory(download_temp_directory)
        after = await inspect_owned_state()
        verified = (
            int(after["database_row_total"]) == 0
            and after["collection_exists"] is False
            and int(after["temp_file_count"]) == 0
        )
        return {
            "success": verified,
            "before": before,
            "after": after,
            "database_rows_zero": int(after["database_row_total"]) == 0,
            "qdrant_collection_absent": after["collection_exists"] is False,
            "temp_files_zero": int(after["temp_file_count"]) == 0,
        }

    if args.cleanup_only:
        cleanup_result = asyncio.run(cleanup_test_data())
        payload = {
            "schema_version": 1,
            "mode": "cleanup-only",
            "collection": settings.qdrant_collection,
            "database_name": settings.database_name,
            "result": cleanup_result,
        }
        if verification_output is not None:
            _write_json(verification_output, payload)
        _emit_event(
            "benchmark_cleanup_only_completed",
            success=cleanup_result["success"],
            qdrant_collection=settings.qdrant_collection,
            database_name=settings.database_name,
        )
        return 0 if cleanup_result["success"] else 2

    from jipsa_rag.api.v1.endpoints.file_processing import (  # type: ignore[import-not-found]
        get_file_downloader,
    )
    from jipsa_rag.infrastructure.file.downloader import (  # type: ignore[import-not-found]
        HttpFileDownloader,
    )
    from jipsa_rag.main import app  # type: ignore[import-not-found]

    class PerformanceDownloadContract:
        """합성 파일을 운영 다운로더에 HTTPS 응답 형태로 전달한다."""

        def handle(self, request: httpx2.Request) -> httpx2.Response:
            match = _DOWNLOAD_PATH_PATTERN.fullmatch(request.url.path)
            if request.method != "GET" or match is None:
                return httpx2.Response(status_code=404, request=request)

            file_idx = int(match.group("file_idx"))
            fixture = fixtures.get(file_idx)
            if fixture is None or match.group("file_name") != fixture["file_name"]:
                return httpx2.Response(status_code=404, request=request)

            fixture_path = Path(cast(str, fixture["path"]))
            content = fixture_path.read_bytes()
            return httpx2.Response(
                status_code=200,
                headers={
                    "Content-Type": cast(str, fixture["content_type"]),
                    "Content-Length": str(len(content)),
                },
                content=content,
                request=request,
            )

    def verify_benchmark_token(value: str | None) -> None:
        """Loopback 관리 API도 실행별 무작위 Token으로 보호한다."""

        if value is None or value != args.benchmark_token:
            raise HTTPException(status_code=401, detail="Invalid benchmark token.")

    # 운영 다운로드 검증은 유지하고 허용 Host와 전송 계층만 합성 Fixture로 교체한다.
    http_settings = settings.model_copy(
        update={"file_download_allowed_host_suffixes": ".performance.invalid"}
    )
    downloader = HttpFileDownloader(
        http_settings,
        transport=httpx2.MockTransport(PerformanceDownloadContract().handle),
        temp_directory=download_temp_directory,
    )
    app.dependency_overrides[get_file_downloader] = lambda: downloader

    server_holder: dict[str, uvicorn.Server] = {}

    @app.get("/__benchmark__/health", include_in_schema=False)
    async def benchmark_health(
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        verify_benchmark_token(x_benchmark_token)
        return {
            "success": True,
            "pid": os.getpid(),
            "owned_file_count": len(owned_file_idxs),
            "test_user_idx": args.test_user_idx,
            "qdrant_collection": settings.qdrant_collection,
            "database_name": settings.database_name,
        }

    @app.get("/__benchmark__/isolation", include_in_schema=False)
    async def benchmark_isolation(
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        verify_benchmark_token(x_benchmark_token)
        return {"success": True, "data": await inspect_owned_state()}

    @app.post("/__benchmark__/cleanup", include_in_schema=False)
    async def benchmark_cleanup(
        request: dict[str, object],
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        verify_benchmark_token(x_benchmark_token)
        user_idx = request.get("user_idx")
        raw_file_idxs = request.get("file_idxs")
        if user_idx != args.test_user_idx:
            raise HTTPException(status_code=422, detail="user_idx is outside the owned scope.")
        if not isinstance(raw_file_idxs, list) or not raw_file_idxs:
            raise HTTPException(status_code=422, detail="file_idxs must be a non-empty array.")
        validated_file_idxs: list[int] = []
        for value in cast(list[object], raw_file_idxs):
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(
                    status_code=422,
                    detail="file_idxs must contain only integers.",
                )
            validated_file_idxs.append(value)
        requested = _validate_owned_ids(
            cast(int, user_idx),
            tuple(validated_file_idxs),
        )
        if set(requested) != set(owned_file_idxs):
            raise HTTPException(status_code=422, detail="file_idxs must match the owned manifest.")
        result = await cleanup_test_data()
        return {"success": bool(result["success"]), "data": result}

    @app.post("/__benchmark__/fault/delay", include_in_schema=False)
    async def benchmark_fault_delay(
        request: dict[str, object],
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        """Client Timeout 기록을 위해 Loopback 관리 요청만 제한적으로 지연한다."""

        verify_benchmark_token(x_benchmark_token)
        delay = request.get("delay_seconds")
        if isinstance(delay, bool) or not isinstance(delay, int | float):
            raise HTTPException(status_code=422, detail="delay_seconds must be numeric.")
        normalized = float(delay)
        if not 0.01 <= normalized <= 60.0:
            raise HTTPException(status_code=422, detail="delay_seconds is outside the safe range.")
        await asyncio.sleep(normalized)
        return {"success": True, "delay_seconds": normalized, "safe_probe": True}

    @app.post("/__benchmark__/fault/controlled-oom", include_in_schema=False)
    async def benchmark_fault_controlled_oom(
        request: dict[str, object],
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        """별도 Worker의 제한된 MemoryError를 기록하고 본 서비스는 계속 유지한다."""

        verify_benchmark_token(x_benchmark_token)
        allocation = request.get("bounded_allocation_mib")
        if isinstance(allocation, bool) or not isinstance(allocation, int):
            raise HTTPException(
                status_code=422,
                detail="bounded_allocation_mib must be an integer.",
            )
        result = await asyncio.to_thread(_controlled_oom_worker, allocation)
        return {"success": bool(result["oom_observed"]), "data": result}

    @app.post("/__benchmark__/shutdown", include_in_schema=False)
    async def benchmark_shutdown(
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        verify_benchmark_token(x_benchmark_token)
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True
        return {"success": True}

    # 이전 실패 실행의 전용 데이터가 남아 있어도 첫 측정에 영향을 주지 않도록 시작 전에
    # 정확한 전용 DB Row·Collection·임시 경로만 정리한다.
    startup_cleanup = asyncio.run(cleanup_test_data())
    if not startup_cleanup["success"]:
        raise RuntimeError("Benchmark startup cleanup verification failed.")

    config = uvicorn.Config(
        app=app,
        host=args.host,
        port=args.port,
        reload=False,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_holder["server"] = server

    _emit_event(
        "benchmark_target_starting",
        pid=os.getpid(),
        host=args.host,
        port=args.port,
        owned_file_count=len(owned_file_idxs),
        test_user_idx=args.test_user_idx,
        qdrant_collection=settings.qdrant_collection,
        database_name=settings.database_name,
        database_override_used=database_override is not None,
    )
    try:
        server.run()
    finally:
        app.dependency_overrides.pop(get_file_downloader, None)
        if not args.keep_test_data:
            try:
                cleanup_result = asyncio.run(cleanup_test_data())
                _emit_event(
                    "benchmark_target_cleanup_completed",
                    success=cleanup_result["success"],
                    database_rows_zero=cleanup_result["database_rows_zero"],
                    qdrant_collection_absent=cleanup_result["qdrant_collection_absent"],
                    temp_files_zero=cleanup_result["temp_files_zero"],
                )
            # 종료 단계의 정리 실패는 원래 서버 종료 상태를 덮어쓰지 않고 별도 이벤트로 남긴다.
            except Exception as error:
                _emit_event(
                    "benchmark_target_cleanup_failed",
                    error_type=type(error).__name__,
                )
        _emit_event("benchmark_target_stopped", pid=os.getpid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
