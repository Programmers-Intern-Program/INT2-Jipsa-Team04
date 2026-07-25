"""Local RAG의 인용·근거 부족·문서 형식·로그 보안 계약을 통합 검증한다.

이 테스트는 AWS Backend, 실제 Claude, CUDA TEI, Local RAG DB 및 Qdrant에
연결하지 않는다.

대신 다음 운영 구성요소는 실제 구현을 사용한다.

- RagAnswerService
- RagPromptBuilder
- RagAnswerRequest 및 RagAnswerResponse 스키마
- FileProcessingRequest를 사용하는 FastAPI 요청 검증
- DocumentParserFactory
- PdfDocumentParser

외부 생성 공급자와 청크 검색기만 결정적인 테스트 대역으로 교체하여
실제 비용이나 네트워크 상태에 영향을 받지 않고 보안 및 도메인 계약을
반복 검증한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from jipsa_rag.infrastructure.document.exceptions import (
    DocumentTextNotFoundError,
    InvalidDocumentError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
)
from jipsa_rag.infrastructure.document.parser_factory import (
    DocumentParserFactory,
)
from jipsa_rag.infrastructure.document.parsers.pdf import (
    PdfDocumentParser,
)
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
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerStatus,
)
from jipsa_rag.services.prompt_builder import (
    RagPromptBuildResult,
    RagPromptBuilder,
)
from jipsa_rag.services.rag_answer import (
    RagAnswerService,
    RagAnswerServiceError,
)


# ============================================================
# 공통 테스트 식별자
# ============================================================

# 실제 사용자와 파일 범위에 섞일 가능성을 줄이기 위해 테스트 전용
# 고정 식별자 범위를 사용한다.
_TEST_USER_IDX: Final[int] = 94_001
_FIRST_FILE_IDX: Final[int] = 940_001
_SECOND_FILE_IDX: Final[int] = 940_002

_FILE_IDXS: Final[tuple[int, int]] = (
    _FIRST_FILE_IDX,
    _SECOND_FILE_IDX,
)

# 운영 RagAnswerService의 고정 근거 부족 응답과 동일한 문구다.
_INSUFFICIENT_EVIDENCE_ANSWER: Final[str] = (
    "제공된 문서 근거만으로는 답변할 수 없습니다."
)

# 답변 본문의 SOURCE-N 인용을 왼쪽에서 오른쪽으로 추출한다.
_SOURCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[(SOURCE-[1-9][0-9]*)\]"
)

# caplog에서 RagAnswerService 로그만 명확하게 수집하기 위한 logger 이름이다.
_RAG_ANSWER_LOGGER_NAME: Final[str] = (
    "jipsa_rag.services.rag_answer"
)

# 민감정보 로그 비노출 검증용 테스트 전용 Sentinel이다.
#
# 실제 API Key, 사용자 질문, Presigned URL 또는 문서 원문을 테스트 코드에
# 작성하지 않는다. 로그에 실수로 포함되면 문자열 비교로 즉시 탐지할 수
# 있는 고유한 가짜 값을 사용한다.
_SECRET_QUERY: Final[str] = (
    "JIPSA-SECRET-QUERY-94 사용자 개인 질의"
)
_SECRET_CHUNK: Final[str] = (
    "JIPSA-SECRET-CHUNK-94 문서 내부 비공개 원문"
)
_SECRET_GENERATED_ANSWER: Final[str] = (
    "JIPSA-SECRET-CLAUDE-ANSWER-94"
)
_SECRET_API_KEY: Final[str] = (
    "sk-ant-test-secret-94-not-a-real-key"
)
_SECRET_PRESIGNED_URL: Final[str] = (
    "https://private-bucket.invalid/private.pdf"
    "?X-Amz-Signature=JIPSA-SECRET-SIGNATURE-94"
)


# ============================================================
# 외부 의존성 테스트 대역
# ============================================================


class StubChunkSearcher:
    """고정된 청크 검색 응답을 반환하는 검색기 대역."""

    def __init__(
        self,
        response: ChunkSearchResponse,
    ) -> None:
        """반환할 응답과 수신 요청 기록을 초기화한다."""

        self._response = response
        self.requests: list[ChunkSearchRequest] = []

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """수신 요청을 기록하고 설정된 검색 응답을 반환한다."""

        self.requests.append(
            request,
        )

        return self._response


class RecordingGenerationClient:
    """Claude 호출 여부와 전달 프롬프트를 기록하는 생성 클라이언트 대역."""

    def __init__(
        self,
        result: GenerationResult | None,
    ) -> None:
        """반환 결과와 생성 요청 기록을 초기화한다.

        result가 None이면 이 대역은 호출되어서는 안 되는 테스트 상황을
        의미한다. 호출될 경우 AssertionError를 발생시켜 Claude 미호출
        계약 위반을 즉시 표시한다.
        """

        self._result = result
        self.requests: list[GenerationRequest] = []

    async def generate(
        self,
        request: GenerationRequest,
    ) -> GenerationResult:
        """생성 요청을 기록하고 고정 결과를 반환한다."""

        self.requests.append(
            request,
        )

        if self._result is None:
            raise AssertionError(
                "근거가 없는 요청에서 Claude 생성 클라이언트가 호출되었습니다."
            )

        return self._result


class FailIfCalledPromptBuilder:
    """근거가 없는 상황에서 프롬프트 구성을 금지하는 테스트 대역."""

    def build(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """호출 자체를 테스트 실패로 처리한다."""

        del request
        del chunks

        raise AssertionError(
            "근거가 없는 요청에서 프롬프트 구성기가 호출되었습니다."
        )


# ============================================================
# 공통 모델 생성 함수
# ============================================================


def _chunk(
    *,
    file_idx: int,
    content: str,
    score: float,
    chunk_index: int = 0,
) -> ChunkSearchResult:
    """유효한 PDF 청크 검색 결과를 생성한다."""

    return ChunkSearchResult(
        chunk_id=(
            f"guardrail-chunk-{file_idx}-{chunk_index}"
        ),
        score=score,
        rag_document_idx=file_idx,
        file_idx=file_idx,
        folder_idx=None,
        file_name=f"guardrail-{file_idx}.pdf",
        file_type="pdf",
        chunk_index=chunk_index,
        content=content,
        token_count=max(
            1,
            len(content.split()),
        ),
        page=1,
        slide_no=None,
        sheet_name=None,
        section_title="Guardrail verification",
        parser_version="1.0.0",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        index_version=2,
    )


def _search_response(
    *chunks: ChunkSearchResult,
) -> ChunkSearchResponse:
    """전달된 청크를 포함한 검색 응답을 생성한다."""

    normalized_chunks = tuple(
        chunks,
    )

    return ChunkSearchResponse(
        user_idx=_TEST_USER_IDX,
        result_count=len(
            normalized_chunks,
        ),
        results=normalized_chunks,
    )


def _answer_request(
    *,
    reference_file_idxs: tuple[int, ...],
    query: str = "선택한 문서의 내용을 근거와 함께 설명해 주세요.",
) -> RagAnswerRequest:
    """RAG 답변 서비스에 전달할 유효한 요청을 생성한다."""

    return RagAnswerRequest(
        user_idx=_TEST_USER_IDX,
        reference_file_idxs=reference_file_idxs,
        query=query,
        top_k=10,
        score_threshold=None,
    )


def _generation_result(
    *,
    answer: str,
    cited_source_ids: tuple[str, ...],
    status: str = "answered",
) -> GenerationResult:
    """Claude 구조화 출력과 동일한 형태의 생성 결과를 만든다."""

    structured_output: dict[str, object] = {
        "status": status,
        "answer": answer,
        "cited_source_ids": list(
            cited_source_ids,
        ),
    }

    # 운영 Claude 클라이언트는 구조화 JSON 원문을 text에 보존하고,
    # 파싱한 객체를 structured_output에 함께 전달한다.
    return GenerationResult(
        text=json.dumps(
            structured_output,
            ensure_ascii=False,
        ),
        model="claude-test-guardrail",
        usage=GenerationUsage(
            input_tokens=120,
            output_tokens=30,
        ),
        stop_reason="end_turn",
        structured_output=structured_output,
    )


def _extract_unique_source_ids(
    answer: str,
) -> tuple[str, ...]:
    """답변 인용을 최초 등장 순서로 중복 없이 추출한다."""

    return tuple(
        dict.fromkeys(
            _SOURCE_PATTERN.findall(
                answer,
            )
        )
    )


# ============================================================
# 1. [SOURCE-N] · cited_source_ids · sources 일치 검증
# ============================================================


def test_source_markers_declared_ids_and_response_sources_match() -> None:
    """본문 인용, 구조화 선언, 최종 sources가 동일한 순서를 유지한다."""

    first_chunk = _chunk(
        file_idx=_FIRST_FILE_IDX,
        content="첫 번째 문서는 ORCHID-ALPHA-21 값을 설명합니다.",
        score=0.92,
    )
    second_chunk = _chunk(
        file_idx=_SECOND_FILE_IDX,
        content="두 번째 문서는 COBALT-BETA-34 값을 설명합니다.",
        score=0.91,
    )

    # 검색 결과 순서는 SOURCE-1, SOURCE-2로 프롬프트에 할당된다.
    searcher = StubChunkSearcher(
        _search_response(
            first_chunk,
            second_chunk,
        )
    )

    # 답변에서는 SOURCE-2를 먼저 사용하고 SOURCE-1을 나중에 사용한다.
    #
    # SOURCE-2가 마지막에 다시 등장하지만 cited_source_ids와 sources에는
    # 최초 등장 순서 기준으로 한 번만 존재해야 한다.
    answer = (
        "두 번째 문서의 값은 COBALT-BETA-34입니다. [SOURCE-2]\n"
        "첫 번째 문서의 값은 ORCHID-ALPHA-21입니다. [SOURCE-1]\n"
        "두 번째 값은 동일하게 COBALT-BETA-34입니다. [SOURCE-2]"
    )
    declared_source_ids = (
        "SOURCE-2",
        "SOURCE-1",
    )

    generation_client = RecordingGenerationClient(
        _generation_result(
            answer=answer,
            cited_source_ids=declared_source_ids,
        )
    )

    service = RagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    response = asyncio.run(
        service.answer(
            _answer_request(
                reference_file_idxs=_FILE_IDXS,
            )
        )
    )

    assert response.status is RagAnswerStatus.ANSWERED
    assert response.answer == answer

    # 실제 답변 본문의 [SOURCE-N]을 최초 등장 순서로 추출한다.
    answer_source_ids = _extract_unique_source_ids(
        response.answer,
    )

    # 외부 응답 sources의 source_id 순서를 추출한다.
    response_source_ids = tuple(
        source.source_id
        for source in response.sources
    )

    # 세 계약이 정확하게 일치해야 한다.
    #
    # 1. 답변 본문의 [SOURCE-N]
    # 2. Claude 구조화 출력의 cited_source_ids
    # 3. Local RAG 외부 응답의 sources[].source_id
    assert answer_source_ids == declared_source_ids
    assert declared_source_ids == response_source_ids

    # sources는 프롬프트 검색 순서가 아니라 답변에서 실제로 처음
    # 인용된 순서로 반환되어야 한다.
    assert tuple(
        source.file_idx
        for source in response.sources
    ) == (
        _SECOND_FILE_IDX,
        _FIRST_FILE_IDX,
    )

    # 같은 SOURCE-2가 두 번 인용되었더라도 외부 출처에는 한 번만
    # 포함되어야 한다.
    assert len(response.sources) == 2
    assert len(
        set(response_source_ids),
    ) == 2

    assert len(searcher.requests) == 1
    assert len(generation_client.requests) == 1

    # 운영 경로에서는 구조화 출력 JSON Schema가 생성 공급자에
    # 전달되어야 한다.
    assert generation_client.requests[0].output_schema is not None


@pytest.mark.parametrize(
    (
        "answer",
        "declared_source_ids",
    ),
    [
        pytest.param(
            "본문은 SOURCE-1을 인용합니다. [SOURCE-1]",
            (
                "SOURCE-2",
            ),
            id="answer-and-declared-ids-mismatch",
        ),
        pytest.param(
            "프롬프트에 존재하지 않는 출처입니다. [SOURCE-999]",
            (
                "SOURCE-999",
            ),
            id="unknown-source-id",
        ),
    ],
)
def test_mismatched_or_unknown_structured_citations_are_rejected(
    answer: str,
    declared_source_ids: tuple[str, ...],
) -> None:
    """선언 불일치 또는 미등록 SOURCE-N을 정상 답변으로 반환하지 않는다."""

    searcher = StubChunkSearcher(
        _search_response(
            _chunk(
                file_idx=_FIRST_FILE_IDX,
                content="SOURCE-1에 대응하는 유효한 문서 근거입니다.",
                score=0.95,
            )
        )
    )

    generation_client = RecordingGenerationClient(
        _generation_result(
            answer=answer,
            cited_source_ids=declared_source_ids,
        )
    )

    service = RagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    with pytest.raises(
        RagAnswerServiceError,
    ) as exception_info:
        asyncio.run(
            service.answer(
                _answer_request(
                    reference_file_idxs=(
                        _FIRST_FILE_IDX,
                    ),
                )
            )
        )

    # API 계층이 INVALID_GENERATION_RESPONSE로 변환하는 기존 작업
    # 식별자가 유지되어야 한다.
    assert exception_info.value.operation == (
        "answer_citation_validation_failed"
    )

    assert len(searcher.requests) == 1
    assert len(generation_client.requests) == 1


# ============================================================
# 2. 근거 부족 시 Claude 미호출 검증
# ============================================================


def test_insufficient_evidence_skips_prompt_and_claude() -> None:
    """검색 결과가 없으면 프롬프트와 Claude를 모두 호출하지 않는다."""

    searcher = StubChunkSearcher(
        ChunkSearchResponse(
            user_idx=_TEST_USER_IDX,
            result_count=0,
            results=(),
        )
    )

    # result=None은 생성 클라이언트가 호출될 경우 AssertionError를
    # 발생시키도록 한다.
    generation_client = RecordingGenerationClient(
        result=None,
    )

    service = RagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=FailIfCalledPromptBuilder(),
        generation_client=generation_client,
    )

    response = asyncio.run(
        service.answer(
            _answer_request(
                reference_file_idxs=(
                    _FIRST_FILE_IDX,
                ),
                query=(
                    "선택 문서에 존재하지 않는 정보를 질문합니다."
                ),
            )
        )
    )

    assert response.status is (
        RagAnswerStatus.INSUFFICIENT_EVIDENCE
    )
    assert response.answer == (
        _INSUFFICIENT_EVIDENCE_ANSWER
    )

    # 근거 부족 응답에는 인용 출처와 Claude 생성 메타데이터가
    # 존재해서는 안 된다.
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert response.stop_reason is None

    # 검색은 정확히 한 번 수행되어야 한다.
    assert len(searcher.requests) == 1

    # 검색 결과가 없으므로 Claude 생성 클라이언트는 한 번도
    # 호출되지 않아야 한다.
    assert generation_client.requests == []


# ============================================================
# 3. 손상 · 빈 · 스캔 PDF 처리 검증
# ============================================================


def _write_pdf_failure_fixture(
    *,
    case_name: str,
    file_path: Path,
) -> None:
    """PDF 실패 유형별 실제 파일을 생성한다."""

    if case_name == "corrupted":
        # PDF Magic Byte는 존재하지만 xref, trailer, page tree가 없는
        # 손상 파일이다.
        file_path.write_bytes(
            b"%PDF-1.7\n"
            b"this-is-not-a-valid-pdf-structure\n"
        )
        return

    writer = PdfWriter()

    if case_name == "empty":
        # 페이지를 추가하지 않은 유효한 PDF 컨테이너를 만든다.
        #
        # PdfDocumentParser는 page_count == 0을 유효하지 않은 문서로
        # 처리해야 한다.
        with file_path.open(
            "wb",
        ) as file_stream:
            writer.write(
                file_stream,
            )
        return

    if case_name == "scanned":
        # 현재 파서는 OCR을 지원하지 않는다.
        #
        # 실제 이미지가 포함된 PDF와 마찬가지로 텍스트 레이어가 없는
        # 빈 페이지를 생성하여 extract_text() 결과가 비어 있는 경우를
        # 검증한다.
        writer.add_blank_page(
            width=612,
            height=792,
        )

        with file_path.open(
            "wb",
        ) as file_stream:
            writer.write(
                file_stream,
            )
        return

    raise ValueError(
        f"Unknown PDF failure fixture: {case_name}"
    )


@pytest.mark.parametrize(
    (
        "case_name",
        "expected_error_type",
    ),
    [
        pytest.param(
            "corrupted",
            InvalidDocumentError,
            id="corrupted-pdf",
        ),
        pytest.param(
            "empty",
            InvalidDocumentError,
            id="zero-page-pdf",
        ),
        pytest.param(
            "scanned",
            DocumentTextNotFoundError,
            id="pdf-without-text-layer",
        ),
    ],
)
def test_pdf_parser_rejects_corrupted_empty_and_scanned_documents(
    tmp_path: Path,
    case_name: str,
    expected_error_type: type[Exception],
) -> None:
    """손상·0페이지·텍스트 레이어 없는 PDF를 색인 전에 거부한다."""

    file_path = tmp_path / f"{case_name}.pdf"

    _write_pdf_failure_fixture(
        case_name=case_name,
        file_path=file_path,
    )

    parser = PdfDocumentParser()

    with pytest.raises(
        expected_error_type,
    ):
        asyncio.run(
            parser.parse(
                file_path,
            )
        )


# ============================================================
# 4. TXT · DOCX · XLSX · PPTX 요청 거부 검증
# ============================================================


@pytest.mark.parametrize(
    (
        "file_type",
        "file_name",
        "document_type",
    ),
    [
        pytest.param(
            "txt",
            "unsupported.txt",
            DocumentType.TXT,
            id="txt",
        ),
        pytest.param(
            "docx",
            "unsupported.docx",
            DocumentType.DOCX,
            id="docx",
        ),
        pytest.param(
            "xlsx",
            "unsupported.xlsx",
            DocumentType.XLSX,
            id="xlsx",
        ),
        pytest.param(
            "pptx",
            "unsupported.pptx",
            DocumentType.PPTX,
            id="pptx",
        ),
    ],
)
def test_non_pdf_file_processing_requests_are_rejected(
    client: TestClient,
    file_type: str,
    file_name: str,
    document_type: DocumentType,
) -> None:
    """비 PDF 요청을 HTTP 요청 검증과 Parser Factory 양쪽에서 거부한다."""

    response = client.post(
        "/api/v1/files/process",
        json={
            "file_idx": _FIRST_FILE_IDX,
            "user_idx": _TEST_USER_IDX,
            "folder_idx": None,
            "file_name": file_name,
            "file_type": file_type,
            "download_url": (
                "https://files.invalid/"
                f"{file_name}?X-Amz-Signature=test-only"
            ),
            "url_expires_in": 900,
        },
    )

    body = response.json()

    # 비 PDF 형식은 엔드포인트 비즈니스 로직에 진입하기 전 요청
    # 스키마 단계에서 거부되어야 한다.
    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["message"] == "Request validation failed."

    invalid_fields = {
        error["field"]
        for error in body["data"]["errors"]
    }

    assert "body.file_type" in invalid_fields

    # 내부 서비스가 DocumentType 값을 직접 전달하는 경우에도
    # Parser Factory는 등록되지 않은 형식을 거부해야 한다.
    factory = DocumentParserFactory()

    assert factory.supports(
        document_type,
    ) is False

    with pytest.raises(
        UnsupportedDocumentTypeError,
    ) as exception_info:
        factory.get_parser(
            document_type,
        )

    assert exception_info.value.file_type is document_type


# ============================================================
# 5. 민감정보 로그 비노출 검증
# ============================================================


def test_sensitive_values_are_not_exposed_in_logs_or_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """질문·청크·프롬프트·응답·비밀값이 로그와 예외에 포함되지 않는다."""

    sensitive_query = (
        f"{_SECRET_QUERY}\n"
        f"가짜 API Key: {_SECRET_API_KEY}\n"
        f"가짜 Presigned URL: {_SECRET_PRESIGNED_URL}"
    )

    searcher = StubChunkSearcher(
        _search_response(
            _chunk(
                file_idx=_FIRST_FILE_IDX,
                content=_SECRET_CHUNK,
                score=0.97,
            )
        )
    )

    # 답변 본문은 SOURCE-1을 인용하지만 구조화 선언은 SOURCE-2로
    # 설정하여 의도적으로 declared_citation_mismatch를 발생시킨다.
    generated_answer = (
        f"{_SECRET_GENERATED_ANSWER} [SOURCE-1]"
    )

    generation_client = RecordingGenerationClient(
        _generation_result(
            answer=generated_answer,
            cited_source_ids=(
                "SOURCE-2",
            ),
        )
    )

    service = RagAnswerService(
        chunk_searcher=searcher,
        prompt_builder=RagPromptBuilder(),
        generation_client=generation_client,
    )

    caplog.set_level(
        logging.ERROR,
        logger=_RAG_ANSWER_LOGGER_NAME,
    )

    with pytest.raises(
        RagAnswerServiceError,
    ) as exception_info:
        asyncio.run(
            service.answer(
                _answer_request(
                    reference_file_idxs=(
                        _FIRST_FILE_IDX,
                    ),
                    query=sensitive_query,
                )
            )
        )

    # 데이터가 실제 프롬프트 구성과 생성 요청까지 전달되었다는 것을
    # 먼저 확인한다.
    #
    # 이 검증이 없으면 테스트가 민감정보를 처리 경로에 전달하지 않은 채
    # 단순히 로그에 없다는 이유만으로 통과할 수 있다.
    assert len(generation_client.requests) == 1

    generation_request = generation_client.requests[0]

    assert _SECRET_QUERY in generation_request.user_prompt
    assert _SECRET_CHUNK in generation_request.user_prompt
    assert _SECRET_API_KEY in generation_request.user_prompt
    assert _SECRET_PRESIGNED_URL in generation_request.user_prompt

    # LogRecord의 메시지뿐 아니라 structured logging extra 필드까지
    # 문자열로 직렬화하여 검사한다.
    rendered_logs = "\n".join(
        (
            f"{record.getMessage()} "
            f"{record.__dict__!r}"
        )
        for record in caplog.records
    )

    rendered_exception = str(
        exception_info.value,
    )

    # 다음 값은 로그 및 외부로 전달 가능한 서비스 예외 문자열에
    # 절대로 포함되어서는 안 된다.
    sensitive_values = (
        _SECRET_QUERY,
        _SECRET_CHUNK,
        _SECRET_GENERATED_ANSWER,
        _SECRET_API_KEY,
        _SECRET_PRESIGNED_URL,
        sensitive_query,
        generated_answer,
        generation_result_text
        if (
            generation_result_text := (
                generation_client._result.text
                if generation_client._result is not None
                else ""
            )
        )
        else "",
        generation_request.user_prompt,
    )

    for sensitive_value in sensitive_values:
        if not sensitive_value:
            continue

        assert sensitive_value not in rendered_logs
        assert sensitive_value not in rendered_exception

    # 운영 진단에 필요한 안전한 분류 정보는 유지되어야 한다.
    assert (
        "rag_answer_citation_validation_failed"
        in rendered_logs
    )
    assert (
        "declared_citation_mismatch"
        in rendered_logs
    )

    # 예외에는 민감한 생성 결과 대신 고정 작업 식별자만 존재해야 한다.
    assert exception_info.value.operation == (
        "answer_citation_validation_failed"
    )
    assert rendered_exception == (
        "RAG answer service operation failed: "
        "answer_citation_validation_failed"
    )