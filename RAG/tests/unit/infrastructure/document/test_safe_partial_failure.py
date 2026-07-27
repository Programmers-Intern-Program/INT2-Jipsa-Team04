"""선택적 이미지 처리 실패가 텍스트 인제스트와 로그 안전성을 훼손하지 않는지 검증한다."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jipsa_rag.infrastructure.document.images.models import DocumentImageExtraction
from jipsa_rag.infrastructure.document.media_aware import _OcrAwareParserMixin
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)


class _FailingExtractor:
    @property
    def file_type(self) -> DocumentType:
        """실패 테스트가 사용하는 PDF 추출기 계약을 충족한다."""

        return DocumentType.PDF

    async def extract(self, file_path: Path) -> DocumentImageExtraction:
        raise RuntimeError(f"secret-path={file_path}")


class _PassThroughEnricher:
    async def enrich(
        self,
        *,
        document: ParsedDocument,
        extraction: DocumentImageExtraction,
    ) -> ParsedDocument:
        return document


class _Parser(_OcrAwareParserMixin):
    pass


@pytest.mark.asyncio
async def test_text_ingest_survives_image_failure_without_logging_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parser = _Parser()
    parser._initialize_ocr(
        image_extractor=_FailingExtractor(),
        ocr_enricher=_PassThroughEnricher(),
    )
    secret_path = Path("/private/source/customer-secret.pdf")

    async def parse_text(_: Path) -> ParsedDocument:
        return ParsedDocument(
            file_type=DocumentType.PDF,
            units=(ParsedDocumentUnit(text="검색 가능한 본문", source_metadata={}),),
        )

    with caplog.at_level(logging.WARNING):
        result = await parser._parse_with_ocr(
            file_path=secret_path,
            file_type=DocumentType.PDF,
            parse_text=parse_text,
        )

    assert result.units[0].text == "검색 가능한 본문"
    assert str(secret_path) not in caplog.text
    assert "customer-secret" not in caplog.text
