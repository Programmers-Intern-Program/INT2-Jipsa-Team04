"""외부 단계형 Stress Plan의 Profile·순서·안전 상한 계약을 검증한다."""

import json
from pathlib import Path

import pytest

from jipsa_rag_benchmark.stress_models import (
    FaultSuiteStage,
    load_stress_suite_plan,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("file_name", "profile", "destructive"),
    (
        ("stress-plan-quick.json", "quick", False),
        ("stress-plan-standard.json", "standard", False),
        ("stress-plan-endurance.json", "endurance", False),
        ("stress-plan-destructive.json", "destructive", True),
    ),
)
def test_builtin_external_profiles_are_search_only(
    file_name: str,
    profile: str,
    destructive: bool,
) -> None:
    plan = load_stress_suite_plan(_PROJECT_ROOT / "configs" / file_name)

    assert plan.profile == profile
    assert plan.destructive is destructive
    assert plan.enabled_stages
    assert plan.maximum_declared_concurrency <= 512
    assert plan.estimated_minimum_duration_seconds > 0
    assert tuple(stage.mode for stage in plan.enabled_stages[:5]) == (
        "burst",
        "interval",
        "batch",
        "ramp",
        "chaos",
    )
    assert all(stage.operation == "search" for stage in plan.enabled_stages)
    assert not any(isinstance(stage, FaultSuiteStage) for stage in plan.enabled_stages)


def test_builtin_profile_rejects_missing_primary_traffic_mode(tmp_path: Path) -> None:
    source = json.loads(
        (_PROJECT_ROOT / "configs/stress-plan-quick.json").read_text(encoding="utf-8")
    )
    source["stages"] = [stage for stage in source["stages"] if stage["mode"] != "interval"]
    target = tmp_path / "invalid-primary-modes.json"
    target.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="five traffic modes"):
        load_stress_suite_plan(target)


def test_destructive_suite_requires_explicit_confirmation_contract(tmp_path: Path) -> None:
    source = json.loads(
        (_PROJECT_ROOT / "configs/stress-plan-destructive.json").read_text(encoding="utf-8")
    )
    source["requires_explicit_confirmation"] = False
    target = tmp_path / "invalid-destructive.json"
    target.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="explicit confirmation"):
        load_stress_suite_plan(target)


def test_builtin_external_profile_rejects_fault_suite(tmp_path: Path) -> None:
    source = json.loads(
        (_PROJECT_ROOT / "configs/stress-plan-quick.json").read_text(encoding="utf-8")
    )
    source["stages"].append(
        {
            "stage_id": "local-only-fault",
            "name": "외부 계획에 허용되지 않는 Local Probe",
            "mode": "fault_suite",
            "operation": "search",
            "enabled": True,
            "destructive": False,
            "probes": ["timeout"],
            "cooldown_seconds": 0.0,
        }
    )
    target = tmp_path / "invalid-external-fault.json"
    target.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="search-only traffic stages"):
        load_stress_suite_plan(target)


def test_concurrency_hard_cap_is_enforced(tmp_path: Path) -> None:
    source = json.loads(
        (_PROJECT_ROOT / "configs/stress-plan-quick.json").read_text(encoding="utf-8")
    )
    source["stages"][0]["concurrency"] = 513
    target = tmp_path / "invalid-concurrency.json"
    target.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="between 1 and 512"):
        load_stress_suite_plan(target)
