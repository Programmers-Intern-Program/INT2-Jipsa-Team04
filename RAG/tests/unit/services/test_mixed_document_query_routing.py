"""혼합 문서 lookup/synthesis와 OCR 인용 계약을 검증한다."""

from typing import Final

import pytest

from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
)
from jipsa_rag.infrastructure.indexing.exceptions import VectorDatabaseUnavailableError
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.rag_answer import RagAnswerRequest, RagAnswerStatus
from jipsa_rag.schemas.source_locator import build_source_locator
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.query_routing import (
    RoutedRagAnswerService,
    SynthesisContextPolicy,
    group_chunks_by_document,
)

_USER_IDX: Final[int] = 45


class _PerFileSearcher:
    def __init__(self, responses: dict[int, ChunkSearchResponse]) -> None:
        self._responses = responses
        self.calls: list[ChunkSearchRequest] = []

    async def search(self, request: ChunkSearchRequest) -> ChunkSearchResponse:
        self.calls.append(request)
        assert len(request.reference_file_idxs) == 1
        return self._responses[request.reference_file_idxs[0]]


class _ScriptedGenerationClient:
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
    chunk_id: str,
    file_idx: int,
    rag_document_idx: int,
    file_name: str,
    file_type: SupportedFileType,
    score: float,
    metadata: dict[str, object],
    content: str,
) -> ChunkSearchResult:
    locator = build_source_locator(
        file_type=file_type,
        source_metadata=metadata,
    )
    return ChunkSearchResult(
        chunk_id=chunk_id,
        score=score,
        rag_document_idx=rag_document_idx,
        file_idx=file_idx,
        folder_idx=9,
        file_name=file_name,
        file_type=file_type,
        chunk_index=0,
        content=content,
        token_count=64,
        page=locator.page,
        slide_no=locator.slide_no,
        sheet_name=locator.sheet_name,
        section_title=locator.section_title,
        source_locator=locator,
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


def test_group_chunks_by_document_accepts_mixed_formats_and_ocr() -> None:
    pdf = _chunk(
        chunk_id="11111111-1111-1111-1111-111111111111",
        file_idx=101,
        rag_document_idx=1001,
        file_name="정책.pdf",
        file_type=SupportedFileType.PDF,
        score=0.95,
        metadata={"page_number": 2},
        content="PDF 일반 텍스트",
    )
    xlsx_ocr = _chunk(
        chunk_id="22222222-2222-2222-2222-222222222222",
        file_idx=202,
        rag_document_idx=2002,
        file_name="현황.xlsx",
        file_type=SupportedFileType.XLSX,
        score=0.94,
        metadata={
            "sheet_name": "대시보드",
            "cell_range": "A1:F20",
            "content_origin": "ocr",
            "unit_type": "ocr_image",
            "image_index": 1,
            "image_id": "xlsx-chart-1",
            "image_kind": "xlsx_chart_render",
        },
        content="[이미지 OCR]\n차트에서 확인한 수치",
    )

    groups = group_chunks_by_document((pdf, xlsx_ocr))

    assert tuple(group.file_type for group in groups) == (
        SupportedFileType.PDF,
        SupportedFileType.XLSX,
    )
    assert groups[1].chunks[0].source_locator is not None
    assert groups[1].chunks[0].source_locator.content_origin.value == "ocr"


def test_context_policy_limits_each_document_and_total_context() -> None:
    chunks = tuple(
        _chunk(
            chunk_id=f"00000000-0000-0000-0000-{index:012d}",
            file_idx=file_idx,
            rag_document_idx=file_idx * 10,
            file_name=f"문서-{file_idx}.txt",
            file_type=SupportedFileType.TXT,
            score=0.99 - index * 0.01,
            metadata={"line_number": index + 1},
            content="가" * 80,
        )
        for index, file_idx in enumerate((1, 2, 1, 2))
    )
    groups = group_chunks_by_document(chunks)
    policy = SynthesisContextPolicy(
        max_chunks_per_document=1,
        max_total_context_chars=100,
        max_chunk_chars=80,
    )

    limited = policy.apply(groups)

    assert sum(len(group.chunks) for group in limited) == 2
    assert sum(
        len(chunk.content)
        for group in limited
        for chunk in group.chunks
    ) <= 100


@pytest.mark.asyncio
async def test_mixed_pdf_and_xlsx_synthesis_keeps_ocr_locator_and_citations() -> None:
    pdf = _chunk(
        chunk_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        file_idx=101,
        rag_document_idx=1001,
        file_name="정책.pdf",
        file_type=SupportedFileType.PDF,
        score=0.95,
        metadata={"page_number": 3},
        content="정책 문서는 목표를 설명한다.",
    )
    xlsx = _chunk(
        chunk_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        file_idx=202,
        rag_document_idx=2002,
        file_name="성과.xlsx",
        file_type=SupportedFileType.XLSX,
        score=0.93,
        metadata={
            "sheet_name": "성과",
            "cell_range": "B2:E10",
            "content_origin": "ocr",
            "unit_type": "ocr_image",
            "image_index": 2,
            "image_id": "chart-2",
            "image_kind": "xlsx_chart_render",
        },
        content="[이미지 OCR]\n성과 차트는 증가 추세를 보인다.",
    )
    searcher = _PerFileSearcher(
        {
            101: ChunkSearchResponse(user_idx=_USER_IDX, result_count=1, results=(pdf,)),
            202: ChunkSearchResponse(user_idx=_USER_IDX, result_count=1, results=(xlsx,)),
        }
    )
    generator = _ScriptedGenerationClient(
        (
            "정책 목표가 제시되어 있습니다. [SOURCE-1]",
            "성과 차트는 증가 추세입니다. [SOURCE-1]",
            "정책 목표와 증가 성과가 함께 확인됩니다. [SOURCE-1][SOURCE-2]",
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
            reference_file_idxs=(101, 202),
            query="두 문서를 종합해서 목표와 성과를 설명해줘",
            top_k=5,
            score_threshold=0.6,
        )
    )

    assert response.status is RagAnswerStatus.ANSWERED
    assert tuple(source.file_type for source in response.sources) == (
        SupportedFileType.PDF,
        SupportedFileType.XLSX,
    )
    assert response.sources[1].source_locator is not None
    assert response.sources[1].source_locator.image_id == "chart-2"
    assert len(generator.calls) == 3


class _SingleResponseSearcher:
    def __init__(self, response: ChunkSearchResponse) -> None:
        self._response = response
        self.calls: list[ChunkSearchRequest] = []

    async def search(self, request: ChunkSearchRequest) -> ChunkSearchResponse:
        self.calls.append(request)
        return self._response


@pytest.mark.parametrize(
    ("file_type", "file_name", "metadata"),
    [
        (SupportedFileType.PDF, "문서.pdf", {"page_number": 1}),
        (
            SupportedFileType.DOCX,
            "문서.docx",
            {"section_index": 0, "block_index": 1},
        ),
        (
            SupportedFileType.PPTX,
            "문서.pptx",
            {"slide_number": 2, "shape_path": "shape:1"},
        ),
        (
            SupportedFileType.TXT,
            "문서.txt",
            {"line_number": 3, "source_char_start": 20, "source_char_end": 60},
        ),
        (
            SupportedFileType.XLSX,
            "문서.xlsx",
            {"sheet_name": "Sheet1", "cell_range": "A1:C5"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_lookup_works_for_every_supported_file_type(
    file_type: SupportedFileType,
    file_name: str,
    metadata: dict[str, object],
) -> None:
    chunk = _chunk(
        chunk_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        file_idx=303,
        rag_document_idx=3003,
        file_name=file_name,
        file_type=file_type,
        score=0.9,
        metadata=metadata,
        content="조회형 질문의 직접 근거입니다.",
    )
    searcher = _SingleResponseSearcher(
        ChunkSearchResponse(
            user_idx=_USER_IDX,
            result_count=1,
            results=(chunk,),
        )
    )
    generator = _ScriptedGenerationClient(("직접 근거입니다. [SOURCE-1]",))
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generator,
    )

    response = await service.answer(
        RagAnswerRequest(
            user_idx=_USER_IDX,
            reference_file_idxs=(303,),
            query="직접 근거를 알려줘",
            top_k=5,
        )
    )

    assert response.status is RagAnswerStatus.ANSWERED
    assert response.sources[0].file_type is file_type
    assert len(searcher.calls) == 1
    assert len(generator.calls) == 1


class _PartiallyFailingSearcher:
    def __init__(
        self,
        *,
        failed_file_idx: int,
        valid_response: ChunkSearchResponse,
    ) -> None:
        self._failed_file_idx = failed_file_idx
        self._valid_response = valid_response
        self.calls: list[ChunkSearchRequest] = []

    async def search(self, request: ChunkSearchRequest) -> ChunkSearchResponse:
        self.calls.append(request)
        file_idx = request.reference_file_idxs[0]
        if file_idx == self._failed_file_idx:
            raise VectorDatabaseUnavailableError("search_chunks")
        return self._valid_response


@pytest.mark.asyncio
async def test_synthesis_continues_when_one_document_search_fails() -> None:
    valid_chunk = _chunk(
        chunk_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        file_idx=202,
        rag_document_idx=2002,
        file_name="유효.docx",
        file_type=SupportedFileType.DOCX,
        score=0.92,
        metadata={"section_index": 1, "block_index": 2},
        content="유효한 나머지 문서의 부분 근거입니다.",
    )
    searcher = _PartiallyFailingSearcher(
        failed_file_idx=101,
        valid_response=ChunkSearchResponse(
            user_idx=_USER_IDX,
            result_count=1,
            results=(valid_chunk,),
        ),
    )
    generator = _ScriptedGenerationClient(
        (
            "유효한 문서 근거입니다. [SOURCE-1]",
            "검색에 성공한 문서에서 근거를 확인했습니다. [SOURCE-1]",
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
            reference_file_idxs=(101, 202),
            query="두 문서를 종합해서 알려줘",
            top_k=5,
        )
    )

    assert response.status is RagAnswerStatus.ANSWERED
    assert tuple(source.file_idx for source in response.sources) == (202,)
    assert len(searcher.calls) == 2
    assert len(generator.calls) == 2
