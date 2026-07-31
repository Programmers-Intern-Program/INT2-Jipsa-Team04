"""마지막 외부 Stress 실행 결과를 README.md와 README.html에 동기화한다.

README는 실행 안내서이자 마지막 검증 결과의 진입점이다. 캠페인 종료 시 Marker 사이의
검증 구간만 원자적으로 교체한다. 외부 대상 Origin, Health 결과, Stage 상태와 관측된 처리
경계를 Markdown·HTML에 동일하게 기록한다.
"""

from __future__ import annotations

import html
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from jipsa_rag_benchmark.stress_models import CapacityBoundary, StageSummary

VerificationStatus = Literal["passed", "degraded", "failed"]

_MARKDOWN_START: Final[str] = "<!-- STRESS-VERIFICATION:START -->"
_MARKDOWN_END: Final[str] = "<!-- STRESS-VERIFICATION:END -->"
_HTML_START: Final[str] = "<!-- STRESS-VERIFICATION:START -->"
_HTML_END: Final[str] = "<!-- STRESS-VERIFICATION:END -->"


@dataclass(frozen=True, slots=True)
class ReadmeVerificationRecord:
    """README에 표시할 마지막 외부 Stress Campaign 결과."""

    run_id: str
    profile: str
    destructive: bool
    completed_at_utc: str
    execution_error_type: str | None
    quality_gate_skipped: bool
    execution_mode: str
    target_origin: str
    target_environment: str
    selection_source: str
    selection_seed: int | None
    selected_user_idx: int
    selected_file_count: int
    preflight_health_passed: bool
    postflight_health_passed: bool
    local_rag_touched: bool
    stage_count: int
    passed_stage_count: int
    degraded_stage_count: int
    failed_stage_count: int
    stopped_stage_count: int
    request_count: int
    success_count: int
    error_count: int
    report_markdown: str
    report_html: str
    stress_report_markdown: str
    stress_report_html: str
    capacity_boundaries: tuple[CapacityBoundary, ...]

    @property
    def status(self) -> VerificationStatus:
        """실행 오류, Health, Local 접근과 Stage 상태로 최종 표시 상태를 결정한다."""

        if (
            self.execution_error_type is not None
            or not self.preflight_health_passed
            or not self.postflight_health_passed
            or self.local_rag_touched
            or self.failed_stage_count > 0
            or self.stopped_stage_count > 0
        ):
            return "failed"
        if self.degraded_stage_count > 0 or self.error_count > 0:
            return "degraded"
        return "passed"


@dataclass(frozen=True, slots=True)
class ReadmeUpdateResult:
    """두 README 파일의 갱신 결과."""

    markdown_path: str
    html_path: str
    status: VerificationStatus
    updated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "markdown_path": self.markdown_path,
            "html_path": self.html_path,
            "status": self.status,
            "updated": self.updated,
        }


def build_readme_verification_record(
    *,
    run_id: str,
    profile: str,
    destructive: bool,
    completed_at_utc: str,
    execution_error_type: str | None,
    quality_gate_skipped: bool,
    execution_mode: str,
    target_origin: str,
    target_environment: str,
    selection_source: str,
    selection_seed: int | None,
    selected_user_idx: int,
    selected_file_count: int,
    preflight_health_passed: bool,
    postflight_health_passed: bool,
    local_rag_touched: bool,
    stage_summaries: Sequence[StageSummary],
    capacity_boundaries: Sequence[CapacityBoundary],
    report_markdown: str,
    report_html: str,
    stress_report_markdown: str,
    stress_report_html: str,
) -> ReadmeVerificationRecord:
    """Campaign 결과를 README 전용 불변 Record로 변환한다."""

    status_counts: dict[str, int] = {}
    for summary in stage_summaries:
        status_counts[summary.status] = status_counts.get(summary.status, 0) + 1

    return ReadmeVerificationRecord(
        run_id=run_id,
        profile=profile,
        destructive=destructive,
        completed_at_utc=completed_at_utc,
        execution_error_type=execution_error_type,
        quality_gate_skipped=quality_gate_skipped,
        execution_mode=execution_mode,
        target_origin=target_origin,
        target_environment=target_environment,
        selection_source=selection_source,
        selection_seed=selection_seed,
        selected_user_idx=selected_user_idx,
        selected_file_count=selected_file_count,
        preflight_health_passed=preflight_health_passed,
        postflight_health_passed=postflight_health_passed,
        local_rag_touched=local_rag_touched,
        stage_count=len(stage_summaries),
        passed_stage_count=status_counts.get("passed", 0),
        degraded_stage_count=status_counts.get("degraded", 0),
        failed_stage_count=status_counts.get("failed", 0),
        stopped_stage_count=status_counts.get("stopped", 0),
        request_count=sum(summary.request_count for summary in stage_summaries),
        success_count=sum(summary.success_count for summary in stage_summaries),
        error_count=sum(summary.error_count for summary in stage_summaries),
        report_markdown=report_markdown,
        report_html=report_html,
        stress_report_markdown=stress_report_markdown,
        stress_report_html=stress_report_html,
        capacity_boundaries=tuple(capacity_boundaries),
    )


def update_readme_verification(
    *,
    markdown_path: Path,
    html_path: Path,
    record: ReadmeVerificationRecord,
) -> ReadmeUpdateResult:
    """Markdown·HTML Marker 구간을 원자적으로 교체한다."""

    markdown = markdown_path.read_text(encoding="utf-8")
    html_document = html_path.read_text(encoding="utf-8")
    updated_markdown = _replace_marker_block(
        document=markdown,
        start_marker=_MARKDOWN_START,
        end_marker=_MARKDOWN_END,
        replacement=_render_markdown(record),
        label=str(markdown_path),
    )
    updated_html = _replace_marker_block(
        document=html_document,
        start_marker=_HTML_START,
        end_marker=_HTML_END,
        replacement=_render_html(record),
        label=str(html_path),
    )
    _atomic_write(markdown_path, updated_markdown)
    _atomic_write(html_path, updated_html)
    return ReadmeUpdateResult(
        markdown_path=str(markdown_path.resolve()),
        html_path=str(html_path.resolve()),
        status=record.status,
        updated=True,
    )


def _replace_marker_block(
    *,
    document: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    label: str,
) -> str:
    start = document.find(start_marker)
    end = document.find(end_marker)
    if start < 0 or end < 0:
        raise ValueError(f"README verification markers are missing: {label}")
    if end <= start:
        raise ValueError(f"README verification markers are out of order: {label}")
    if document.find(start_marker, start + len(start_marker)) >= 0:
        raise ValueError(f"README start marker is duplicated: {label}")
    if document.find(end_marker, end + len(end_marker)) >= 0:
        raise ValueError(f"README end marker is duplicated: {label}")

    block_end = end + len(end_marker)
    normalized = replacement.strip("\n")
    return (
        document[:start]
        + start_marker
        + "\n"
        + normalized
        + "\n"
        + end_marker
        + document[block_end:]
    )


def _render_markdown(record: ReadmeVerificationRecord) -> str:
    status_label = {
        "passed": "PASS",
        "degraded": "DEGRADED",
        "failed": "FAIL",
    }[record.status]
    lines = [
        "## 16. 마지막 검증 기록",
        "",
        "> 이 구간은 외부 단계형 Stress Campaign 종료 시 자동 갱신됩니다. ",
        "> 성공뿐 아니라 실패·중단 결과도 마지막 실행 상태로 기록합니다.",
        "",
        f"**상태: `{status_label}` · Profile: `{record.profile}` · Run ID: `{record.run_id}`**",
        "",
        "| 항목 | 마지막 실행 값 |",
        "|---|---|",
        f"| 완료 시각 | `{record.completed_at_utc}` |",
        f"| 실행 방식 | `{record.execution_mode}` |",
        f"| 외부 Target | `{record.target_origin}` |",
        f"| Target 환경 | `{record.target_environment}` |",
        f"| 데이터 Source | `{record.selection_source}` |",
        (
            "| 선정 Seed | `"
            f"{record.selection_seed if record.selection_seed is not None else 'configured'}"
            "` |"
        ),
        f"| 선정 User IDX | `{record.selected_user_idx}` |",
        f"| 선정 File 수 | `{record.selected_file_count}` |",
        f"| 파괴적 Profile | `{record.destructive}` |",
        f"| 품질 게이트 | `{'생략' if record.quality_gate_skipped else '실행'}` |",
        f"| 사전 Health | `{record.preflight_health_passed}` |",
        f"| 사후 Health | `{record.postflight_health_passed}` |",
        f"| Local RAG 접근 | `{record.local_rag_touched}` |",
        f"| 실행 오류 | `{record.execution_error_type or '없음'}` |",
        f"| Stage | 전체 `{record.stage_count}` · 통과 `{record.passed_stage_count}` · "
        f"저하 `{record.degraded_stage_count}` · 실패 `{record.failed_stage_count}` · "
        f"중단 `{record.stopped_stage_count}` |",
        f"| 요청 | 전체 `{record.request_count}` · 성공 `{record.success_count}` · "
        f"실패 `{record.error_count}` |",
        "",
        "### 처리 한계 관측",
        "",
        "| 작업 | 확인된 정상 최대 동시성 | 최초 실패 동시성 | 해석 |",
        "|---|---:|---:|---|",
    ]
    if record.capacity_boundaries:
        for boundary in record.capacity_boundaries:
            normal = _markdown_nullable(boundary.normal_maximum_concurrency)
            failure = _markdown_nullable(boundary.first_failure_concurrency)
            interpretation = (
                "계획 상한까지 실패 없음 — 실제 최대치는 더 높을 수 있음"
                if boundary.upper_bound_censored
                else boundary.first_failure_reason or "실패 경계 관측"
            )
            lines.append(f"| `{boundary.operation}` | {normal} | {failure} | {interpretation} |")
    else:
        lines.append("| - | - | - | Ramp 근거 없음 |")

    lines.extend(
        [
            "",
            "### 마지막 결과 바로가기",
            "",
            f"- [캠페인 Markdown 보고서]({record.report_markdown})",
            f"- [캠페인 HTML 보고서]({record.report_html})",
            f"- [상세 Stress Markdown]({record.stress_report_markdown})",
            f"- [상세 Stress HTML]({record.stress_report_html})",
            "",
            "README 갱신으로 작업 트리에 변경이 생기는 것은 의도된 동작입니다.",
        ]
    )
    return "\n".join(lines)


def _render_html(record: ReadmeVerificationRecord) -> str:
    status_label = {
        "passed": "PASS",
        "degraded": "DEGRADED",
        "failed": "FAIL",
    }[record.status]
    status_icon = {"passed": "✓", "degraded": "!", "failed": "X"}[record.status]
    capacity_rows = (
        "".join(_capacity_html_row(boundary) for boundary in record.capacity_boundaries)
        or "<tr><td>-</td><td>-</td><td>-</td><td>Ramp 근거 없음</td></tr>"
    )
    stage_note = (
        f"통과 {record.passed_stage_count} · "
        f"저하 {record.degraded_stage_count} · "
        f"실패 {record.failed_stage_count} · "
        f"중단 {record.stopped_stage_count}"
    )
    request_note = f"성공 {record.success_count} · 실패 {record.error_count}"
    quality_gate = "생략" if record.quality_gate_skipped else "실행"
    execution_error = _escape(record.execution_error_type or "없음")
    section_class = f"doc-section verification verification-{record.status}"
    status_class = f"status-pill status-{record.status}"
    status_text = f"{status_icon} {status_label} · {_escape(record.profile)}"

    lines = [
        f"          <section class='{section_class}'",
        "                   id='verification'",
        "                   data-search-section>",
        f"            <span class='{status_class}'>{status_text}</span>",
        "            <h2>16. 마지막 검증 기록</h2>",
        "            <p class='lead'>",
        "              외부 RAG 단계형 Stress Campaign의 마지막 실행 결과입니다.",
        "              Local RAG와 Docker는 제어하지 않습니다.",
        "            </p>",
        "            <div class='metric-grid'>",
        "              <article class='metric-card'>",
        f"                <span class='metric-value'>{record.stage_count}</span>",
        "                <span class='metric-label'>Stages</span>",
        f"                <span class='metric-note'>{stage_note}</span>",
        "              </article>",
        "              <article class='metric-card'>",
        f"                <span class='metric-value'>{record.request_count}</span>",
        "                <span class='metric-label'>Requests</span>",
        f"                <span class='metric-note'>{request_note}</span>",
        "              </article>",
        "              <article class='metric-card'>",
        (f"                <span class='metric-value'>{record.preflight_health_passed!s}</span>"),
        "                <span class='metric-label'>Preflight health</span>",
        f"                <span class='metric-note'>{_escape(record.target_origin)}</span>",
        "              </article>",
        "              <article class='metric-card'>",
        (f"                <span class='metric-value'>{record.postflight_health_passed!s}</span>"),
        "                <span class='metric-label'>Postflight health</span>",
        "                <span class='metric-note'>테스트 종료 후 응답 가능 여부</span>",
        "              </article>",
        "            </div>",
        "            <div class='table-scroll' role='region' tabindex='0'",
        "                 aria-label='마지막 외부 실행 요약'>",
        "              <table>",
        "                <tbody>",
        _html_row("Run ID", record.run_id),
        _html_row("완료 시각", record.completed_at_utc),
        _html_row("실행 방식", record.execution_mode),
        _html_row("외부 Target", record.target_origin),
        _html_row("Target 환경", record.target_environment),
        _html_row("데이터 Source", record.selection_source),
        _html_row(
            "선정 Seed",
            str(record.selection_seed) if record.selection_seed is not None else "configured",
        ),
        _html_row("선정 User IDX", str(record.selected_user_idx)),
        _html_row("선정 File 수", str(record.selected_file_count)),
        _html_row("파괴적 Profile", str(record.destructive)),
        _html_row("품질 게이트", quality_gate),
        _html_row("Local RAG 접근", str(record.local_rag_touched)),
        _html_row("실행 오류", execution_error, already_escaped=True),
        "                </tbody>",
        "              </table>",
        "            </div>",
        "            <h3>처리 한계 관측</h3>",
        "            <div class='table-scroll' role='region' tabindex='0'",
        "                 aria-label='처리 한계 관측'>",
        "              <table>",
        "                <thead><tr><th>작업</th><th>정상 최대</th>",
        "                  <th>최초 실패</th><th>해석</th></tr></thead>",
        f"                <tbody>{capacity_rows}</tbody>",
        "              </table>",
        "            </div>",
        "            <h3>마지막 결과 바로가기</h3>",
        "            <div class='quick-links'>",
        _html_link(record.report_markdown, "캠페인 Markdown"),
        _html_link(record.report_html, "캠페인 HTML"),
        _html_link(record.stress_report_markdown, "상세 Stress Markdown"),
        _html_link(record.stress_report_html, "상세 Stress HTML"),
        "            </div>",
        "          </section>",
    ]
    return "\n".join(lines)


def _capacity_html_row(boundary: CapacityBoundary) -> str:
    normal = _html_nullable(boundary.normal_maximum_concurrency)
    failure = _html_nullable(boundary.first_failure_concurrency)
    interpretation = (
        "계획 상한까지 실패 없음 — 실제 최대치는 더 높을 수 있음"
        if boundary.upper_bound_censored
        else boundary.first_failure_reason or "실패 경계 관측"
    )
    return (
        "<tr>"
        f"<td><code>{_escape(boundary.operation)}</code></td>"
        f"<td>{normal}</td>"
        f"<td>{failure}</td>"
        f"<td>{_escape(interpretation)}</td>"
        "</tr>"
    )


def _html_row(label: str, value: str, *, already_escaped: bool = False) -> str:
    safe_value = value if already_escaped else _escape(value)
    return f"                  <tr><td>{_escape(label)}</td><td><code>{safe_value}</code></td></tr>"


def _html_link(path: str, label: str) -> str:
    return f"              <a href='{_escape(path)}'>{_escape(label)}</a>"


def _markdown_nullable(value: int | None) -> str:
    return "-" if value is None else f"`{value}`"


def _html_nullable(value: int | None) -> str:
    return "-" if value is None else str(value)


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
