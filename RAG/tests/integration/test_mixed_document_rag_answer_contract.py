"""혼합 문서 검색부터 최종 sources 축소까지의 서비스 통합 계약을 검증한다.

실제 Qdrant와 Claude 대신 프로토콜 호환 테스트 대역을 사용하되,
``RoutedRagAnswerService``와 ``RagPromptBuilder``의 실제 오케스트레이션을 통과한다.
"""

from typing import Final

import pytest

from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
)
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.rag_answer import RagAnswerRequest, RagAnswerStatus
from jipsa_rag.schemas.source_locator import build_source_locator
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.query_routing import RoutedRagAnswerService

_USER_IDX: Final[int] = 45


class _PerDocumentSearcher:
    """선택된 파일마다 미리 준비한 독립 검색 결과를 반환한다."""

    def __init__(self, responses: dict[int, ChunkSearchResponse]) -> None:
        self._responses = responses
        self.calls: list[ChunkSearchRequest] = []

    async def search(self, request: ChunkSearchRequest) -> ChunkSearchResponse:
        self.calls.append(request)
        assert len(request.reference_file_idxs) == 1
        return self._responses[request.reference_file_idxs[0]]


class _ScriptedGenerationClient:
    """문서별 부분 답변 5개와 최종 종합 답변을 순서대로 반환한다."""

    def __init__(self, texts: tuple[str, ...]) -> None:
        self._texts = texts
        self.calls: list[GenerationRequest] = []

    async def generate(self, *, request: GenerationRequest) -> GenerationResult:
        index = len(self.calls)
        self.calls.append(request)
        return GenerationResult(
            text=self._texts[index],
            model="claude-sonnet-5",
            usage=GenerationUsage(input_tokens=100, output_tokens=30),
            stop_reason="end_turn",
        )


def _chunk(
    *,
    file_idx: int,
    file_name: str,
    file_type: SupportedFileType,
    metadata: dict[str, object],
) -> ChunkSearchResult:
    """형식별 Source Locator가 포함된 검색 청크를 생성한다."""

    locator = build_source_locator(
        file_type=file_type,
        source_metadata=metadata,
    )
    return ChunkSearchResult(
        chunk_id=f"00000000-0000-0000-0000-{file_idx:012d}",
        score=0.95,
        rag_document_idx=file_idx * 10,
        file_idx=file_idx,
        folder_idx=9,
        file_name=file_name,
        file_type=file_type,
        chunk_index=0,
        content=f"{file_name}의 검증 가능한 근거입니다.",
        token_count=32,
        page=locator.page,
        slide_no=locator.slide_no,
        sheet_name=locator.sheet_name,
        section_title=locator.section_title,
        source_locator=locator,
        parser_version="1.1.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


@pytest.mark.asyncio
async def test_mixed_formats_return_only_sources_used_by_final_answer() -> None:
    """5개 형식과 OCR을 종합해도 최종 본문이 쓴 출처만 반환해야 한다."""

    chunks = (
        _chunk(
            file_idx=101,
            file_name="정책.pdf",
            file_type=SupportedFileType.PDF,
            metadata={"page_number": 3},
        ),
        _chunk(
            file_idx=102,
            file_name="절차.docx",
            file_type=SupportedFileType.DOCX,
            metadata={
                "section_index": 2,
                "block_index": 4,
                "paragraph_index": 3,
                "section_title": "운영 절차",
            },
        ),
        _chunk(
            file_idx=103,
            file_name="발표.pptx",
            file_type=SupportedFileType.PPTX,
            metadata={
                "slide_number": 5,
                "shape_index": 2,
                "shape_id": 14,
                "shape_path": "2",
            },
        ),
        _chunk(
            file_idx=104,
            file_name="원문.txt",
            file_type=SupportedFileType.TXT,
            metadata={
                "line_start_number": 10,
                "line_end_number": 12,
                "source_char_start": 90,
                "source_char_end": 150,
            },
        ),
        _chunk(
            file_idx=105,
            file_name="성과.xlsx",
            file_type=SupportedFileType.XLSX,
            metadata={
                "sheet_number": 1,
                "sheet_name": "성과",
                "cell_range": "B2:E8",
                "content_origin": "ocr",
                "unit_type": "ocr_image",
                "image_index": 2,
                "image_id": "xlsx-chart-2",
                "image_kind": "xlsx_chart_render",
            },
        ),
    )
    responses = {
        chunk.file_idx: ChunkSearchResponse(
            user_idx=_USER_IDX,
            result_count=1,
            results=(chunk,),
        )
        for chunk in chunks
    }
    searcher = _PerDocumentSearcher(responses)
    generator = _ScriptedGenerationClient(
        (
            "PDF 부분 근거입니다. [SOURCE-1]",
            "DOCX 부분 근거입니다. [SOURCE-1]",
            "PPTX 부분 근거입니다. [SOURCE-1]",
            "TXT 부분 근거입니다. [SOURCE-1]",
            "XLSX OCR 부분 근거입니다. [SOURCE-1]",
            # 전역 재매핑 결과 중 PDF와 XLSX OCR 출처만 실제 사용한다.
            "정책과 성과 차트를 함께 확인했습니다. [SOURCE-1][SOURCE-5]",
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generator,
    )

    response = await service.answer(
        RagAnswerRequest(
            user_idx=_USER_IDX,
            reference_file_idxs=(101, 102, 103, 104, 105),
            query="모든 문서를 종합해 정책과 성과를 비교해줘",
            top_k=5,
        )
    )

    assert response.status is RagAnswerStatus.ANSWERED
    assert response.cited_source_ids == ("SOURCE-1", "SOURCE-5")
    assert tuple(source.file_idx for source in response.sources) == (101, 105)
    assert response.sources[0].page == 3

    ocr_locator = response.sources[1].source_locator
    assert ocr_locator is not None
    assert ocr_locator.sheet_name == "성과"
    assert ocr_locator.cell_range == "B2:E8"
    assert ocr_locator.image_ordinal == 2
    assert ocr_locator.image_id == "xlsx-chart-2"
    assert len(searcher.calls) == 5
    assert len(generator.calls) == 6
