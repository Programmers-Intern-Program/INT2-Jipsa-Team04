"""Qdrant·DB·Snapshot 자동 데이터 선정의 우선순위와 재현성을 검증한다."""

import io
import tarfile
from pathlib import Path

import pytest

from jipsa_rag_benchmark.rag_environment import RagEnvironmentSettings
from jipsa_rag_benchmark.test_data_discovery import (
    DataCandidate,
    _candidate_from_qdrant_point,
    _resolve_qdrant_image,
    _select_test_data,
    discover_test_data,
    find_snapshot,
)


def _settings(tmp_path: Path) -> RagEnvironmentSettings:
    return RagEnvironmentSettings(
        source_path=tmp_path / ".env.local",
        external_base_url="http://rag.example.com:9802",
        target_environment="test",
        api_v1_prefix="/api/v1",
        ingest_token="token",
        qdrant_url="http://127.0.0.1:6333",
        qdrant_collection="rag_collection",
        qdrant_api_key=None,
        database_host="127.0.0.1",
        database_port=3306,
        database_name="Jipsa_Local_RAG",
        database_user="jipsa",
        database_password="secret",
        embedding_model="model",
        embedding_dimension=1024,
    )


def _candidates() -> list[DataCandidate]:
    return [
        DataCandidate(10, 100, "첫 번째 문서의 배포 절차와 검증 기준입니다."),
        DataCandidate(10, 101, "두 번째 문서의 복구 정책과 예외 처리입니다."),
        DataCandidate(10, 102, "세 번째 문서의 운영 제한과 측정 기준입니다."),
        DataCandidate(11, 200, "다른 사용자의 문서입니다."),
    ]


def test_seeded_selection_is_reproducible_and_hides_raw_content() -> None:
    first = _select_test_data(
        _candidates(),
        source="qdrant",
        source_detail="http://127.0.0.1:6333",
        files_per_user=2,
        query_count=4,
        random_seed=159,
        fallback_errors=(),
    )
    second = _select_test_data(
        _candidates(),
        source="qdrant",
        source_detail="http://127.0.0.1:6333",
        files_per_user=2,
        query_count=4,
        random_seed=159,
        fallback_errors=(),
    )

    assert first.user_idx == 10
    assert first.file_idxs == second.file_idxs
    assert first.queries == second.queries
    assert first.random_seed == 159
    public = repr(first.to_public_dict())
    assert "배포 절차" not in public
    assert "복구 정책" not in public


def test_auto_source_prefers_qdrant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def qdrant(**_: object) -> list[DataCandidate]:
        calls.append("qdrant")
        return _candidates()

    def database(*_: object, **__: object) -> list[DataCandidate]:
        calls.append("database")
        raise AssertionError("database fallback must not run")

    monkeypatch.setattr(
        "jipsa_rag_benchmark.test_data_discovery._discover_from_qdrant",
        qdrant,
    )
    monkeypatch.setattr(
        "jipsa_rag_benchmark.test_data_discovery._discover_from_database",
        database,
    )

    result = discover_test_data(
        _settings(tmp_path),
        source="auto",
        files_per_user=2,
        query_count=4,
        random_seed=159,
        snapshot_path=None,
        snapshot_search_roots=(tmp_path,),
    )

    assert calls == ["qdrant"]
    assert result.source == "qdrant"
    assert result.user_idx == 10


def test_auto_source_falls_back_after_qdrant_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def qdrant(**_: object) -> list[DataCandidate]:
        raise ConnectionError("qdrant unavailable")

    def database(*_: object, **__: object) -> list[DataCandidate]:
        return _candidates()

    monkeypatch.setattr(
        "jipsa_rag_benchmark.test_data_discovery._discover_from_qdrant",
        qdrant,
    )
    monkeypatch.setattr(
        "jipsa_rag_benchmark.test_data_discovery._discover_from_database",
        database,
    )

    result = discover_test_data(
        _settings(tmp_path),
        source="auto",
        files_per_user=2,
        query_count=4,
        random_seed=159,
        snapshot_path=None,
        snapshot_search_roots=(tmp_path,),
    )

    assert result.source == "database"
    assert result.fallback_errors
    assert result.fallback_errors[0].startswith("qdrant:ConnectionError")


def test_qdrant_point_requires_active_positive_identifiers() -> None:
    valid = _candidate_from_qdrant_point(
        {
            "payload": {
                "users_idx": 10,
                "file_idx": 100,
                "is_active": True,
                "content": "검색 가능한 문서 내용",
            }
        }
    )
    inactive = _candidate_from_qdrant_point(
        {
            "payload": {
                "users_idx": 10,
                "file_idx": 100,
                "is_active": False,
            }
        }
    )

    assert valid == DataCandidate(10, 100, "검색 가능한 문서 내용")
    assert inactive is None


def test_find_snapshot_prefers_matching_collection_and_latest(tmp_path: Path) -> None:
    older_matching = tmp_path / "rag_collection-old.snapshot"
    newer_other = tmp_path / "other-new.snapshot"
    _write_snapshot(older_matching)
    _write_snapshot(newer_other)
    older_matching.touch()
    newer_other.touch()

    selected = find_snapshot(
        explicit_path=None,
        search_roots=(tmp_path,),
        collection_name="rag_collection",
    )

    assert selected == older_matching.resolve()


def _write_snapshot(path: Path) -> None:
    with tarfile.open(path, "w") as archive:
        for name, content in (
            ("version.info", b"1"),
            ("config.json", b"{}"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_snapshot_image_reuses_project_container_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    calls: list[list[str]] = []

    def run(command: list[str], **_: object) -> Result:
        calls.append(command)
        return Result("qdrant/qdrant:v1.15.3\n")

    monkeypatch.setattr("subprocess.run", run)

    assert _resolve_qdrant_image("docker") == "qdrant/qdrant:v1.15.3"
    assert calls == [
        [
            "docker",
            "inspect",
            "--format",
            "{{.Config.Image}}",
            "jipsa-qdrant",
        ]
    ]


def test_snapshot_image_does_not_pull_latest_when_no_local_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    results = iter((Result(""), Result("")))

    def run(_: list[str], **__: object) -> Result:
        return next(results)

    monkeypatch.setattr("subprocess.run", run)

    with pytest.raises(RuntimeError, match="No existing Local Qdrant Docker image"):
        _resolve_qdrant_image("docker")
