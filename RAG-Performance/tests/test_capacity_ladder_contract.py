"""Quick 상한 검열 시 Standard로 승격하는 Capacity Ladder 계약을 검증한다."""

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _powershell_code_without_full_line_comments(script: str) -> str:
    """PowerShell의 전체 행 주석을 제외한 실행 코드만 반환한다.

    계약 테스트는 실제 실행식에 금지된 속성 접근이 남아 있는지를 검증해야 한다. 설명
    주석에 과거 오류 표현이 포함되는 것은 실행 동작과 무관하므로 검사 대상에서 제외한다.
    """

    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def test_capacity_ladder_escalates_quick_to_standard_with_same_seed() -> None:
    script = (_PROJECT_ROOT / "scripts/run-capacity-ladder.ps1").read_text(encoding="utf-8-sig")

    assert "[ValidateSet('quick', 'standard')]" in script
    assert "$Profiles.Add('standard')" in script
    assert "UpperBoundCensored" in script
    assert "FirstFailureConcurrency" in script
    assert "RandomSeed = $EffectiveSeed" in script
    assert "capacity-ladder-$Timestamp.json" in script
    assert "capacity-ladder-$Timestamp.md" in script


def test_capacity_ladder_never_runs_destructive_without_explicit_approval() -> None:
    script = (_PROJECT_ROOT / "scripts/run-capacity-ladder.ps1").read_text(encoding="utf-8-sig")

    assert "if ($AllowDestructive)" in script
    assert "-AllowDestructive가 필요합니다" in script
    assert "-ConfirmTargetHost가 필요합니다" in script
    assert "$Profiles.Add('destructive')" not in script


def test_capacity_ladder_streams_child_output_without_return_value_pollution() -> None:
    """하위 Script stdout 문자열이 Profile 결과 PSCustomObject에 섞이지 않아야 한다."""

    script = (_PROJECT_ROOT / "scripts/run-capacity-ladder.ps1").read_text(encoding="utf-8-sig")
    executable_code = _powershell_code_without_full_line_comments(script)

    assert "& $RunnerPath @Arguments | Out-Host" in executable_code
    assert "& $RunnerPath @Arguments\n" not in executable_code
    assert "return [PSCustomObject]@{" in executable_code


def test_capacity_ladder_rejects_old_or_incomplete_run_directories() -> None:
    """새 Run만 허용하고 다음 단계 전에 핵심 보고서와 상태를 다시 검증한다."""

    script = (_PROJECT_ROOT / "scripts/run-capacity-ladder.ps1").read_text(encoding="utf-8-sig")

    assert "function Assert-CompletedProfileReport" in script
    assert "function Assert-RunArtifact" in script
    assert "-not $BeforePaths.Contains($_.FullName)" in script
    assert "새 결과 폴더를 찾을 수 없습니다" in script
    assert "external-stress/report.html" in script
    assert "$Report.execution_error_type" in script
    assert "$Report.preflight_health_passed" in script
    assert "$Report.postflight_health_passed" in script
    assert "오래된" in script


def test_capacity_ladder_normalizes_explicit_seed_without_nullable_value_access() -> None:
    """명시된 Int64 Seed가 StrictMode에서도 안전하게 처리되는지 검증한다."""

    script = (_PROJECT_ROOT / "scripts/run-capacity-ladder.ps1").read_text(encoding="utf-8-sig")
    executable_code = _powershell_code_without_full_line_comments(script)

    assert "$PSBoundParameters.ContainsKey('RandomSeed')" in executable_code
    assert "$HasExplicitRandomSeed" in executable_code
    assert "[long] $RandomSeed" in executable_code

    # 설명 주석에는 과거 오류 표현이 남아 있어도 된다. 실제 실행 코드에서만 Nullable 전용
    # Value 속성 접근이 사라졌는지를 검증한다.
    assert "$RandomSeed.Value" not in executable_code
