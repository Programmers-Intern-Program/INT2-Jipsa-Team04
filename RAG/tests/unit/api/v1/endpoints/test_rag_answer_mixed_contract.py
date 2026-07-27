"""혼합 문서 RAG endpoint의 OpenAPI 및 응답 스키마 계약을 검증한다."""

from http import HTTPStatus

from fastapi.routing import APIRoute

from jipsa_rag.api.v1.endpoints.rag_answer import router
from jipsa_rag.schemas.rag_answer import RagAnswerResponse


def _answer_route() -> APIRoute:
    """테스트 대상 POST /rag/answers route를 반환한다."""

    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == "/rag/answers":
            return route
    raise AssertionError("POST /rag/answers route was not registered.")


def test_response_json_schema_exposes_public_cited_source_ids() -> None:
    """AWS Backend가 본문 인용 순서를 명시적으로 받을 수 있어야 한다."""

    properties = RagAnswerResponse.model_json_schema()["properties"]

    assert "cited_source_ids" in properties
    assert properties["cited_source_ids"]["type"] == "array"
    assert (
        "source_locator"
        in RagAnswerResponse.model_json_schema()["$defs"]["RagAnswerSource"]["properties"]
    )


def test_openapi_description_documents_mixed_formats_and_partial_failure() -> None:
    """OpenAPI 설명이 PDF 전용 표현으로 회귀하지 않아야 한다."""

    route = _answer_route()
    description = route.description

    assert "PDF, DOCX, PPTX, TXT, XLSX" in description
    assert "source_locator" in description
    assert "일부 문서" in description
    assert "최종 Claude 호출" in description
    assert "실제 인용된 출처만" in description


def test_openapi_error_contract_includes_citation_validation_gateway_error() -> None:
    """인용 계약 위반을 502 응답 계약에 명시해야 한다."""

    route = _answer_route()

    assert HTTPStatus.BAD_GATEWAY in route.responses
    description = route.responses[HTTPStatus.BAD_GATEWAY]["description"]
    assert "SOURCE-N" in description
    assert "cited_source_ids" in description
    assert "sources" in description


def test_response_mapping_contract_failure_is_exposed_as_invalid_generation() -> None:
    """최종 인용 응답 매핑 실패도 내부 500이 아니라 생성 응답 502로 분류한다."""

    # 이 테스트는 공개 endpoint가 아니라 endpoint 내부 오류 변환 함수를 직접
    # 검증한다. 테스트 범위 밖에서 내부 구현을 불필요하게 import하지 않도록
    # 함수 내부에서 필요한 객체만 지연 import한다.
    from jipsa_rag.api.v1.endpoints.rag_answer import (
        _convert_rag_answer_service_error,
    )
    from jipsa_rag.core.error_codes import ErrorCode
    from jipsa_rag.services.rag_answer import RagAnswerServiceError

    converted = _convert_rag_answer_service_error(
        RagAnswerServiceError(operation="response_mapping_failed"),
        user_idx=45,
    )

    assert converted.error_code is ErrorCode.INVALID_GENERATION_RESPONSE
