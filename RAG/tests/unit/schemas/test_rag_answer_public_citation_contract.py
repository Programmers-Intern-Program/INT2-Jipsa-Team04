"""최종 RAG 응답의 공개 인용 및 sources 축소 계약을 검증한다."""

import pytest
from pydantic import ValidationError

from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.rag_answer import (
    RagAnswerResponse,
    RagAnswerSource,
    RagAnswerStatus,
    RagAnswerUsage,
)
from jipsa_rag.schemas.source_locator import build_source_locator


def _source(*, number: int, file_idx: int = 100) -> RagAnswerSource:
    """테스트용 PDF 출처를 만든다."""

    return RagAnswerSource(
        source_id=f"SOURCE-{number}",
        chunk_id=f"00000000-0000-0000-0000-{number:012d}",
        rag_document_idx=file_idx * 10,
        file_idx=file_idx,
        folder_idx=9,
        file_name=f"문서-{file_idx}.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=number - 1,
        score=0.9,
        source_locator=build_source_locator(
            file_type=SupportedFileType.PDF,
            source_metadata={"page_number": number},
        ),
        excerpt=f"출처 {number} 발췌문",
    )


def _usage() -> RagAnswerUsage:
    return RagAnswerUsage(input_tokens=100, output_tokens=20)


def test_response_auto_fills_cited_source_ids_from_answer_order() -> None:
    """기존 서비스가 필드를 생략해도 본문 최초 인용 순서로 채워야 한다."""

    response = RagAnswerResponse(
        answer="두 번째 근거 [SOURCE-2], 첫 번째 근거 [SOURCE-1][SOURCE-2]",
        status=RagAnswerStatus.ANSWERED,
        sources=(_source(number=2), _source(number=1)),
        model="claude-sonnet-5",
        usage=_usage(),
    )

    assert response.cited_source_ids == ("SOURCE-2", "SOURCE-1")
    assert tuple(source.source_id for source in response.sources) == (
        "SOURCE-2",
        "SOURCE-1",
    )


def test_response_rejects_uncited_candidate_source() -> None:
    """본문에서 사용하지 않은 후보 출처가 최종 sources에 남을 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match="sources must match answer citations",
    ):
        RagAnswerResponse(
            answer="첫 번째 근거만 사용합니다. [SOURCE-1]",
            status=RagAnswerStatus.ANSWERED,
            sources=(_source(number=1), _source(number=2)),
            model="claude-sonnet-5",
            usage=_usage(),
        )


def test_response_rejects_sources_in_different_citation_order() -> None:
    """sources 순서는 본문 SOURCE-N 최초 등장 순서와 같아야 한다."""

    with pytest.raises(
        ValidationError,
        match="sources must match answer citations",
    ):
        RagAnswerResponse(
            answer="두 번째가 먼저입니다. [SOURCE-2][SOURCE-1]",
            status=RagAnswerStatus.ANSWERED,
            sources=(_source(number=1), _source(number=2)),
            model="claude-sonnet-5",
            usage=_usage(),
        )


def test_response_rejects_declared_ids_in_different_order() -> None:
    """cited_source_ids도 본문 최초 등장 순서와 정확히 일치해야 한다."""

    with pytest.raises(
        ValidationError,
        match="cited_source_ids must match answer citations",
    ):
        RagAnswerResponse(
            answer="두 번째가 먼저입니다. [SOURCE-2][SOURCE-1]",
            status=RagAnswerStatus.ANSWERED,
            cited_source_ids=("SOURCE-1", "SOURCE-2"),
            sources=(_source(number=2), _source(number=1)),
            model="claude-sonnet-5",
            usage=_usage(),
        )


def test_insufficient_evidence_has_no_citations_or_generation_metadata() -> None:
    """최종 Claude 호출을 생략한 응답은 빈 인용 계약을 가져야 한다."""

    response = RagAnswerResponse(
        answer="제공된 문서 근거만으로는 답변할 수 없습니다.",
        status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
    )

    assert response.cited_source_ids == ()
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
