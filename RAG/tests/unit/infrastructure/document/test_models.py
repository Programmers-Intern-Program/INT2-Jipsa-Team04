"""공통 문서 파싱 결과 모델의 불변성과 편의 속성을 테스트한다."""

import pytest

from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)


def test_parsed_document_unit_copies_and_freezes_source_metadata() -> None:
    """원본 위치 메타데이터가 외부 변경으로 수정되지 않도록 보호한다."""

    source_metadata = {
        "page_number": 1,
    }
    unit = ParsedDocumentUnit(
        text="First page",
        source_metadata=source_metadata,
    )

    source_metadata["page_number"] = 2

    assert unit.source_metadata["page_number"] == 1

    with pytest.raises(TypeError):
        unit.source_metadata["page_number"] = 3  # type: ignore[index]


def test_parsed_document_combines_only_non_empty_units() -> None:
    """빈 원본 단위는 유지하되 전체 텍스트 결합에서는 제외한다."""

    document = ParsedDocument(
        file_type=DocumentType.PDF,
        units=(
            ParsedDocumentUnit(
                text="First page",
                source_metadata={
                    "page_number": 1,
                },
            ),
            ParsedDocumentUnit(
                text="",
                source_metadata={
                    "page_number": 2,
                },
            ),
            ParsedDocumentUnit(
                text="Third page",
                source_metadata={
                    "page_number": 3,
                },
            ),
        ),
        document_metadata={
            "page_count": 3,
        },
    )

    assert document.text == "First page\n\nThird page"
    assert document.unit_count == 3
    assert document.text_unit_count == 2
    assert document.document_metadata["page_count"] == 3
