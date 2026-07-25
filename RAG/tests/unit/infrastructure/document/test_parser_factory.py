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


def test_factory_registers_only_pdf_parser_by_default() -> None:
    """기본 Factory에는 현재 지원 형식인 PDF 파서만 등록되어야 한다."""

    factory = DocumentParserFactory()

    pdf_parser = factory.get_parser(DocumentType.PDF)

    assert isinstance(pdf_parser, PdfDocumentParser)

    # TXT, DOCX, XLSX 및 PPTX는 내부 DocumentType에 정의되어 있더라도
    # 현재 Local RAG 파일 처리 정책에서는 지원하지 않는다.
    assert factory.registered_file_types == frozenset(
        {
            DocumentType.PDF,
        }
    )


@pytest.mark.parametrize(
    "file_type_value",
    [
        pytest.param(
            "pdf",
            id="lowercase-pdf",
        ),
        pytest.param(
            " PDF ",
            id="uppercase-pdf-with-whitespace",
        ),
        pytest.param(
            DocumentType.PDF,
            id="pdf-enum",
        ),
    ],
)
def test_factory_normalizes_supported_pdf_type(
    file_type_value: DocumentType | str,
) -> None:
    """PDF 문자열의 대소문자와 앞뒤 공백을 정규화해야 한다."""

    factory = DocumentParserFactory()

    parser = factory.get_parser(file_type_value)

    assert parser.file_type is DocumentType.PDF
    assert factory.supports(file_type_value) is True


@pytest.mark.parametrize(
    "unsupported_file_type",
    [
        pytest.param(
            DocumentType.TXT,
            id="txt-enum",
        ),
        pytest.param(
            "txt",
            id="txt-string",
        ),
        pytest.param(
            DocumentType.DOCX,
            id="docx-enum",
        ),
        pytest.param(
            "docx",
            id="docx-string",
        ),
        pytest.param(
            DocumentType.XLSX,
            id="xlsx-enum",
        ),
        pytest.param(
            "xlsx",
            id="xlsx-string",
        ),
        pytest.param(
            DocumentType.PPTX,
            id="pptx-enum",
        ),
        pytest.param(
            "pptx",
            id="pptx-string",
        ),
    ],
)
def test_factory_rejects_unregistered_document_types(
    unsupported_file_type: DocumentType | str,
) -> None:
    """TXT, DOCX, XLSX 및 PPTX 파서 조회를 거부해야 한다."""

    factory = DocumentParserFactory()

    assert factory.supports(unsupported_file_type) is False

    with pytest.raises(UnsupportedDocumentTypeError):
        factory.get_parser(unsupported_file_type)


def test_factory_rejects_unknown_document_type_string() -> None:
    """DocumentType에도 정의되지 않은 문자열을 거부해야 한다."""

    factory = DocumentParserFactory()

    assert factory.supports("CSV") is False

    with pytest.raises(UnsupportedDocumentTypeError) as exception_info:
        factory.get_parser("CSV")

    assert exception_info.value.file_type == "CSV"


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


def test_factory_allows_explicit_empty_registration() -> None:
    """빈 파서 목록을 전달하면 모든 문서 형식을 미지원으로 처리한다."""

    factory = DocumentParserFactory(parsers=())

    assert factory.registered_file_types == frozenset()
    assert factory.supports(DocumentType.PDF) is False

    with pytest.raises(UnsupportedDocumentTypeError):
        factory.get_parser(DocumentType.PDF)