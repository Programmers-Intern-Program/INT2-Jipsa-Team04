"""외부 RAG Target 설정의 URL·보안·자동 선정 범위 계약을 검증한다."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jipsa_rag_benchmark.external_target import (
    build_external_target_config,
    load_external_target_config,
    validate_search_scope,
)
from jipsa_rag_benchmark.rag_environment import RagEnvironmentSettings
from jipsa_rag_benchmark.test_data_discovery import DiscoveredTestData


def _write_config(path: Path, *, base_url: str = "https://rag-test.example.com") -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_base_url": base_url,
                "target_environment": "test",
                "health_path": "/api/v1/health/live",
                "readiness_path": "/api/v1/health/ready",
                "search_path": "/api/v1/chunks/search",
                "test_user_idx": 159000,
                "reference_file_idxs": [1590000, 1590001],
                "queries": ["문서의 핵심 내용을 알려줘", "조건을 찾아줘"],
                "top_k": 5,
                "score_threshold": None,
                "connect_timeout_seconds": 10.0,
                "request_timeout_seconds": 60.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _settings(tmp_path: Path) -> RagEnvironmentSettings:
    return RagEnvironmentSettings(
        source_path=tmp_path / ".env.local",
        external_base_url="http://rag.example.com:9802",
        target_environment="test",
        api_v1_prefix="/api/v1",
        ingest_token="secret",
        qdrant_url="http://127.0.0.1:6333",
        qdrant_collection="collection",
        qdrant_api_key=None,
        database_host=None,
        database_port=None,
        database_name=None,
        database_user=None,
        database_password=None,
        embedding_model=None,
        embedding_dimension=None,
    )


def _discovered() -> DiscoveredTestData:
    return DiscoveredTestData(
        source="qdrant",
        user_idx=10,
        file_idxs=(100, 101),
        queries=("첫 문서 검색", "둘째 문서 검색"),
        random_seed=159,
        candidate_user_count=2,
        candidate_file_count=4,
        candidate_chunk_count=20,
        source_detail="http://127.0.0.1:6333",
        fallback_errors=(),
    )


def test_external_target_config_hides_queries_and_keeps_origin(tmp_path: Path) -> None:
    config = load_external_target_config(
        _write_config(tmp_path / "target.json"),
        allow_insecure_http=False,
        allow_loopback_target=False,
    )

    public = config.to_public_dict()
    assert config.target_origin == "https://rag-test.example.com"
    assert config.target_host == "rag-test.example.com"
    assert public["query_count"] == 2
    assert "queries" not in public


def test_automatic_target_uses_rag_environment_and_selection(tmp_path: Path) -> None:
    config = build_external_target_config(
        _settings(tmp_path),
        _discovered(),
        allow_insecure_http=True,
    )

    assert config.target_origin == "http://rag.example.com:9802"
    assert config.search_path == "/api/v1/chunks/search"
    assert config.test_user_idx == 10
    assert config.reference_file_idxs == (100, 101)
    assert config.selection_source == "qdrant"
    assert config.selection_seed == 159


def test_scope_validation_keeps_only_files_with_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        file_idx = request.read().decode("utf-8")
        result_count = 1 if "100" in file_idx else 0
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"result_count": result_count},
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def client_factory(*args: object, **kwargs: Any) -> httpx.Client:
        return original_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(
        "jipsa_rag_benchmark.external_target.httpx.Client",
        client_factory,
    )
    config = build_external_target_config(
        _settings(tmp_path),
        _discovered(),
        allow_insecure_http=True,
    )

    validated = validate_search_scope(
        config,
        internal_token="secret",
        verify_tls=True,
    )

    assert validated.reference_file_idxs == (100,)
    assert validated.queries[0] == "첫 문서 검색"


def test_loopback_target_is_blocked_by_default(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Loopback target is blocked"):
        load_external_target_config(
            _write_config(tmp_path / "target.json", base_url="http://127.0.0.1:8077"),
            allow_insecure_http=True,
            allow_loopback_target=False,
        )


def test_http_external_target_requires_explicit_switch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allow-insecure-http"):
        load_external_target_config(
            _write_config(
                tmp_path / "target.json",
                base_url="http://rag-test.example.com",
            ),
            allow_insecure_http=False,
            allow_loopback_target=False,
        )


def test_top_k_must_follow_chunk_search_contract(tmp_path: Path) -> None:
    path = _write_config(tmp_path / "target.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["top_k"] = 21
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="between 1 and 20"):
        load_external_target_config(
            path,
            allow_insecure_http=False,
            allow_loopback_target=False,
        )
