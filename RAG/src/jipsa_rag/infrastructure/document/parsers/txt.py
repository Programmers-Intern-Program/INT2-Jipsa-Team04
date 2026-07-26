"""TXT 인코딩을 감지하고 줄 번호·문자 범위 메타데이터를 추출한다.

TXT에는 고정된 컨테이너나 Magic Byte가 없으므로 strict 디코딩 성공만으로는
일반 텍스트 여부를 보장할 수 없다. 이 파서는 BOM, UTF-8, charset-normalizer와
CP949 보조 휴리스틱을 순서대로 적용하고, 알려진 바이너리 시그니처·NULL 바이트·
제어 문자 비율을 별도로 검사한다.

줄바꿈은 LF로 정규화한 뒤 각 줄을 독립적인 ``ParsedDocumentUnit``으로 만든다.
각 unit은 1부터 시작하는 줄 번호뿐 아니라 정규화된 원문 문자열 기준의
``source_char_start``와 ``source_char_end``를 보존한다. end 값은 Python 슬라이스와
동일한 exclusive 위치다. 줄 끝 공백을 제거한 실제 색인 텍스트 범위는
``text_char_start``와 ``text_char_end``로 별도 기록한다.
"""

import asyncio
import codecs
from pathlib import Path
from typing import Final

from charset_normalizer import from_bytes

from jipsa_rag.infrastructure.document.exceptions import (
    DocumentReadError,
    DocumentTextNotFoundError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)
from jipsa_rag.infrastructure.document.parsers._common import validate_regular_file

_TXT_PARSER_TYPE: Final[str] = "TXT_TEXT"

# 1.1.0부터 각 줄에 정규화 원문 기준 문자 범위와 줄바꿈 범위를 보존한다.
# Parser_Version 변화는 기존 TXT 문서와 다른 결정적 Chunk ID를 생성하므로
# Local RAG DB와 Qdrant가 이전 색인을 안전하게 대체한다.
_TXT_PARSER_VERSION: Final[str] = "1.1.0"

_BINARY_CONTROL_RATIO_LIMIT: Final[float] = 0.05

_KNOWN_BINARY_PREFIXES: Final[tuple[bytes, ...]] = (
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"\x7fELF",
    b"MZ",
)


class TxtDocumentParser:
    """일반 텍스트를 검증하고 원본 줄 위치를 보존해 파싱한다."""

    @property
    def file_type(self) -> DocumentType:
        """이 파서가 처리하는 문서 형식을 반환한다."""

        return DocumentType.TXT

    @property
    def parser_type(self) -> str:
        """Local RAG DB에 저장할 파서 종류를 반환한다."""

        return _TXT_PARSER_TYPE

    @property
    def parser_version(self) -> str:
        """재파싱과 결정적 Chunk ID에 사용하는 파서 버전을 반환한다."""

        return _TXT_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        """동기식 파일 읽기와 인코딩 감지를 작업 스레드에서 실행한다."""

        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: Path) -> ParsedDocument:
        """TXT 바이트를 검증하고 줄별 위치 unit으로 변환한다."""

        validate_regular_file(file_path)

        try:
            payload = file_path.read_bytes()
        except OSError as error:
            raise DocumentReadError(file_path) from error

        if not payload:
            raise DocumentTextNotFoundError(self.file_type)

        encoding, has_bom = self._detect_encoding(payload)

        try:
            text = payload.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as error:
            raise InvalidDocumentError(self.file_type) from error

        text = text.removeprefix("\ufeff")
        self._reject_binary_content(
            payload,
            text,
            encoding=encoding,
            has_bom=has_bom,
        )

        # 문자 범위는 플랫폼별 CRLF 차이와 무관해야 하므로 LF 정규화 이후 문자열을
        # 기준으로 계산한다. 원본 바이트 오프셋과 혼동하지 않도록 키 이름에 char를 쓴다.
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        units = self._build_line_units(lines, encoding=encoding)

        parsed_document = ParsedDocument(
            file_type=self.file_type,
            units=units,
            document_metadata={
                "line_count": len(lines),
                "encoding": encoding,
                "byte_order_mark": has_bom,
                "normalized_char_count": len(normalized),
                "source_byte_count": len(payload),
            },
        )

        if parsed_document.text_unit_count == 0:
            raise DocumentTextNotFoundError(self.file_type)

        return parsed_document

    @staticmethod
    def _build_line_units(
        lines: list[str],
        *,
        encoding: str,
    ) -> tuple[ParsedDocumentUnit, ...]:
        """정규화 문자열의 각 줄과 exclusive 문자 범위를 unit으로 만든다.

        ``str.split("\\n")``은 마지막 줄바꿈 뒤의 빈 문자열도 반환한다. 이 빈 unit을
        유지하면 편집기에서 보이는 줄 수와 다음 줄 번호가 정확하게 일치한다.
        """

        units: list[ParsedDocumentUnit] = []
        cursor = 0
        last_line_index = len(lines) - 1

        for zero_based_index, raw_line in enumerate(lines):
            line_number = zero_based_index + 1
            indexed_text = raw_line.rstrip()

            source_char_start = cursor
            source_char_end = source_char_start + len(raw_line)
            text_char_end = source_char_start + len(indexed_text)
            has_line_break = zero_based_index < last_line_index
            line_break_end = source_char_end + (1 if has_line_break else 0)

            units.append(
                ParsedDocumentUnit(
                    text=indexed_text,
                    source_metadata={
                        "unit_type": "line",
                        "location_kind": "txt_line",
                        "line_number": line_number,
                        "line_start_number": line_number,
                        "line_end_number": line_number,
                        "source_char_start": source_char_start,
                        "source_char_end": source_char_end,
                        "text_char_start": source_char_start,
                        "text_char_end": text_char_end,
                        "line_break_start": source_char_end,
                        "line_break_end": line_break_end,
                        "has_line_break": has_line_break,
                        "source_line_length": len(raw_line),
                        "indexed_line_length": len(indexed_text),
                        "encoding": encoding,
                    },
                )
            )

            cursor = line_break_end

        return tuple(units)

    @staticmethod
    def _detect_encoding(payload: bytes) -> tuple[str, bool]:
        """BOM과 strict 디코딩을 우선하여 원본 바이트 인코딩을 결정한다."""

        bom_candidates = (
            (codecs.BOM_UTF32_LE, "utf-32"),
            (codecs.BOM_UTF32_BE, "utf-32"),
            (codecs.BOM_UTF8, "utf-8-sig"),
            (codecs.BOM_UTF16_LE, "utf-16"),
            (codecs.BOM_UTF16_BE, "utf-16"),
        )

        for bom, encoding in bom_candidates:
            if payload.startswith(bom):
                return encoding, True

        try:
            payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        else:
            return "utf-8", False

        best_match = from_bytes(payload).best()
        if best_match is None or best_match.encoding is None:
            raise InvalidDocumentError(DocumentType.TXT)

        best_encoding = best_match.encoding
        best_text = str(best_match)

        try:
            cp949_text = payload.decode("cp949", errors="strict")
        except UnicodeDecodeError:
            cp949_text = ""

        if TxtDocumentParser._count_hangul(cp949_text) > TxtDocumentParser._count_hangul(best_text):
            return "cp949", False

        return best_encoding, False

    @staticmethod
    def _count_hangul(text: str) -> int:
        """제한적인 CP949 후보 비교에 사용할 완성형 한글 음절 수를 반환한다."""

        return sum("가" <= character <= "힣" for character in text)

    @staticmethod
    def _reject_binary_content(
        payload: bytes,
        text: str,
        *,
        encoding: str,
        has_bom: bool,
    ) -> None:
        """디코딩 가능한 바이너리 입력이 TXT로 색인되는 것을 방지한다."""

        if any(payload.startswith(prefix) for prefix in _KNOWN_BINARY_PREFIXES):
            raise InvalidDocumentError(DocumentType.TXT)

        normalized_encoding = encoding.replace("-", "_").lower()
        is_multibyte_utf = normalized_encoding.startswith(("utf_16", "utf_32"))

        if not has_bom and b"\x00" in payload and not is_multibyte_utf:
            raise InvalidDocumentError(DocumentType.TXT)

        if not text:
            return

        disallowed_controls = sum(
            1
            for character in text
            if ord(character) < 32 and character not in {"\n", "\r", "\t", "\f", "\b"}
        )

        if disallowed_controls / len(text) > _BINARY_CONTROL_RATIO_LIMIT:
            raise InvalidDocumentError(DocumentType.TXT)
