"""참조문서 기반 RAG 요청 스키마의 공통 검증 계약을 테스트한다."""

import pytest
from pydantic import BaseModel, ValidationError

from jipsa_rag.schemas.chunk_search import ChunkSearchRequest
from jipsa_rag.schemas.rag_answer import RagAnswerRequest
from jipsa_rag.schemas.reference_files import MAX_REFERENCE_FILE_COUNT

TEST_USER_IDX = 45
TEST_REFERENCE_FILE_IDXS = (123, 456)

# 두 요청 모델은 동일한 참조문서 식별자 계약을 사용해야 한다.
_REQUEST_MODELS: tuple[type[BaseModel], ...] = (
    RagAnswerRequest,
    ChunkSearchRequest,
)

# reference_file_idxs 필드를 의도적으로 생략하는 테스트에서 사용할 sentinel이다.
_MISSING_REFERENCE_FILE_IDXS = object()


def _validate_request(
    request_model: type[BaseModel],
    *,
    reference_file_idxs: object = _MISSING_REFERENCE_FILE_IDXS,
) -> BaseModel:
    """공통 요청 본문을 지정한 Pydantic 요청 모델로 검증한다.

    ``model_validate``를 사용하면 잘못된 외부 JSON 값도 정적 타입 검사를
    우회하지 않고 실제 API 요청과 동일한 Pydantic 경로로 검증할 수 있다.

    Args:
        request_model:
            검증할 ``RagAnswerRequest`` 또는 ``ChunkSearchRequest`` 모델이다.
        reference_file_idxs:
            요청에 전달할 참조문서 식별자 값이다. sentinel이면 해당 필드를
            본문에서 완전히 생략한다.

    Returns:
        검증이 완료된 요청 모델이다.
    """

    request_body: dict[str, object] = {
        "user_idx": TEST_USER_IDX,
        "query": "프로젝트의 로컬 실행 방법을 알려줘",
        "top_k": 5,
        "score_threshold": 0.6,
    }

    if reference_file_idxs is not _MISSING_REFERENCE_FILE_IDXS:
        request_body["reference_file_idxs"] = reference_file_idxs

    return request_model.model_validate(request_body)


@pytest.mark.parametrize("request_model", _REQUEST_MODELS)
def test_reference_file_idxs_are_required_and_preserved_as_tuple(
    request_model: type[BaseModel],
) -> None:
    """유효한 JSON 배열을 순서가 유지되는 불변 tuple로 저장해야 한다."""

    request = _validate_request(
        request_model,
        reference_file_idxs=list(TEST_REFERENCE_FILE_IDXS),
    )

    assert request.model_dump()["reference_file_idxs"] == TEST_REFERENCE_FILE_IDXS


@pytest.mark.parametrize("request_model", _REQUEST_MODELS)
def test_reference_file_idxs_field_is_required(
    request_model: type[BaseModel],
) -> None:
    """참조문서 필드 자체가 없는 요청을 거부해야 한다."""

    with pytest.raises(ValidationError):
        _validate_request(request_model)


@pytest.mark.parametrize("request_model", _REQUEST_MODELS)
def test_reference_file_idxs_rejects_empty_collection(
    request_model: type[BaseModel],
) -> None:
    """참조문서가 하나도 선택되지 않은 요청을 거부해야 한다."""

    with pytest.raises(ValidationError):
        _validate_request(
            request_model,
            reference_file_idxs=[],
        )


@pytest.mark.parametrize("request_model", _REQUEST_MODELS)
def test_reference_file_idxs_rejects_duplicate_values(
    request_model: type[BaseModel],
) -> None:
    """동일한 파일 식별자가 중복된 요청을 자동 정리하지 않고 거부해야 한다."""

    with pytest.raises(
        ValidationError,
        match="reference_file_idxs must contain unique values",
    ):
        _validate_request(
            request_model,
            reference_file_idxs=[123, 123],
        )


@pytest.mark.parametrize("request_model", _REQUEST_MODELS)
@pytest.mark.parametrize(
    "invalid_reference_file_idxs",
    [
        None,
        123,
        "123",
        [0],
        [-1],
        [True],
        [123.0],
        ["123"],
    ],
)
def test_reference_file_idxs_rejects_invalid_values(
    request_model: type[BaseModel],
    invalid_reference_file_idxs: object,
) -> None:
    """양의 정수 배열이 아닌 컨테이너와 원소 값을 모두 거부해야 한다."""

    with pytest.raises(ValidationError):
        _validate_request(
            request_model,
            reference_file_idxs=invalid_reference_file_idxs,
        )


@pytest.mark.parametrize("request_model", _REQUEST_MODELS)
def test_reference_file_idxs_accepts_maximum_selection_count(
    request_model: type[BaseModel],
) -> None:
    """정확히 최대 선택 개수까지는 유효한 요청으로 허용해야 한다."""

    reference_file_idxs = list(
        range(
            1,
            MAX_REFERENCE_FILE_COUNT + 1,
        )
    )

    request = _validate_request(
        request_model,
        reference_file_idxs=reference_file_idxs,
    )

    assert len(request.model_dump()["reference_file_idxs"]) == MAX_REFERENCE_FILE_COUNT


@pytest.mark.parametrize("request_model", _REQUEST_MODELS)
def test_reference_file_idxs_rejects_selection_over_limit(
    request_model: type[BaseModel],
) -> None:
    """최대 선택 개수를 한 개라도 초과한 요청을 거부해야 한다."""

    reference_file_idxs = list(
        range(
            1,
            MAX_REFERENCE_FILE_COUNT + 2,
        )
    )

    with pytest.raises(ValidationError):
        _validate_request(
            request_model,
            reference_file_idxs=reference_file_idxs,
        )