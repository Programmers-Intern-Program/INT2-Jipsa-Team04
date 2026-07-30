"""외부 Stress 종료 후 Markdown·HTML 검증 기록 동기화 계약을 검증한다."""

from pathlib import Path

from jipsa_rag_benchmark.stress_models import CapacityBoundary
from jipsa_rag_benchmark.verification_readme import (
    ReadmeVerificationRecord,
    update_readme_verification,
)


def _record(run_id: str = "20260730T080000Z-abc12345") -> ReadmeVerificationRecord:
    return ReadmeVerificationRecord(
        run_id=run_id,
        profile="quick",
        destructive=False,
        completed_at_utc="2026-07-30T08:10:00.000+00:00",
        execution_error_type=None,
        quality_gate_skipped=False,
        execution_mode="external_http_black_box",
        target_origin="https://rag-test.example.com",
        target_environment="test",
        selection_source="qdrant",
        selection_seed=159,
        selected_user_idx=10,
        selected_file_count=2,
        preflight_health_passed=True,
        postflight_health_passed=True,
        local_rag_touched=False,
        stage_count=6,
        passed_stage_count=6,
        degraded_stage_count=0,
        failed_stage_count=0,
        stopped_stage_count=0,
        request_count=500,
        success_count=500,
        error_count=0,
        report_markdown="artifacts/external-stress/run/report.md",
        report_html="artifacts/external-stress/run/report.html",
        stress_report_markdown="artifacts/external-stress/run/external-stress/report.md",
        stress_report_html="artifacts/external-stress/run/external-stress/report.html",
        capacity_boundaries=(
            CapacityBoundary(
                operation="search",
                normal_maximum_concurrency=32,
                first_failure_concurrency=None,
                first_failure_stage_id=None,
                first_failure_reason=None,
                upper_bound_censored=True,
                evidence_count=8,
            ),
        ),
    )


def test_readme_verification_updates_both_documents_without_duplication(
    tmp_path: Path,
) -> None:
    markdown_path = tmp_path / "README.md"
    html_path = tmp_path / "README.html"
    markdown_path.write_text(
        "before\n<!-- STRESS-VERIFICATION:START -->\nold\n"
        "<!-- STRESS-VERIFICATION:END -->\nafter\n",
        encoding="utf-8",
    )
    html_path.write_text(
        "<body>\n<!-- STRESS-VERIFICATION:START -->\n<section>old</section>\n"
        "<!-- STRESS-VERIFICATION:END -->\n</body>\n",
        encoding="utf-8",
    )

    first = update_readme_verification(
        markdown_path=markdown_path,
        html_path=html_path,
        record=_record(),
    )
    second = update_readme_verification(
        markdown_path=markdown_path,
        html_path=html_path,
        record=_record("20260730T090000Z-def67890"),
    )

    markdown = markdown_path.read_text(encoding="utf-8")
    html = html_path.read_text(encoding="utf-8")
    assert first.updated is True
    assert second.updated is True
    assert markdown.count("STRESS-VERIFICATION:START") == 1
    assert html.count("STRESS-VERIFICATION:START") == 1
    assert "20260730T090000Z-def67890" in markdown
    assert "20260730T090000Z-def67890" in html
    assert "20260730T080000Z-abc12345" not in markdown
    assert "20260730T080000Z-abc12345" not in html
    assert "https://rag-test.example.com" in markdown
    assert "https://rag-test.example.com" in html
    assert "계획 상한까지 실패 없음" in markdown
    assert "qdrant" in markdown
    assert "선정 Seed" in html


def test_failed_external_health_is_rendered_as_fail(tmp_path: Path) -> None:
    markdown_path = tmp_path / "README.md"
    html_path = tmp_path / "README.html"
    markdown_path.write_text(
        "<!-- STRESS-VERIFICATION:START -->\nold\n<!-- STRESS-VERIFICATION:END -->",
        encoding="utf-8",
    )
    html_path.write_text(
        "<!-- STRESS-VERIFICATION:START -->\nold\n<!-- STRESS-VERIFICATION:END -->",
        encoding="utf-8",
    )
    source = _record()
    failed = ReadmeVerificationRecord(
        run_id=source.run_id,
        profile="destructive",
        destructive=True,
        completed_at_utc=source.completed_at_utc,
        execution_error_type="RuntimeError",
        quality_gate_skipped=True,
        execution_mode=source.execution_mode,
        target_origin=source.target_origin,
        target_environment="staging",
        selection_source=source.selection_source,
        selection_seed=source.selection_seed,
        selected_user_idx=source.selected_user_idx,
        selected_file_count=source.selected_file_count,
        preflight_health_passed=True,
        postflight_health_passed=False,
        local_rag_touched=False,
        stage_count=2,
        passed_stage_count=1,
        degraded_stage_count=0,
        failed_stage_count=1,
        stopped_stage_count=0,
        request_count=100,
        success_count=80,
        error_count=20,
        report_markdown=source.report_markdown,
        report_html=source.report_html,
        stress_report_markdown=source.stress_report_markdown,
        stress_report_html=source.stress_report_html,
        capacity_boundaries=source.capacity_boundaries,
    )

    result = update_readme_verification(
        markdown_path=markdown_path,
        html_path=html_path,
        record=failed,
    )

    assert result.status == "failed"
    assert "상태: `FAIL`" in markdown_path.read_text(encoding="utf-8")
    assert "status-failed" in html_path.read_text(encoding="utf-8")
