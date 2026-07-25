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


def _serialize_pdf_objects(
    objects: tuple[bytes, ...],
) -> bytes:
    """PDF 객체 목록을 xref와 trailer가 포함된 유효한 PDF로 직렬화한다.

    테스트 PDF를 만들기 위해 별도의 PDF 생성 패키지나 외부 파일을
    사용하지 않는다.

    각 객체의 실제 바이트 오프셋을 계산하여 xref 테이블에 기록하므로
    pypdf가 일반 PDF와 동일한 방식으로 구조를 해석할 수 있다.
    """

    # PDF 헤더와 바이너리 데이터 표식으로 문서 본문을 시작한다.
    pdf_body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    # 객체 번호는 1부터 시작한다.
    #
    # 객체 0은 PDF 표준에서 free 객체로 사용하므로 오프셋 목록에
    # 예약된 값을 먼저 추가한다.
    object_offsets = [0]

    for object_number, object_content in enumerate(
        objects,
        start=1,
    ):
        object_offsets.append(len(pdf_body))

        pdf_body.extend(
            f"{object_number} 0 obj\n".encode("ascii"),
        )
        pdf_body.extend(object_content)
        pdf_body.extend(b"\nendobj\n")

    # xref 테이블 시작 위치는 trailer의 startxref 값으로 사용한다.
    xref_offset = len(pdf_body)

    pdf_body.extend(
        f"xref\n0 {len(objects) + 1}\n".encode("ascii"),
    )

    # 객체 0은 사용하지 않는 free 객체다.
    pdf_body.extend(b"0000000000 65535 f \n")

    for object_offset in object_offsets[1:]:
        pdf_body.extend(
            f"{object_offset:010d} 00000 n \n".encode("ascii"),
        )

    pdf_body.extend(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_offset}\n"
            "%%EOF\n"
        ).encode("ascii")
    )

    return bytes(pdf_body)


def _build_text_pdf(
    page_texts: tuple[str, ...],
) -> bytes:
    """페이지별 텍스트를 포함한 테스트 PDF 바이트를 생성한다."""

    objects: list[bytes] = []

    # 1번 객체는 Catalog, 2번 객체는 Pages 루트로 사용한다.
    #
    # 이후 각 페이지마다 Page 객체와 Contents 객체를 순서대로 배치한다.
    page_object_numbers = [
        3 + page_index * 2
        for page_index in range(
            len(page_texts),
        )
    ]

    content_object_numbers = [
        4 + page_index * 2
        for page_index in range(
            len(page_texts),
        )
    ]

    # 모든 페이지가 공통으로 사용할 Helvetica 폰트 객체 번호다.
    font_object_number = 3 + len(page_texts) * 2

    objects.append(
        b"<< /Type /Catalog /Pages 2 0 R >>",
    )

    page_references = " ".join(
        f"{object_number} 0 R"
        for object_number in page_object_numbers
    )

    objects.append(
        (
            "<< /Type /Pages "
            f"/Kids [{page_references}] "
            f"/Count {len(page_texts)} >>"
        ).encode("ascii")
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
            # PDF 텍스트 연산자 BT/ET 사이에서 Helvetica 12pt로
            # 테스트 문자열을 페이지 좌표 (72, 720)에 출력한다.
            content = (
                "BT\n"
                "/F1 12 Tf\n"
                "72 720 Td\n"
                f"({_escape_pdf_text(text)}) Tj\n"
                "ET\n"
            ).encode("latin-1")
        else:
            # 빈 페이지는 길이가 0인 콘텐츠 스트림으로 구성한다.
            content = b""

        content_stream = (
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\n"
            + b"stream\n"
            + content
            + b"endstream"
        )

        objects.append(content_stream)

    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )

    return _serialize_pdf_objects(tuple(objects))


def _build_scanned_pdf() -> bytes:
    """실제 이미지 객체만 있고 텍스트 레이어가 없는 PDF를 생성한다.

    단순한 빈 페이지가 아니라 PDF Image XObject를 페이지에 배치한다.

    따라서 pypdf는 페이지 안의 이미지를 확인할 수 있지만
    extract_text()로 추출할 텍스트 연산자는 존재하지 않는다.

    이 구조는 OCR이 필요한 이미지 기반 스캔 PDF를 재현한다.
    """

    # 1×1 Gray 이미지 한 픽셀이다.
    #
    # 테스트 목적은 이미지 내용이 아니라 PDF 페이지가 실제 이미지
    # XObject를 포함하면서 텍스트 레이어는 갖지 않는 상황을 만드는 것이다.
    image_bytes = b"\x80"

    # /Im0 이미지 객체를 페이지 좌표에 그리는 PDF 그래픽 명령이다.
    #
    # q와 Q는 그래픽 상태를 저장하고 복원하며,
    # cm은 이미지의 크기와 위치를 지정한다.
    drawing_commands = (
        b"q\n"
        b"100 0 0 100 72 600 cm\n"
        b"/Im0 Do\n"
        b"Q\n"
    )

    objects = (
        # 1: Catalog
        b"<< /Type /Catalog /Pages 2 0 R >>",
        # 2: Pages
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        # 3: 이미지 XObject를 참조하는 Page
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << /XObject << /Im0 5 0 R >> >> "
            b"/Contents 4 0 R >>"
        ),
        # 4: 이미지를 페이지에 그리는 Contents Stream
        (
            b"<< /Length "
            + str(len(drawing_commands)).encode("ascii")
            + b" >>\n"
            + b"stream\n"
            + drawing_commands
            + b"endstream"
        ),
        # 5: 실제 Image XObject
        (
            b"<< /Type /XObject "
            b"/Subtype /Image "
            b"/Width 1 "
            b"/Height 1 "
            b"/ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 "
            b"/Length "
            + str(len(image_bytes)).encode("ascii")
            + b" >>\n"
            + b"stream\n"
            + image_bytes
            + b"\nendstream"
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

    assert [
        unit.source_metadata["page_number"]
        for unit in parsed_document.units
    ] == [
        1,
        2,
        3,
    ]


@pytest.mark.asyncio
async def test_rejects_zero_page_pdf(
    tmp_path: Path,
) -> None:
    """페이지가 하나도 없는 PDF를 유효한 검색 문서로 처리하지 않는다."""

    file_path = tmp_path / "zero-page.pdf"

    # PdfWriter에 페이지를 추가하지 않고 저장하면 PDF 구조 자체는
    # 유효하지만 Pages 트리의 페이지 수가 0인 문서가 생성된다.
    writer = PdfWriter()

    with file_path.open("wb") as file_stream:
        writer.write(file_stream)

    with pytest.raises(InvalidDocumentError) as exception_info:
        await PdfDocumentParser().parse(file_path)

    assert exception_info.value.file_type is DocumentType.PDF


@pytest.mark.asyncio
async def test_rejects_blank_pdf_without_extractable_text(
    tmp_path: Path,
) -> None:
    """페이지는 있지만 모든 콘텐츠 스트림이 비어 있는 PDF를 거부한다."""

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
async def test_rejects_scanned_pdf_without_text_layer(
    tmp_path: Path,
) -> None:
    """이미지만 포함하고 텍스트 레이어가 없는 스캔 PDF를 거부한다."""

    file_path = tmp_path / "scanned-image-only.pdf"

    # 페이지 안에 실제 Image XObject가 존재하지만 텍스트 연산자는
    # 존재하지 않는 PDF를 사용한다.
    #
    # 현재 Local RAG는 OCR을 지원하지 않으므로 검색용 텍스트를 만들지
    # 않고 DocumentTextNotFoundError로 명확히 거부해야 한다.
    file_path.write_bytes(
        _build_scanned_pdf(),
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

    file_path.write_bytes(
        b"%PDF-1.7\nThis is not a complete PDF file.",
    )

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