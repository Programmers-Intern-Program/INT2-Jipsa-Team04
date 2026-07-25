"""문서 형식별 파서 선택 Factory의 PDF 전용 계약을 테스트한다."""

import pytest

from jipsa_rag.infrastructure.document.exceptions import (
    DuplicateDocumentParserError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser_factory import (
    DocumentParserFactory,
)
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser


def test_factory_registers_only_pdf_parser_by_default() -> None:
    """기본 Factory는 현재 정식 지원 형식인 PDF만 등록한다."""

    factory = DocumentParserFactory()

    assert isinstance(
        factory.get_parser(DocumentType.PDF),
        PdfDocumentParser,
    )

    # DocumentType에는 향후 지원 후보 형식이 남아 있어도 운영 기본
    # Factory에는 PDF만 등록되어야 한다.
    assert factory.registered_file_types == frozenset(
        {
            DocumentType.PDF,
        }
    )


@pytest.mark.parametrize(
    "file_type_value",
    [
        pytest.param(
            "PDF",
            id="uppercase",
        ),
        pytest.param(
            "pdf",
            id="lowercase",
        ),
        pytest.param(
            " Pdf ",
            id="mixed-case-with-whitespace",
        ),
    ],
)
def test_factory_normalizes_pdf_file_type_strings(
    file_type_value: str,
) -> None:
    """PDF 문자열의 대소문자와 앞뒤 공백을 정규화한다."""

    factory = DocumentParserFactory()
    parser = factory.get_parser(file_type_value)

    assert parser.file_type is DocumentType.PDF
    assert factory.supports(file_type_value) is True


@pytest.mark.parametrize(
    "file_type",
    [
        pytest.param(
            DocumentType.TXT,
            id="txt",
        ),
        pytest.param(
            DocumentType.DOCX,
            id="docx",
        ),
        pytest.param(
            DocumentType.XLSX,
            id="xlsx",
        ),
        pytest.param(
            DocumentType.PPTX,
            id="pptx",
        ),
    ],
)
def test_factory_rejects_non_pdf_document_types(
    file_type: DocumentType,
) -> None:
    """TXT·DOCX·XLSX·PPTX는 등록 파서가 없으므로 명시적으로 거부한다."""

    factory = DocumentParserFactory()

    assert factory.supports(file_type) is False
    assert factory.supports(file_type.value.lower()) is False

    with pytest.raises(
        UnsupportedDocumentTypeError
    ) as exception_info:
        factory.get_parser(file_type)

    # Enum 입력은 예외에서도 동일한 형식 값으로 보존되어야 한다.
    assert exception_info.value.file_type is file_type


def test_factory_rejects_unknown_document_type_string() -> None:
    """공통 DocumentType에도 정의되지 않은 문자열을 거부한다."""

    factory = DocumentParserFactory()

    assert factory.supports("CSV") is False

    with pytest.raises(
        UnsupportedDocumentTypeError
    ) as exception_info:
        factory.get_parser("CSV")

    assert exception_info.value.file_type == "CSV"


def test_factory_allows_explicit_empty_registration_for_isolated_tests() -> None:
    """빈 파서 목록을 전달하면 기본 PDF 파서도 자동 추가하지 않는다."""

    factory = DocumentParserFactory(
        parsers=()
    )

    assert factory.registered_file_types == frozenset()
    assert factory.supports(DocumentType.PDF) is False


def test_factory_rejects_duplicate_parser_registration() -> None:
    """동일한 PDF 파서를 두 번 등록하면 선택 모호성을 방지하기 위해 거부한다."""

    with pytest.raises(
        DuplicateDocumentParserError
    ) as exception_info:
        DocumentParserFactory(
            parsers=(
                PdfDocumentParser(),
                PdfDocumentParser(),
            )
        )

    assert exception_info.value.file_type is DocumentType.PDF