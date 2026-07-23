"""근거 기반 RAG 답변 요청 및 응답 스키마의 계약을 테스트한다."""

import pytest
from pydantic import ValidationError

from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagAnswerSource,
    RagAnswerStatus,
    RagAnswerUsage,
)

# 테스트 전반에서 동일하게 사용하는 사용자 식별자다.
TEST_USER_IDX = 45

# Qdrant Point ID이자 Local RAG DB RAG_Chunk.Chunk_ID로 사용할
# 유효한 UUID 형식의 테스트 청크 식별자다.
TEST_CHUNK_ID = "11111111-1111-1111-1111-111111111111"


def _create_source(
    *,
    source_id: str = "SOURCE-1",
    chunk_id: str = TEST_CHUNK_ID,
    page: int | None = 2,
    slide_no: int | None = None,
    sheet_name: str | None = None,
    excerpt: str = "로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다.",
) -> RagAnswerSource:
    """스키마 테스트에 사용할 유효한 문서 출처를 생성한다.

    각 테스트가 확인하려는 필드만 선택적으로 변경할 수 있도록
    정상적인 기본값을 제공한다.

    Args:
        source_id:
            프롬프트와 최종 답변 인용을 연결하는 SOURCE-N 식별자다.
        chunk_id:
            Local RAG DB 청크와 Qdrant Point를 연결하는 청크 ID다.
        page:
            PDF 원본 페이지 번호다.
        slide_no:
            PPTX 원본 슬라이드 번호다.
        sheet_name:
            XLSX 원본 시트 이름이다.
        excerpt:
            최종 답변에서 사용자에게 공개할 제한된 청크 발췌문이다.

    Returns:
        테스트에 사용할 유효한 ``RagAnswerSource`` 객체다.
    """

    return RagAnswerSource(
        source_id=source_id,
        chunk_id=chunk_id,
        rag_document_idx=100,
        file_idx=123,
        folder_idx=9,
        file_name="프로젝트 가이드.pdf",
        file_type=SupportedFileType.PDF,
        chunk_index=0,
        score=0.92,
        page=page,
        slide_no=slide_no,
        sheet_name=sheet_name,
        section_title="로컬 실행 방법",
        excerpt=excerpt,
    )


def _create_usage() -> RagAnswerUsage:
    """정상 답변 테스트에 사용할 Claude 토큰 사용량을 생성한다.

    Returns:
        입력 및 출력 토큰 수가 모두 유효한 ``RagAnswerUsage`` 객체다.
    """

    return RagAnswerUsage(
        input_tokens=1024,
        output_tokens=128,
    )


def test_rag_answer_request_normalizes_query_and_preserves_constraints() -> None:
    """질문의 앞뒤 공백을 제거하고 검색 제약을 보존해야 한다."""

    request = RagAnswerRequest(
        user_idx=TEST_USER_IDX,
        query="  프로젝트의 로컬 실행 방법을 알려줘  ",
        top_k=5,
        score_threshold=0.6,
    )

    assert request.user_idx == TEST_USER_IDX
    assert request.query == "프로젝트의 로컬 실행 방법을 알려줘"
    assert request.top_k == 5
    assert request.score_threshold == 0.6


@pytest.mark.parametrize(
    (
        "query",
        "top_k",
        "score_threshold",
    ),
    [
        ("", 5, None),
        ("   ", 5, None),
        ("정상 질문", 0, None),
        ("정상 질문", 21, None),
        ("정상 질문", 5, -1.1),
        ("정상 질문", 5, 1.1),
    ],
)
def test_rag_answer_request_rejects_invalid_values(
    query: str,
    top_k: int,
    score_threshold: float | None,
) -> None:
    """비어 있는 질문과 허용 범위를 벗어난 검색 조건을 거부해야 한다."""

    with pytest.raises(ValidationError):
        RagAnswerRequest(
            user_idx=TEST_USER_IDX,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
        )


def test_rag_answer_source_preserves_document_metadata() -> None:
    """출처 식별자와 원본 문서 위치 정보를 보존해야 한다."""

    source = _create_source()

    assert source.source_id == "SOURCE-1"
    assert source.chunk_id == TEST_CHUNK_ID
    assert source.rag_document_idx == 100
    assert source.file_idx == 123
    assert source.folder_idx == 9
    assert source.file_name == "프로젝트 가이드.pdf"
    assert source.file_type is SupportedFileType.PDF
    assert source.chunk_index == 0
    assert source.score == 0.92
    assert source.page == 2
    assert source.slide_no is None
    assert source.sheet_name is None
    assert source.section_title == "로컬 실행 방법"
    assert source.excerpt == ("로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다.")


def test_rag_answer_source_normalizes_source_id_whitespace() -> None:
    """SOURCE 식별자의 앞뒤 공백을 제거한 뒤 형식을 검증해야 한다.

    ``RagAnswerSource``는 ``str_strip_whitespace=True``를 사용한다.
    따라서 공백이 포함된 SOURCE 식별자는 잘못된 입력으로 거부하는 대신
    정규화한 값을 저장하는 것이 현재 스키마 계약이다.
    """

    source = _create_source(
        source_id="  SOURCE-1  ",
    )

    assert source.source_id == "SOURCE-1"


def test_rag_answer_source_rejects_multiple_primary_locations() -> None:
    """페이지, 슬라이드 및 시트 위치를 동시에 설정할 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match="Only one of page, slide_no, or sheet_name may be provided",
    ):
        _create_source(
            page=2,
            slide_no=1,
        )


@pytest.mark.parametrize(
    "source_id",
    [
        "",
        "SOURCE-0",
        "SOURCE-01",
        "SOURCE-A",
        "source-1",
        "SOURCE_1",
    ],
)
def test_rag_answer_source_rejects_invalid_source_id(
    source_id: str,
) -> None:
    """정규화 이후에도 SOURCE-N 형식이 아닌 식별자를 거부해야 한다.

    앞뒤 공백은 스키마 설정에 따라 제거되므로 이 테스트에서는
    공백 정규화 이후에도 유효하지 않은 값만 검증한다.
    """

    with pytest.raises(ValidationError):
        _create_source(
            source_id=source_id,
        )


def test_rag_answer_source_rejects_excerpt_over_schema_limit() -> None:
    """외부 발췌문이 응답 스키마의 최대 길이를 초과할 수 없어야 한다."""

    with pytest.raises(ValidationError):
        _create_source(
            excerpt="가" * 1001,
        )


def test_answered_response_accepts_sources_and_generation_metadata() -> None:
    """정상 답변은 출처와 Claude 생성 메타데이터를 포함해야 한다."""

    source = _create_source()
    usage = _create_usage()

    response = RagAnswerResponse(
        answer=("로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다. [SOURCE-1]"),
        status=RagAnswerStatus.ANSWERED,
        sources=(source,),
        model="  claude-sonnet-5  ",
        usage=usage,
        stop_reason="  end_turn  ",
    )

    assert response.status is RagAnswerStatus.ANSWERED
    assert response.sources == (source,)
    assert response.model == "claude-sonnet-5"
    assert response.usage is usage
    assert response.usage.input_tokens == 1024
    assert response.usage.output_tokens == 128
    assert response.stop_reason == "end_turn"


def test_answered_response_requires_at_least_one_source() -> None:
    """정상 답변 상태에는 최소 하나의 문서 출처가 있어야 한다."""

    with pytest.raises(
        ValidationError,
        match="answered responses must contain at least one source",
    ):
        RagAnswerResponse(
            answer="출처가 없는 잘못된 답변",
            status=RagAnswerStatus.ANSWERED,
            model="claude-sonnet-5",
            usage=_create_usage(),
        )


def test_answered_response_requires_model() -> None:
    """정상 답변 상태에는 실제 Claude 응답 모델 ID가 있어야 한다."""

    with pytest.raises(
        ValidationError,
        match="answered responses must contain a model",
    ):
        RagAnswerResponse(
            answer="모델 정보가 없는 잘못된 답변",
            status=RagAnswerStatus.ANSWERED,
            sources=(_create_source(),),
            usage=_create_usage(),
        )


def test_answered_response_requires_usage() -> None:
    """정상 답변 상태에는 Claude 토큰 사용량이 있어야 한다."""

    with pytest.raises(
        ValidationError,
        match="answered responses must contain usage",
    ):
        RagAnswerResponse(
            answer="토큰 사용량이 없는 잘못된 답변",
            status=RagAnswerStatus.ANSWERED,
            sources=(_create_source(),),
            model="claude-sonnet-5",
        )


def test_insufficient_evidence_response_excludes_generation_metadata() -> None:
    """근거 부족 응답에는 출처와 Claude 생성 정보가 없어야 한다."""

    response = RagAnswerResponse(
        answer="제공된 문서 근거만으로는 답변할 수 없습니다.",
        status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
    )

    assert response.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.sources == ()
    assert response.model is None
    assert response.usage is None
    assert response.stop_reason is None


def test_insufficient_evidence_response_rejects_sources() -> None:
    """근거 부족 응답에 문서 출처를 포함할 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match="insufficient_evidence responses must not contain sources",
    ):
        RagAnswerResponse(
            answer="잘못된 근거 부족 응답",
            status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
            sources=(_create_source(),),
        )


def test_insufficient_evidence_response_rejects_generation_metadata() -> None:
    """Claude를 호출하지 않은 응답에 생성 메타데이터를 포함할 수 없어야 한다."""

    with pytest.raises(
        ValidationError,
        match=("insufficient_evidence responses must not contain generation metadata"),
    ):
        RagAnswerResponse(
            answer="잘못된 근거 부족 응답",
            status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
            model="claude-sonnet-5",
            usage=_create_usage(),
            stop_reason="end_turn",
        )


def test_answer_response_rejects_duplicate_source_ids() -> None:
    """하나의 응답에서 동일한 SOURCE 식별자를 중복 사용할 수 없어야 한다."""

    first_source = _create_source(
        source_id="SOURCE-1",
        chunk_id="11111111-1111-1111-1111-111111111111",
    )

    second_source = _create_source(
        source_id="SOURCE-1",
        chunk_id="22222222-2222-2222-2222-222222222222",
    )

    with pytest.raises(
        ValidationError,
        match="sources must contain unique source_id values",
    ):
        RagAnswerResponse(
            answer="중복 출처 식별자가 포함된 잘못된 답변",
            status=RagAnswerStatus.ANSWERED,
            sources=(
                first_source,
                second_source,
            ),
            model="claude-sonnet-5",
            usage=_create_usage(),
        )


def test_answer_response_rejects_duplicate_chunk_ids() -> None:
    """하나의 응답에서 동일한 청크를 중복 출처로 반환할 수 없어야 한다."""

    first_source = _create_source(
        source_id="SOURCE-1",
        chunk_id=TEST_CHUNK_ID,
    )

    second_source = _create_source(
        source_id="SOURCE-2",
        chunk_id=TEST_CHUNK_ID,
    )

    with pytest.raises(
        ValidationError,
        match="sources must contain unique chunk_id values",
    ):
        RagAnswerResponse(
            answer="중복 청크가 포함된 잘못된 답변",
            status=RagAnswerStatus.ANSWERED,
            sources=(
                first_source,
                second_source,
            ),
            model="claude-sonnet-5",
            usage=_create_usage(),
        )


def test_rag_answer_response_preserves_answer_formatting() -> None:
    """Markdown과 줄바꿈이 포함된 생성 답변 원문을 변경하지 않아야 한다."""

    answer = "\n## 실행 방법\n\n1. 시작 스크립트를 실행합니다. [SOURCE-1]\n"

    response = RagAnswerResponse(
        answer=answer,
        status=RagAnswerStatus.ANSWERED,
        sources=(_create_source(),),
        model="claude-sonnet-5",
        usage=_create_usage(),
    )

    assert response.answer == answer


def test_rag_answer_response_rejects_empty_answer() -> None:
    """길이가 0인 답변을 Pydantic 필드 길이 제약으로 거부해야 한다.

    ``answer`` 필드는 ``min_length=1``을 사용한다. 따라서 완전히 빈
    문자열은 사용자 정의 ``validate_answer``보다 먼저 Pydantic의
    필드 길이 검증 단계에서 거부된다.
    """

    with pytest.raises(
        ValidationError,
        match="String should have at least 1 character",
    ):
        RagAnswerResponse(
            answer="",
            status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
        )


@pytest.mark.parametrize(
    "answer",
    [
        "   ",
        "\n\t",
    ],
)
def test_rag_answer_response_rejects_whitespace_only_answer(
    answer: str,
) -> None:
    """공백으로만 구성된 답변을 사용자 정의 validator에서 거부해야 한다.

    공백 문자열은 문자 수가 1 이상이므로 ``min_length`` 검사를 통과한다.
    이후 ``validate_answer``가 ``strip()`` 결과를 확인하여 거부해야 한다.
    """

    with pytest.raises(
        ValidationError,
        match="answer must not be empty",
    ):
        RagAnswerResponse(
            answer=answer,
            status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
        )
