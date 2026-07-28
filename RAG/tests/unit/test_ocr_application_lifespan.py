"""FastAPI lifespan이 공유 OCR Parser Factory를 등록하고 종료하는지 검증한다."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI

import jipsa_rag.main as main_module


@dataclass(frozen=True, slots=True)
class _StubSettings:
    """lifespan 분기와 로그에 필요한 최소 애플리케이션 설정."""

    database_check_on_startup: bool = False


@dataclass(frozen=True, slots=True)
class _StubProcessingSettings:
    """공유 Factory 생성과 시작 로그에 필요한 최소 OCR 설정."""

    ocr_enabled: bool = True
    ocr_max_concurrency: int = 1


class _StubParserFactory:
    """실제 CUDA process 없이 app.state 등록과 close 호출을 기록한다."""

    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


@pytest.mark.asyncio
async def test_lifespan_registers_one_factory_and_closes_it_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """애플리케이션 실행 중 같은 Factory를 노출하고 종료 시 정확히 한 번 닫는다."""

    application = FastAPI()
    factory = _StubParserFactory()
    qdrant_close_count = 0
    database_close_count = 0

    async def close_qdrant() -> None:
        nonlocal qdrant_close_count
        qdrant_close_count += 1

    async def close_database() -> None:
        nonlocal database_close_count
        database_close_count += 1

    monkeypatch.setattr(main_module, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(
        main_module,
        "get_document_processing_settings",
        lambda: _StubProcessingSettings(),
    )
    monkeypatch.setattr(
        main_module,
        "DocumentParserFactory",
        lambda *, settings: factory,
    )
    monkeypatch.setattr(main_module, "close_qdrant_vector_store", close_qdrant)
    monkeypatch.setattr(main_module, "close_database", close_database)

    async with main_module.lifespan(application):
        assert application.state.document_parser_factory is factory
        assert factory.close_count == 0

    assert factory.close_count == 1
    assert not hasattr(application.state, "document_parser_factory")
    assert qdrant_close_count == 1
    assert database_close_count == 1
