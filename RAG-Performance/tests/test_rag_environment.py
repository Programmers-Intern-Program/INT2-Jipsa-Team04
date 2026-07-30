"""RAG 환경 파일 자동 로드와 비밀정보 비노출 계약을 검증한다."""

from pathlib import Path

import pytest

from jipsa_rag_benchmark.rag_environment import (
    load_rag_environment,
    parse_dotenv,
)


def _write_env(path: Path) -> Path:
    path.write_text(
        "\n".join(
            (
                "JIPSA_RAG_EXTERNAL_BASE_URL=http://rag.example.com:9802",
                "JIPSA_RAG_API_V1_PREFIX=/api/v1",
                "RAG_INGEST_TOKEN=secret-ingest-token",
                "JIPSA_RAG_QDRANT_URL=http://127.0.0.1:6333",
                "JIPSA_RAG_QDRANT_COLLECTION=rag_collection",
                "JIPSA_RAG_QDRANT_API_KEY=",
                "JIPSA_RAG_DATABASE_HOST=127.0.0.1",
                "JIPSA_RAG_DATABASE_PORT=3306",
                "JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG",
                "JIPSA_RAG_DATABASE_USER=jipsa",
                "JIPSA_RAG_DATABASE_PASSWORD='db-secret'",
                "JIPSA_RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B",
                "JIPSA_RAG_EMBEDDING_DIM=1024",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_load_rag_environment_derives_paths_and_redacts_secrets(
    tmp_path: Path,
) -> None:
    settings = load_rag_environment(_write_env(tmp_path / ".env.local"))

    assert settings.target_origin == "http://rag.example.com:9802"
    assert settings.health_path == "/api/v1/health/live"
    assert settings.readiness_path == "/api/v1/health/ready"
    assert settings.search_path == "/api/v1/chunks/search"
    assert settings.ingest_token == "secret-ingest-token"
    assert settings.embedding_dimension == 1024

    public = settings.to_public_dict()
    serialized = repr(public)
    assert public["ingest_token_loaded"] is True
    assert public["database_password_loaded"] is True
    assert "secret-ingest-token" not in serialized
    assert "db-secret" not in serialized


def test_parse_dotenv_supports_export_quotes_and_comments(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(
        "export FIRST='one two'\nSECOND=value # comment\nTHIRD=\"line\\nvalue\"\n",
        encoding="utf-8",
    )

    values = parse_dotenv(path)

    assert values == {
        "FIRST": "one two",
        "SECOND": "value",
        "THIRD": "line\nvalue",
    }


def test_missing_ingest_token_is_rejected(tmp_path: Path) -> None:
    path = _write_env(tmp_path / ".env.local")
    text = path.read_text(encoding="utf-8").replace(
        "RAG_INGEST_TOKEN=secret-ingest-token\n",
        "",
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="RAG_INGEST_TOKEN"):
        load_rag_environment(path)
