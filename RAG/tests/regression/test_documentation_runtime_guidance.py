"""실행 정책과 구조화 로그 문서의 운영 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

_RAG_ROOT = Path(__file__).resolve().parents[2]

# 주요 진입 문서는 PowerShell 실행 정책 오류를 독립적으로 해결할 수 있어야 한다.
# 같은 핵심 명령을 요구해 README와 상세 Runbook 사이의 설명 불일치를 방지한다.
_POWERSHELL_GUIDANCE_DOCUMENTS = (
    _RAG_ROOT / "README.md",
    _RAG_ROOT / "README.html",
    _RAG_ROOT / "docs" / "operations" / "local-runtime.md",
    _RAG_ROOT / "docs" / "testing" / "powershell-e2e.md",
    _RAG_ROOT / "docs" / "testing" / "test-guide.md",
)

# 로그 설정 문서는 공개 환경 변수 이름을 구현과 동일하게 사용해야 한다.
# 오탈자가 생기면 사용자가 존재하지 않는 변수를 설정하고 로그를 잃을 수 있다.
_REQUIRED_LOG_VARIABLES = (
    "JIPSA_RAG_LOG_LEVEL",
    "JIPSA_RAG_LOG_FORMAT",
    "JIPSA_RAG_LOG_CONSOLE_TIMEZONE",
    "JIPSA_RAG_LOG_COLOR",
    "JIPSA_RAG_LOG_REQUEST_ID_LENGTH",
    "JIPSA_RAG_LOG_THIRD_PARTY_LEVEL",
    "JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS",
)

_REQUIRED_PIPELINE_EVENTS = (
    "ingest_manifest_fetch_completed",
    "file_download_completed",
    "document_parsing_ocr_completed",
    "document_chunking_completed",
    "document_embedding_completed",
    "file_indexing_completed",
    "file_processing_completed",
    "ingest_success_callback_completed",
    "http_request_completed",
)

_REQUIRED_HTML_FEATURES = (
    'href="#main-content"',
    'id="main-content"',
    'href="README.md"',
    'id="print-button"',
    'id="theme-toggle"',
    'id="document-search"',
    "reading-progress-bar",
    "back-to-top",
    "prefers-reduced-motion",
    "@media print",
    'id="documentation-map"',
    'id="verified-test-results"',
    "Verified 2026-07-28",
    "README Hub 2026-07-29",
)

_README_HUB_SECTION_ORDER = (
    "1. 문서 바로가기",
    "2. 서비스 책임",
    "3. 지원 문서와 OCR",
    "4. 처리 흐름",
    "5. API 표면",
    "6. 구조화 로그와 요청 추적",
    "7. 인제스트와 재인제스트",
    "8. 검색 범위",
    "9. lookup과 synthesis",
    "10. 근거 부족과 인용",
    "11. 설치와 실행",
    "12. 품질 게이트와 실제 E2E",
    "13. 실제 검증 기록",
    "14. 환경 변수와 비밀정보",
    "15. 병합 전 체크리스트",
)


def _read_text(path: Path) -> str:
    """UTF-8 문서를 읽고 파일 누락 시 이해 가능한 실패 메시지를 제공한다."""

    assert path.is_file(), f"필수 문서가 없습니다: {path.relative_to(_RAG_ROOT)}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("document_path", _POWERSHELL_GUIDANCE_DOCUMENTS)
def test_powershell_documents_use_process_scoped_bypass(
    document_path: Path,
) -> None:
    """주요 문서가 시스템 전체가 아닌 현재 프로세스 해결책을 안내한다."""

    content = _read_text(document_path)

    assert "Set-ExecutionPolicy" in content
    assert "Process" in content
    assert "Bypass" in content
    assert "run-all-rag-tests.ps1" in content

    # LocalMachine Unrestricted를 실행 가능한 권장 명령으로 제공하면 개발 PC의
    # 시스템 전체 보안 정책을 불필요하게 완화하므로 문서 계약에서 금지한다.
    forbidden_command = "Set-ExecutionPolicy -Scope LocalMachine -ExecutionPolicy Unrestricted"
    normalized = " ".join(content.split())
    assert forbidden_command not in normalized


def test_readme_markdown_and_html_document_the_same_runtime_entrypoint() -> None:
    """두 README가 동일한 표준 실행 순서를 제공하는지 검증한다."""

    markdown = _read_text(_RAG_ROOT / "README.md")
    html = _read_text(_RAG_ROOT / "README.html")

    required_fragments = (
        "Set-StrictMode -Version Latest",
        "$ErrorActionPreference = 'Stop'",
        "Set-ExecutionPolicy",
        "Scope Process",
        "ExecutionPolicy Bypass",
        "scripts\\run-all-rag-tests.ps1",
    )

    for fragment in required_fragments:
        assert fragment in markdown, f"README.md 누락: {fragment}"
        assert fragment in html, f"README.html 누락: {fragment}"


def test_observability_document_lists_public_log_configuration() -> None:
    """관측성 문서가 공개 로그 환경 변수 전체를 정확히 열거한다."""

    content = _read_text(_RAG_ROOT / "docs" / "operations" / "observability-and-troubleshooting.md")

    for variable_name in _REQUIRED_LOG_VARIABLES:
        assert variable_name in content, f"로그 환경 변수 문서 누락: {variable_name}"

    assert "log_schema_version" in content
    assert "RFC 3339" in content
    assert "Request ID" in content


def test_readme_documents_complete_ingest_log_flow() -> None:
    """README가 inbound 요청부터 callback과 HTTP 결과까지 연결한다."""

    content = _read_text(_RAG_ROOT / "README.md")

    for event_name in _REQUIRED_PIPELINE_EVENTS:
        assert event_name in content, f"인제스트 로그 이벤트 문서 누락: {event_name}"


def test_readme_preserves_search_and_citation_contracts() -> None:
    """README가 검색 범위와 공개 인용 순서 계약을 유지한다."""

    content = _read_text(_RAG_ROOT / "README.md")
    required_terms = (
        "users_idx == request.user_idx",
        "is_active == true",
        "file_idx IN request.reference_file_idxs",
        "SOURCE-N 최초 등장 순서",
        "실제로 인용한 출처",
        "source_locator",
    )

    for term in required_terms:
        assert term in content, f"README 검색·인용 계약 누락: {term}"


def test_security_document_forbids_sensitive_rag_payloads() -> None:
    """보안 문서가 RAG 원문·벡터·인증정보 로그 출력을 금지한다."""

    content = _read_text(_RAG_ROOT / "docs" / "security" / "environment-and-secrets.md")

    required_sensitive_terms = (
        "AWS_ACCESS_KEY_ID",
        "사용자 질문",
        "청크 텍스트",
        "OCR 텍스트",
        "프롬프트",
        "임베딩 벡터",
        "Presigned URL",
        "DB DSN",
        "API Key",
        "토큰",
    )

    for term in required_sensitive_terms:
        assert term in content, f"민감정보 정책 문서 누락: {term}"


def test_html_readme_preserves_accessible_interactive_features() -> None:
    """HTML README가 기존 접근성·검색·인쇄 기능을 유지한다."""

    content = _read_text(_RAG_ROOT / "README.html")
    lower_content = content.lower()

    assert "<!doctype html>" in lower_content
    assert 'lang="ko"' in lower_content
    assert '<meta charset="utf-8">' in lower_content
    assert 'name="viewport"' in lower_content
    assert "<main" in lower_content
    assert "<nav" in lower_content
    assert "<aside" in lower_content
    assert "<h1" in lower_content
    assert "aria-label" in lower_content

    for feature in _REQUIRED_HTML_FEATURES:
        assert feature in content, f"README.html 기능 누락: {feature}"


def test_readme_preserves_comprehensive_hub_order() -> None:
    """README가 문서 허브 중심의 안정적인 정보 구조를 유지한다."""

    markdown = _read_text(_RAG_ROOT / "README.md")
    html = _read_text(_RAG_ROOT / "README.html")

    markdown_positions = []
    html_positions = []

    for section_title in _README_HUB_SECTION_ORDER:
        markdown_marker = f"## {section_title}"
        html_marker = f">{section_title}</h2>"

        markdown_positions.append(markdown.index(markdown_marker))
        html_positions.append(html.index(html_marker))

    assert markdown_positions == sorted(markdown_positions)
    assert html_positions == sorted(html_positions)

    assert markdown_positions[0] < markdown.index("PowerShell 실행 정책")
    assert html_positions[0] < html.index("PowerShell 실행 정책")


def test_html_readme_preserves_document_hub_toolbar() -> None:
    """HTML README가 Markdown 보기·인쇄·검색·테마 도구를 유지한다."""

    content = _read_text(_RAG_ROOT / "README.html")

    required_toolbar_terms = (
        'href="README.md"',
        'id="print-button"',
        "window.print()",
        'id="theme-toggle"',
        'id="document-search"',
        'href="#documentation-map"',
        'href="#observability"',
        'href="#verified-test-results"',
        'href="docs/README.md"',
    )

    for term in required_toolbar_terms:
        assert term in content, f"README.html 허브 도구 누락: {term}"
