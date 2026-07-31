"""외부 단계형 Stress 실행기와 README의 자동 환경·데이터·산출물 계약을 검증한다."""

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _powershell_code_without_full_line_comments(script: str) -> str:
    """PowerShell 전체 행 주석을 제외한 실행 코드만 반환한다.

    문서화 주석에는 과거 오류 사례나 금지된 표현이 들어갈 수 있다. 계약 테스트는 실제
    실행식에 필요한 설정과 금지된 접근이 남아 있는지를 검사해야 하므로 전체 행 주석만
    제외한다.
    """

    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def test_powershell_loads_rag_environment_and_discovers_existing_data() -> None:
    script = (_PROJECT_ROOT / "scripts/run-staged-stress-test.ps1").read_text(encoding="utf-8-sig")

    assert "ValidateSet('quick', 'standard', 'endurance', 'destructive')" in script
    assert "[string] $RagEnvironmentPath" in script
    assert "ValidateSet('auto', 'qdrant', 'database', 'snapshot')" in script
    assert "RAG_INGEST_TOKEN" in script
    assert "JIPSA_RAG_EXTERNAL_BASE_URL" in script
    assert "JIPSA_RAG_QDRANT_COLLECTION" in script
    assert "$env:JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN = $IngestToken" in script
    assert "$OriginalPerformanceToken" in script
    assert "JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN" in script
    assert "--rag-env-file" in script
    assert "--data-source" in script
    assert "--files-per-user" in script
    assert "--query-count" in script
    assert "jipsa-rag-stress" in script
    assert "Get-NetTCPConnection" not in script
    assert "nvidia-smi" not in script


def test_staged_runner_forces_python_utf8_and_restores_original_environment() -> None:
    """중첩 PowerShell Pipeline에서도 Python stdout이 cp1252로 후퇴하지 않아야 한다."""

    script = (_PROJECT_ROOT / "scripts/run-staged-stress-test.ps1").read_text(encoding="utf-8-sig")
    executable_code = _powershell_code_without_full_line_comments(script)

    assert "$OriginalPythonUtf8 = $env:PYTHONUTF8" in executable_code
    assert "$OriginalPythonIoEncoding = $env:PYTHONIOENCODING" in executable_code
    assert "$env:PYTHONUTF8 = '1'" in executable_code
    assert "$env:PYTHONIOENCODING = 'utf-8'" in executable_code
    assert "Restore-ProcessEnvironmentVariable" in executable_code
    assert "-Name 'PYTHONUTF8'" in executable_code
    assert "-Name 'PYTHONIOENCODING'" in executable_code


def test_staged_runner_validates_complete_campaign_artifacts() -> None:
    """정상 종료 안내 전에 Campaign과 상세 Stress 파일 전체를 검사해야 한다."""

    script = (_PROJECT_ROOT / "scripts/run-staged-stress-test.ps1").read_text(encoding="utf-8-sig")

    required_artifacts = (
        "report.json",
        "report.md",
        "report.html",
        "external-stress/external_target.resolved.json",
        "external-stress/stress_plan.resolved.json",
        "external-stress/execution_command.txt",
        "external-stress/environment.json",
        "external-stress/requests.json",
        "external-stress/requests.csv",
        "external-stress/stage_summaries.json",
        "external-stress/stage_summaries.csv",
        "external-stress/capacity_boundaries.json",
        "external-stress/capacity_boundaries.csv",
        "external-stress/health_checks.json",
        "external-stress/progress.log",
        "external-stress/report.json",
        "external-stress/report.md",
        "external-stress/report.html",
    )

    assert "function Assert-CampaignArtifacts" in script
    assert "function Assert-GeneratedArtifact" in script
    for artifact in required_artifacts:
        assert f"'{artifact}'" in script

    assert "$CampaignReport.execution_error_type" in script
    assert "$CampaignReport.preflight_health_passed" in script
    assert "$CampaignReport.postflight_health_passed" in script
    assert "$DetailedReport.stage_count" in script
    assert "$DetailedReport.request_count" in script
    assert "상세 Stress HTML(표·그래프)" in script


def test_staged_runner_selects_only_a_new_campaign_directory() -> None:
    """이전 실패 폴더를 최신 결과로 재사용하지 않고 이번 실행의 새 폴더만 선택한다."""

    script = (_PROJECT_ROOT / "scripts/run-staged-stress-test.ps1").read_text(encoding="utf-8-sig")

    assert "$BeforeRunPaths" in script
    assert "$NewRunDirectories" in script
    assert "-not $BeforeRunPaths.Contains($_.FullName)" in script
    assert "정상 종료 후 새 Campaign 결과 폴더를 찾을 수 없습니다" in script


def test_readme_markdown_and_html_cover_auto_selection_profiles_and_artifacts() -> None:
    markdown = (_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    html = (_PROJECT_ROOT / "README.html").read_text(encoding="utf-8")

    required_terms = (
        "RAG/.env.local",
        "JIPSA_RAG_EXTERNAL_BASE_URL",
        "RAG_INGEST_TOKEN",
        "Qdrant",
        "DB",
        "Snapshot",
        "Quick",
        "Standard",
        "Endurance",
        "Destructive",
        "Burst",
        "Interval",
        "Batch",
        "Ramp",
        "Chaos",
        "Soak",
        "DataSource",
        "RandomSeed",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "external_target.resolved.json",
        "stress_plan.resolved.json",
        "environment.json",
        "external-stress/report.html",
        "STRESS-VERIFICATION:START",
        "16. 마지막 검증 기록",
    )
    for term in required_terms:
        assert term in markdown
        assert term in html

    assert "## 1. 문서 바로가기" in markdown
    assert "1. 문서 바로가기" in html
    assert 'href="README.md"' in html
    assert 'id="theme-toggle"' in html
    assert 'id="print-button"' in html
    assert 'id="document-search"' in html
    assert "progress-bar" in html
    assert "copy" in html


def test_quick_soak_is_duration_first_without_request_cap() -> None:
    import json

    plan = json.loads(
        (_PROJECT_ROOT / "configs/stress-plan-quick.json").read_text(encoding="utf-8")
    )
    soak = next(stage for stage in plan["stages"] if stage["mode"] == "soak")

    assert soak["duration_seconds"] == 120.0
    assert soak["max_requests"] == 0
    assert plan["max_total_requests"] == 55_000

    runner = (_PROJECT_ROOT / "src/jipsa_rag_benchmark/stress_runner.py").read_text(
        encoding="utf-8"
    )
    assert "soak_max_requests_reached_before_duration" in runner
    assert "scheduled_request_count=submitted" in runner
    assert "request_cap_enabled = stage.max_requests > 0" in runner
    assert "remaining_seconds > 0.5" in runner


def test_staged_runner_normalizes_explicit_seed_without_nullable_value_access() -> None:
    """직접 Quick 실행에서도 명시 Seed를 StrictMode 안전하게 전달하는지 검증한다."""

    script = (_PROJECT_ROOT / "scripts/run-staged-stress-test.ps1").read_text(encoding="utf-8-sig")
    executable_code = _powershell_code_without_full_line_comments(script)

    assert "$PSBoundParameters.ContainsKey('RandomSeed')" in executable_code
    assert "$HasExplicitRandomSeed" in executable_code
    assert "[string] ([long] $RandomSeed)" in executable_code

    # 설명 주석의 오류 사례는 허용하지만 실제 실행 코드에는 Nullable 전용 Value 속성 접근이
    # 남아 있으면 안 된다.
    assert "$RandomSeed.Value" not in executable_code
