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
from jipsa_rag.infrastructure.document.parsers.pdf import (
    PdfDocumentParser,
)


def test_factory_registers_only_pdf_parser_by_default() -> None:
    """기본 Factory가 공식 지원 형식인 PDF 파서만 등록한다."""

    factory = DocumentParserFactory()

    parser = factory.get_parser(
        DocumentType.PDF,
    )

    assert isinstance(
        parser,
        PdfDocumentParser,
    )

    # TXT 모듈이 저장소에 존재하더라도 실제 파서 구현과 공식 지원
    # 계약이 없는 상태이므로 기본 등록 형식에 포함되어서는 안 된다.
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
            " Pdf ",
            id="mixed-case-pdf-with-whitespace",
        ),
    ],
)
def test_factory_normalizes_pdf_file_type_string(
    file_type_value: str,
) -> None:
    """PDF 문자열의 대소문자와 앞뒤 공백을 정규화한다."""

    factory = DocumentParserFactory()

    parser = factory.get_parser(
        file_type_value,
    )

    assert parser.file_type is DocumentType.PDF
    assert factory.supports(file_type_value) is True


@pytest.mark.parametrize(
    "unsupported_file_type",
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
def test_factory_rejects_unregistered_non_pdf_document_types(
    unsupported_file_type: DocumentType,
) -> None:
    """TXT·DOCX·XLSX·PPTX는 파서 미등록 형식으로 거부한다."""

    factory = DocumentParserFactory()

    # Enum에는 형식 이름이 정의되어 있더라도 실제 파서가 등록되지
    # 않았으므로 현재 서비스 지원 형식으로 판단하면 안 된다.
    assert factory.supports(
        unsupported_file_type,
    ) is False

    with pytest.raises(
        UnsupportedDocumentTypeError,
    ) as exception_info:
        factory.get_parser(
            unsupported_file_type,
        )

    # DocumentType으로 전달한 값은 예외에도 같은 Enum 값으로 보존되어
    # 상위 계층이 실패 형식을 안전하게 분류할 수 있어야 한다.
    assert exception_info.value.file_type is unsupported_file_type


@pytest.mark.parametrize(
    (
        "file_type_value",
        "expected_document_type",
    ),
    [
        pytest.param(
            "txt",
            DocumentType.TXT,
            id="lowercase-txt",
        ),
        pytest.param(
            " DOCX ",
            DocumentType.DOCX,
            id="uppercase-docx-with-whitespace",
        ),
        pytest.param(
            "xlsx",
            DocumentType.XLSX,
            id="lowercase-xlsx",
        ),
        pytest.param(
            "Pptx",
            DocumentType.PPTX,
            id="mixed-case-pptx",
        ),
    ],
)
def test_factory_normalizes_but_rejects_unregistered_file_type_strings(
    file_type_value: str,
    expected_document_type: DocumentType,
) -> None:
    """미지원 문자열은 정규화되더라도 파서 조회 단계에서 거부한다."""

    factory = DocumentParserFactory()

    assert factory.supports(
        file_type_value,
    ) is False

    with pytest.raises(
        UnsupportedDocumentTypeError,
    ) as exception_info:
        factory.get_parser(
            file_type_value,
        )

    # 문자열이 공통 DocumentType으로 정상 변환된 뒤 등록 여부 검증에서
    # 실패했다는 것을 확인한다.
    assert exception_info.value.file_type is expected_document_type


def test_factory_rejects_unknown_document_type_string() -> None:
    """DocumentType에도 정의되지 않은 문자열을 거부한다."""

    factory = DocumentParserFactory()

    assert factory.supports(
        "CSV",
    ) is False

    with pytest.raises(
        UnsupportedDocumentTypeError,
    ) as exception_info:
        factory.get_parser(
            "CSV",
        )

    # Enum 변환 자체가 불가능한 입력은 원래 문자열을 예외에 보존한다.
    assert exception_info.value.file_type == "CSV"


def test_factory_allows_explicit_empty_parser_registration() -> None:
    """빈 파서 목록을 전달하면 아무 형식도 지원하지 않는 Factory를 만든다."""

    factory = DocumentParserFactory(
        parsers=(),
    )

    assert factory.registered_file_types == frozenset()
    assert factory.supports(DocumentType.PDF) is False

    with pytest.raises(
        UnsupportedDocumentTypeError,
    ):
        factory.get_parser(
            DocumentType.PDF,
        )


def test_factory_rejects_duplicate_parser_registration() -> None:
    """동일한 PDF 파서가 중복 등록되는 것을 방지한다."""

    with pytest.raises(
        DuplicateDocumentParserError,
    ) as exception_info:
        DocumentParserFactory(
            parsers=(
                PdfDocumentParser(),
                PdfDocumentParser(),
            )
        )

    assert exception_info.value.file_type is DocumentType.PDF