"""장시간·실패·격리·정리 계획의 검증 계약을 확인한다."""

import json
from pathlib import Path

import pytest

from jipsa_rag_benchmark.reliability_models import load_reliability_plan


def _valid_plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "campaign_name": "issue-159-test",
        "soak": {
            "enabled": True,
            "duration_seconds": 60,
            "window_seconds": 10,
            "concurrency": 2,
            "max_requests": 100,
            "cooldown_seconds": 0,
            "latency_p95_growth_report_percent": 20,
            "rss_p95_growth_report_percent": 15,
            "vram_p95_growth_report_percent": 15,
            "thread_growth_report_count": 5,
            "handle_growth_report_count": 100,
        },
        "failure_probes": {
            "timeout": {
                "enabled": True,
                "delay_seconds": 1,
                "client_timeout_seconds": 0.1,
            },
            "oom": {
                "enabled": True,
                "mode": "controlled_worker",
                "bounded_allocation_mib": 32,
            },
            "external_services": {
                "enabled": True,
                "services": ["embedding", "qdrant"],
                "request_timeout_seconds": 5,
                "recovery_timeout_seconds": 60,
            },
            "abnormal_termination": {
                "enabled": True,
                "restart_for_cleanup": True,
            },
        },
        "isolation": {
            "qdrant_collection_prefix": "rag_benchmark_issue_159_",
            "test_user_idx_min": 159000,
            "file_idx_min": 1590000,
            "require_test_app_env": True,
            "database_name_override": None,
        },
        "cleanup": {
            "restore_infrastructure": True,
            "verify_database_rows_zero": True,
            "verify_qdrant_collection_absent": True,
            "verify_temp_files_zero": True,
            "verify_target_process_stopped": True,
        },
        "scope_guard": {
            "forbidden_environment_overrides": ["JIPSA_RAG_CHUNK_SIZE_CHARS"],
            "protected_repository_paths": ["RAG/src"],
        },
    }


def _write_plan(tmp_path: Path, value: dict[str, object]) -> Path:
    path = tmp_path / "reliability-plan.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_load_reliability_plan_accepts_safe_campaign(tmp_path: Path) -> None:
    plan = load_reliability_plan(_write_plan(tmp_path, _valid_plan()))

    assert plan.soak.duration_seconds == 60
    assert plan.failure_probes.oom.mode == "controlled_worker"
    assert plan.isolation.qdrant_collection_prefix == "rag_benchmark_issue_159_"
    assert plan.cleanup.restore_infrastructure is True


def test_load_reliability_plan_rejects_timeout_longer_than_delay(tmp_path: Path) -> None:
    value = _valid_plan()
    failure = value["failure_probes"]
    assert isinstance(failure, dict)
    timeout = failure["timeout"]
    assert isinstance(timeout, dict)
    timeout["client_timeout_seconds"] = 2

    with pytest.raises(ValueError, match="shorter than delay_seconds"):
        load_reliability_plan(_write_plan(tmp_path, value))


def test_load_reliability_plan_rejects_unsafe_collection_prefix(tmp_path: Path) -> None:
    value = _valid_plan()
    isolation = value["isolation"]
    assert isinstance(isolation, dict)
    isolation["qdrant_collection_prefix"] = "production_collection"

    with pytest.raises(ValueError, match="must start"):
        load_reliability_plan(_write_plan(tmp_path, value))


def test_load_reliability_plan_limits_controlled_oom_allocation(tmp_path: Path) -> None:
    value = _valid_plan()
    failure = value["failure_probes"]
    assert isinstance(failure, dict)
    oom = failure["oom"]
    assert isinstance(oom, dict)
    oom["bounded_allocation_mib"] = 1024

    with pytest.raises(ValueError, match="between 1 and 256"):
        load_reliability_plan(_write_plan(tmp_path, value))


def test_repository_default_and_smoke_plans_are_valid() -> None:
    project_root = Path(__file__).resolve().parents[1]

    default = load_reliability_plan(project_root / "configs/reliability-plan.json")
    smoke = load_reliability_plan(project_root / "configs/reliability-plan-smoke.json")

    assert default.soak.duration_seconds == 3600
    assert smoke.soak.duration_seconds == 300
    assert smoke.soak.max_requests == 200
    assert default.isolation == smoke.isolation
