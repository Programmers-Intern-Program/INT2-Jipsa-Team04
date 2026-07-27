"""문서 형식별 Parser Factory의 다중 형식 선택 계약을 테스트한다."""

from collections.abc import Callable

import pytest

from jipsa_rag.infrastructure.document.exceptions import (
    DuplicateDocumentParserError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser_factory import DocumentParserFactory
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser


def test_factory_registers_all_supported_parsers_by_default() -> None:
    factory = DocumentParserFactory()

    assert factory.registered_file_types == frozenset(DocumentType)
    for file_type in DocumentType:
        assert factory.get_parser(file_type).file_type is file_type


@pytest.mark.parametrize("file_type", list(DocumentType))
@pytest.mark.parametrize("value_transform", [str.upper, str.lower, lambda value: f" {value} "])
def test_factory_normalizes_supported_file_type_strings(
    file_type: DocumentType,
    value_transform: Callable[[str], str],
) -> None:
    factory = DocumentParserFactory()
    transformed = value_transform(file_type.value)

    assert factory.get_parser(transformed).file_type is file_type
    assert factory.supports(transformed) is True


def test_factory_rejects_unknown_document_type_string() -> None:
    factory = DocumentParserFactory()

    assert factory.supports("CSV") is False
    with pytest.raises(UnsupportedDocumentTypeError) as exception_info:
        factory.get_parser("CSV")
    assert exception_info.value.file_type == "CSV"


def test_factory_allows_explicit_empty_registration_for_isolated_tests() -> None:
    factory = DocumentParserFactory(parsers=())

    assert factory.registered_file_types == frozenset()
    assert factory.supports(DocumentType.PDF) is False


def test_factory_rejects_duplicate_parser_registration() -> None:
    with pytest.raises(DuplicateDocumentParserError) as exception_info:
        DocumentParserFactory(parsers=(PdfDocumentParser(), PdfDocumentParser()))

    assert exception_info.value.file_type is DocumentType.PDF
