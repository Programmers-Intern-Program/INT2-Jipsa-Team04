"""PDF 문서를 페이지 단위 텍스트로 변환하는 파서를 제공한다."""

import asyncio
import re
from pathlib import Path
from typing import Final

from pypdf import PageObject, PdfReader
from pypdf.errors import PdfReadError

from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentParserError,
    DocumentReadError,
    DocumentTextExtractionError,
    DocumentTextNotFoundError,
    EncryptedDocumentError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)

# PDF 추출 결과에 과도한 빈 줄이 남으면 후속 청킹 시
# 의미 없는 공백이 토큰으로 포함될 수 있다.
# 따라서 세 줄 이상의 연속된 줄바꿈을 두 줄로 제한한다.
_EXCESSIVE_BLANK_LINES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")


class PdfDocumentParser:
    """pypdf를 사용하여 PDF 텍스트를 페이지 단위로 추출한다."""

    @property
    def file_type(self) -> DocumentType:
        """이 파서가 처리하는 문서 형식을 반환한다."""

        return DocumentType.PDF

    async def parse(
        self,
        file_path: Path,
    ) -> ParsedDocument:
        """동기 PDF 파싱을 작업 스레드에서 실행한다.

        PDF 파싱 라이브러리는 동기 파일 입출력과 CPU 작업을 수행한다.
        asyncio.to_thread()를 사용하여 FastAPI 이벤트 루프가
        파싱이 끝날 때까지 직접 차단되지 않도록 한다.
        """

        return await asyncio.to_thread(
            self._parse_sync,
            file_path,
        )

    def _parse_sync(
        self,
        file_path: Path,
    ) -> ParsedDocument:
        """PDF 파일을 열고 페이지 순서대로 텍스트를 추출한다."""

        self._validate_file_path(file_path)

        try:
            with file_path.open("rb") as file_stream:
                # strict=False는 복구 가능한 PDF 구조 문제를 허용한다.
                # 실제로 해석할 수 없는 문서는 PdfReadError로 구분한다.
                reader = PdfReader(
                    file_stream,
                    strict=False,
                )

                # 비밀번호 전달 경로가 현재 API 계약에 없으므로
                # 암호화 PDF는 임의로 해제하지 않고 명시적으로 거부한다.
                if reader.is_encrypted:
                    raise EncryptedDocumentError(self.file_type)

                page_count = len(reader.pages)

                if page_count == 0:
                    raise InvalidDocumentError(self.file_type)

                units = tuple(
                    self._parse_page(
                        page=page,
                        page_number=page_index + 1,
                    )
                    for page_index, page in enumerate(reader.pages)
                )

        except DocumentParserError:
            raise

        except PdfReadError as error:
            raise InvalidDocumentError(self.file_type) from error

        except OSError as error:
            raise DocumentReadError(file_path) from error

        parsed_document = ParsedDocument(
            file_type=self.file_type,
            units=units,
            document_metadata={
                "page_count": page_count,
            },
        )

        # 모든 페이지가 이미지뿐인 스캔 PDF처럼 추출 가능한 텍스트가
        # 하나도 없는 문서는 현재 OCR 미지원 정책에 따라 실패로 처리한다.
        if parsed_document.text_unit_count == 0:
            raise DocumentTextNotFoundError(self.file_type)

        return parsed_document

    def _parse_page(
        self,
        *,
        page: PageObject,
        page_number: int,
    ) -> ParsedDocumentUnit:
        """PDF 한 페이지의 텍스트와 페이지 번호를 반환한다."""

        try:
            # extract_text()는 PDF의 텍스트 레이어만 읽는다.
            # 이미지 자체의 글자는 OCR 대상이므로 여기서 추출하지 않는다.
            raw_text = page.extract_text()
        except Exception as error:
            # pypdf는 손상된 페이지 객체에 따라
            # 여러 예외를 발생시킬 수 있다.
            # 파서 외부에는 라이브러리 예외 대신
            # 공통 문서 예외를 전달한다.
            raise DocumentTextExtractionError(
                file_type=self.file_type,
                source_metadata={
                    "page_number": page_number,
                },
            ) from error

        return ParsedDocumentUnit(
            text=self._normalize_text(raw_text),
            source_metadata={
                # 사용자에게 표시하는 페이지 번호와 동일하게
                # 0이 아닌 1부터 시작하는 값을 저장한다.
                "page_number": page_number,
            },
        )

    @staticmethod
    def _validate_file_path(
        file_path: Path,
    ) -> None:
        """파싱 대상 경로가 실제 일반 파일인지 확인한다."""

        if not file_path.exists() or not file_path.is_file():
            raise DocumentFileNotFoundError(file_path)

    @staticmethod
    def _normalize_text(
        text: str | None,
    ) -> str:
        """줄바꿈과 행 끝 공백을 일관된 형태로 정규화한다."""

        if text is None:
            return ""

        normalized_line_endings = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        trimmed_lines = "\n".join(line.rstrip() for line in normalized_line_endings.split("\n"))
        normalized_blank_lines = _EXCESSIVE_BLANK_LINES_PATTERN.sub(
            "\n\n",
            trimmed_lines,
        )

        return normalized_blank_lines.strip()
