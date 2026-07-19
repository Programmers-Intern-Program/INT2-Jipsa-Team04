"""PDF 문서의 페이지 단위 텍스트 추출과 예외 처리를 테스트한다."""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentTextNotFoundError,
    EncryptedDocumentError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parsers.pdf import (
    PdfDocumentParser,
)


def _escape_pdf_text(value: str) -> str:
    """PDF 문자열 객체에서 특별한 의미를 갖는 문자를 이스케이프한다."""

    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_text_pdf(
    page_texts: tuple[str, ...],
) -> bytes:
    """외부 PDF 생성 패키지 없이 테스트용 텍스트 PDF 바이트를 생성한다."""

    objects: list[bytes] = []
    page_object_numbers = [3 + page_index * 2 for page_index in range(len(page_texts))]
    content_object_numbers = [4 + page_index * 2 for page_index in range(len(page_texts))]
    font_object_number = 3 + len(page_texts) * 2

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")

    page_references = " ".join(f"{object_number} 0 R" for object_number in page_object_numbers)
    objects.append(
        (f"<< /Type /Pages /Kids [{page_references}] /Count {len(page_texts)} >>").encode("ascii")
    )

    for page_index, text in enumerate(page_texts):
        page_object = (
            "<< /Type /Page /Parent 2 0 R "
            "/MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
            f"/Contents {content_object_numbers[page_index]} 0 R >>"
        ).encode("ascii")
        objects.append(page_object)

        if text:
            content = (f"BT\n/F1 12 Tf\n72 720 Td\n({_escape_pdf_text(text)}) Tj\nET\n").encode(
                "latin-1"
            )
        else:
            content = b""

        content_stream = (
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"endstream"
        )
        objects.append(content_stream)

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf_body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    object_offsets = [0]

    for object_number, object_content in enumerate(objects, start=1):
        object_offsets.append(len(pdf_body))
        pdf_body.extend(f"{object_number} 0 obj\n".encode("ascii"))
        pdf_body.extend(object_content)
        pdf_body.extend(b"\nendobj\n")

    xref_offset = len(pdf_body)
    pdf_body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf_body.extend(b"0000000000 65535 f \n")

    for object_offset in object_offsets[1:]:
        pdf_body.extend(f"{object_offset:010d} 00000 n \n".encode("ascii"))

    pdf_body.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n"
            "%%EOF\n"
        ).encode("ascii")
    )

    return bytes(pdf_body)


@pytest.mark.asyncio
async def test_extracts_pdf_text_by_page(
    tmp_path: Path,
) -> None:
    """페이지 순서와 1부터 시작하는 페이지 번호를 유지한다."""

    file_path = tmp_path / "document.pdf"
    file_path.write_bytes(
        _build_text_pdf(
            (
                "First page",
                "",
                "Third page",
            )
        )
    )

    parsed_document = await PdfDocumentParser().parse(file_path)

    assert parsed_document.file_type is DocumentType.PDF
    assert parsed_document.document_metadata["page_count"] == 3
    assert parsed_document.unit_count == 3
    assert parsed_document.text_unit_count == 2
    assert [unit.text for unit in parsed_document.units] == [
        "First page",
        "",
        "Third page",
    ]
    assert [unit.source_metadata["page_number"] for unit in parsed_document.units] == [
        1,
        2,
        3,
    ]


@pytest.mark.asyncio
async def test_rejects_pdf_without_extractable_text(
    tmp_path: Path,
) -> None:
    """모든 페이지가 비어 있으면 OCR 대상 문서로 보고 거부한다."""

    file_path = tmp_path / "image-only.pdf"
    file_path.write_bytes(
        _build_text_pdf(
            (
                "",
                "",
            )
        )
    )

    with pytest.raises(DocumentTextNotFoundError) as exception_info:
        await PdfDocumentParser().parse(file_path)

    assert exception_info.value.file_type is DocumentType.PDF


@pytest.mark.asyncio
async def test_rejects_encrypted_pdf(
    tmp_path: Path,
) -> None:
    """비밀번호 입력 경로가 없는 현재 단계에서는 암호화 PDF를 거부한다."""

    file_path = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(
        width=612,
        height=792,
    )
    writer.encrypt("test-password")

    with file_path.open("wb") as file_stream:
        writer.write(file_stream)

    with pytest.raises(EncryptedDocumentError) as exception_info:
        await PdfDocumentParser().parse(file_path)

    assert exception_info.value.file_type is DocumentType.PDF


@pytest.mark.asyncio
async def test_rejects_invalid_pdf_structure(
    tmp_path: Path,
) -> None:
    """PDF 헤더만 위장한 손상 파일을 유효한 문서로 처리하지 않는다."""

    file_path = tmp_path / "invalid.pdf"
    file_path.write_bytes(b"%PDF-1.7\nThis is not a complete PDF file.")

    with pytest.raises(InvalidDocumentError) as exception_info:
        await PdfDocumentParser().parse(file_path)

    assert exception_info.value.file_type is DocumentType.PDF


@pytest.mark.asyncio
async def test_rejects_missing_pdf_file(
    tmp_path: Path,
) -> None:
    """존재하지 않는 임시 파일 경로를 명확한 문서 예외로 변환한다."""

    file_path = tmp_path / "missing.pdf"

    with pytest.raises(DocumentFileNotFoundError) as exception_info:
        await PdfDocumentParser().parse(file_path)

    assert exception_info.value.file_path == file_path
