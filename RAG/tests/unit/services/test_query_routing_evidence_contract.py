"""다문서 종합의 일부·전체 근거 부족 및 로그 비노출 계약을 검증한다."""

import json
import logging
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
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerStatus,
)
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.query_routing import (
    RoutedRagAnswerService,
)

_TEST_USER_IDX: Final[int] = 45
_FIRST_FILE_IDX: Final[int] = 123
_SECOND_FILE_IDX: Final[int] = 456
_INSUFFICIENT_ANSWER: Final[str] = "제공된 문서 근거만으로는 답변할 수 없습니다."


class _PerFileChunkSearcher:
    """단일 PDF 검색 범위별로 결정적인 검색 결과를 반환한다."""

    def __init__(
        self,
        responses: dict[int, ChunkSearchResponse],
    ) -> None:
        """PDF별 응답과 호출 기록을 초기화한다."""

        self._responses = responses
        self.calls: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """현재 단일 PDF 범위에 대응하는 응답을 반환한다."""

        self.calls.append(
            request,
        )

        if (
            len(
                request.reference_file_idxs,
            )
            != 1
        ):
            raise AssertionError("Synthesis must search one PDF at a time.")

        return self._responses[request.reference_file_idxs[0]]


class _ScriptedGenerationClient:
    """부분 답변과 최종 답변을 정해진 호출 순서대로 반환한다."""

    def __init__(
        self,
        results: tuple[GenerationResult, ...],
    ) -> None:
        """결과 시나리오와 요청 기록을 초기화한다."""

        self._results = results
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """요청을 기록하고 같은 순번의 결과를 반환한다."""

        call_index = len(
            self.calls,
        )
        self.calls.append(
            request,
        )

        if call_index >= len(
            self._results,
        ):
            raise AssertionError("Unexpected final Claude generation call.")

        return self._results[call_index]


def _chunk(
    *,
    file_idx: int,
    chunk_id: str,
    content: str,
) -> ChunkSearchResult:
    """한 PDF에 속한 실제 검색 결과 형태의 청크를 만든다."""

    return ChunkSearchResult(
        chunk_id=chunk_id,
        score=0.91,
        rag_document_idx=file_idx + 1_000,
        file_idx=file_idx,
        folder_idx=9,
        file_name=f"문서-{file_idx}.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        content=content,
        token_count=32,
        page=1,
        slide_no=None,
        sheet_name=None,
        section_title="근거 섹션",
        parser_version="1.0.0",
        embedding_model=("Qwen/Qwen3-Embedding-0.6B"),
        index_version=2,
    )


def _search_response(
    *,
    file_idx: int,
    content: str,
) -> ChunkSearchResponse:
    """지정 PDF의 청크 하나를 포함한 검색 응답을 만든다."""

    chunk_id = (
        "11111111-1111-1111-1111-111111111111"
        if file_idx == _FIRST_FILE_IDX
        else "22222222-2222-2222-2222-222222222222"
    )
    chunk = _chunk(
        file_idx=file_idx,
        chunk_id=chunk_id,
        content=content,
    )

    return ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=1,
        results=(chunk,),
    )


def _answered_result(
    *,
    answer: str,
    cited_source_ids: tuple[str, ...],
) -> GenerationResult:
    """운영 Claude 구조화 출력과 같은 정상 답변 결과를 만든다."""

    structured_output: dict[
        str,
        object,
    ] = {
        "status": "answered",
        "answer": answer,
        "cited_source_ids": list(
            cited_source_ids,
        ),
    }

    return GenerationResult(
        text=json.dumps(
            structured_output,
            ensure_ascii=False,
        ),
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=100,
            output_tokens=20,
        ),
        stop_reason="end_turn",
        structured_output=structured_output,
    )


def _insufficient_result() -> GenerationResult:
    """운영 Claude 구조화 출력과 같은 근거 부족 결과를 만든다."""

    structured_output: dict[
        str,
        object,
    ] = {
        "status": "insufficient_evidence",
        "answer": _INSUFFICIENT_ANSWER,
        "cited_source_ids": [],
    }

    return GenerationResult(
        text=json.dumps(
            structured_output,
            ensure_ascii=False,
        ),
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=80,
            output_tokens=10,
        ),
        stop_reason="end_turn",
        structured_output=structured_output,
    )


def _request(
    *,
    query: str,
) -> RagAnswerRequest:
    """명시적인 다문서 종합 질문을 만든다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=(
            _FIRST_FILE_IDX,
            _SECOND_FILE_IDX,
        ),
        query=query,
        top_k=5,
        score_threshold=None,
    )


def _searcher(
    *,
    first_content: str,
    second_content: str,
) -> _PerFileChunkSearcher:
    """두 PDF 각각의 검색 결과를 반환하는 검색기를 만든다."""

    return _PerFileChunkSearcher(
        {
            _FIRST_FILE_IDX: _search_response(
                file_idx=_FIRST_FILE_IDX,
                content=first_content,
            ),
            _SECOND_FILE_IDX: _search_response(
                file_idx=_SECOND_FILE_IDX,
                content=second_content,
            ),
        }
    )


@pytest.mark.asyncio
async def test_synthesis_partial_prompt_preserves_each_pdf_supported_subset() -> None:
    """각 PDF가 전체 질문의 일부만 지원해도 부분 답변을 보존해야 한다."""

    searcher = _searcher(
        first_content=("첫 번째 문서는 exact recovery code가 RECOVERY-21이라고 명시합니다."),
        second_content=("두 번째 문서는 exact validation code가 VALIDATION-34라고 명시합니다."),
    )
    generation_client = _ScriptedGenerationClient(
        (
            _answered_result(
                answer=("복구 코드는 RECOVERY-21입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
            _answered_result(
                answer=("검증 코드는 VALIDATION-34입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
            _answered_result(
                answer=(
                    "복구 코드는 RECOVERY-21입니다. "
                    "[SOURCE-1] "
                    "검증 코드는 VALIDATION-34입니다. "
                    "[SOURCE-2]"
                ),
                cited_source_ids=(
                    "SOURCE-1",
                    "SOURCE-2",
                ),
            ),
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    # 기본 질의 분류기는 여러 문서가 선택되었다는 사실만으로 synthesis를
    # 선택하지 않고, 비교·종합 등 명시적인 다문서 종합 의도를 요구한다.
    #
    # 이 테스트의 검증 대상은 분류기 표현 범위가 아니라 PDF별 부분 생성
    # 프롬프트와 부분 근거 보존 계약이다. 따라서 질문에 "종합"을 명시하여
    # lookup 경로로 우회하지 않고 의도한 synthesis 경로를 실행하게 한다.
    response = await service.answer(
        _request(
            query=(
                "두 PDF를 종합하여 exact recovery code와 exact validation code를 각각 답해 주세요."
            ),
        )
    )

    assert (
        len(
            generation_client.calls,
        )
        == 3
    )

    # 첫 두 호출은 한 PDF만 보는 부분 생성 단계다. 전체 질문의 모든
    # 하위 항목을 한 PDF가 지원해야 한다고 오해하지 않도록 전용 시스템
    # 규칙과 사용자 프롬프트 표식을 모두 포함해야 한다.
    for partial_request in generation_client.calls[:2]:
        assert partial_request.system_prompt is not None
        assert "PDF별 부분 근거 추출 단계" in partial_request.system_prompt
        assert "하위 항목 하나 이상" in partial_request.system_prompt
        assert "<partial_synthesis_stage>" in partial_request.user_prompt
        assert (
            "다른 PDF가 필요한 나머지 항목 때문에 전체를 insufficient_evidence로 처리하지 마세요."
        ) in partial_request.user_prompt

    # 세 번째 호출은 검증된 부분 결과만 사용하는 최종 종합 단계다.
    final_request = generation_client.calls[2]

    assert "<partial_answers_json>" in final_request.user_prompt
    assert "<partial_synthesis_stage>" not in final_request.user_prompt
    assert "RECOVERY-21" in final_request.user_prompt
    assert "VALIDATION-34" in final_request.user_prompt

    assert response.status is RagAnswerStatus.ANSWERED
    assert tuple(source.file_idx for source in response.sources) == (
        _FIRST_FILE_IDX,
        _SECOND_FILE_IDX,
    )
    assert tuple(source.source_id for source in response.sources) == (
        "SOURCE-1",
        "SOURCE-2",
    )


@pytest.mark.asyncio
async def test_synthesis_continues_with_supported_pdf_when_one_partial_is_insufficient() -> None:
    """일부 PDF만 근거가 있으면 해당 부분만 최종 종합에 사용한다."""

    searcher = _searcher(
        first_content=("첫 번째 문서는 RECOVERY-21 값을 명시합니다."),
        second_content=("두 번째 문서에는 질문의 대상 값이 없습니다."),
    )
    generation_client = _ScriptedGenerationClient(
        (
            _answered_result(
                answer=("복구 코드는 RECOVERY-21입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
            _insufficient_result(),
            _answered_result(
                answer=("확인 가능한 복구 코드는 RECOVERY-21입니다. [SOURCE-1]"),
                cited_source_ids=("SOURCE-1",),
            ),
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    response = await service.answer(
        _request(
            query=("두 PDF를 비교하여 확인 가능한 복구 코드를 종합해 주세요."),
        )
    )

    # 두 PDF 부분 호출 뒤, 근거가 있는 첫 번째 부분만 사용하여
    # 최종 호출한다.
    assert (
        len(
            generation_client.calls,
        )
        == 3
    )

    final_prompt = generation_client.calls[2].user_prompt

    assert "RECOVERY-21" in final_prompt
    assert _INSUFFICIENT_ANSWER not in final_prompt
    assert response.status is RagAnswerStatus.ANSWERED
    assert tuple(source.file_idx for source in response.sources) == (_FIRST_FILE_IDX,)
    assert tuple(source.source_id for source in response.sources) == ("SOURCE-1",)


@pytest.mark.asyncio
async def test_synthesis_skips_final_generation_when_all_partials_are_insufficient() -> None:
    """모든 PDF의 부분 근거가 부족하면 최종 Claude 호출 없이 종료한다."""

    searcher = _searcher(
        first_content=("질문과 무관한 첫 번째 문서 내용입니다."),
        second_content=("질문과 무관한 두 번째 문서 내용입니다."),
    )
    generation_client = _ScriptedGenerationClient(
        (
            _insufficient_result(),
            _insufficient_result(),
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    response = await service.answer(
        _request(
            query=("두 PDF를 비교하여 존재하지 않는 배포 키를 종합해 주세요."),
        )
    )

    # PDF별 부분 호출 두 번까지만 실행하고 세 번째 최종 종합은
    # 실행하지 않는다.
    assert (
        len(
            generation_client.calls,
        )
        == 2
    )
    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.answer == _INSUFFICIENT_ANSWER
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert response.stop_reason is None


@pytest.mark.asyncio
async def test_synthesis_logs_do_not_expose_question_chunk_or_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """근거 부족 경로에서도 질문·청크·프롬프트를 로그에 남기지 않는다."""

    question_secret = "QUESTION-SECRET-7F4A"
    first_chunk_secret = "CHUNK-SECRET-A12B"
    second_chunk_secret = "CHUNK-SECRET-C34D"
    searcher = _searcher(
        first_content=first_chunk_secret,
        second_content=second_chunk_secret,
    )
    generation_client = _ScriptedGenerationClient(
        (
            _insufficient_result(),
            _insufficient_result(),
        )
    )
    service = RoutedRagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    caplog.set_level(
        logging.INFO,
    )

    response = await service.answer(
        _request(
            query=(f"두 PDF를 비교하여 {question_secret} 값을 종합해 주세요."),
        )
    )

    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert question_secret not in caplog.text
    assert first_chunk_secret not in caplog.text
    assert second_chunk_secret not in caplog.text
    assert "<document_sources_json>" not in caplog.text
    assert "<partial_answers_json>" not in caplog.text
