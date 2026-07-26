"""RAG 질의 분류, 전략 라우팅 및 PDF 그룹화 계약을 테스트한다."""

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
from jipsa_rag.schemas.rag_answer import RagAnswerRequest
from jipsa_rag.services.prompt_builder import (
    RagPromptBuilder,
    RagPromptBuildResult,
)
from jipsa_rag.services.query_routing import (
    RagQueryStrategyRouter,
    RagQueryType,
    RoutedChunkSearcher,
    RoutedRagAnswerService,
    RuleBasedRagQueryClassifier,
    group_chunks_by_pdf,
)

_TEST_USER_IDX: Final[int] = 45
_TEST_REFERENCE_FILE_IDXS: Final[tuple[int, ...]] = (
    123,
    456,
)


class _StubChunkSearcher:
    """준비된 검색 응답을 반환하고 요청을 기록하는 테스트 대역."""

    def __init__(
        self,
        response: ChunkSearchResponse,
    ) -> None:
        """검색 응답과 호출 기록을 초기화한다."""

        self._response = response
        self.calls: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """요청을 기록하고 네트워크 호출 없이 응답한다."""

        self.calls.append(
            request,
        )

        return self._response


class _RecordingPromptBuilder:
    """실제 프롬프트 구성 결과와 전달된 청크 순서를 함께 기록한다."""

    def __init__(self) -> None:
        """실제 구성기와 호출 기록을 초기화한다."""

        self._delegate = RagPromptBuilder()
        self.calls: list[tuple[ChunkSearchResult, ...]] = []

    def build(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """청크 순서를 기록한 뒤 실제 프롬프트 구성기로 위임한다."""

        self.calls.append(
            chunks,
        )

        return self._delegate.build(
            request=request,
            chunks=chunks,
        )


class _StubGenerationClient:
    """고정된 SOURCE-1 답변을 반환하는 생성 테스트 대역."""

    def __init__(self) -> None:
        """생성 요청 호출 기록을 초기화한다."""

        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """요청을 기록하고 결정적인 생성 결과를 반환한다."""

        self.calls.append(
            request,
        )

        return GenerationResult(
            text="선택한 PDF 근거를 확인했습니다. [SOURCE-1]",
            model="claude-sonnet-5",
            usage=GenerationUsage(
                input_tokens=120,
                output_tokens=30,
            ),
            stop_reason="end_turn",
        )


def _create_search_request(
    *,
    query: str,
    reference_file_idxs: tuple[int, ...] = _TEST_REFERENCE_FILE_IDXS,
) -> ChunkSearchRequest:
    """질의 분류 테스트용 검색 요청을 생성한다."""

    return ChunkSearchRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=reference_file_idxs,
        query=query,
        top_k=5,
        score_threshold=0.6,
    )


def _create_answer_request(
    *,
    query: str,
) -> RagAnswerRequest:
    """라우팅된 답변 서비스 테스트용 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=_TEST_REFERENCE_FILE_IDXS,
        query=query,
        top_k=5,
        score_threshold=0.6,
    )


def _create_chunk(
    *,
    chunk_id: str,
    file_idx: int,
    rag_document_idx: int,
    file_name: str,
    chunk_index: int,
    score: float,
) -> ChunkSearchResult:
    """PDF 그룹화 테스트에 사용할 유효한 검색 청크를 생성한다."""

    return ChunkSearchResult(
        chunk_id=chunk_id,
        score=score,
        rag_document_idx=rag_document_idx,
        file_idx=file_idx,
        folder_idx=9,
        file_name=file_name,
        file_type=SupportedFileType.PDF,
        chunk_index=chunk_index,
        content=f"{file_name}의 {chunk_index}번 근거 청크입니다.",
        token_count=128,
        page=chunk_index + 1,
        slide_no=None,
        sheet_name=None,
        section_title="테스트 섹션",
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


def _create_interleaved_chunks() -> tuple[ChunkSearchResult, ...]:
    """전역 관련도 순서에서 두 PDF가 교차한 검색 결과를 생성한다."""

    first_pdf_first_chunk = _create_chunk(
        chunk_id="11111111-1111-1111-1111-111111111111",
        file_idx=123,
        rag_document_idx=100,
        file_name="첫 번째.pdf",
        chunk_index=0,
        score=0.95,
    )
    second_pdf_first_chunk = _create_chunk(
        chunk_id="22222222-2222-2222-2222-222222222222",
        file_idx=456,
        rag_document_idx=200,
        file_name="두 번째.pdf",
        chunk_index=0,
        score=0.94,
    )
    first_pdf_second_chunk = _create_chunk(
        chunk_id="33333333-3333-3333-3333-333333333333",
        file_idx=123,
        rag_document_idx=100,
        file_name="첫 번째.pdf",
        chunk_index=1,
        score=0.93,
    )

    return (
        first_pdf_first_chunk,
        second_pdf_first_chunk,
        first_pdf_second_chunk,
    )


def _create_search_response(
    chunks: tuple[ChunkSearchResult, ...],
) -> ChunkSearchResponse:
    """전달받은 청크로 검색 응답을 생성한다."""

    return ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=len(chunks),
        results=chunks,
    )


def test_rag_query_type_uses_stable_external_values() -> None:
    """질의 유형 문자열은 lookup과 synthesis로 고정되어야 한다."""

    assert RagQueryType.LOOKUP.value == "lookup"
    assert RagQueryType.SYNTHESIS.value == "synthesis"


@pytest.mark.parametrize(
    "query",
    [
        "두 PDF를 비교해서 차이점을 알려줘",
        "각 문서별 핵심 내용을 정리해줘",
        "전체 문서를 종합해서 결론을 알려줘",
        "Compare the two documents and explain the differences.",
        "Summarize all PDF files.",
    ],
)
def test_classifier_detects_explicit_multi_document_synthesis(
    query: str,
) -> None:
    """두 개 이상 문서와 명시적 종합 표현이 있으면 synthesis여야 한다."""

    classifier = RuleBasedRagQueryClassifier()

    query_type = classifier.classify(
        _create_search_request(
            query=query,
        )
    )

    assert query_type is RagQueryType.SYNTHESIS


def test_classifier_keeps_plain_multi_document_question_as_lookup() -> None:
    """여러 문서를 선택해도 단일 사실 조회 질문은 lookup이어야 한다."""

    classifier = RuleBasedRagQueryClassifier()

    query_type = classifier.classify(
        _create_search_request(
            query="프로젝트의 로컬 실행 절차를 알려줘",
        )
    )

    assert query_type is RagQueryType.LOOKUP


def test_classifier_keeps_single_document_comparison_as_lookup() -> None:
    """참조문서가 한 개면 비교 표현이 있어도 기존 lookup을 유지해야 한다."""

    classifier = RuleBasedRagQueryClassifier()

    query_type = classifier.classify(
        _create_search_request(
            query="내용의 장점과 단점을 비교해줘",
            reference_file_idxs=(123,),
        )
    )

    assert query_type is RagQueryType.LOOKUP


def test_group_chunks_by_pdf_preserves_group_and_internal_order() -> None:
    """PDF 최초 등장 순서와 각 PDF 내부 청크 순서를 보존해야 한다."""

    chunks = _create_interleaved_chunks()

    groups = group_chunks_by_pdf(
        chunks,
    )

    assert tuple(group.file_idx for group in groups) == (
        123,
        456,
    )
    assert tuple(chunk.chunk_id for chunk in groups[0].chunks) == (
        chunks[0].chunk_id,
        chunks[2].chunk_id,
    )
    assert tuple(chunk.chunk_id for chunk in groups[1].chunks) == (chunks[1].chunk_id,)


def test_group_chunks_by_pdf_rejects_conflicting_document_metadata() -> None:
    """같은 file_idx에 서로 다른 활성 문서 메타데이터가 섞이면 거부해야 한다."""

    chunks = _create_interleaved_chunks()
    conflicting_chunk = chunks[2].model_copy(
        update={
            "rag_document_idx": 999,
        },
    )

    with pytest.raises(
        ValueError,
        match="document metadata",
    ):
        group_chunks_by_pdf(
            (
                chunks[0],
                conflicting_chunk,
            )
        )


def test_lookup_router_preserves_original_tuple_and_order() -> None:
    """lookup 전략은 기존 검색 결과 tuple을 그대로 전달해야 한다."""

    chunks = _create_interleaved_chunks()
    router = RagQueryStrategyRouter()

    plan = router.route(
        query_type=RagQueryType.LOOKUP,
        chunks=chunks,
    )

    assert plan.query_type is RagQueryType.LOOKUP
    assert plan.prompt_chunks is chunks
    assert plan.pdf_groups == ()


def test_synthesis_router_groups_and_flattens_chunks_by_pdf() -> None:
    """synthesis 전략은 PDF별 그룹을 연속된 프롬프트 순서로 변환해야 한다."""

    chunks = _create_interleaved_chunks()
    router = RagQueryStrategyRouter()

    plan = router.route(
        query_type=RagQueryType.SYNTHESIS,
        chunks=chunks,
    )

    assert tuple(group.file_idx for group in plan.pdf_groups) == (
        123,
        456,
    )
    assert tuple(chunk.chunk_id for chunk in plan.prompt_chunks) == (
        chunks[0].chunk_id,
        chunks[2].chunk_id,
        chunks[1].chunk_id,
    )


@pytest.mark.asyncio
async def test_routed_chunk_searcher_returns_original_lookup_response() -> None:
    """lookup은 기존 ChunkSearchResponse 객체까지 그대로 유지해야 한다."""

    chunks = _create_interleaved_chunks()
    original_response = _create_search_response(
        chunks,
    )
    delegate = _StubChunkSearcher(
        original_response,
    )
    searcher = RoutedChunkSearcher(
        delegate=delegate,
    )

    response = await searcher.search(
        _create_search_request(
            query="프로젝트의 로컬 실행 절차를 알려줘",
        )
    )

    assert response is original_response
    assert response.results is original_response.results


@pytest.mark.asyncio
async def test_routed_rag_answer_service_applies_pdf_grouping_only_to_synthesis() -> None:
    """답변 서비스는 synthesis 질문에만 PDF 그룹 순서를 적용해야 한다."""

    chunks = _create_interleaved_chunks()
    original_response = _create_search_response(
        chunks,
    )
    searcher = _StubChunkSearcher(
        original_response,
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _StubGenerationClient()
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )

    response = await service.answer(
        _create_answer_request(
            query="두 PDF를 비교해서 차이점을 알려줘",
        )
    )

    assert tuple(chunk.chunk_id for chunk in prompt_builder.calls[0]) == (
        chunks[0].chunk_id,
        chunks[2].chunk_id,
        chunks[1].chunk_id,
    )
    assert response.sources[0].chunk_id == chunks[0].chunk_id
    assert len(generation_client.calls) == 1


@pytest.mark.asyncio
async def test_routed_rag_answer_service_keeps_lookup_prompt_order() -> None:
    """답변 서비스의 lookup 질문은 기존 전역 관련도 순서를 유지해야 한다."""

    chunks = _create_interleaved_chunks()
    original_response = _create_search_response(
        chunks,
    )
    searcher = _StubChunkSearcher(
        original_response,
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _StubGenerationClient()
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )

    response = await service.answer(
        _create_answer_request(
            query="프로젝트의 로컬 실행 절차를 알려줘",
        )
    )

    assert prompt_builder.calls[0] is original_response.results
    assert tuple(chunk.chunk_id for chunk in prompt_builder.calls[0]) == (
        chunks[0].chunk_id,
        chunks[1].chunk_id,
        chunks[2].chunk_id,
    )
    assert response.sources[0].chunk_id == chunks[0].chunk_id
    assert len(generation_client.calls) == 1
