"""문서 형식별 파서 선택 Factory의 등록과 조회 동작을 테스트한다."""

import pytest

from jipsa_rag.infrastructure.document.exceptions import (
    DuplicateDocumentParserError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser_factory import (
    DocumentParserFactory,
)
from jipsa_rag.infrastructure.document.parsers.pdf import (
    PdfDocumentParser,
)


def test_factory_registers_pdf_parser_by_default() -> None:
    """기본 Factory가 현재 구현된 PDF 파서를 등록한다."""

    factory = DocumentParserFactory()

    parser = factory.get_parser(DocumentType.PDF)

    assert isinstance(parser, PdfDocumentParser)
    assert factory.registered_file_types == frozenset(
        {
            DocumentType.PDF,
        }
    )


def test_factory_normalizes_lowercase_file_type() -> None:
    """API에서 전달된 소문자 형식 문자열도 공통 문서 형식으로 변환한다."""

    factory = DocumentParserFactory()

    parser = factory.get_parser("pdf")

    assert parser.file_type is DocumentType.PDF


def test_factory_reports_unregistered_document_type() -> None:
    """아직 파서가 없는 DOCX를 미지원 형식으로 구분한다."""

    factory = DocumentParserFactory()

    assert factory.supports(DocumentType.DOCX) is False

    with pytest.raises(UnsupportedDocumentTypeError) as exception_info:
        factory.get_parser(DocumentType.DOCX)

    assert exception_info.value.file_type is DocumentType.DOCX


def test_factory_rejects_unknown_document_type_string() -> None:
    """공통 문서 형식에 정의되지 않은 문자열을 거부한다."""

    factory = DocumentParserFactory()

    assert factory.supports("TXT") is False

    with pytest.raises(UnsupportedDocumentTypeError):
        factory.get_parser("TXT")


def test_factory_rejects_duplicate_parser_registration() -> None:
    """동일한 문서 형식의 파서가 중복 등록되는 것을 방지한다."""

    with pytest.raises(DuplicateDocumentParserError) as exception_info:
        DocumentParserFactory(
            parsers=(
                PdfDocumentParser(),
                PdfDocumentParser(),
            )
        )

    assert exception_info.value.file_type is DocumentType.PDF
