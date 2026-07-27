"""Issue #119 혼합 문서 RAG 문서 계약이 다시 퇴행하지 않는지 검증한다.

README와 두 RAG Answer 계약 문서는 운영 코드의 외부 계약을 설명한다. 구현은
혼합 문서와 OCR을 지원하지만 문서가 과거 PDF 전용 설명으로 남아 있으면 AWS
Backend 개발자와 운영자가 잘못된 요청·응답을 구현할 수 있다.

이 테스트는 문장 전체를 스냅샷으로 고정하지 않고 반드시 유지해야 하는 핵심
용어와 계약만 검증한다. 따라서 오탈자 수정이나 문장 구조 개선은 허용하면서
지원 형식, 검색 범위, 출처 위치와 인용 무결성의 의미가 사라지는 것을 차단한다.
"""

from pathlib import Path
from typing import Final

_RAG_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_README_PATH: Final[Path] = _RAG_ROOT / "README.md"
_API_CONTRACT_PATH: Final[Path] = _RAG_ROOT / "docs" / "api" / "rag-answer-api-contract.md"
_ANSWER_CONTRACT_PATH: Final[Path] = _RAG_ROOT / "docs" / "api" / "rag-answer-contract.md"

_SUPPORTED_FORMATS: Final[tuple[str, ...]] = (
    "PDF",
    "DOCX",
    "PPTX",
    "XLSX",
    "TXT",
)


def _read_document(path: Path) -> str:
    """UTF-8 문서를 읽고 비어 있는 계약 파일을 명시적으로 거부한다."""

    content = path.read_text(encoding="utf-8")
    assert content.strip(), f"Documentation file must not be empty: {path}"
    return content


def test_readme_describes_all_supported_formats_and_ocr() -> None:
    """README가 모든 지원 형식과 OCR 답변 범위를 명시해야 한다."""

    readme = _read_document(_README_PATH)

    for file_type in _SUPPORTED_FORMATS:
        assert file_type in readme

    assert "OCR" in readme
    assert "일반 텍스트와 OCR" in readme
    assert "CUDA 12.9" in readme


def test_readme_excludes_stale_pdf_only_answer_limitations() -> None:
    """과거 PDF 전용·OCR 미지원 제한 문구가 다시 들어오면 실패해야 한다."""

    readme = _read_document(_README_PATH)
    stale_statements = (
        "현재 답변 대상 문서 형식은 **텍스트 레이어가 있는 PDF만 지원**",
        "OCR, TXT, DOCX, XLSX, PPTX 기반 답변 생성은 지원하지 않습니다",
        "현재 기본 Parser Factory에는 PDF 파서만 등록되어 있습니다",
        "지원됨: PDF\n미지원: DOCX, XLSX, PPTX",
        "이미지만 포함된 스캔 PDF에 대한 OCR은 수행하지 않습니다",
    )

    for stale_statement in stale_statements:
        assert stale_statement not in readme


def test_readme_documents_search_scope_and_partial_failure() -> None:
    """README가 선택 문서 범위와 문서별 부분 실패 정책을 설명해야 한다."""

    readme = _read_document(_README_PATH)

    assert "users_idx == request.user_idx" in readme
    assert "is_active == true" in readme
    assert "file_idx IN request.reference_file_idxs" in readme
    assert "부분 실패" in readme
    assert "최종 Claude 호출" in readme
    assert "insufficient_evidence" in readme


def test_readme_documents_public_citation_order_contract() -> None:
    """본문, cited_source_ids와 sources 순서 계약이 README에 있어야 한다."""

    readme = _read_document(_README_PATH)

    assert "cited_source_ids" in readme
    assert "sources[].source_id" in readme
    assert "실제로 인용한 출처만" in readme
    assert "본문 최초 등장 순서" in readme


def test_api_contract_documents_mixed_sources_and_error_contract() -> None:
    """AWS Backend용 계약이 혼합 문서 위치와 주요 오류 코드를 포함해야 한다."""

    contract = _read_document(_API_CONTRACT_PATH)

    for file_type in _SUPPORTED_FORMATS:
        assert file_type in contract

    required_terms = (
        "source_locator",
        "image_ordinal",
        "cited_source_ids",
        "INVALID_GENERATION_RESPONSE",
        "REFERENCE_DOCUMENT_REQUIRED",
        "insufficient_evidence",
        "최종 Claude 호출",
    )

    for term in required_terms:
        assert term in contract


def test_internal_answer_contract_documents_every_locator_family() -> None:
    """내부 답변 계약이 형식별 원본 위치 필드를 모두 설명해야 한다."""

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
    )

    for term in locator_terms:
        assert term in contract


def test_api_documents_share_the_same_citation_integrity_terms() -> None:
    """두 계약 문서가 공개 인용 무결성 용어를 동일하게 유지해야 한다."""

    api_contract = _read_document(_API_CONTRACT_PATH)
    answer_contract = _read_document(_ANSWER_CONTRACT_PATH)
    shared_terms = (
        "cited_source_ids",
        "SOURCE-N",
        "sources",
        "최초 등장 순서",
    )

    for term in shared_terms:
        assert term in api_contract
        assert term in answer_contract
