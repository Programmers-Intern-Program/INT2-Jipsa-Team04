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
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser


def _escape_pdf_text(
    value: str,
) -> str:
    """PDF literal string에서 문법 문자로 사용되는 값을 이스케이프한다."""

    return (
        value.replace(
            "\\",
            "\\\\",
        )
        .replace(
            "(",
            "\\(",
        )
        .replace(
            ")",
            "\\)",
        )
    )


def _serialize_pdf_objects(
    objects: tuple[bytes, ...],
) -> bytes:
    """완전한 xref와 trailer를 포함한 결정적 PDF 바이트를 생성한다.

    테스트 fixture가 단순히 ``%PDF`` 시그니처만 흉내 내지 않고
    ``PdfReader``가 실제 객체 그래프와 content stream을 읽도록 한다.
    """

    pdf_body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    # xref의 0번 객체는 free entry이므로 실제 객체 offset 앞에
    # 예약된 0 값을 둔다.
    object_offsets = [
        0,
    ]

    for object_number, object_content in enumerate(
        objects,
        start=1,
    ):
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
            f"trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n"
            f"{xref_offset}\n"
            f"%%EOF\n"
        ).encode("ascii")
    )

    return bytes(pdf_body)


def _build_text_pdf(
    page_texts: tuple[str, ...],
) -> bytes:
    """외부 PDF 생성 패키지 없이 페이지별 텍스트 PDF를 생성한다."""

    objects: list[bytes] = []

    # 1번 객체는 Catalog, 2번 객체는 Pages 루트다.
    #
    # 그 이후에는 페이지 객체와 해당 페이지의 content stream을
    # 번갈아 배치하고 마지막 객체를 공통 Helvetica 폰트로 사용한다.
    page_object_numbers = [3 + page_index * 2 for page_index in range(len(page_texts))]
    content_object_numbers = [4 + page_index * 2 for page_index in range(len(page_texts))]
    font_object_number = 3 + len(page_texts) * 2

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")

    page_references = " ".join(f"{object_number} 0 R" for object_number in page_object_numbers)

    objects.append(
        (f"<< /Type /Pages /Kids [{page_references}] /Count {len(page_texts)} >>").encode("ascii")
    )

    for page_index, text in enumerate(page_texts):
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R "
                "/MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
                f"/Contents {content_object_numbers[page_index]} 0 R >>"
            ).encode("ascii")
        )

        if text:
            content = (f"BT\n/F1 12 Tf\n72 720 Td\n({_escape_pdf_text(text)}) Tj\nET\n").encode(
                "latin-1"
            )
        else:
            # 빈 페이지는 유효한 PDF 페이지이지만 텍스트 연산자가 없는
            # 길이 0 content stream으로 구성한다.
            content = b""

        objects.append(
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"endstream"
        )

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    return _serialize_pdf_objects(tuple(objects))


def _build_image_only_scanned_pdf() -> bytes:
    """텍스트 레이어 없이 이미지 XObject만 포함한 스캔 PDF를 생성한다.

    단순 빈 페이지가 아니라 실제 ``/Subtype /Image`` 객체와 ``Do`` 연산자를
    포함한다. 따라서 테스트가 OCR 미지원 스캔 문서 경로를 명확히 재현한다.
    """

    # 1x1 검은색 RGB 픽셀이다.
    image_bytes = b"\x00\x00\x00"

    # 이미지 XObject를 페이지 대부분 크기로 그리지만 BT/Tj 같은
    # 텍스트 연산자는 포함하지 않는다.
    page_content = b"q\n500 0 0 700 56 46 cm\n/Im1 Do\nQ\n"

    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << /XObject << /Im1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        (
            b"<< /Type /XObject /Subtype /Image "
            b"/Width 1 /Height 1 "
            b"/ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 "
            b"/Length 3 >>\n"
            b"stream\n" + image_bytes + b"\nendstream"
        ),
        (
            b"<< /Length "
            + str(len(page_content)).encode("ascii")
            + b" >>\nstream\n"
            + page_content
            + b"endstream"
        ),
    )

    return _serialize_pdf_objects(objects)


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
async def test_rejects_zero_byte_pdf_file(
    tmp_path: Path,
) -> None:
    """크기가 0인 빈 PDF 파일을 유효한 문서로 처리하지 않는다."""

    file_path = tmp_path / "empty.pdf"

    file_path.write_bytes(b"")

    with pytest.raises(InvalidDocumentError) as exception_info:
        await PdfDocumentParser().parse(file_path)

    assert exception_info.value.file_type is DocumentType.PDF


@pytest.mark.asyncio
async def test_rejects_pdf_without_pages(
    tmp_path: Path,
) -> None:
    """PDF 구조는 유효하지만 페이지가 하나도 없는 문서를 거부한다."""

    file_path = tmp_path / "no-pages.pdf"

    writer = PdfWriter()

    with file_path.open("wb") as file_stream:
        writer.write(file_stream)

    with pytest.raises(InvalidDocumentError) as exception_info:
        await PdfDocumentParser().parse(file_path)

    assert exception_info.value.file_type is DocumentType.PDF


@pytest.mark.asyncio
async def test_rejects_pdf_without_extractable_text(
    tmp_path: Path,
) -> None:
    """모든 페이지가 비어 있으면 검색 가능한 텍스트가 없는 것으로 거부한다."""

    file_path = tmp_path / "blank-pages.pdf"

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
async def test_rejects_image_only_scanned_pdf_without_ocr(
    tmp_path: Path,
) -> None:
    """이미지 객체만 있는 실제 스캔 PDF는 OCR 미지원 정책에 따라 거부한다."""

    file_path = tmp_path / "scanned-image-only.pdf"

    file_path.write_bytes(_build_image_only_scanned_pdf())

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
