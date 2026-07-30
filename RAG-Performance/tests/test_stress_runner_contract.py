"""외부 단계형 Stress 실행기와 README의 자동 환경·데이터 계약을 검증한다."""

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _powershell_code_without_full_line_comments(script: str) -> str:
    """PowerShell 전체 행 주석을 제외한 실행 코드만 반환한다."""

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
    assert "Env:JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN" in script
    assert "--rag-env-file" in script
    assert "--data-source" in script
    assert "--files-per-user" in script
    assert "--query-count" in script
    assert "jipsa-rag-stress" in script
    assert "Get-NetTCPConnection" not in script
    assert "nvidia-smi" not in script


def test_readme_markdown_and_html_cover_auto_selection_and_profiles() -> None:
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

    # 설명 주석의 오류 사례는 허용하지만 실제 실행 코드에는 Nullable 전용
    # Value 속성 접근이 남아 있으면 안 된다.
    assert "$RandomSeed.Value" not in executable_code
