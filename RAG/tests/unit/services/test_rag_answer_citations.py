"""Claude 답변 인용 검증과 실제 출처 선택 계약을 테스트한다."""

import logging

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

_TEST_USER_IDX = 45
_TEST_REFERENCE_FILE_IDXS = (
    123,
    456,
)
_TEST_SENSITIVE_GENERATED_ANSWER = (
    "민감한 Claude 생성 답변은 로그와 예외에 노출되면 안 됩니다. [SOURCE-999]"
)


class _StubChunkSearcher:
    """준비된 검색 결과를 반환하고 서비스가 전달한 검색 요청을 기록한다."""

    def __init__(
        self,
        response: ChunkSearchResponse,
    ) -> None:
        """결정적인 검색 응답과 호출 기록을 초기화한다."""

        self._response = response
        self.calls: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """실제 TEI 또는 Qdrant 호출 없이 준비된 청크 검색 결과를 반환한다."""

        self.calls.append(request)

        return self._response


class _StubGenerationClient:
    """실제 Claude API 대신 지정된 답변을 반환하는 생성 클라이언트 대역."""

    def __init__(
        self,
        result: GenerationResult,
    ) -> None:
        """결정적인 생성 결과와 호출 기록을 초기화한다."""

        self._result = result
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """생성 요청을 기록한 뒤 네트워크 통신 없이 결과를 반환한다."""

        self.calls.append(request)

        return self._result


def _create_request() -> RagAnswerRequest:
    """인용 검증 테스트에 사용할 유효한 RAG 답변 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=_TEST_REFERENCE_FILE_IDXS,
        query="선택한 문서의 로컬 실행 방법을 알려줘",
        top_k=5,
        score_threshold=0.7,
    )


def _create_chunk(
    *,
    chunk_id: str,
    rag_document_idx: int,
    file_idx: int,
    chunk_index: int,
    content: str,
) -> ChunkSearchResult:
    """프롬프트에서 SOURCE-N 후보로 변환할 유효한 PDF 청크를 생성한다."""

    return ChunkSearchResult(
        chunk_id=chunk_id,
        score=0.92,
        rag_document_idx=rag_document_idx,
        file_idx=file_idx,
        folder_idx=9,
        file_name=f"참조문서-{file_idx}.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=chunk_index,
        content=content,
        token_count=128,
        page=chunk_index + 1,
        slide_no=None,
        sheet_name=None,
        section_title=f"테스트 섹션 {chunk_index + 1}",
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


def _create_chunks() -> tuple[ChunkSearchResult, ...]:
    """SOURCE-1부터 SOURCE-3까지 생성할 서로 다른 검색 청크를 반환한다."""

    return (
        _create_chunk(
            chunk_id="11111111-1111-1111-1111-111111111111",
            rag_document_idx=100,
            file_idx=123,
            chunk_index=0,
            content="첫 번째 문서는 PowerShell 실행 절차를 설명합니다.",
        ),
        _create_chunk(
            chunk_id="22222222-2222-2222-2222-222222222222",
            rag_document_idx=200,
            file_idx=456,
            chunk_index=1,
            content="두 번째 문서는 Docker 의존 서비스 실행 절차를 설명합니다.",
        ),
        _create_chunk(
            chunk_id="33333333-3333-3333-3333-333333333333",
            rag_document_idx=100,
            file_idx=123,
            chunk_index=2,
            content="세 번째 청크는 서비스 상태 확인 방법을 설명합니다.",
        ),
    )


def _create_generation_result(
    *,
    text: str,
) -> GenerationResult:
    """지정된 Claude 답변과 결정적인 생성 메타데이터를 생성한다."""

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
    answer: str,
    chunks: tuple[ChunkSearchResult, ...] | None = None,
) -> RagAnswerService:
    """실제 프롬프트 구성기와 네트워크 없는 검색·생성 대역을 연결한다."""

    selected_chunks = _create_chunks() if chunks is None else chunks

    search_response = ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=len(selected_chunks),
        results=selected_chunks,
    )

    return RagAnswerService(
        chunk_searcher=_StubChunkSearcher(search_response),
        prompt_builder=RagPromptBuilder(),
        generation_client=_StubGenerationClient(
            _create_generation_result(
                text=answer,
            )
        ),
    )


def _render_log_records(
    records: list[logging.LogRecord],
) -> str:
    """로그 메시지와 extra 필드를 민감 정보 검사 문자열로 변환한다."""

    return "\n".join(repr(record.__dict__) for record in records)


@pytest.mark.asyncio
async def test_answer_returns_only_cited_sources_in_first_citation_order() -> None:
    """실제 인용 출처만 중복 없이 최초 등장 순서로 반환해야 한다."""

    chunks = _create_chunks()
    service = _create_service(
        chunks=chunks,
        answer=(
            "Docker 의존 서비스를 먼저 실행합니다. [SOURCE-2] "
            "그다음 PowerShell로 RAG 서버를 실행합니다. [SOURCE-1] "
            "Docker 실행 조건은 동일합니다. [SOURCE-2]"
        ),
    )

    response = await service.answer(_create_request())

    assert response.status is RagAnswerStatus.ANSWERED

    # SOURCE-2가 SOURCE-1보다 먼저 인용되었으므로 검색 순서가 아니라
    # Claude 답변의 최초 인용 순서대로 응답 출처가 정렬되어야 한다.
    assert tuple(source.source_id for source in response.sources) == (
        "SOURCE-2",
        "SOURCE-1",
    )

    # SOURCE-2가 답변에서 두 번 등장해도 응답 출처에는 한 번만 포함한다.
    assert tuple(source.chunk_id for source in response.sources) == (
        chunks[1].chunk_id,
        chunks[0].chunk_id,
    )

    # 프롬프트에는 포함되었지만 실제 답변에서 인용하지 않은 SOURCE-3은
    # 외부 응답 sources에 포함되면 안 된다.
    assert all(source.source_id != "SOURCE-3" for source in response.sources)


@pytest.mark.asyncio
async def test_answer_rejects_unknown_citation_even_when_valid_citation_exists() -> None:
    """유효한 인용과 존재하지 않는 인용이 섞여 있어도 정상 처리하면 안 된다."""

    service = _create_service(
        answer=(
            "문서에서 확인한 실행 방법입니다. [SOURCE-1] "
            "프롬프트에 없는 출처도 함께 인용합니다. [SOURCE-99]"
        ),
    )

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    assert exception_info.value.operation == "answer_citation_validation_failed"


@pytest.mark.asyncio
async def test_answer_requires_at_least_one_valid_citation() -> None:
    """근거 부족 문구가 아닌 정상 답변에는 최소 한 개의 인용이 필요하다."""

    service = _create_service(
        answer="문서에 근거한 것처럼 보이지만 출처 인용이 없는 답변입니다.",
    )

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    assert exception_info.value.operation == "answer_citation_validation_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_citation",
    [
        "SOURCE-0",
        "SOURCE-01",
        "SOURCE-999",
    ],
)
async def test_answer_rejects_numeric_source_id_not_present_in_prompt(
    invalid_citation: str,
) -> None:
    """숫자형 SOURCE 표기라도 프롬프트 후보에 없으면 거부해야 한다."""

    service = _create_service(
        answer=f"존재하지 않는 출처를 사용한 답변입니다. [{invalid_citation}]",
    )

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    assert exception_info.value.operation == "answer_citation_validation_failed"


@pytest.mark.asyncio
async def test_generated_insufficient_evidence_answer_returns_insufficient_status() -> None:
    """Claude가 고정 근거 부족 문구를 반환하면 인용 없이 정상 종료해야 한다."""

    service = _create_service(
        answer="  제공된 문서 근거만으로는 답변할 수 없습니다.\n",
    )

    response = await service.answer(_create_request())

    assert response.answer == "제공된 문서 근거만으로는 답변할 수 없습니다."
    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert response.stop_reason is None


@pytest.mark.asyncio
async def test_citation_validation_failure_does_not_log_generated_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """잘못된 인용 검증 중 Claude 답변 원문과 인용 ID를 로그에 남기면 안 된다."""

    service = _create_service(
        answer=_TEST_SENSITIVE_GENERATED_ANSWER,
    )

    caplog.set_level(
        logging.DEBUG,
        logger="jipsa_rag.services.rag_answer",
    )

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    error = exception_info.value
    rendered_logs = _render_log_records(caplog.records)

    assert error.operation == "answer_citation_validation_failed"
    assert error.__cause__ is None
    assert error.__context__ is None

    assert _TEST_SENSITIVE_GENERATED_ANSWER not in str(error)
    assert _TEST_SENSITIVE_GENERATED_ANSWER not in rendered_logs
    assert "SOURCE-999" not in str(error)
    assert "SOURCE-999" not in rendered_logs

    # 원문 대신 실패 종류와 안전한 수량 메타데이터만 기록한다.
    assert "rag_answer_citation_validation_failed" in rendered_logs
    assert "'validation_reason': 'unknown_source'" in rendered_logs
    assert "'unknown_source_count': 1" in rendered_logs