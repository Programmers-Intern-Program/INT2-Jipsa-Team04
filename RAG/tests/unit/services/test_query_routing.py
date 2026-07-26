"""RAG 질의 분류, PDF별 부분 생성 및 최종 종합 계약을 테스트한다."""

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
    SynthesisContextPolicy,
    group_chunks_by_pdf,
)
from jipsa_rag.services.rag_answer import RagAnswerServiceError

_TEST_USER_IDX: Final[int] = 45
_TEST_REFERENCE_FILE_IDXS: Final[tuple[int, ...]] = (
    123,
    456,
)


class _StubChunkSearcher:
    """준비된 검색 응답 하나를 반환하고 요청을 기록하는 테스트 대역."""

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


class _PerFileChunkSearcher:
    """단일 PDF 검색 요청별로 준비된 검색 응답을 반환한다."""

    def __init__(
        self,
        responses_by_file_idx: dict[int, ChunkSearchResponse],
    ) -> None:
        """PDF별 응답과 호출 기록을 초기화한다."""

        self._responses_by_file_idx = responses_by_file_idx
        self.calls: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """현재 단일 PDF 범위에 대응하는 검색 응답을 반환한다."""

        self.calls.append(
            request,
        )

        if len(request.reference_file_idxs) != 1:
            raise AssertionError("Synthesis search must isolate one PDF per request.")

        file_idx = request.reference_file_idxs[0]

        return self._responses_by_file_idx[file_idx]


class _RecordingPromptBuilder:
    """실제 프롬프트 구성 결과와 전달된 청크 그룹을 함께 기록한다."""

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
        """청크 그룹을 기록한 뒤 실제 프롬프트 구성기로 위임한다."""

        self.calls.append(
            chunks,
        )

        return self._delegate.build(
            request=request,
            chunks=chunks,
        )


class _ScriptedGenerationClient:
    """호출 순서에 따라 PDF 부분 결과와 최종 결과를 반환한다."""

    def __init__(
        self,
        results: tuple[GenerationResult, ...],
    ) -> None:
        """생성 결과 시나리오와 호출 기록을 초기화한다."""

        self._results = results
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """생성 요청을 기록하고 같은 순번의 준비된 결과를 반환한다."""

        call_index = len(self.calls)
        self.calls.append(
            request,
        )

        if call_index >= len(self._results):
            raise AssertionError("Generation client received an unexpected extra call.")

        return self._results[call_index]


class _UnexpectedGenerationClient:
    """범위 검증 또는 근거 부족 경로에서 호출되면 테스트를 실패시킨다."""

    def __init__(self) -> None:
        """호출 횟수를 초기화한다."""

        self.call_count = 0

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """호출되면 Claude 호출 차단 계약 위반으로 처리한다."""

        self.call_count += 1

        raise AssertionError("Generation must not run after a scope or evidence failure.")


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
    reference_file_idxs: tuple[int, ...] = _TEST_REFERENCE_FILE_IDXS,
    top_k: int = 5,
) -> RagAnswerRequest:
    """라우팅된 답변 서비스 테스트용 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=reference_file_idxs,
        query=query,
        top_k=top_k,
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
    content: str | None = None,
) -> ChunkSearchResult:
    """PDF 그룹화와 종합 답변 테스트에 사용할 검색 청크를 생성한다."""

    return ChunkSearchResult(
        chunk_id=chunk_id,
        score=score,
        rag_document_idx=rag_document_idx,
        file_idx=file_idx,
        folder_idx=9,
        file_name=file_name,
        file_type=SupportedFileType.PDF,
        chunk_index=chunk_index,
        content=(
            content
            if content is not None
            else f"{file_name}의 {chunk_index}번 원문 근거 청크입니다."
        ),
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
    *chunks: ChunkSearchResult,
) -> ChunkSearchResponse:
    """전달받은 청크로 검색 응답을 생성한다."""

    return ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=len(chunks),
        results=chunks,
    )


def _create_generation_result(
    *,
    text: str,
    input_tokens: int,
    output_tokens: int,
) -> GenerationResult:
    """실제 Claude 호출 대신 사용할 결정적인 생성 결과를 생성한다."""

    return GenerationResult(
        text=text,
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        stop_reason="end_turn",
    )


def _create_per_file_searcher(
    chunks: tuple[ChunkSearchResult, ...],
) -> _PerFileChunkSearcher:
    """두 PDF 청크를 각 단일 PDF 검색 응답으로 분리한다."""

    responses_by_file_idx = {
        file_idx: _create_search_response(
            *tuple(chunk for chunk in chunks if chunk.file_idx == file_idx)
        )
        for file_idx in _TEST_REFERENCE_FILE_IDXS
    }

    return _PerFileChunkSearcher(
        responses_by_file_idx,
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


def test_synthesis_context_policy_limits_chunks_and_total_context() -> None:
    """PDF별 청크 수와 모든 PDF의 실제 원문 문자 합계를 함께 제한해야 한다."""

    chunks = (
        _create_chunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            file_idx=123,
            rag_document_idx=100,
            file_name="첫 번째.pdf",
            chunk_index=0,
            score=0.95,
            content="A" * 10,
        ),
        _create_chunk(
            chunk_id="22222222-2222-2222-2222-222222222222",
            file_idx=456,
            rag_document_idx=200,
            file_name="두 번째.pdf",
            chunk_index=0,
            score=0.94,
            content="B" * 10,
        ),
        _create_chunk(
            chunk_id="33333333-3333-3333-3333-333333333333",
            file_idx=123,
            rag_document_idx=100,
            file_name="첫 번째.pdf",
            chunk_index=1,
            score=0.93,
            content="C" * 10,
        ),
        _create_chunk(
            chunk_id="44444444-4444-4444-4444-444444444444",
            file_idx=456,
            rag_document_idx=200,
            file_name="두 번째.pdf",
            chunk_index=1,
            score=0.92,
            content="D" * 10,
        ),
    )
    groups = group_chunks_by_pdf(
        chunks,
    )
    policy = SynthesisContextPolicy(
        max_chunks_per_pdf=2,
        max_total_context_chars=25,
        max_chunk_chars=10,
    )

    limited_groups = policy.apply(
        groups,
    )

    assert tuple(len(group.chunks) for group in limited_groups) == (
        2,
        1,
    )
    assert sum(len(chunk.content) for group in limited_groups for chunk in group.chunks) == 25
    assert limited_groups[0].chunks[1].content.endswith("…")
    assert all(len(group.chunks) <= 2 for group in limited_groups)


def test_synthesis_context_policy_rejects_non_positive_limits() -> None:
    """종합 컨텍스트 제한은 모두 양수여야 한다."""

    with pytest.raises(
        ValueError,
        match="max_chunks_per_pdf",
    ):
        SynthesisContextPolicy(
            max_chunks_per_pdf=0,
        )

    with pytest.raises(
        ValueError,
        match="max_total_context_chars",
    ):
        SynthesisContextPolicy(
            max_total_context_chars=0,
        )

    with pytest.raises(
        ValueError,
        match="max_chunk_chars",
    ):
        SynthesisContextPolicy(
            max_chunk_chars=0,
        )


@pytest.mark.asyncio
async def test_routed_chunk_searcher_returns_original_lookup_response() -> None:
    """lookup은 기존 ChunkSearchResponse 객체까지 그대로 유지해야 한다."""

    chunks = _create_interleaved_chunks()
    original_response = _create_search_response(
        *chunks,
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
async def test_routed_chunk_searcher_groups_synthesis_results_by_pdf() -> None:
    """독립 검색 어댑터의 synthesis 결과는 기존 집합을 PDF별로 재배열해야 한다."""

    chunks = _create_interleaved_chunks()
    delegate = _StubChunkSearcher(
        _create_search_response(
            *chunks,
        )
    )
    searcher = RoutedChunkSearcher(
        delegate=delegate,
    )

    response = await searcher.search(
        _create_search_request(
            query="두 PDF를 비교해서 차이점을 알려줘",
        )
    )

    assert tuple(chunk.chunk_id for chunk in response.results) == (
        chunks[0].chunk_id,
        chunks[2].chunk_id,
        chunks[1].chunk_id,
    )


@pytest.mark.asyncio
async def test_routed_rag_answer_service_keeps_lookup_flow_unchanged() -> None:
    """lookup은 기존 단일 검색, 프롬프트 및 생성 흐름을 그대로 사용해야 한다."""

    chunks = _create_interleaved_chunks()
    original_response = _create_search_response(
        *chunks,
    )
    searcher = _StubChunkSearcher(
        original_response,
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _ScriptedGenerationClient(
        (
            _create_generation_result(
                text="첫 번째 PDF의 실행 절차를 확인했습니다. [SOURCE-1]",
                input_tokens=120,
                output_tokens=30,
            ),
        )
    )
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

    assert len(searcher.calls) == 1
    assert searcher.calls[0].reference_file_idxs == _TEST_REFERENCE_FILE_IDXS
    assert len(prompt_builder.calls) == 1
    assert prompt_builder.calls[0] is original_response.results
    assert len(generation_client.calls) == 1
    assert response.status is RagAnswerStatus.ANSWERED
    assert response.sources[0].chunk_id == chunks[0].chunk_id


@pytest.mark.asyncio
async def test_synthesis_limits_search_top_k_for_each_pdf() -> None:
    """종합 검색은 요청 top_k와 PDF별 최대 청크 수 중 작은 값을 사용해야 한다."""

    searcher = _PerFileChunkSearcher(
        {
            123: _create_search_response(),
            456: _create_search_response(),
        }
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _UnexpectedGenerationClient()
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
        synthesis_context_policy=SynthesisContextPolicy(
            max_chunks_per_pdf=2,
            max_total_context_chars=1_000,
            max_chunk_chars=500,
        ),
    )

    response = await service.answer(
        _create_answer_request(
            query="두 PDF를 비교해서 차이점을 알려줘",
            top_k=5,
        )
    )

    assert tuple(call.reference_file_idxs for call in searcher.calls) == (
        (123,),
        (456,),
    )
    assert tuple(call.top_k for call in searcher.calls) == (
        2,
        2,
    )
    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert prompt_builder.calls == []
    assert generation_client.call_count == 0


@pytest.mark.asyncio
async def test_synthesis_generates_pdf_partials_then_final_answer() -> None:
    """각 PDF 부분 답변을 먼저 생성하고 그 결과로 최종 종합 답변을 만들어야 한다."""

    chunks = _create_interleaved_chunks()
    searcher = _create_per_file_searcher(
        chunks,
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _ScriptedGenerationClient(
        (
            _create_generation_result(
                text="첫 번째 PDF는 로컬 실행 절차를 설명합니다. [SOURCE-1]",
                input_tokens=100,
                output_tokens=20,
            ),
            _create_generation_result(
                text="두 번째 PDF는 배포 절차를 설명합니다. [SOURCE-1]",
                input_tokens=110,
                output_tokens=21,
            ),
            _create_generation_result(
                text=(
                    "첫 번째 PDF는 로컬 실행, 두 번째 PDF는 배포 절차를 설명합니다. "
                    "[SOURCE-1][SOURCE-2]"
                ),
                input_tokens=130,
                output_tokens=30,
            ),
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
        synthesis_context_policy=SynthesisContextPolicy(
            max_chunks_per_pdf=2,
            max_total_context_chars=10_000,
            max_chunk_chars=2_000,
        ),
    )

    response = await service.answer(
        _create_answer_request(
            query="두 PDF를 비교해서 차이점을 알려줘",
        )
    )

    # 각 PDF를 별도의 검색 범위와 별도의 부분 프롬프트로 처리한다.
    assert tuple(call.reference_file_idxs for call in searcher.calls) == (
        (123,),
        (456,),
    )
    assert len(prompt_builder.calls) == 2
    assert {chunk.file_idx for chunk in prompt_builder.calls[0]} == {123}
    assert {chunk.file_idx for chunk in prompt_builder.calls[1]} == {456}

    # 두 번의 부분 호출 이후 세 번째 호출에서 최종 종합을 수행한다.
    assert len(generation_client.calls) == 3
    final_prompt = generation_client.calls[2].user_prompt

    assert "<partial_answers_json>" in final_prompt
    assert "첫 번째 PDF는 로컬 실행 절차를 설명합니다." in final_prompt
    assert "두 번째 PDF는 배포 절차를 설명합니다." in final_prompt

    # 최종 단계에는 원본 청크 content를 다시 전달하지 않는다.
    assert chunks[0].content not in final_prompt
    assert chunks[1].content not in final_prompt

    assert response.status is RagAnswerStatus.ANSWERED
    assert tuple(source.source_id for source in response.sources) == (
        "SOURCE-1",
        "SOURCE-2",
    )
    assert tuple(source.file_idx for source in response.sources) == (
        123,
        456,
    )

    # 기존 응답 계약을 유지하여 usage는 최종 Claude 호출 값이다.
    assert response.usage is not None
    assert response.usage.input_tokens == 130
    assert response.usage.output_tokens == 30


@pytest.mark.asyncio
async def test_synthesis_tracks_only_sources_used_by_partials_and_final() -> None:
    """부분 단계와 최종 단계에서 실제 인용한 출처만 최종 응답에 남겨야 한다."""

    chunks = _create_interleaved_chunks()
    searcher = _create_per_file_searcher(
        chunks,
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _ScriptedGenerationClient(
        (
            # 첫 PDF의 두 후보 중 두 번째 청크만 실제 사용한다.
            _create_generation_result(
                text="첫 번째 PDF의 두 번째 근거만 사용합니다. [SOURCE-2]",
                input_tokens=100,
                output_tokens=20,
            ),
            _create_generation_result(
                text="두 번째 PDF의 근거를 사용합니다. [SOURCE-1]",
                input_tokens=110,
                output_tokens=20,
            ),
            # 전역 SOURCE-2는 두 번째 PDF의 실제 부분 출처다.
            _create_generation_result(
                text="최종 결론은 두 번째 PDF 근거에 기반합니다. [SOURCE-2]",
                input_tokens=120,
                output_tokens=20,
            ),
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
        synthesis_context_policy=SynthesisContextPolicy(
            max_chunks_per_pdf=2,
            max_total_context_chars=10_000,
            max_chunk_chars=2_000,
        ),
    )

    response = await service.answer(
        _create_answer_request(
            query="두 PDF를 비교해서 차이점을 알려줘",
        )
    )

    final_prompt = generation_client.calls[2].user_prompt

    # 첫 PDF에서 사용하지 않은 SOURCE-1 후보 청크는 최종 후보에도 포함되지 않는다.
    assert chunks[0].chunk_id not in final_prompt
    assert chunks[2].chunk_id in final_prompt
    assert chunks[1].chunk_id in final_prompt

    # 최종 답변이 실제 사용한 두 번째 PDF 출처 하나만 외부로 반환한다.
    assert tuple(source.source_id for source in response.sources) == ("SOURCE-2",)
    assert tuple(source.chunk_id for source in response.sources) == (chunks[1].chunk_id,)
    assert tuple(source.file_idx for source in response.sources) == (456,)


@pytest.mark.asyncio
async def test_synthesis_rejects_unselected_pdf_chunk_before_generation() -> None:
    """검색기가 선택하지 않은 PDF 청크를 반환하면 부분 생성 전에 차단해야 한다."""

    out_of_scope_chunk = _create_chunk(
        chunk_id="99999999-9999-9999-9999-999999999999",
        file_idx=999,
        rag_document_idx=999,
        file_name="선택하지 않은.pdf",
        chunk_index=0,
        score=0.99,
    )
    selected_second_pdf_chunk = _create_chunk(
        chunk_id="22222222-2222-2222-2222-222222222222",
        file_idx=456,
        rag_document_idx=200,
        file_name="두 번째.pdf",
        chunk_index=0,
        score=0.94,
    )
    searcher = _PerFileChunkSearcher(
        {
            123: _create_search_response(
                out_of_scope_chunk,
            ),
            456: _create_search_response(
                selected_second_pdf_chunk,
            ),
        }
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _UnexpectedGenerationClient()
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )

    with pytest.raises(RagAnswerServiceError) as exc_info:
        await service.answer(
            _create_answer_request(
                query="두 PDF를 비교해서 차이점을 알려줘",
            )
        )

    assert exc_info.value.operation == ("search_reference_file_scope_contract_violation")
    assert len(searcher.calls) == 1
    assert prompt_builder.calls == []
    assert generation_client.call_count == 0


@pytest.mark.asyncio
async def test_synthesis_rejects_unknown_final_source_id() -> None:
    """최종 모델이 부분 결과 후보에 없는 SOURCE-N을 인용하면 응답을 차단해야 한다."""

    chunks = _create_interleaved_chunks()
    searcher = _create_per_file_searcher(
        chunks,
    )
    prompt_builder = _RecordingPromptBuilder()
    generation_client = _ScriptedGenerationClient(
        (
            _create_generation_result(
                text="첫 번째 PDF 근거입니다. [SOURCE-1]",
                input_tokens=100,
                output_tokens=20,
            ),
            _create_generation_result(
                text="두 번째 PDF 근거입니다. [SOURCE-1]",
                input_tokens=110,
                output_tokens=20,
            ),
            _create_generation_result(
                text="존재하지 않는 출처를 인용합니다. [SOURCE-999]",
                input_tokens=120,
                output_tokens=20,
            ),
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )

    with pytest.raises(RagAnswerServiceError) as exc_info:
        await service.answer(
            _create_answer_request(
                query="두 PDF를 비교해서 차이점을 알려줘",
            )
        )

    assert exc_info.value.operation == "answer_citation_validation_failed"
    assert len(generation_client.calls) == 3
