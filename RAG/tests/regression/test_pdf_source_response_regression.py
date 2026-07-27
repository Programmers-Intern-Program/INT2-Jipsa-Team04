"""공통 출처 모델 도입 후 기존 PDF 응답이 유지되는지 회귀 검증한다."""

from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.rag_answer import (
    RagAnswerResponse,
    RagAnswerSource,
    RagAnswerStatus,
    RagAnswerUsage,
)
from jipsa_rag.schemas.source_locator import SourceLocatorKind


def test_legacy_pdf_page_field_builds_equivalent_source_locator() -> None:
    """기존 호출자가 page만 전달해도 page와 공통 locator를 함께 반환한다."""

    source = RagAnswerSource(
        source_id="SOURCE-1",
        chunk_id="11111111-1111-1111-1111-111111111111",
        rag_document_idx=1001,
        file_idx=101,
        folder_idx=9,
        file_name="정책.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        score=0.95,
        page=7,
        excerpt="정책 문서의 기존 PDF 페이지 근거입니다.",
    )

    assert source.page == 7
    assert source.source_locator is not None
    assert source.source_locator.kind is SourceLocatorKind.PDF_PAGE
    assert source.source_locator.page == 7
    assert source.source_locator.structure_path == "page:7"


def test_pdf_answer_keeps_legacy_fields_and_adds_cited_ids() -> None:
    """기존 PDF source 구조를 유지하면서 공개 인용 목록만 추가한다."""

    source = RagAnswerSource(
        source_id="SOURCE-1",
        chunk_id="11111111-1111-1111-1111-111111111111",
        rag_document_idx=1001,
        file_idx=101,
        file_name="정책.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        score=0.95,
        page=7,
        excerpt="정책 문서의 기존 PDF 페이지 근거입니다.",
    )
    response = RagAnswerResponse(
        answer="정책은 7페이지에서 확인됩니다. [SOURCE-1]",
        status=RagAnswerStatus.ANSWERED,
        sources=(source,),
        model="claude-sonnet-5",
        usage=RagAnswerUsage(input_tokens=100, output_tokens=20),
        stop_reason="end_turn",
    )
    payload = response.model_dump(mode="json")

    assert payload["cited_source_ids"] == ["SOURCE-1"]
    assert payload["sources"][0]["page"] == 7
    assert payload["sources"][0]["source_locator"]["page"] == 7
