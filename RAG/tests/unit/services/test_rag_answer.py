"""근거 기반 RAG 답변 서비스의 오케스트레이션과 보안 계약을 테스트한다."""

import logging

import pytest

from jipsa_rag.infrastructure.generation.exceptions import GenerationAuthenticationError
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
from jipsa_rag.services.prompt_builder import RagPromptBuilder, RagPromptBuildResult
from jipsa_rag.services.rag_answer import RagAnswerService, RagAnswerServiceError

_TEST_USER_IDX = 45

# 로그 및 예외 노출 검증에서 사용할 고유한 민감 정보다.
#
# 일반적인 단어를 사용하면 다른 로그 메시지와 우연히 일치할 수 있으므로,
# 테스트에서만 사용하는 식별 가능한 문자열을 사용한다.
_TEST_QUESTION = "민감한 사용자 질문: 내부 계약 금액은 얼마인가?"
_TEST_CHUNK = "민감한 청크 원문: 내부 계약 금액은 9,999원이다."
_TEST_API_KEY = "sk-ant-sensitive-test-key"


class _StubChunkSearcher:
    """준비된 검색 응답을 반환하고 전달받은 요청을 기록한다."""

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
        """검색 요청을 기록하고 실제 임베딩 또는 Qdrant 호출 없이 응답한다."""

        self.calls.append(request)

        return self._response


class _RecordingPromptBuilder:
    """실제 프롬프트 구성기를 사용하면서 호출 기록을 남긴다."""

    def __init__(self) -> None:
        """실제 구성기와 호출 기록을 초기화한다."""

        self._delegate = RagPromptBuilder()
        self.calls: list[
            tuple[
                RagAnswerRequest,
                tuple[ChunkSearchResult, ...],
            ]
        ] = []

    def build(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """호출 인수를 기록한 뒤 실제 프롬프트 구성기로 위임한다."""

        self.calls.append(
            (
                request,
                chunks,
            )
        )

        return self._delegate.build(
            request=request,
            chunks=chunks,
        )


class _StubGenerationClient:
    """실제 Claude API 대신 준비된 생성 결과를 반환한다."""

    def __init__(
        self,
        result: GenerationResult,
        *,
        api_key: str = _TEST_API_KEY,
    ) -> None:
        """생성 결과, 테스트 API Key 및 호출 기록을 초기화한다.

        API Key는 서비스가 이 객체 전체를 로그에 출력하는 실수를
        탐지하기 위해 보관한다. 실제 네트워크 호출에는 사용하지 않는다.
        """

        self._result = result
        self.api_key = api_key
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """생성 요청을 기록하고 네트워크 호출 없이 결과를 반환한다."""

        self.calls.append(request)

        return self._result


class _UnexpectedPromptBuilder:
    """근거 부족 경로에서 호출되면 테스트를 실패시키는 대역."""

    def __init__(self) -> None:
        """호출 횟수를 초기화한다."""

        self.call_count = 0

    def build(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """호출되면 근거 부족 조기 반환 계약 위반으로 처리한다."""

        self.call_count += 1

        raise AssertionError("Prompt builder must not be called without search results.")


class _UnexpectedGenerationClient:
    """근거 부족 경로에서 호출되면 테스트를 실패시키는 대역."""

    def __init__(self) -> None:
        """호출 횟수를 초기화한다."""

        self.call_count = 0

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """호출되면 Claude 호출 생략 계약 위반으로 처리한다."""

        self.call_count += 1

        raise AssertionError("Generation client must not be called without search results.")


class _SensitiveFailingPromptBuilder:
    """민감한 입력을 오류 메시지에 포함하는 잘못된 구성기 대역.

    답변 서비스는 하위 구현체가 이러한 오류를 발생시키더라도 원본
    예외 메시지를 외부로 전달하거나 로그에 기록하면 안 된다.
    """

    def build(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """질문, 청크 및 테스트 API Key를 포함한 오류를 발생시킨다."""

        raise ValueError(f"{request.query}|{chunks[0].content}|{_TEST_API_KEY}")


class _SensitiveFailingGenerationClient:
    """민감한 원인 예외를 연결한 생성 오류를 반환하는 대역."""

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """프롬프트와 API Key가 포함된 원인 예외를 생성 오류에 연결한다."""

        sensitive_cause = RuntimeError(f"{request.user_prompt}|{_TEST_API_KEY}")

        error = GenerationAuthenticationError(
            "Generation provider authentication failed.",
            provider="anthropic",
            status_code=401,
        )

        # 실제 하위 SDK 예외가 요청 객체나 공급자 오류 정보를
        # 원인 예외에 보관한 상황을 재현한다.
        error.__cause__ = sensitive_cause
        error.__context__ = sensitive_cause
        error.__suppress_context__ = False

        raise error


def _create_request(
    *,
    query: str = "프로젝트의 로컬 실행 방법을 알려줘",
) -> RagAnswerRequest:
    """서비스 테스트에 사용할 유효한 RAG 답변 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        query=query,
        top_k=3,
        score_threshold=0.7,
    )


def _create_chunk(
    *,
    content: str = "로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다.",
) -> ChunkSearchResult:
    """서비스 테스트에 사용할 유효한 PDF 청크를 생성한다."""

    return ChunkSearchResult(
        chunk_id="11111111-1111-1111-1111-111111111111",
        score=0.92,
        rag_document_idx=100,
        file_idx=123,
        folder_idx=9,
        file_name="프로젝트 가이드.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        content=content,
        token_count=128,
        page=2,
        slide_no=None,
        sheet_name=None,
        section_title="로컬 실행 방법",
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


def _create_search_response(
    *chunks: ChunkSearchResult,
) -> ChunkSearchResponse:
    """전달받은 청크로 사용자 범위 검색 응답을 생성한다."""

    return ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=len(chunks),
        results=chunks,
    )


def _create_generation_result() -> GenerationResult:
    """실제 Claude 호출 대신 사용할 결정적인 생성 결과를 생성한다."""

    return GenerationResult(
        text="로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다. [SOURCE-1]",
        model="claude-sonnet-5",
        usage=GenerationUsage(
            input_tokens=120,
            output_tokens=30,
        ),
        stop_reason="end_turn",
    )


def _render_log_records(
    records: list[logging.LogRecord],
) -> str:
    """로그 메시지와 extra 필드를 민감 정보 검사 문자열로 변환한다.

    ``getMessage()``만 검사하면 extra 필드에 민감한 객체를 넣은 실수를
    발견하지 못한다. LogRecord 전체 속성을 문자열로 변환하여 질문,
    청크 및 API Key가 어느 로그 필드에도 없는지 확인한다.
    """

    return "\n".join(repr(record.__dict__) for record in records)


@pytest.mark.asyncio
async def test_answer_connects_search_prompt_and_generation() -> None:
    """검색, 프롬프트 구성 및 생성을 순서대로 연결해야 한다."""

    chunk = _create_chunk()

    searcher = _StubChunkSearcher(_create_search_response(chunk))

    prompt_builder = _RecordingPromptBuilder()

    generation_client = _StubGenerationClient(_create_generation_result())

    service = RagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )

    request = _create_request()

    response = await service.answer(request)

    # RAG 답변 검색 조건이 기존 청크 검색 요청으로 손실 없이
    # 변환되었는지 확인한다.
    assert len(searcher.calls) == 1

    search_request = searcher.calls[0]

    assert search_request.user_idx == request.user_idx
    assert search_request.query == request.query
    assert search_request.top_k == request.top_k
    assert search_request.score_threshold == request.score_threshold

    # 검색된 청크가 프롬프트 구성기로 한 번 전달되었는지 확인한다.
    assert prompt_builder.calls == [
        (
            request,
            (chunk,),
        )
    ]

    # 실제 Claude API를 사용하지 않은 테스트 생성 클라이언트가
    # 정확히 한 번 호출되었는지 확인한다.
    assert len(generation_client.calls) == 1

    generation_request = generation_client.calls[0]

    # 프롬프트 구성 결과에 질문과 검색 청크가 포함되어 실제 RAG
    # 생성 흐름이 연결되었는지 확인한다.
    assert request.query in generation_request.user_prompt
    assert chunk.content in generation_request.user_prompt
    assert generation_request.system_prompt is not None

    assert response.status is RagAnswerStatus.ANSWERED
    assert response.answer == _create_generation_result().text
    assert response.model == "claude-sonnet-5"
    assert response.stop_reason == "end_turn"

    assert response.usage is not None
    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 30

    assert len(response.sources) == 1
    assert response.sources[0].source_id == "SOURCE-1"
    assert response.sources[0].chunk_id == chunk.chunk_id
    assert response.sources[0].file_idx == chunk.file_idx
    assert response.sources[0].page == chunk.page


@pytest.mark.asyncio
async def test_answer_returns_insufficient_evidence_without_generation() -> None:
    """검색 결과가 없으면 프롬프트 구성과 Claude 생성을 생략해야 한다."""

    searcher = _StubChunkSearcher(_create_search_response())

    prompt_builder = _UnexpectedPromptBuilder()
    generation_client = _UnexpectedGenerationClient()

    service = RagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=prompt_builder,
        generation_client=generation_client,
    )

    response = await service.answer(_create_request())

    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.answer == "제공된 문서 근거만으로는 답변할 수 없습니다."

    # 근거 부족 응답에는 출처 또는 Claude 생성 메타데이터가
    # 포함되면 안 된다.
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert response.stop_reason is None

    # 검색 결과가 없으면 프롬프트와 외부 생성 호출이 모두 생략된다.
    assert prompt_builder.call_count == 0
    assert generation_client.call_count == 0


@pytest.mark.asyncio
async def test_answer_logs_only_safe_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """질문, 청크 및 API Key가 어느 로그 필드에도 없어야 한다."""

    chunk = _create_chunk(
        content=_TEST_CHUNK,
    )

    service = RagAnswerService(
        chunk_searcher=_StubChunkSearcher(_create_search_response(chunk)),
        prompt_builder=_RecordingPromptBuilder(),
        generation_client=_StubGenerationClient(_create_generation_result()),
    )

    caplog.set_level(
        logging.DEBUG,
        logger="jipsa_rag.services.rag_answer",
    )

    await service.answer(
        _create_request(
            query=_TEST_QUESTION,
        )
    )

    rendered_logs = _render_log_records(caplog.records)

    assert _TEST_QUESTION not in rendered_logs
    assert _TEST_CHUNK not in rendered_logs
    assert _TEST_API_KEY not in rendered_logs

    # 원문 대신 안전한 이벤트 및 수량 메타데이터는 남아야 한다.
    assert "rag_answer_search_completed" in rendered_logs
    assert "rag_answer_generation_completed" in rendered_logs
    assert "'result_count': 1" in rendered_logs
    assert "'source_count': 1" in rendered_logs


@pytest.mark.asyncio
async def test_answer_sanitizes_prompt_builder_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """프롬프트 구성 오류에서 질문, 청크 및 API Key를 제거해야 한다."""

    chunk = _create_chunk(
        content=_TEST_CHUNK,
    )

    generation_client = _UnexpectedGenerationClient()

    service = RagAnswerService(
        chunk_searcher=_StubChunkSearcher(_create_search_response(chunk)),
        prompt_builder=_SensitiveFailingPromptBuilder(),
        generation_client=generation_client,
    )

    caplog.set_level(
        logging.DEBUG,
        logger="jipsa_rag.services.rag_answer",
    )

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(
            _create_request(
                query=_TEST_QUESTION,
            )
        )

    error = exception_info.value

    assert error.operation == "prompt_build_failed"

    # 새 서비스 예외에 민감한 원본 예외가 원인 또는 문맥으로
    # 연결되지 않아야 한다.
    assert error.__cause__ is None
    assert error.__context__ is None

    assert _TEST_QUESTION not in str(error)
    assert _TEST_CHUNK not in str(error)
    assert _TEST_API_KEY not in str(error)

    # 프롬프트 구성에 실패했으므로 생성 호출은 발생하지 않는다.
    assert generation_client.call_count == 0

    rendered_logs = _render_log_records(caplog.records)

    assert _TEST_QUESTION not in rendered_logs
    assert _TEST_CHUNK not in rendered_logs
    assert _TEST_API_KEY not in rendered_logs
    assert "rag_answer_prompt_build_failed" in rendered_logs


@pytest.mark.asyncio
async def test_answer_removes_sensitive_generation_exception_chain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """생성 오류의 원인 예외에 남은 질문과 API Key를 제거해야 한다."""

    chunk = _create_chunk(
        content=_TEST_CHUNK,
    )

    service = RagAnswerService(
        chunk_searcher=_StubChunkSearcher(_create_search_response(chunk)),
        prompt_builder=_RecordingPromptBuilder(),
        generation_client=_SensitiveFailingGenerationClient(),
    )

    caplog.set_level(
        logging.DEBUG,
        logger="jipsa_rag.services.rag_answer",
    )

    with pytest.raises(GenerationAuthenticationError) as exception_info:
        await service.answer(
            _create_request(
                query=_TEST_QUESTION,
            )
        )

    error = exception_info.value

    # API 계층이 인증 오류 타입을 구분할 수 있도록 기존 타입은
    # 유지하면서 민감한 원인 예외 참조만 제거한다.
    assert error.provider == "anthropic"
    assert error.status_code == 401
    assert error.__cause__ is None
    assert error.__context__ is None

    assert _TEST_QUESTION not in str(error)
    assert _TEST_CHUNK not in str(error)
    assert _TEST_API_KEY not in str(error)

    rendered_logs = _render_log_records(caplog.records)

    assert _TEST_QUESTION not in rendered_logs
    assert _TEST_CHUNK not in rendered_logs
    assert _TEST_API_KEY not in rendered_logs
    assert "rag_answer_generation_failed" in rendered_logs
