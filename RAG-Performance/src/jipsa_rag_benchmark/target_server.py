"""성능 측정 전용 Local RAG 대상 프로세스를 실행한다.

이 파일은 ``RAG-Performance``의 자체 Python 환경이 아니라 측정 대상 ``RAG``의
``uv run python``으로 실행된다. 따라서 실제 RAG 의존성과 소스 코드를 사용하면서도,
부하 생성기와 자원 수집기는 별도 프로세스로 격리된다.

AWS Backend와 S3는 측정 범위에서 제외한다. 합성 Fixture는 RAG의 실제
``HttpFileDownloader``에 ``httpx2.MockTransport``로 공급하므로 HTTPS URL 검증,
MIME·Magic Byte·OOXML 검증, 임시 파일 기록·정리 이후의 파싱/OCR/청킹/임베딩/색인
경로는 운영 구현을 그대로 사용한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, cast

_DOWNLOAD_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^/issue-159/(?P<file_idx>[1-9][0-9]*)/(?P<file_name>[^/]+)$"
)
_BENCHMARK_HEADER: Final[str] = "X-Benchmark-Token"


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
    return parser.parse_args()


def _prepare_import_path(rag_root: Path) -> None:
    """측정 대상 RAG의 src-layout을 현재 프로세스 import 경로에 추가한다."""

    source_root = rag_root / "src"
    if not (source_root / "jipsa_rag").is_dir():
        raise FileNotFoundError(f"RAG source package does not exist: {source_root}")
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))


def _load_fixture_manifest(path: Path) -> dict[int, dict[str, object]]:
    """생성 Fixture manifest를 읽고 File_IDX·파일 경로 계약을 검증한다."""

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


def _emit_event(event: str, **fields: object) -> None:
    """부하 생성기가 읽을 수 있는 한 줄 JSON 생명주기 이벤트를 출력한다."""

    payload = {"event": event, "component": "benchmark-target", **fields}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def main() -> int:
    args = _parse_arguments()
    rag_root = args.rag_root.resolve()
    fixture_manifest_path = args.fixture_manifest.resolve()
    download_temp_directory = args.download_temp_directory.resolve()
    download_temp_directory.mkdir(parents=True, exist_ok=True)

    # RAG 설정 객체가 import되는 시점 전에 환경을 확정한다. benchmark target은 실제
    # 운영 데이터를 정리할 수 없도록 test 프로필에서만 실행하며, 단계 로그를 외부
    # 수집기가 안정적으로 파싱할 수 있도록 JSON 로그와 reload 비활성화를 강제한다.
    os.environ["JIPSA_RAG_APP_ENV"] = "test"
    os.environ["JIPSA_RAG_LOG_FORMAT"] = "json"
    os.environ["JIPSA_RAG_LOG_LEVEL"] = "INFO"
    os.environ["JIPSA_RAG_DEBUG"] = "false"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    _prepare_import_path(rag_root)
    fixtures = _load_fixture_manifest(fixture_manifest_path)
    owned_file_idxs = tuple(sorted(fixtures))

    # 아래 import는 측정 대상 RAG의 uv 환경과 환경 변수가 준비된 뒤 수행해야 한다.
    import httpx2  # type: ignore[import-not-found]
    import uvicorn  # type: ignore[import-not-found]
    from fastapi import Header, HTTPException  # type: ignore[import-not-found]
    from qdrant_client import AsyncQdrantClient, models  # type: ignore[import-not-found]
    from sqlalchemy import bindparam, text  # type: ignore[import-not-found]
    from sqlalchemy.ext.asyncio import create_async_engine  # type: ignore[import-not-found]

    from jipsa_rag.api.v1.endpoints.file_processing import (  # type: ignore[import-not-found]
        get_file_downloader,
    )
    from jipsa_rag.core.config import get_settings  # type: ignore[import-not-found]
    from jipsa_rag.infrastructure.file.downloader import (  # type: ignore[import-not-found]
        HttpFileDownloader,
    )
    from jipsa_rag.main import app  # type: ignore[import-not-found]

    settings = get_settings()
    if settings.app_env != "test":
        raise RuntimeError("Benchmark target cleanup is allowed only in test environment.")

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

    async def cleanup_test_data(user_idx: int, file_idxs: Sequence[int]) -> None:
        """Qdrant Point 후 Local RAG DB Row를 정확한 범위로 정리한다."""

        normalized = tuple(dict.fromkeys(file_idxs))
        if user_idx != args.test_user_idx:
            raise ValueError("Cleanup user_idx does not match the configured test user.")
        if not normalized or any(value not in fixtures for value in normalized):
            raise ValueError("Cleanup file_idxs must be owned by this benchmark run.")

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
                await qdrant.delete(
                    collection_name=settings.qdrant_collection,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="users_idx",
                                    match=models.MatchValue(value=user_idx),
                                ),
                                models.FieldCondition(
                                    key="file_idx",
                                    match=models.MatchAny(any=list(normalized)),
                                ),
                            ]
                        )
                    ),
                    wait=True,
                )
        finally:
            await qdrant.close()

        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        parameters = {"users_idx": user_idx, "file_idxs": normalized}
        try:
            async with engine.begin() as connection:
                for table_name in ("RAG_Index_Run", "RAG_Chunk", "RAG_Document"):
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

    def verify_benchmark_token(value: str | None) -> None:
        """Loopback 관리 API도 실행별 무작위 토큰으로 보호한다."""

        if value is None or value != args.benchmark_token:
            raise HTTPException(status_code=401, detail="Invalid benchmark token.")

    # 운영 다운로드 검증은 유지하고 허용 호스트와 전송 계층만 합성 Fixture로 교체한다.
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
        }

    @app.post("/__benchmark__/cleanup", include_in_schema=False)
    async def benchmark_cleanup(
        request: dict[str, object],
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        verify_benchmark_token(x_benchmark_token)
        user_idx = request.get("user_idx")
        raw_file_idxs = request.get("file_idxs")
        if isinstance(user_idx, bool) or not isinstance(user_idx, int) or user_idx <= 0:
            raise HTTPException(status_code=422, detail="user_idx must be positive.")
        if not isinstance(raw_file_idxs, list) or not raw_file_idxs:
            raise HTTPException(status_code=422, detail="file_idxs must be a non-empty array.")
        file_idxs: list[int] = []
        for value in cast(list[object], raw_file_idxs):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise HTTPException(status_code=422, detail="file_idxs must be positive integers.")
            file_idxs.append(value)
        await cleanup_test_data(user_idx, file_idxs)
        return {"success": True, "deleted_file_count": len(file_idxs)}

    @app.post("/__benchmark__/shutdown", include_in_schema=False)
    async def benchmark_shutdown(
        x_benchmark_token: str | None = Header(default=None, alias=_BENCHMARK_HEADER),
    ) -> dict[str, object]:
        verify_benchmark_token(x_benchmark_token)
        server = server_holder.get("server")
        if server is not None:
            server.should_exit = True
        return {"success": True}

    # 이전 실패 실행의 전용 데이터가 남아 있어도 첫 측정에 영향을 주지 않게 시작 전에
    # 정확한 owned File_IDX만 정리한다. 운영 사용자나 범위 삭제는 수행하지 않는다.
    asyncio.run(cleanup_test_data(args.test_user_idx, owned_file_idxs))

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
    )
    try:
        server.run()
    finally:
        app.dependency_overrides.pop(get_file_downloader, None)
        if not args.keep_test_data:
            try:
                asyncio.run(cleanup_test_data(args.test_user_idx, owned_file_idxs))
            # 종료 단계의 정리 실패는 원래 서버 종료 상태를 덮어쓰지 않고
            # 별도 이벤트로 남겨 측정 결과에서 확인할 수 있게 한다.
            except Exception as error:
                _emit_event(
                    "benchmark_target_cleanup_failed",
                    error_type=type(error).__name__,
                )
        _emit_event("benchmark_target_stopped", pid=os.getpid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
