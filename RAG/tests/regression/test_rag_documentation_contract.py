"""Local RAG 문서 계약, 구조, 링크와 용어가 다시 퇴행하지 않는지 검증한다.

이 테스트는 문장 전체를 스냅샷으로 고정하지 않는다. 표현과 문단은 개선할 수 있지만,
다음 외부 계약과 문서 품질 기준은 사라지거나 과거 상태로 되돌아가면 안 된다.

- PDF, DOCX, PPTX, XLSX, TXT와 이미지 OCR 지원
- AWS Backend와 Local RAG의 명확한 책임 경계
- 사용자·활성 색인·선택 문서 Qdrant 검색 범위
- lookup, synthesis와 문서별 부분 실패
- SOURCE-N, cited_source_ids, sources 순서 무결성
- 재인제스트 staging, 활성 전환, 동시성·소유권과 보상 처리
- CUDA 12.9, TEI, Qdrant, Local RAG DB 실행 절차
- Ruff, Mypy, Pytest와 실제 E2E의 구분
- 환경 변수와 비밀정보 비노출
- 문서 상태·독자·검토 기준과 역할별 탐색 경로
- RAG 디렉터리 Markdown 상대 링크, heading hierarchy와 code fence 무결성

Markdown 링크 검사는 `RAG` 디렉터리 내부에서 자동 생성물과 의존성 디렉터리를 제외하고 수행한다.
외부 URL과 문서 내부 anchor는 네트워크 상태나 heading slug 구현에 의존하므로 검사 범위가 아니다.
또한 Local RAG 전용 테스트가 상위 프로젝트 문서의 내용이나 상태에 의존하지 않도록 검사 경계를
`RAG` 루트에 고정한다.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

_RAG_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

_RAG_README_PATH: Final[Path] = _RAG_ROOT / "README.md"
_RAG_HTML_README_PATH: Final[Path] = _RAG_ROOT / "README.html"
_DOCS_INDEX_PATH: Final[Path] = _RAG_ROOT / "docs" / "README.md"
_GLOSSARY_PATH: Final[Path] = _RAG_ROOT / "docs" / "glossary.md"
_BOUNDARY_PATH: Final[Path] = _RAG_ROOT / "docs" / "architecture" / "responsibility-boundary.md"
_FORMAT_OCR_PATH: Final[Path] = _RAG_ROOT / "docs" / "features" / "document-support-and-ocr.md"
_CHUNK_SEARCH_PATH: Final[Path] = _RAG_ROOT / "docs" / "chunk-search-api.md"
_COMPREHENSIVE_API_SPEC_PATH: Final[Path] = (
    _RAG_ROOT / "docs" / "api" / "comprehensive-api-specification.md"
)
_API_GOVERNANCE_PATH: Final[Path] = (
    _RAG_ROOT / "docs" / "api" / "api-governance-and-compatibility.md"
)
_API_CONTRACT_PATH: Final[Path] = _RAG_ROOT / "docs" / "api" / "rag-answer-api-contract.md"
_ANSWER_CONTRACT_PATH: Final[Path] = _RAG_ROOT / "docs" / "api" / "rag-answer-contract.md"
_INGEST_POLICY_PATH: Final[Path] = _RAG_ROOT / "docs" / "operations" / "ingest-recovery-policy.md"
_RUNTIME_PATH: Final[Path] = _RAG_ROOT / "docs" / "operations" / "local-runtime.md"
_OBSERVABILITY_PATH: Final[Path] = (
    _RAG_ROOT / "docs" / "operations" / "observability-and-troubleshooting.md"
)
_TEST_GUIDE_PATH: Final[Path] = _RAG_ROOT / "docs" / "testing" / "test-guide.md"
_POWERSHELL_E2E_PATH: Final[Path] = _RAG_ROOT / "docs" / "testing" / "powershell-e2e.md"
_SECURITY_PATH: Final[Path] = _RAG_ROOT / "docs" / "security" / "environment-and-secrets.md"
_QUALITY_STANDARD_PATH: Final[Path] = (
    _RAG_ROOT / "docs" / "governance" / "documentation-quality-standard.md"
)
_API_SPEC_QUALITY_REPORT_PATH: Final[Path] = (
    _RAG_ROOT / "docs" / "governance" / "comprehensive-api-specification-quality-report.md"
)
_HTML_QUALITY_REPORT_PATH: Final[Path] = (
    _RAG_ROOT / "docs" / "governance" / "readme-html-quality-report.md"
)
_QUALITY_REPORT_PATH: Final[Path] = (
    _RAG_ROOT / "docs" / "governance" / "documentation-review-report.md"
)

_SUPPORTED_FORMATS: Final[tuple[str, ...]] = (
    "PDF",
    "DOCX",
    "PPTX",
    "XLSX",
    "TXT",
)

_CORE_DOCUMENT_PATHS: Final[tuple[Path, ...]] = (
    _RAG_README_PATH,
    _DOCS_INDEX_PATH,
    _GLOSSARY_PATH,
    _BOUNDARY_PATH,
    _FORMAT_OCR_PATH,
    _CHUNK_SEARCH_PATH,
    _COMPREHENSIVE_API_SPEC_PATH,
    _API_GOVERNANCE_PATH,
    _API_CONTRACT_PATH,
    _ANSWER_CONTRACT_PATH,
    _INGEST_POLICY_PATH,
    _RUNTIME_PATH,
    _OBSERVABILITY_PATH,
    _TEST_GUIDE_PATH,
    _POWERSHELL_E2E_PATH,
    _SECURITY_PATH,
    _QUALITY_STANDARD_PATH,
    _QUALITY_REPORT_PATH,
    _API_SPEC_QUALITY_REPORT_PATH,
    _HTML_QUALITY_REPORT_PATH,
)

# 일반적인 Markdown 인라인 링크와 이미지 링크의 destination만 추출한다.
#
# CommonMark 전체 파서를 테스트 의존성으로 추가하지 않기 위해 코드 fence를 먼저
# 제거한 뒤 링크 목적지만 검사한다. 외부 URL과 anchor는 파일 시스템 검증 대상이 아니다.
_MARKDOWN_LINK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"!?\[[^\]]*]\((?P<destination>[^)\n]+)\)"
)
_ATX_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(?P<hashes>#{1,6})\s+\S")

# 실제 장기 자격 증명으로 보이는 대표 패턴을 탐지한다.
#
# placeholder와 변수명 자체는 문서에 필요하므로 키 형식이 충분히 구체적인 패턴만
# 검사한다. 정교한 secret scanning은 별도 도구의 책임이며 이 테스트는 명백한 회귀를
# 빠르게 차단하는 최소 방어선이다.
_REAL_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
)

_MARKDOWN_SCAN_EXCLUDED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        ".cache",
        ".git",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vscode",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "venv",
    }
)


class _ReadmeHtmlContractParser(HTMLParser):
    """README.html의 구조·링크·외부 자산을 표준 라이브러리만으로 수집한다.

    브라우저 렌더링 엔진을 테스트 의존성으로 추가하지 않으면서도 ID 중복, 내부
    fragment, landmark와 원격 runtime asset 회귀를 빠르게 차단한다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.external_runtime_assets: list[str] = []
        self.tags: list[str] = []
        self.attributes_by_tag: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.tags.append(tag)
        self.attributes_by_tag.append((tag, attributes))

        element_id = attributes.get("id")
        if element_id:
            self.ids.append(element_id)

        href = attributes.get("href")
        if href:
            self.hrefs.append(href)

        if tag == "script" and attributes.get("src"):
            self.external_runtime_assets.append(attributes["src"])

        if tag == "link" and "stylesheet" in attributes.get("rel", ""):
            self.external_runtime_assets.append(attributes.get("href", ""))


def _parse_html_readme() -> tuple[str, _ReadmeHtmlContractParser]:
    """HTML README를 읽어 계약 검사에 사용할 parser 상태를 반환한다."""

    html = _read_document(_RAG_HTML_README_PATH)
    parser = _ReadmeHtmlContractParser()
    parser.feed(html)
    parser.close()
    return html, parser


def _read_document(path: Path) -> str:
    """UTF-8 문서를 읽고 누락되거나 비어 있는 계약 파일을 명시적으로 거부한다."""

    assert path.is_file(), f"Documentation file does not exist: {path}"
    content = path.read_text(encoding="utf-8")
    assert content.strip(), f"Documentation file must not be empty: {path}"
    return content


def _iter_markdown_files() -> tuple[Path, ...]:
    """자동 생성물과 의존성 디렉터리를 제외한 RAG Markdown 파일을 반환한다.

    이 회귀 테스트는 Local RAG 코드와 문서의 품질 계약만 담당한다. 따라서 상위
    프로젝트 루트, Backend, Frontend 문서는 탐색하지 않는다. 이렇게 범위를 고정하면
    다른 구성요소의 문서 상태 때문에 Local RAG 테스트가 실패하는 결합을 방지할 수 있다.
    """

    markdown_files: list[Path] = []

    for path in _RAG_ROOT.rglob("*.md"):
        relative_parts = path.relative_to(_RAG_ROOT).parts
        if any(part in _MARKDOWN_SCAN_EXCLUDED_DIRECTORIES for part in relative_parts):
            continue
        markdown_files.append(path)

    return tuple(sorted(markdown_files))


def _strip_fenced_code_blocks(markdown: str) -> str:
    """코드 예시를 heading·링크 검사에서 제외한다."""

    visible_lines: list[str] = []
    active_fence: str | None = None

    for line in markdown.splitlines():
        stripped = line.lstrip()

        if active_fence is None:
            if stripped.startswith("```"):
                active_fence = "```"
                visible_lines.append("")
                continue
            if stripped.startswith("~~~"):
                active_fence = "~~~"
                visible_lines.append("")
                continue
            visible_lines.append(line)
            continue

        if stripped.startswith(active_fence):
            active_fence = None
        visible_lines.append("")

    return "\n".join(visible_lines)


def _assert_balanced_fenced_code_blocks(path: Path, markdown: str) -> None:
    """닫히지 않은 fence 때문에 이후 문서 전체가 코드로 렌더링되는 회귀를 차단한다."""

    active_fence: str | None = None

    for line in markdown.splitlines():
        stripped = line.lstrip()

        if active_fence is None:
            if stripped.startswith("```"):
                active_fence = "```"
            elif stripped.startswith("~~~"):
                active_fence = "~~~"
            continue

        if stripped.startswith(active_fence):
            active_fence = None

    assert active_fence is None, f"Unclosed Markdown fence: {path}"


def _normalize_link_destination(raw_destination: str) -> str:
    """선택적 title을 제거하고 실제 link destination만 반환한다."""

    destination = raw_destination.strip()

    if destination.startswith("<"):
        closing_index = destination.find(">")
        assert closing_index > 0, (
            f"Markdown link destination starts with '<' but has no closing '>': {raw_destination}"
        )
        return destination[1:closing_index].strip()

    return destination.split(maxsplit=1)[0]


def _is_external_or_anchor_link(destination: str) -> bool:
    """네트워크 링크, 특수 scheme와 문서 내부 anchor인지 판정한다."""

    lowered = destination.lower()
    return (
        not destination
        or destination.startswith("#")
        or lowered.startswith(("http://", "https://", "mailto:", "tel:", "data:"))
    )


def _resolve_rag_link(document_path: Path, destination: str) -> Path:
    """문서 기준 상대 경로를 RAG 내부의 실제 경로로 변환한다.

    Local RAG 문서는 `RAG` 루트 안에서 독립적으로 탐색·검증할 수 있어야 한다.
    따라서 `../`를 사용해 상위 프로젝트로 빠져나가는 링크도 회귀로 간주한다.
    """

    parsed = urlsplit(destination)
    decoded_path = unquote(parsed.path)

    assert decoded_path, (
        f"Local Markdown link must include a path: {document_path} -> {destination}"
    )
    assert not decoded_path.startswith(("/", "\\")), (
        "Repository documentation must use relative links instead of absolute links: "
        f"{document_path} -> {destination}"
    )

    resolved_path = (document_path.parent / decoded_path).resolve()

    assert resolved_path.is_relative_to(_RAG_ROOT), (
        f"Markdown link escapes RAG root: {document_path} -> {destination}"
    )

    return resolved_path


def test_required_documentation_files_exist_and_are_not_empty() -> None:
    """세계적 수준 문서 패키지의 모든 핵심 파일이 실제로 존재해야 한다."""

    for path in _CORE_DOCUMENT_PATHS:
        _read_document(path)


def test_core_documents_have_single_h1_metadata_and_balanced_fences() -> None:
    """핵심 문서가 명확한 제목·독자·상태를 제공하고 정상 렌더링돼야 한다."""

    for path in _CORE_DOCUMENT_PATHS:
        markdown = _read_document(path)
        visible_markdown = _strip_fenced_code_blocks(markdown)
        h1_count = sum(
            1
            for line in visible_markdown.splitlines()
            if line.startswith("# ") and not line.startswith("## ")
        )

        assert h1_count == 1, f"Core document must contain one H1: {path}"
        assert "문서 상태" in markdown, f"Document status is missing: {path}"
        assert "주 독자" in markdown, f"Primary audience is missing: {path}"
        assert "최종 검토" in markdown, f"Review date is missing: {path}"
        _assert_balanced_fenced_code_blocks(path, markdown)


def test_markdown_heading_levels_do_not_jump() -> None:
    """H2 다음 H4처럼 heading level을 건너뛰는 정보 구조 회귀를 차단한다."""

    violations: list[str] = []

    for path in _iter_markdown_files():
        visible_markdown = _strip_fenced_code_blocks(path.read_text(encoding="utf-8"))
        previous_level = 0

        for line_number, line in enumerate(
            visible_markdown.splitlines(),
            start=1,
        ):
            match = _ATX_HEADING_PATTERN.match(line)
            if match is None:
                continue

            current_level = len(match.group("hashes"))
            if previous_level and current_level > previous_level + 1:
                violations.append(
                    f"{path.relative_to(_RAG_ROOT)}:{line_number} "
                    f"heading jumped from H{previous_level} to H{current_level}"
                )
            previous_level = current_level

    assert not violations, "\n".join(violations)


def test_rag_readme_contains_role_based_document_navigation() -> None:
    """RAG README가 Local RAG 범위의 역할별 문서 진입점을 제공해야 한다.

    상위 프로젝트 README는 Backend와 Frontend까지 포함하는 별도 구성요소의 문서다.
    Local RAG 회귀 테스트는 `RAG/README.md`를 자체 진입점으로 사용하며 상위 문서의
    존재 여부나 내용에 의존하지 않는다.
    """

    readme = _read_document(_RAG_README_PATH)
    required_terms = (
        "AWS Backend",
        "Local RAG",
        "reference_file_idxs",
        "docs/README.md",
        "api-governance-and-compatibility.md",
        "observability-and-troubleshooting.md",
        "documentation-quality-standard.md",
    )

    for term in required_terms:
        assert term in readme


def test_rag_readme_describes_all_supported_formats_and_ocr() -> None:
    """RAG README가 모든 지원 형식과 일반 텍스트·OCR 통합 범위를 명시해야 한다."""

    readme = _read_document(_RAG_README_PATH)

    for file_type in _SUPPORTED_FORMATS:
        assert file_type in readme

    assert "일반 텍스트와 OCR 텍스트" in readme
    assert "CUDA 12.9" in readme
    assert "source_locator" in readme


def test_rag_documents_exclude_stale_pdf_only_limitations() -> None:
    """과거 PDF 전용·OCR 미지원 제한 문구가 다시 들어오면 실패한다."""

    rag_documents = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _iter_markdown_files()
        if path.is_relative_to(_RAG_ROOT)
    )
    stale_statements = (
        "현재 답변 대상 문서 형식은 **텍스트 레이어가 있는 PDF만 지원**",
        "OCR, TXT, DOCX, XLSX, PPTX 기반 답변 생성은 지원하지 않습니다",
        "현재 기본 Parser Factory에는 PDF 파서만 등록되어 있습니다",
        "지원됨: PDF\n미지원: DOCX, XLSX, PPTX",
        "이미지만 포함된 스캔 PDF에 대한 OCR은 수행하지 않습니다",
        "synthesis가 PDF별로만 동작",
    )

    for stale_statement in stale_statements:
        assert stale_statement not in rag_documents


def test_responsibility_boundary_keeps_aws_credentials_out_of_local_rag() -> None:
    """책임 경계 문서가 인증·S3·토큰 방향을 명확하게 구분해야 한다."""

    boundary = _read_document(_BOUNDARY_PATH)
    required_terms = (
        "사용자 인증·인가",
        "Presigned GET URL",
        "RAG_INGEST_TOKEN",
        "INTERNAL_TOKEN",
        "AWS Access Key",
        "reference_file_idxs",
        "Backend → Local RAG",
        "Local RAG → AWS Backend",
    )

    for term in required_terms:
        assert term in boundary


def test_search_and_answer_contracts_share_selected_file_scope() -> None:
    """검색과 답변 API가 같은 사용자·활성·선택 문서 범위를 사용해야 한다."""

    documents = (
        _read_document(_RAG_README_PATH),
        _read_document(_CHUNK_SEARCH_PATH),
        _read_document(_COMPREHENSIVE_API_SPEC_PATH),
        _read_document(_API_CONTRACT_PATH),
        _read_document(_ANSWER_CONTRACT_PATH),
    )
    shared_terms = (
        "reference_file_idxs",
        "users_idx == request.user_idx",
        "is_active == true",
        "file_idx IN request.reference_file_idxs",
    )

    for document in documents:
        for term in shared_terms:
            assert term in document


def test_answer_api_contract_versions_are_synchronized() -> None:
    """공개·상세·거버넌스 문서가 동일한 답변 계약 버전을 사용해야 한다."""

    api_contract = _read_document(_API_CONTRACT_PATH)
    answer_contract = _read_document(_ANSWER_CONTRACT_PATH)
    governance = _read_document(_API_GOVERNANCE_PATH)

    assert "`1.3.0`" in api_contract
    assert "`1.3.0`" in answer_contract
    assert "현재 버전은 `1.3.0`" in governance
    assert "`1.2.1`" not in api_contract.split("### 변경 이력", maxsplit=1)[0]


def test_api_contracts_document_public_citation_order() -> None:
    """본문, cited_source_ids와 sources가 동일한 최초 등장 순서를 설명해야 한다."""

    documents = (
        _read_document(_RAG_README_PATH),
        _read_document(_COMPREHENSIVE_API_SPEC_PATH),
        _read_document(_API_GOVERNANCE_PATH),
        _read_document(_API_CONTRACT_PATH),
        _read_document(_ANSWER_CONTRACT_PATH),
    )
    required_terms = (
        "SOURCE-N",
        "cited_source_ids",
        "sources",
        "최초 등장 순서",
        "실제로 인용한 출처",
    )

    for document in documents:
        for term in required_terms:
            assert term in document


def test_answer_contract_documents_every_locator_family() -> None:
    """상세 답변 계약이 모든 형식과 OCR 위치 필드를 포함해야 한다."""

    contract = _read_document(_ANSWER_CONTRACT_PATH)
    locator_terms = (
        "pdf_page",
        "docx_block",
        "pptx_shape",
        "xlsx_cell_range",
        "txt_line",
        "section_index",
        "paragraph_index",
        "shape_path",
        "cell_range",
        "line_start",
        "char_start",
        "image_ordinal",
        "ocr_mean_confidence",
    )

    for term in locator_terms:
        assert term in contract


def test_ingest_policy_documents_idempotency_concurrency_and_compensation() -> None:
    """재인제스트 문서가 현재 색인 서비스의 안전 전환 규칙을 유지해야 한다."""

    policy = _read_document(_INGEST_POLICY_PATH)
    required_terms = (
        "비활성 staging point",
        "신규 point 활성화",
        "이전 정상 point 비활성화",
        "MySQL advisory lock",
        "결정적 Chunk ID",
        "실행 소유권 상실",
        "보상 처리",
        "성공 콜백",
        "실패 콜백",
    )

    for term in required_terms:
        assert term in policy


def test_runtime_observability_and_test_guides_are_operationally_complete() -> None:
    """실행·진단·테스트 문서가 준비 상태와 실제 E2E 구분을 제공해야 한다."""

    runtime = _read_document(_RUNTIME_PATH)
    observability = _read_document(_OBSERVABILITY_PATH)
    test_guide = _read_document(_TEST_GUIDE_PATH)

    runtime_terms = (
        "CUDA 12.9",
        "127.0.0.1:18081",
        "127.0.0.1:6333",
        "Jipsa_Local_RAG",
        "start-local-rag.ps1",
        "Docker Desktop",
    )
    observability_terms = (
        "file_index_run_ownership_lost",
        "file_index_previous_reactivation_failed",
        "ingest_failure_callback_failed",
        "Prometheus metrics endpoint",
        "답변에 잘못된 출처가 포함됨",
    )
    test_terms = (
        "ruff format --check",
        "ruff check",
        "mypy src tests",
        "uv run pytest",
        "run-issue-123-e2e.ps1",
        "run-all-rag-tests.ps1",
        "실제로 실행하지 않은",
    )

    for term in runtime_terms:
        assert term in runtime
    for term in observability_terms:
        assert term in observability
    for term in test_terms:
        assert term in test_guide


def test_security_guide_documents_secret_and_log_redaction_contract() -> None:
    """환경·보안 문서가 실제 비밀값과 민감 원문의 비노출 원칙을 유지해야 한다."""

    security = _read_document(_SECURITY_PATH)
    required_terms = (
        ".env.local",
        ".env.test",
        ".env.example",
        "ANTHROPIC_API_KEY",
        "RAG_INGEST_TOKEN",
        "INTERNAL_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "Presigned URL",
        "사용자 질문",
        "OCR 텍스트",
        "임베딩 벡터",
    )

    for term in required_terms:
        assert term in security


def test_glossary_and_quality_standard_define_maintenance_contract() -> None:
    """용어집과 품질 표준이 전 문서의 일관된 리뷰 기준을 제공해야 한다."""

    glossary = _read_document(_GLOSSARY_PATH)
    quality_standard = _read_document(_QUALITY_STANDARD_PATH)
    review_report = _read_document(_QUALITY_REPORT_PATH)

    for term in (
        "AWS Backend",
        "Local RAG",
        "reference_file_idxs",
        "Source Locator",
        "lookup",
        "synthesis",
        "RAG_INGEST_TOKEN",
        "INTERNAL_TOKEN",
    ):
        assert term in glossary

    for term in (
        "필수 게이트",
        "구현 정확성과 근거",
        "API 계약 정밀성",
        "보안·개인정보",
        "97점 이상",
        "Critical·High",
        "종합 API 명세 완전성",
    ):
        assert term in quality_standard

    for term in (
        "1차 평가",
        "2차 평가",
        "3차 평가",
        "99.1",
        "실제 실행 결과",
        "문서 품질 기준",
    ):
        assert term in review_report


def test_html_readme_is_accessible_self_contained_and_current() -> None:
    """시각적 README가 접근성·자립성·최신 Local RAG 계약을 유지해야 한다."""

    html, parser = _parse_html_readme()
    required_terms = (
        'lang="ko"',
        'name="viewport"',
        'name="description"',
        'name="theme-color"',
        'href="#main-content"',
        'id="main-content"',
        'id="theme-toggle"',
        'id="document-search"',
        "prefers-reduced-motion",
        "@media print",
        "#00236f",
        "#00687a",
        "PDF",
        "DOCX",
        "PPTX",
        "XLSX",
        "TXT",
        "CUDA 12.9",
        "reference_file_idxs",
        "SOURCE-N",
        "source_locator",
        "Verified 2026-07-28",
    )

    for term in required_terms:
        assert term in html

    for landmark in ("header", "main", "nav", "aside", "footer"):
        assert landmark in parser.tags

    assert len(parser.ids) == len(set(parser.ids)), "README.html contains duplicate IDs"
    assert not parser.external_runtime_assets, (
        "README.html must not depend on external scripts or stylesheets: "
        f"{parser.external_runtime_assets}"
    )


def test_html_readme_internal_links_and_fragments_resolve() -> None:
    """HTML README의 anchor와 상대 링크가 RAG 경계 안에서 실제 대상으로 해석돼야 한다."""

    _, parser = _parse_html_readme()
    known_ids = set(parser.ids)
    broken_links: list[str] = []

    for href in parser.hrefs:
        if href.startswith("#"):
            fragment = unquote(href[1:])
            if fragment and fragment not in known_ids:
                broken_links.append(f"Missing HTML fragment target: {href}")
            continue

        if _is_external_or_anchor_link(href):
            continue

        try:
            resolved_path = _resolve_rag_link(_RAG_HTML_README_PATH, href)
        except AssertionError as error:
            broken_links.append(str(error))
            continue

        if not resolved_path.exists():
            broken_links.append(f"Broken README.html link: {href} (resolved: {resolved_path})")

    assert not broken_links, "\n".join(broken_links)


def test_comprehensive_api_spec_covers_complete_surface_and_common_contracts() -> None:
    """종합 명세서가 모든 inbound·outbound API와 공통 transport 계약을 포함한다."""

    specification = _read_document(_COMPREHENSIVE_API_SPEC_PATH)
    endpoint_terms = (
        "POST /ingest",
        "POST /api/v1/files/process",
        "GET /api/v1/health/live",
        "GET /api/v1/health/ready",
        "GET /api/v1/diagnostics/network",
        "POST /api/v1/chunks/search",
        "POST /api/v1/rag/answers",
        "GET /internal/files/{file_idx}/manifest",
        "POST /internal/files/{file_idx}/ingest-complete",
    )
    common_terms = (
        "종합 명세 버전:** `1.0.0`",
        "X-Internal-Token",
        "RAG_INGEST_TOKEN",
        "INTERNAL_TOKEN",
        "X-Request-ID",
        "현재 outbound 미전파",
        "REQUEST_VALIDATION_FAILED",
        'extra="forbid"',
        "public·protected endpoint",
    )

    for term in endpoint_terms + common_terms:
        assert term in specification


def test_comprehensive_api_spec_documents_callbacks_limits_and_known_non_guarantees() -> None:
    """종합 명세서가 callback·검색·답변·운영 한계를 구현 의미대로 고정한다."""

    specification = _read_document(_COMPREHENSIVE_API_SPEC_PATH)
    quality_report = _read_document(_API_SPEC_QUALITY_REPORT_PATH)
    required_terms = (
        "chunk_count == len(chunks)",
        "0부터 연속",
        "성공 callback 전송을 시작한 뒤",
        "users_idx == request.user_idx",
        "is_active == true",
        "file_idx IN request.reference_file_idxs",
        "REFERENCE_DOCUMENT_REQUIRED",
        "insufficient_evidence",
        "제공된 문서 근거만으로는 답변할 수 없습니다.",
        "image_ordinal",
        "ocr_mean_confidence",
        "readiness는 DB만 검사",
        "Idempotency-Key",
    )

    for term in required_terms:
        assert term in specification

    for term in (
        "1차 평가",
        "2차 평가",
        "3차 평가",
        "99.2",
        "Critical",
        "High",
        "현재 outbound 미전파",
    ):
        assert term in quality_report


def test_documentation_contains_no_obvious_real_secret_patterns() -> None:
    """문서와 예시에 실제로 보이는 장기 자격 증명 패턴이 포함되지 않아야 한다."""

    findings: list[str] = []

    for path in _iter_markdown_files():
        content = path.read_text(encoding="utf-8")
        for pattern in _REAL_SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(f"{path.relative_to(_RAG_ROOT)} matched {pattern.pattern}")

    assert not findings, "\n".join(findings)


def test_all_rag_markdown_relative_links_resolve() -> None:
    """RAG Markdown의 모든 로컬 상대 링크가 RAG 내부 실제 경로를 가리킨다."""

    broken_links: list[str] = []

    for document_path in _iter_markdown_files():
        markdown = _strip_fenced_code_blocks(document_path.read_text(encoding="utf-8"))

        for match in _MARKDOWN_LINK_PATTERN.finditer(markdown):
            destination = _normalize_link_destination(match.group("destination"))

            if _is_external_or_anchor_link(destination):
                continue

            try:
                resolved_path = _resolve_rag_link(
                    document_path,
                    destination,
                )
            except AssertionError as error:
                broken_links.append(str(error))
                continue

            if not resolved_path.exists():
                broken_links.append(
                    "Broken Markdown link: "
                    f"{document_path.relative_to(_RAG_ROOT)} "
                    f"-> {destination} "
                    f"(resolved: {resolved_path})"
                )

    assert not broken_links, "\n".join(broken_links)
