"""문서별 부분 실패와 최종 Claude 호출 생략 계약을 검증한다."""

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
from jipsa_rag.services.query_routing import RoutedRagAnswerService

_USER_IDX: Final[int] = 45


class _MixedOutcomeSearcher:
    """파싱 실패 대체 빈 결과, 검색 실패 및 정상 결과를 파일별로 재현한다."""

    def __init__(
        self,
        outcomes: dict[int, ChunkSearchResponse | Exception],
    ) -> None:
        self._outcomes = outcomes
        self.calls: list[ChunkSearchRequest] = []

    async def search(self, request: ChunkSearchRequest) -> ChunkSearchResponse:
        self.calls.append(request)
        file_idx = request.reference_file_idxs[0]
        outcome = self._outcomes[file_idx]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


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
            usage=GenerationUsage(input_tokens=100, output_tokens=20),
            stop_reason="end_turn",
        )


def _empty_response() -> ChunkSearchResponse:
    return ChunkSearchResponse(
        user_idx=_USER_IDX,
        result_count=0,
        results=(),
    )


def _valid_docx_response(*, file_idx: int) -> ChunkSearchResponse:
    locator = build_source_locator(
        file_type=SupportedFileType.DOCX,
        source_metadata={
            "section_index": 1,
            "block_index": 3,
            "paragraph_index": 2,
            "section_title": "유효 문서",
        },
    )
    chunk = ChunkSearchResult(
        chunk_id=f"00000000-0000-0000-0000-{file_idx:012d}",
        score=0.94,
        rag_document_idx=file_idx * 10,
        file_idx=file_idx,
        folder_idx=9,
        file_name="유효.docx",
        file_type=SupportedFileType.DOCX,
        chunk_index=0,
        content="파싱과 색인에 성공한 문서의 유효한 근거입니다.",
        token_count=32,
        section_title=locator.section_title,
        source_locator=locator,
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )
    return ChunkSearchResponse(
        user_idx=_USER_IDX,
        result_count=1,
        results=(chunk,),
    )


@pytest.mark.asyncio
async def test_empty_or_failed_documents_do_not_discard_valid_document() -> None:
    """파싱 실패로 미색인된 문서와 검색 실패가 있어도 정상 문서로 계속한다."""

    searcher = _MixedOutcomeSearcher(
        {
            # 파싱·색인 실패 문서는 활성 Qdrant point가 없어 빈 검색 결과가 된다.
            101: _empty_response(),
            102: VectorDatabaseUnavailableError("search_chunks"),
            103: _valid_docx_response(file_idx=103),
        }
    )
    generator = _ScriptedGenerationClient(
        (
            "정상 문서의 부분 근거입니다. [SOURCE-1]",
            "사용 가능한 문서에서 답을 확인했습니다. [SOURCE-1]",
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
            reference_file_idxs=(101, 102, 103),
            query="세 문서를 종합해서 알려줘",
            top_k=5,
        )
    )

    assert response.status is RagAnswerStatus.ANSWERED
    assert response.cited_source_ids == ("SOURCE-1",)
    assert tuple(source.file_idx for source in response.sources) == (103,)
    assert len(searcher.calls) == 3
    assert len(generator.calls) == 2


@pytest.mark.asyncio
async def test_all_documents_without_evidence_skip_every_claude_call() -> None:
    """검색 가능한 문서가 하나도 없으면 부분·최종 Claude 호출을 모두 생략한다."""

    searcher = _MixedOutcomeSearcher(
        {
            101: _empty_response(),
            102: VectorDatabaseUnavailableError("search_chunks"),
        }
    )
    generator = _ScriptedGenerationClient(())
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generator,
    )

    response = await service.answer(
        RagAnswerRequest(
            user_idx=_USER_IDX,
            reference_file_idxs=(101, 102),
            query="두 문서를 종합해서 알려줘",
            top_k=5,
        )
    )

    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.cited_source_ids == ()
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert len(generator.calls) == 0


@pytest.mark.asyncio
async def test_no_valid_partial_answer_skips_final_claude_call() -> None:
    """검색 근거가 있어도 부분 답변이 전부 근거 부족이면 최종 호출을 생략한다."""

    searcher = _MixedOutcomeSearcher(
        {
            101: _empty_response(),
            103: _valid_docx_response(file_idx=103),
        }
    )
    generator = _ScriptedGenerationClient(("제공된 문서 근거만으로는 답변할 수 없습니다.",))
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generator,
    )

    response = await service.answer(
        RagAnswerRequest(
            user_idx=_USER_IDX,
            reference_file_idxs=(101, 103),
            query="두 문서를 종합해서 알려줘",
            top_k=5,
        )
    )

    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert len(generator.calls) == 1
