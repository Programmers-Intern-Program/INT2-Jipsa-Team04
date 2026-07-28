"""파일 처리 API가 lifespan 범위의 공유 Parser Factory를 사용하는지 검증한다."""

from fastapi import FastAPI
from starlette.requests import Request

from jipsa_rag.api.v1.endpoints.file_processing import get_document_parser_factory
from jipsa_rag.infrastructure.document.parser_factory import DocumentParserFactory


def _request(application: FastAPI) -> Request:
    """dependency 함수를 직접 호출할 수 있는 최소 HTTP Request를 생성한다."""

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/files/process",
            "raw_path": b"/api/v1/files/process",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "app": application,
        }
    )


def test_dependency_returns_same_lifespan_factory_for_repeated_requests() -> None:
    """요청마다 Factory를 재생성하지 않고 app.state 객체를 그대로 반환한다."""

    application = FastAPI()
    factory = DocumentParserFactory(parsers=())
    application.state.document_parser_factory = factory

    first = get_document_parser_factory(_request(application))
    second = get_document_parser_factory(_request(application))

    assert first is factory
    assert second is factory
    assert first is second


def test_dependency_rejects_call_outside_application_lifespan() -> None:
    """공유 Factory가 없는 경로에서 임시 CUDA 엔진을 묵시적으로 만들지 않는다."""

    application = FastAPI()

    try:
        get_document_parser_factory(_request(application))
    except RuntimeError as error:
        assert "outside application lifespan" in str(error)
    else:
        raise AssertionError("The dependency must reject a missing lifespan factory.")
