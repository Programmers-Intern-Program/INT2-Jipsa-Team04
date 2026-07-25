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
from jipsa_rag.infrastructure.document.parsers.txt import (
    TxtDocumentParser,
)


def test_factory_registers_pdf_and_txt_parsers_by_default() -> None:
    """기본 Factory가 구현이 완료된 PDF와 TXT 파서를 등록한다."""

    factory = DocumentParserFactory()

    pdf_parser = factory.get_parser(DocumentType.PDF)
    txt_parser = factory.get_parser(DocumentType.TXT)

    assert isinstance(pdf_parser, PdfDocumentParser)
    assert isinstance(txt_parser, TxtDocumentParser)

    # 기본 등록 형식은 현재 실제 파서 구현이 완료된 PDF와 TXT다.
    #
    # DOCX, XLSX 및 PPTX는 DocumentType에 정의되어 있더라도
    # 파서 구현과 기본 등록이 완료되기 전까지 포함하지 않는다.
    assert factory.registered_file_types == frozenset(
        {
            DocumentType.PDF,
            DocumentType.TXT,
        }
    )


@pytest.mark.parametrize(
    (
        "file_type_value",
        "expected_file_type",
    ),
    [
        pytest.param(
            "pdf",
            DocumentType.PDF,
            id="lowercase-pdf",
        ),
        pytest.param(
            " txt ",
            DocumentType.TXT,
            id="lowercase-txt-with-whitespace",
        ),
    ],
)
def test_factory_normalizes_supported_file_type_strings(
    file_type_value: str,
    expected_file_type: DocumentType,
) -> None:
    """지원 형식 문자열의 대소문자와 앞뒤 공백을 정규화한다."""

    factory = DocumentParserFactory()

    parser = factory.get_parser(file_type_value)

    assert parser.file_type is expected_file_type
    assert factory.supports(file_type_value) is True


def test_factory_reports_unregistered_document_type() -> None:
    """Enum에는 정의됐지만 파서가 없는 DOCX를 미지원 형식으로 구분한다."""

    factory = DocumentParserFactory()

    assert factory.supports(DocumentType.DOCX) is False

    with pytest.raises(UnsupportedDocumentTypeError) as exception_info:
        factory.get_parser(DocumentType.DOCX)

    # DocumentType으로 전달된 값은 예외에서도 같은 Enum 값으로
    # 보존되어야 한다.
    assert exception_info.value.file_type is DocumentType.DOCX


def test_factory_rejects_unknown_document_type_string() -> None:
    """공통 DocumentType에 정의되지 않은 문자열을 거부한다."""

    factory = DocumentParserFactory()

    # TXT는 현재 정식 지원 형식이므로 알 수 없는 형식 테스트에 사용할 수 없다.
    #
    # DocumentType에 정의되지 않았으며 등록 파서도 없는 CSV를 사용하여
    # 문자열 정규화 실패와 미지원 형식 처리를 검증한다.
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