"""RAG 답변 서비스의 Claude 구조화 출력 검증 계약을 테스트한다."""

import json
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
from jipsa_rag.schemas.rag_answer import RagAnswerRequest, RagAnswerStatus
from jipsa_rag.services.prompt_builder import RagPromptBuilder
from jipsa_rag.services.rag_answer import RagAnswerService, RagAnswerServiceError

_TEST_USER_IDX = 45
_TEST_REFERENCE_FILE_IDXS = (
    123,
    456,
)
_TEST_MODEL = "claude-sonnet-5"
_TEST_INSUFFICIENT_EVIDENCE_ANSWER = "제공된 문서 근거만으로는 답변할 수 없습니다."
_TEST_SENSITIVE_GENERATED_VALUE = "민감한 Claude 구조화 응답 원문: 계약 금액은 9,999원이다."


class _StubChunkSearcher:
    """준비된 검색 응답을 반환하고 검색 요청을 기록한다."""

    def __init__(
        self,
        response: ChunkSearchResponse,
    ) -> None:
        self._response = response
        self.calls: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """실제 임베딩과 Qdrant 호출 없이 검색 결과를 반환한다."""

        self.calls.append(request)

        return self._response


class _StubGenerationClient:
    """준비된 구조화 생성 결과를 반환하고 요청을 기록한다."""

    def __init__(
        self,
        result: GenerationResult,
    ) -> None:
        self._result = result
        self.calls: list[GenerationRequest] = []

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """네트워크 호출 없이 구조화 생성 결과를 반환한다."""

        self.calls.append(request)

        return self._result


def _create_request() -> RagAnswerRequest:
    """구조화 출력 테스트에 사용할 유효한 답변 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=_TEST_REFERENCE_FILE_IDXS,
        query="두 참조문서의 로컬 실행 절차를 비교해줘",
        top_k=5,
        score_threshold=0.6,
    )


def _create_chunk(
    *,
    source_number: int,
    file_idx: int,
    file_name: str,
    content: str,
) -> ChunkSearchResult:
    """서로 다른 SOURCE-N을 생성할 유효한 PDF 청크를 만든다."""

    return ChunkSearchResult(
        chunk_id=f"{source_number:08d}-1111-1111-1111-111111111111",
        score=0.95 - (source_number * 0.01),
        rag_document_idx=100 + source_number,
        file_idx=file_idx,
        folder_idx=9,
        file_name=file_name,
        file_type=SupportedFileType.PDF,
        chunk_index=source_number - 1,
        content=content,
        token_count=128,
        page=source_number,
        slide_no=None,
        sheet_name=None,
        section_title="로컬 실행 방법",
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


def _create_search_response() -> ChunkSearchResponse:
    """SOURCE-1과 SOURCE-2 후보를 포함한 검색 응답을 생성한다."""

    first_chunk = _create_chunk(
        source_number=1,
        file_idx=123,
        file_name="첫 번째 가이드.pdf",
        content="첫 번째 문서는 PowerShell 실행 절차를 설명합니다.",
    )
    second_chunk = _create_chunk(
        source_number=2,
        file_idx=456,
        file_name="두 번째 가이드.pdf",
        content="두 번째 문서는 환경 변수 설정 절차를 설명합니다.",
    )

    return ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=2,
        results=(
            first_chunk,
            second_chunk,
        ),
    )


def _create_structured_generation_result(
    payload: dict[str, object],
) -> GenerationResult:
    """Claude 클라이언트가 JSON 파싱을 완료한 상황을 재현한다."""

    return GenerationResult(
        text=json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        model=_TEST_MODEL,
        usage=GenerationUsage(
            input_tokens=240,
            output_tokens=60,
        ),
        stop_reason="end_turn",
        structured_output=payload,
    )


def _create_service(
    payload: dict[str, object],
) -> tuple[
    RagAnswerService,
    _StubGenerationClient,
]:
    """실제 PromptBuilder와 테스트 대역을 연결한다."""

    generation_client = _StubGenerationClient(
        _create_structured_generation_result(payload),
    )

    service = RagAnswerService(
        chunk_searcher=_StubChunkSearcher(_create_search_response()),
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    return (
        service,
        generation_client,
    )


def _render_log_records(
    records: list[logging.LogRecord],
) -> str:
    """로그 메시지와 extra 필드를 함께 검사할 문자열로 변환한다."""

    return "\n".join(repr(record.__dict__) for record in records)


@pytest.mark.asyncio
async def test_structured_answer_uses_schema_and_returns_actual_sources_in_order() -> None:
    """구조화 스키마를 요청하고 실제 인용 출처만 반환한다."""

    payload: dict[str, object] = {
        # 구조화 출력 enum의 대소문자 차이가 발생해도 서비스가
        # 안전하게 정규화하는지 함께 검증한다.
        "status": "Answered",
        "answer": (
            "환경 변수는 두 번째 가이드에 따라 설정합니다. [SOURCE-2] "
            "PowerShell 실행 절차는 첫 번째 가이드를 따릅니다. [SOURCE-1]"
        ),
        "cited_source_ids": [
            "SOURCE-2",
            "SOURCE-1",
        ],
    }
    service, generation_client = _create_service(payload)

    response = await service.answer(_create_request())

    assert len(generation_client.calls) == 1

    generation_request = generation_client.calls[0]

    # PromptBuilder가 일반 자유 텍스트가 아니라 JSON Schema
    # 구조화 출력을 요청하는지 확인한다.
    assert generation_request.output_schema is not None
    assert generation_request.output_schema["type"] == "object"
    assert generation_request.output_schema["required"] == [
        "status",
        "answer",
        "cited_source_ids",
    ]

    assert response.status is RagAnswerStatus.ANSWERED
    assert response.answer == payload["answer"]
    assert response.model == _TEST_MODEL

    assert response.usage is not None
    assert response.usage.input_tokens == 240
    assert response.usage.output_tokens == 60

    # 후보 출처의 검색 순서가 아니라 답변 본문의 최초 인용 순서와
    # cited_source_ids 선언 순서대로 실제 사용 출처만 반환해야 한다.
    assert [source.source_id for source in response.sources] == [
        "SOURCE-2",
        "SOURCE-1",
    ]
    assert [source.file_idx for source in response.sources] == [
        456,
        123,
    ]


@pytest.mark.asyncio
async def test_structured_insufficient_evidence_omits_sources_and_metadata() -> None:
    """구조화 근거 부족 결과를 외부 근거 부족 응답으로 변환한다."""

    payload: dict[str, object] = {
        "status": "INSUFFICIENT_EVIDENCE",
        "answer": _TEST_INSUFFICIENT_EVIDENCE_ANSWER,
        "cited_source_ids": [],
    }
    service, _ = _create_service(payload)

    response = await service.answer(_create_request())

    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.answer == _TEST_INSUFFICIENT_EVIDENCE_ANSWER
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert response.stop_reason is None


@pytest.mark.asyncio
async def test_structured_answer_rejects_declared_source_order_mismatch() -> None:
    """본문 인용과 cited_source_ids 순서가 다르면 거부한다."""

    payload: dict[str, object] = {
        "status": "answered",
        "answer": "첫 번째 근거입니다. [SOURCE-1] 두 번째 근거입니다. [SOURCE-2]",
        "cited_source_ids": [
            "SOURCE-2",
            "SOURCE-1",
        ],
    }
    service, _ = _create_service(payload)

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    assert exception_info.value.operation == "answer_citation_validation_failed"


@pytest.mark.asyncio
async def test_structured_answer_rejects_unknown_source() -> None:
    """프롬프트에 없는 SOURCE-N은 구조화 필드와 일치해도 거부한다."""

    payload: dict[str, object] = {
        "status": "answered",
        "answer": "존재하지 않는 근거를 사용한 답변입니다. [SOURCE-99]",
        "cited_source_ids": [
            "SOURCE-99",
        ],
    }
    service, _ = _create_service(payload)

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    assert exception_info.value.operation == "answer_citation_validation_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {
                "status": "answered",
                "answer": "출처 선언이 없는 정상 답변입니다. [SOURCE-1]",
                "cited_source_ids": [],
            },
            id="answered-without-declared-source",
        ),
        pytest.param(
            {
                "status": "insufficient_evidence",
                "answer": _TEST_INSUFFICIENT_EVIDENCE_ANSWER,
                "cited_source_ids": [
                    "SOURCE-1",
                ],
            },
            id="insufficient-with-source",
        ),
        pytest.param(
            {
                "status": "insufficient_evidence",
                "answer": _TEST_SENSITIVE_GENERATED_VALUE,
                "cited_source_ids": [],
            },
            id="insufficient-with-wrong-answer",
        ),
        pytest.param(
            {
                "status": "answered",
                "answer": "중복 출처 선언입니다. [SOURCE-1]",
                "cited_source_ids": [
                    "SOURCE-1",
                    "SOURCE-1",
                ],
            },
            id="duplicate-declared-source",
        ),
        pytest.param(
            {
                "status": "answered",
                "answer": "잘못된 출처 형식입니다. [SOURCE-01]",
                "cited_source_ids": [
                    "SOURCE-01",
                ],
            },
            id="invalid-source-format",
        ),
    ],
)
async def test_structured_answer_rejects_invalid_domain_contract(
    payload: dict[str, object],
) -> None:
    """JSON이 유효해도 RAG 의미 계약 위반은 거부한다."""

    service, _ = _create_service(payload)

    with pytest.raises(RagAnswerServiceError) as exception_info:
        await service.answer(_create_request())

    assert exception_info.value.operation == "answer_citation_validation_failed"


@pytest.mark.asyncio
async def test_structured_validation_failure_does_not_expose_payload_or_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """구조화 검증 실패 로그와 예외에 JSON과 프롬프트를 노출하지 않는다."""

    payload: dict[str, object] = {
        "status": "insufficient_evidence",
        "answer": _TEST_SENSITIVE_GENERATED_VALUE,
        "cited_source_ids": [],
    }
    service, generation_client = _create_service(payload)

    # 로그 캡처와 예외 검증을 하나의 with 문으로 결합한다.
    #
    # Ruff SIM117 규칙을 지키면서도 동일한 범위에서 서비스 로그와
    # 발생 예외를 함께 검증한다.
    with (
        caplog.at_level(
            logging.DEBUG,
            logger="jipsa_rag.services.rag_answer",
        ),
        pytest.raises(RagAnswerServiceError) as exception_info,
    ):
        await service.answer(_create_request())

    assert len(generation_client.calls) == 1

    generation_request = generation_client.calls[0]
    rendered_logs = _render_log_records(caplog.records)
    rendered_error = str(exception_info.value)

    assert _TEST_SENSITIVE_GENERATED_VALUE not in rendered_logs
    assert _TEST_SENSITIVE_GENERATED_VALUE not in rendered_error
    assert generation_request.user_prompt not in rendered_logs
    assert generation_request.user_prompt not in rendered_error

    # 안전한 운영 메타데이터만 남아 원인을 분류할 수 있어야 한다.
    assert "structured_response_invalid" in rendered_logs
    assert exception_info.value.operation == "answer_citation_validation_failed"
