"""Claude 인용 실패와 근거 부족 응답의 보안 계약을 단위 테스트한다."""

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
from jipsa_rag.services.rag_answer import (
    RagAnswerService,
    RagAnswerServiceError,
)

_TEST_USER_IDX: Final[int] = 45
_TEST_FILE_IDX: Final[int] = 123
_TEST_SENSITIVE_QUESTION: Final[str] = (
    "민감한 사용자 질문: 외부 로그에 기록되면 안 되는 계약 내용을 알려줘"
)
_TEST_SENSITIVE_CHUNK: Final[str] = "민감한 문서 청크: 외부 로그에 기록되면 안 되는 내부 계약 원문"
_TEST_SENSITIVE_CLAUDE_ANSWER: Final[str] = (
    "민감한 Claude 답변 원문이며 존재하지 않는 출처를 인용합니다. [SOURCE-999]"
)
_TEST_INSUFFICIENT_EVIDENCE_ANSWER: Final[str] = "제공된 문서 근거만으로는 답변할 수 없습니다."


class _StubChunkSearcher:
    """실제 TEI와 Qdrant를 호출하지 않고 준비된 청크를 반환한다."""

    def __init__(
        self,
        response: ChunkSearchResponse,
    ) -> None:
        """검색 응답과 서비스에서 전달한 요청 기록을 초기화한다."""

        self._response = response
        self.calls: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """검색 요청을 기록하고 결정적인 검색 응답을 반환한다."""

        self.calls.append(request)

        return self._response


class _RecordingGenerationClient:
    """Claude 호출 대신 준비된 생성 결과를 반환하고 프롬프트를 기록한다."""

    def __init__(
        self,
        result: GenerationResult,
    ) -> None:
        """생성 결과와 GenerationRequest 호출 기록을 초기화한다."""

        self._result = result
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """생성 프롬프트를 기록한 뒤 네트워크 호출 없이 결과를 반환한다."""

        self.calls.append(request)

        return self._result


def _create_request() -> RagAnswerRequest:
    """민감한 질문을 포함한 유효한 RAG 답변 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=(_TEST_FILE_IDX,),
        query=_TEST_SENSITIVE_QUESTION,
        top_k=1,
        score_threshold=0.7,
    )


def _create_chunk() -> ChunkSearchResult:
    """민감한 원문을 포함한 단일 PDF 청크 검색 결과를 생성한다."""

    return ChunkSearchResult(
        chunk_id="11111111-1111-1111-1111-111111111111",
        score=0.92,
        rag_document_idx=100,
        file_idx=_TEST_FILE_IDX,
        folder_idx=9,
        file_name="내부 계약서.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        content=_TEST_SENSITIVE_CHUNK,
        token_count=128,
        page=2,
        slide_no=None,
        sheet_name=None,
        section_title="내부 계약",
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


def _create_generation_result(
    *,
    text: str,
) -> GenerationResult:
    """테스트별 Claude 답변과 결정적인 생성 메타데이터를 생성한다."""

    return GenerationResult(
        text=text,
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=180,
            output_tokens=45,
        ),
        stop_reason="end_turn",
    )


def _create_service(
    *,
    generated_text: str,
) -> tuple[RagAnswerService, _RecordingGenerationClient]:
    """실제 프롬프트 구성기와 검색·생성 테스트 대역을 연결한다."""

    chunk = _create_chunk()
    search_response = ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=1,
        results=(chunk,),
    )

    generation_client = _RecordingGenerationClient(
        _create_generation_result(
            text=generated_text,
        )
    )

    service = RagAnswerService(
        chunk_searcher=_StubChunkSearcher(search_response),
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    return service, generation_client


def _render_log_records(
    records: list[logging.LogRecord],
) -> str:
    """로그 메시지와 extra 필드를 하나의 검사 문자열로 변환한다."""

    return "\n".join(repr(record.__dict__) for record in records)


@pytest.mark.asyncio
async def test_invalid_citation_stops_answer_without_exposing_response_or_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """잘못된 출처 인용은 정상 응답을 중단하고 민감 원문을 노출하지 않아야 한다."""

    service, generation_client = _create_service(
        generated_text=_TEST_SENSITIVE_CLAUDE_ANSWER,
    )

    caplog.set_level(
        logging.DEBUG,
        logger="jipsa_rag.services.rag_answer",
    )

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    error = exception_info.value

    assert error.operation == "answer_citation_validation_failed"

    # 서비스가 Claude 응답을 RagAnswerResponse로 변환하기 전에 중단되어야 한다.
    # 또한 안전한 서비스 예외에는 하위 예외나 민감한 생성 데이터가 원인 체인으로
    # 연결되지 않아야 한다.
    assert error.__cause__ is None
    assert error.__context__ is None

    assert len(generation_client.calls) == 1

    generation_request = generation_client.calls[0]
    rendered_logs = _render_log_records(caplog.records)
    rendered_error = repr(error)

    sensitive_values = [
        _TEST_SENSITIVE_QUESTION,
        _TEST_SENSITIVE_CHUNK,
        _TEST_SENSITIVE_CLAUDE_ANSWER,
        generation_request.user_prompt,
    ]

    if generation_request.system_prompt is not None:
        sensitive_values.append(generation_request.system_prompt)

    for sensitive_value in sensitive_values:
        assert sensitive_value not in rendered_error
        assert sensitive_value not in rendered_logs

    # 실제 잘못된 인용 ID도 로그나 외부 예외에서 재현하지 않고,
    # 운영에 필요한 실패 종류와 개수만 안전한 메타데이터로 남긴다.
    assert "SOURCE-999" not in rendered_error
    assert "SOURCE-999" not in rendered_logs
    assert "rag_answer_citation_validation_failed" in rendered_logs
    assert "'validation_reason': 'unknown_source'" in rendered_logs
    assert "'unknown_source_count': 1" in rendered_logs


@pytest.mark.asyncio
async def test_generated_fixed_phrase_returns_insufficient_evidence_response() -> None:
    """Claude가 고정 근거 부족 문구를 반환하면 성공적인 근거 부족 상태로 변환한다."""

    service, generation_client = _create_service(
        generated_text=f"  {_TEST_INSUFFICIENT_EVIDENCE_ANSWER}\n",
    )

    response = await service.answer(_create_request())

    # 검색 결과가 있었으므로 Claude 생성은 한 번 수행되지만,
    # 고정 문구는 인용 없는 정상 answered 응답이 아니라
    # insufficient_evidence 상태로 정규화되어야 한다.
    assert len(generation_client.calls) == 1
    assert response.answer == _TEST_INSUFFICIENT_EVIDENCE_ANSWER
    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE

    # 근거 부족 응답에는 실제 인용 출처가 없으며 스키마 계약에 따라
    # 생성 모델과 사용량 같은 생성 메타데이터도 외부 응답에 포함하지 않는다.
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert response.stop_reason is None
