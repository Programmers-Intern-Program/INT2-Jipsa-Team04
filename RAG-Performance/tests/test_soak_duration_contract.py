"""모든 외부 Profile의 Soak 시간과 Ramp 상한 계약을 검증한다."""

import json
from pathlib import Path
from typing import cast

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROFILE_DURATIONS = {
    "quick": 120.0,
    "standard": 1200.0,
    "endurance": 18000.0,
    "destructive": 900.0,
}
_PROFILE_RAMP_LIMITS = {
    "quick": 32,
    "standard": 128,
    "endurance": 128,
    "destructive": 256,
}


def _require_json_object(value: object, *, source: str) -> dict[str, object]:
    """JSON 값을 문자열 Key 기반 Object로 검증하고 정적 타입을 확정한다.

    ``json.loads``의 반환 타입은 ``Any``이므로 그대로 반환하면 Mypy Strict의
    ``no-any-return`` 규칙을 위반한다. 런타임에서 Object 여부와 문자열 Key 여부를
    먼저 검증한 뒤, 검증이 끝난 경계에서만 ``cast``를 사용한다.

    이 방식은 단순히 오류를 숨기는 것이 아니라 잘못된 Plan 구조를 테스트 수집
    단계에서 명확한 ``TypeError``로 중단시킨다.
    """

    if not isinstance(value, dict):
        raise TypeError(f"{source}는 JSON Object여야 합니다.")

    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{source}의 모든 Key는 문자열이어야 합니다.")

    return cast(dict[str, object], value)


def _load_profile(profile: str) -> dict[str, object]:
    """Profile JSON을 읽고 최상위 Object 계약을 검증한다."""

    path = _PROJECT_ROOT / f"configs/stress-plan-{profile}.json"

    # json.loads 결과를 object로 제한해 Any가 함수 반환 경계를 통과하지 못하게 한다.
    decoded: object = json.loads(path.read_text(encoding="utf-8"))
    return _require_json_object(decoded, source=str(path))


def _load_stages(profile: str, plan: dict[str, object]) -> list[dict[str, object]]:
    """Plan의 Stage 배열을 검증해 타입이 확정된 Stage 목록으로 반환한다."""

    raw_stages = plan.get("stages")
    if not isinstance(raw_stages, list):
        raise TypeError(f"{profile} Profile의 stages는 JSON Array여야 합니다.")

    return [
        _require_json_object(
            stage,
            source=f"{profile} Profile stages[{index}]",
        )
        for index, stage in enumerate(raw_stages)
    ]


def test_all_profiles_use_duration_only_soak() -> None:
    """모든 Profile이 요청 수가 아닌 설정 시간으로 Soak를 종료하는지 검증한다."""

    for profile, expected_duration in _PROFILE_DURATIONS.items():
        plan = _load_profile(profile)
        stages = _load_stages(profile, plan)

        soak = next(stage for stage in stages if stage.get("mode") == "soak")
        duration_seconds = soak.get("duration_seconds")
        max_requests = soak.get("max_requests")

        # bool은 int의 하위 타입이므로 숫자 계약 검증에서 명시적으로 제외한다.
        assert isinstance(duration_seconds, int | float)
        assert not isinstance(duration_seconds, bool)
        assert float(duration_seconds) == expected_duration

        assert isinstance(max_requests, int)
        assert not isinstance(max_requests, bool)
        assert max_requests == 0


def test_all_profiles_keep_expected_ramp_boundaries() -> None:
    """Soak 수정 과정에서 Profile별 Ramp 상한과 1~5단계 순서가 보존되는지 검증한다."""

    for profile, expected_limit in _PROFILE_RAMP_LIMITS.items():
        plan = _load_profile(profile)
        stages = _load_stages(profile, plan)

        enabled_modes = [stage.get("mode") for stage in stages if stage.get("enabled") is True]
        assert enabled_modes[:5] == ["burst", "interval", "batch", "ramp", "chaos"]

        ramp = next(stage for stage in stages if stage.get("mode") == "ramp")
        max_concurrency = ramp.get("max_concurrency")

        assert isinstance(max_concurrency, int)
        assert not isinstance(max_concurrency, bool)
        assert max_concurrency == expected_limit


def test_runner_supports_disabled_soak_request_cap() -> None:
    """Runner가 max_requests=0을 시간 우선 Soak로 처리하는 계약을 검증한다."""

    runner = (_PROJECT_ROOT / "src/jipsa_rag_benchmark/stress_runner.py").read_text(
        encoding="utf-8"
    )

    assert "request_cap_enabled = stage.max_requests > 0" in runner
    assert "not request_cap_enabled or submitted < stage.max_requests" in runner
    assert "soak_max_requests_reached_before_duration" in runner
