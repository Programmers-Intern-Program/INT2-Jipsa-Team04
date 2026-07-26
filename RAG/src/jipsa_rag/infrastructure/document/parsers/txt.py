"""TXT 파일의 인코딩을 감지하고 줄 단위 텍스트와 위치 정보를 추출한다.

TXT는 고정된 컨테이너 구조나 Magic Byte가 없으므로 다른 문서 형식보다 입력
검증이 까다롭다. 이 파서는 다음 순서로 안전하게 일반 텍스트를 판별한다.

1. 파일 존재 여부와 빈 파일 여부 확인
2. UTF BOM 우선 탐지
3. BOM이 없으면 UTF-8 strict 디코딩 우선 시도
4. 통계 기반 ``charset-normalizer`` 결과와 CP949 후보 비교
5. strict 디코딩으로 실제 바이트 전체 검증
6. 알려진 바이너리 시그니처, NULL 바이트와 제어 문자 비율 검사
7. 원본 줄 번호를 유지하여 ``ParsedDocumentUnit`` 생성

인코딩 탐지는 텍스트를 최대한 복구하기 위한 기능이 아니라, 검색에 사용할 수
있는 정상 텍스트 파일만 허용하기 위한 검증 절차다. 디코딩 오류를 replacement
문자로 숨기지 않고 strict 모드로 실패시켜 손상 데이터가 색인되는 것을 막는다.
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

# Local RAG DB에 저장할 일반 텍스트 파서 종류다.
_TXT_PARSER_TYPE: Final[str] = "TXT_TEXT"

# 인코딩 선택, 바이너리 판별 또는 줄 unit 생성 규칙이 달라지면 증가시킨다.
_TXT_PARSER_VERSION: Final[str] = "1.0.0"

# 허용하지 않는 제어 문자가 전체 디코딩 문자열의 5%를 초과하면 일반 텍스트가
# 아니라 바이너리 데이터가 우연히 디코딩된 것으로 판단한다.
_BINARY_CONTROL_RATIO_LIMIT: Final[float] = 0.05

# TXT로 위장하기 쉬운 대표 바이너리 형식의 시작 시그니처다. 다운로드 계층에서도
# 기본 Magic Byte를 확인하지만 파서는 직접 호출될 수 있으므로 다시 방어한다.
_KNOWN_BINARY_PREFIXES: Final[tuple[bytes, ...]] = (
    b"%PDF-",                 # PDF
    b"PK\x03\x04",           # ZIP/OOXML 일반 로컬 파일 헤더
    b"PK\x05\x06",           # 빈 ZIP 중앙 디렉터리
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",        # JPEG
    b"GIF87a",                # GIF 87a
    b"GIF89a",                # GIF 89a
    b"\x7fELF",              # Linux ELF 실행 파일
    b"MZ",                    # Windows PE/DOS 실행 파일
)


class TxtDocumentParser:
    """일반 텍스트를 감지하고 각 원본 줄을 독립적인 검색 단위로 변환한다.

    빈 줄도 원본 줄 번호 보존을 위해 unit으로 유지한다. 후속 청커는 텍스트가 빈
    unit에서는 청크를 생성하지 않지만, 다음 줄의 ``line_number``는 실제 파일과
    동일하게 유지된다.
    """

    @property
    def file_type(self) -> DocumentType:
        """이 구현체가 처리하는 공통 문서 형식인 TXT를 반환한다."""

        return DocumentType.TXT

    @property
    def parser_type(self) -> str:
        """Local RAG DB에 저장할 TXT 파서 종류를 반환한다."""

        return _TXT_PARSER_TYPE

    @property
    def parser_version(self) -> str:
        """재파싱과 결정적 Chunk ID에 사용할 파서 호환 버전을 반환한다."""

        return _TXT_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        """동기식 파일 읽기와 인코딩 감지를 작업 스레드에서 실행한다."""

        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: Path) -> ParsedDocument:
        """TXT 원본 바이트를 검증하고 줄 단위 공통 모델로 변환한다."""

        validate_regular_file(file_path)

        try:
            # 인코딩 감지와 바이너리 판별에는 원본 바이트가 필요하므로 text mode가
            # 아니라 bytes 전체를 읽는다. 파일 크기 제한은 다운로더에서 이미 적용된다.
            payload = file_path.read_bytes()
        except OSError as error:
            raise DocumentReadError(file_path) from error

        if not payload:
            # 길이가 0인 파일은 유효한 텍스트 unit을 만들 수 없다.
            raise DocumentTextNotFoundError(self.file_type)

        encoding, has_bom = self._detect_encoding(payload)

        try:
            # errors="replace"를 사용하면 잘못된 바이트가 U+FFFD로 조용히 바뀌고
            # 검색 원문과 해시가 손상될 수 있으므로 strict 모드를 고정한다.
            text = payload.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as error:
            raise InvalidDocumentError(self.file_type) from error

        # utf-8-sig/utf-16/utf-32 codec가 대부분 BOM을 제거하지만, 인코딩 별칭이나
        # 비표준 입력에서도 첫 문자에 남을 수 있으므로 최종 문자열에서 한 번 더 제거한다.
        text = text.removeprefix("\ufeff")

        self._reject_binary_content(
            payload,
            text,
            encoding=encoding,
            has_bom=has_bom,
        )

        # 운영체제별 줄바꿈을 LF로 통일한다. splitlines() 대신 split("\n")을
        # 사용하여 파일이 마지막 줄바꿈으로 끝날 때의 마지막 빈 줄 위치도 보존한다.
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")

        units = tuple(
            ParsedDocumentUnit(
                # 왼쪽 공백은 들여쓰기나 코드 구조일 수 있으므로 보존하고,
                # 줄 끝의 불필요한 공백만 제거한다.
                text=line.rstrip(),
                source_metadata={
                    "unit_type": "line",
                    # 사용자 편집기와 동일하게 1부터 시작하는 줄 번호를 사용한다.
                    "line_number": line_number,
                    # 검색 결과나 운영 진단에서 실제 해석 인코딩을 확인할 수 있게 한다.
                    "encoding": encoding,
                },
            )
            for line_number, line in enumerate(lines, start=1)
        )

        parsed_document = ParsedDocument(
            file_type=self.file_type,
            units=units,
            document_metadata={
                "line_count": len(lines),
                "encoding": encoding,
                "byte_order_mark": has_bom,
            },
        )

        # 공백과 빈 줄만 포함한 문서는 line unit이 존재하더라도 검색 가능한 텍스트가
        # 없다. ParsedDocument의 공통 계산 결과를 사용하여 실패로 처리한다.
        if parsed_document.text_unit_count == 0:
            raise DocumentTextNotFoundError(self.file_type)

        return parsed_document

    @staticmethod
    def _detect_encoding(payload: bytes) -> tuple[str, bool]:
        """원본 바이트의 인코딩과 BOM 존재 여부를 결정한다.

        BOM은 통계 추정보다 강한 신호이므로 가장 먼저 확인한다. UTF-32 BOM은
        UTF-16 BOM으로 시작하는 바이트 패턴을 포함하므로 UTF-32를 반드시 먼저
        검사해야 한다.

        Returns:
            Python codec 이름과 BOM 존재 여부의 tuple이다.

        Raises:
            InvalidDocumentError:
                신뢰할 수 있는 인코딩 후보를 찾지 못한 경우 발생한다.
        """

        bom_candidates = (
            # 긴 BOM을 먼저 검사하여 UTF-32를 UTF-16으로 오인하지 않는다.
            (codecs.BOM_UTF32_LE, "utf-32"),
            (codecs.BOM_UTF32_BE, "utf-32"),
            (codecs.BOM_UTF8, "utf-8-sig"),
            (codecs.BOM_UTF16_LE, "utf-16"),
            (codecs.BOM_UTF16_BE, "utf-16"),
        )

        for bom, encoding in bom_candidates:
            if payload.startswith(bom):
                return encoding, True

        # UTF-8은 현재 가장 일반적인 텍스트 인코딩이고 strict 디코딩 성공 여부가
        # 결정적이므로 통계 라이브러리보다 먼저 검사한다.
        try:
            payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        else:
            return "utf-8", False

        # UTF-8이 아니면 charset-normalizer의 통계 기반 최적 후보를 사용한다.
        best_match = from_bytes(payload).best()
        if best_match is None or best_match.encoding is None:
            raise InvalidDocumentError(DocumentType.TXT)

        best_encoding = best_match.encoding
        best_text = str(best_match)

        # 짧은 한국어 Windows 문서는 통계적으로 Big5 등 다른 동아시아 인코딩으로
        # 오인될 수 있다. CP949 strict 디코딩이 가능하고, 그 결과에 실제 완성형 한글
        # 음절이 통계 후보보다 더 많을 때만 CP949를 우선한다. 단순히 한국 환경이라는
        # 이유로 모든 비UTF-8 파일을 CP949로 강제하지 않는다.
        try:
            cp949_text = payload.decode("cp949", errors="strict")
        except UnicodeDecodeError:
            cp949_text = ""

        if (
            TxtDocumentParser._count_hangul(cp949_text)
            > TxtDocumentParser._count_hangul(best_text)
        ):
            return "cp949", False

        return best_encoding, False

    @staticmethod
    def _count_hangul(text: str) -> int:
        """문자열에 포함된 완성형 한글 음절 수를 반환한다.

        인코딩 후보 선택을 위한 제한적인 휴리스틱으로만 사용한다. 자모, 한자나
        일본어 문자는 계산하지 않으므로 한국어 여부를 일반적으로 판별하는 함수가 아니다.
        """

        return sum("가" <= character <= "힣" for character in text)

    @staticmethod
    def _reject_binary_content(
        payload: bytes,
        text: str,
        *,
        encoding: str,
        has_bom: bool,
    ) -> None:
        """디코딩에 성공했더라도 일반 텍스트가 아닌 바이너리 입력을 거부한다.

        일부 바이너리 파일은 우연히 단일 바이트 인코딩으로 디코딩될 수 있다. 따라서
        디코딩 성공 여부만으로 TXT를 허용하지 않고 원본 시그니처, NULL 바이트와
        제어 문자 비율을 추가로 검사한다.
        """

        if any(payload.startswith(prefix) for prefix in _KNOWN_BINARY_PREFIXES):
            raise InvalidDocumentError(DocumentType.TXT)

        # NULL 바이트는 일반적인 단일 바이트 텍스트에서는 강한 바이너리 신호다.
        # 다만 BOM 없는 UTF-16/32는 정상적으로 NULL을 포함할 수 있으므로 인코딩 감지
        # 결과가 해당 계열일 때만 예외적으로 허용한다.
        normalized_encoding = encoding.replace("-", "_").lower()
        is_multibyte_utf = normalized_encoding.startswith(("utf_16", "utf_32"))

        if not has_bom and b"\x00" in payload and not is_multibyte_utf:
            raise InvalidDocumentError(DocumentType.TXT)

        if not text:
            # 빈 문자열 여부는 호출부가 DocumentTextNotFoundError로 처리한다.
            return

        # 줄바꿈, 탭, 폼 피드와 백스페이스는 텍스트 문서에서 나타날 수 있으므로
        # 허용한다. 그 외 C0 제어 문자의 비율이 높으면 바이너리로 판단한다.
        disallowed_controls = sum(
            1
            for character in text
            if (
                ord(character) < 32
                and character not in {"\n", "\r", "\t", "\f", "\b"}
            )
        )

        if disallowed_controls / len(text) > _BINARY_CONTROL_RATIO_LIMIT:
            raise InvalidDocumentError(DocumentType.TXT)
