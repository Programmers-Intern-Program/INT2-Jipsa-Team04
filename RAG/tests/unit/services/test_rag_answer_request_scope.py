"""질문별 참조문서 스냅샷과 빈 범위 방어 계약을 테스트한다."""

import asyncio

import pytest
from pydantic import ValidationError

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
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.rag_answer import RagAnswerService

_TEST_USER_IDX = 45


class _ScopeAwareChunkSearcher:
    """첫 질문을 일시 정지하고 각 호출의 참조문서 범위로
    결과를 생성한다.
    """

    def __init__(self) -> None:
        """질문 처리 동기화 이벤트와 검색 요청 기록을 초기화한다."""

        self.first_call_started = asyncio.Event()
        self.release_first_call = asyncio.Event()
        self.requests: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """첫 검색만 대기시켜 처리 중 선택 상태 변경을 재현한다."""

        call_index = len(self.requests)
        self.requests.append(request)

        if call_index == 0:
            self.first_call_started.set()
            await self.release_first_call.wait()

        file_idx = request.reference_file_idxs[0]

        return ChunkSearchResponse(
            user_idx=request.user_idx,
            result_count=1,
            results=(
                ChunkSearchResult(
                    chunk_id=(f"{file_idx:08d}-1111-1111-1111-111111111111"),
                    score=0.92,
                    rag_document_idx=100,
                    file_idx=file_idx,
                    folder_idx=9,
                    file_name=f"참조문서-{file_idx}.pdf",
                    file_type=SupportedFileType.PDF,
                    chunk_index=0,
                    content=f"참조문서 {file_idx}의 근거 내용입니다.",
                    token_count=64,
                    page=2,
                    slide_no=None,
                    sheet_name=None,
                    section_title="참조문서 범위 테스트",
                    parser_version="1.0.0",
                    embedding_model="test/embedding-model",
                    index_version=2,
                ),
            ),
        )


class _StubGenerationClient:
    """실제 Claude API 호출 없이 결정적인 답변을 반환한다."""

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """프롬프트 구성 완료 여부만 검증하고
        고정 생성 결과를 반환한다.
        """

        assert request.system_prompt is not None
        assert request.user_prompt

        return GenerationResult(
            text="선택된 참조문서에 근거한 답변입니다. [SOURCE-1]",
            model="claude-sonnet-5",
            usage=GenerationUsage(
                input_tokens=100,
                output_tokens=20,
            ),
            stop_reason="end_turn",
        )


def _create_answer_request(
    *,
    reference_file_idxs: tuple[int, ...],
    query: str,
) -> RagAnswerRequest:
    """참조문서 범위 테스트에 사용할 유효한 답변 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=reference_file_idxs,
        query=query,
        top_k=3,
        score_threshold=0.7,
    )


@pytest.mark.asyncio
async def test_answer_keeps_in_flight_scope_and_applies_changes_to_next_question() -> None:
    """처리 중 선택 변경은 현재 질문이 아니라
    이후 질문부터 적용해야 한다.
    """

    searcher = _ScopeAwareChunkSearcher()
    service = RagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=_StubGenerationClient(),
    )

    request = _create_answer_request(
        reference_file_idxs=(123,),
        query="첫 번째 질문",
    )

    # 첫 번째 answer()는 메서드 시작 시 요청을 깊은 복사한 뒤
    # 검색 대역의 Event에서 대기한다.
    first_answer_task = asyncio.create_task(service.answer(request))

    await asyncio.wait_for(
        searcher.first_call_started.wait(),
        timeout=1.0,
    )

    # 첫 질문 처리 중 참조문서 456을 추가한 상황을 재현한다.
    # 이미 시작된 첫 질문은 (123,) 범위를 계속 사용해야 한다.
    request.reference_file_idxs = (
        123,
        456,
    )
    request.query = "참조문서를 추가한 두 번째 질문"

    searcher.release_first_call.set()
    first_response = await first_answer_task

    # 다음 answer() 호출부터 추가된 참조문서 목록이 적용된다.
    second_response = await service.answer(request)

    # 이후 123을 일부 해제한 다음 질문은 (456,)만 사용해야 한다.
    request.reference_file_idxs = (456,)
    request.query = "일부 참조문서를 해제한 세 번째 질문"

    third_response = await service.answer(request)

    assert tuple(search_request.reference_file_idxs for search_request in searcher.requests) == (
        (123,),
        (123, 456),
        (456,),
    )

    # 첫 질문의 출처는 처리 중 선택 변경과 무관하게
    # 최초 범위를 유지한다.
    assert first_response.sources[0].file_idx == 123

    # 두 번째와 세 번째 질문은 각각 호출 시점의
    # 새 범위를 독립적으로 사용한다.
    assert second_response.sources[0].file_idx == 123
    assert third_response.sources[0].file_idx == 456


def test_answer_and_chunk_requests_reject_empty_reference_scope() -> None:
    """빈 참조문서 범위가 전체 문서 검색 의미로
    전달되는 것을 차단해야 한다.
    """

    with pytest.raises(ValidationError):
        _create_answer_request(
            reference_file_idxs=(),
            query="참조문서를 모두 해제한 질문",
        )

    with pytest.raises(ValidationError):
        ChunkSearchRequest(
            user_idx=_TEST_USER_IDX,
            reference_file_idxs=(),
            query="참조문서를 모두 해제한 검색",
            top_k=3,
            score_threshold=0.7,
        )
