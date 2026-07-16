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

# PDF 텍스트 추출 결과에 세 줄 이상의 연속된 빈 줄이 포함되면
# 후속 청킹 단계에서 의미 없는 공백이 청크에 포함될 수 있다.
#
# 원본 문단 구분에 필요한 두 줄의 줄바꿈은 유지하면서
# 과도한 연속 줄바꿈만 두 줄로 제한한다.
_EXCESSIVE_BLANK_LINES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")


class PdfDocumentParser:
    """pypdf를 사용하여 PDF 텍스트를 페이지 단위로 추출한다.

    PDF의 각 페이지를 하나의 ParsedDocumentUnit으로 변환한다.

    각 ParsedDocumentUnit의 source_metadata에는 사용자에게 표시되는
    일반적인 페이지 번호와 동일하게 1부터 시작하는 page_number를 저장한다.

    일부 페이지에서 텍스트가 추출되지 않더라도 원본 페이지 위치를
    보존하기 위해 빈 ParsedDocumentUnit을 유지한다.

    다만 문서 전체에서 텍스트가 하나도 추출되지 않으면 이미지 기반
    스캔 PDF 또는 텍스트 레이어가 없는 PDF로 판단한다.

    현재 기능 범위에서는 OCR을 지원하지 않으므로 이러한 문서는
    DocumentTextNotFoundError로 처리한다.
    """

    @property
    def file_type(self) -> DocumentType:
        """이 파서가 처리하는 문서 형식인 PDF를 반환한다.

        Returns:
            DocumentType.PDF를 반환한다.
        """

        return DocumentType.PDF

    async def parse(
        self,
        file_path: Path,
    ) -> ParsedDocument:
        """PDF 파싱 작업을 별도의 작업 스레드에서 실행한다.

        pypdf는 동기식 파일 입출력과 텍스트 추출을 수행한다.

        FastAPI 이벤트 루프에서 동기 PDF 파싱을 직접 실행하면
        파싱이 끝날 때까지 동일한 이벤트 루프가 다른 비동기 요청을
        처리하지 못할 수 있다.

        asyncio.to_thread()를 사용하여 동기 파싱을 별도 작업 스레드로
        이동하고 호출자는 비동기 방식으로 결과를 기다린다.

        Args:
            file_path:
                다운로드와 유효성 검증이 완료된 PDF 임시 파일 경로다.

        Returns:
            페이지별 ParsedDocumentUnit과 문서 전체 페이지 수를 포함하는
            ParsedDocument 결과다.

        Raises:
            DocumentFileNotFoundError:
                파일이 존재하지 않거나 일반 파일이 아닌 경우 발생한다.
            DocumentReadError:
                파일 시스템에서 PDF를 읽을 수 없는 경우 발생한다.
            InvalidDocumentError:
                PDF 구조가 손상되었거나 페이지가 없는 경우 발생한다.
            EncryptedDocumentError:
                암호화된 PDF인 경우 발생한다.
            DocumentTextExtractionError:
                특정 페이지의 텍스트 추출에 실패한 경우 발생한다.
            DocumentTextNotFoundError:
                문서 전체에서 추출 가능한 텍스트가 없는 경우 발생한다.
        """

        return await asyncio.to_thread(
            self._parse_sync,
            file_path,
        )

    def _parse_sync(
        self,
        file_path: Path,
    ) -> ParsedDocument:
        """PDF 파일을 열고 모든 페이지를 원본 순서대로 파싱한다.

        Args:
            file_path:
                파싱할 PDF 파일 경로다.

        Returns:
            페이지별 텍스트 단위와 문서 메타데이터를 포함한
            ParsedDocument 결과다.
        """

        # PdfReader를 생성하기 전에 파일 경로가 유효한지 확인한다.
        #
        # 존재하지 않는 파일과 손상된 PDF를 서로 다른 예외로 구분하여
        # 상위 계층에서 정확한 실패 원인을 처리할 수 있도록 한다.
        self._validate_file_path(file_path)

        try:
            with file_path.open("rb") as file_stream:
                # strict=False:
                # PDF 표준을 완전히 준수하지 않더라도 pypdf가
                # 복구할 수 있는 수준의 구조 문제는 허용한다.
                #
                # 실제로 해석할 수 없는 파일은 PdfReadError 또는
                # ValueError를 발생시키며 아래에서 공통 예외로 변환한다.
                reader = PdfReader(
                    file_stream,
                    strict=False,
                )

                # 현재 파일 처리 API에는 PDF 비밀번호를 전달하는
                # 요청 계약이 없다.
                #
                # 빈 비밀번호로 열 수 있는지 임의로 시도하지 않고
                # 암호화 상태가 확인된 모든 PDF를 명시적으로 거부한다.
                if reader.is_encrypted:
                    raise EncryptedDocumentError(self.file_type)

                page_count = len(reader.pages)

                # 페이지가 하나도 없는 PDF는 검색에 사용할 텍스트나
                # 원본 위치 단위를 만들 수 없으므로 유효하지 않은
                # 문서로 처리한다.
                if page_count == 0:
                    raise InvalidDocumentError(self.file_type)

                # 페이지를 원본 순서대로 순회하여
                # 각 페이지를 ParsedDocumentUnit으로 변환한다.
                #
                # tuple로 확정하여 반환 후 단위 순서와 개수가
                # 변경되지 않도록 한다.
                units = tuple(
                    self._parse_page(
                        page=page,
                        # 사용자에게 표시되는 PDF 페이지 번호와 동일하게
                        # 0이 아닌 1부터 시작하는 값을 저장한다.
                        page_number=page_index + 1,
                    )
                    for page_index, page in enumerate(reader.pages)
                )

        except DocumentParserError:
            # 이미 문서 파서 계층의 의미 있는 예외로 변환된 경우
            # 해당 예외를 그대로 상위 계층으로 전달한다.
            raise

        except (PdfReadError, ValueError) as error:
            # pypdf 내부 구현 예외를 API 또는 서비스 계층에
            # 직접 노출하지 않고 공통 문서 예외로 변환한다.
            raise InvalidDocumentError(self.file_type) from error

        except OSError as error:
            # 파일 권한, 파일 잠금 및 장치 오류처럼 파일 시스템에서
            # 발생한 예외는 문서 읽기 실패로 통일한다.
            raise DocumentReadError(file_path) from error

        parsed_document = ParsedDocument(
            file_type=self.file_type,
            units=units,
            document_metadata={
                # 페이지 수는 특정 페이지가 아닌 문서 전체에 적용되는
                # 정보이므로 unit의 source_metadata가 아니라
                # document_metadata에 저장한다.
                "page_count": page_count,
            },
        )

        # 일부 페이지만 텍스트가 없는 PDF는 정상 처리한다.
        # 빈 페이지도 원본 페이지 위치 보존을 위해 단위로 유지한다.
        #
        # 모든 페이지에서 텍스트가 추출되지 않았다면
        # 이미지 스캔 PDF 또는 텍스트 레이어가 없는 PDF일 가능성이 높다.
        #
        # 현재 OCR을 지원하지 않으므로 명확한 문서 예외로 처리한다.
        if parsed_document.text_unit_count == 0:
            raise DocumentTextNotFoundError(self.file_type)

        return parsed_document

    def _parse_page(
        self,
        *,
        page: PageObject,
        page_number: int,
    ) -> ParsedDocumentUnit:
        """PDF 한 페이지의 텍스트와 원본 페이지 위치를 반환한다.

        Args:
            page:
                pypdf가 읽은 단일 PDF 페이지 객체다.
            page_number:
                사용자 화면과 검색 결과 출처에 사용할
                1부터 시작하는 페이지 번호다.

        Returns:
            정규화된 페이지 텍스트와 page_number가 저장된
            ParsedDocumentUnit이다.

        Raises:
            DocumentTextExtractionError:
                현재 페이지의 텍스트를 추출하지 못한 경우 발생한다.
        """

        try:
            # extract_text()는 PDF 내부의 텍스트 레이어만 읽는다.
            #
            # 이미지에 포함된 글자를 인식하는 OCR 기능은 수행하지 않으며,
            # 차트, 도형 및 이미지 자체의 의미도 별도로 분석하지 않는다.
            raw_text = page.extract_text()

        except Exception as error:
            # 손상된 콘텐츠 스트림이나 잘못된 글꼴 정보 등
            # 페이지 내부 문제에 따라 pypdf가 여러 종류의 예외를
            # 발생시킬 수 있다.
            #
            # 라이브러리별 예외를 파서 외부에 직접 노출하지 않고
            # 실패한 페이지 번호를 포함한 공통 예외로 변환한다.
            raise DocumentTextExtractionError(
                file_type=self.file_type,
                source_metadata={
                    "page_number": page_number,
                },
            ) from error

        return ParsedDocumentUnit(
            text=self._normalize_text(raw_text),
            source_metadata={
                # 검색 결과를 받은 사용자가 실제 PDF의 출처 페이지를
                # 바로 찾을 수 있도록 1부터 시작하는 번호를 저장한다.
                "page_number": page_number,
            },
        )

    @staticmethod
    def _validate_file_path(
        file_path: Path,
    ) -> None:
        """파싱 대상 경로가 존재하는 일반 파일인지 검증한다.

        디렉터리 경로나 존재하지 않는 경로를 PdfReader에 전달하지 않고
        문서 파서 계층의 명확한 예외로 처리한다.

        Args:
            file_path:
                존재 여부와 파일 유형을 확인할 경로다.

        Raises:
            DocumentFileNotFoundError:
                경로가 존재하지 않거나 일반 파일이 아닌 경우 발생한다.
        """

        if not file_path.exists() or not file_path.is_file():
            raise DocumentFileNotFoundError(file_path)

    @staticmethod
    def _normalize_text(
        text: str | None,
    ) -> str:
        """PDF에서 추출한 텍스트의 공백과 줄바꿈을 정규화한다.

        다음 정규화를 적용한다.

        - Windows 줄바꿈 CRLF를 LF로 통일한다.
        - 이전 Mac 줄바꿈 CR을 LF로 통일한다.
        - NULL 문자를 제거한다.
        - 각 줄 끝의 불필요한 공백을 제거한다.
        - 세 줄 이상의 연속 줄바꿈을 두 줄로 제한한다.
        - 전체 텍스트의 앞뒤 공백을 제거한다.

        단어 사이의 공백과 각 줄 앞쪽 공백은 가능한 한 유지하여
        목록, 들여쓰기 및 원본 문맥이 과도하게 손실되지 않도록 한다.

        Args:
            text:
                pypdf가 반환한 페이지 텍스트다.
                텍스트가 없는 페이지는 None일 수 있다.

        Returns:
            정규화된 페이지 텍스트다.
            추출 가능한 텍스트가 없으면 빈 문자열을 반환한다.
        """

        if text is None:
            return ""

        # 서로 다른 운영체제 줄바꿈을 LF 형식으로 통일하고
        # 후속 저장이나 청킹에서 문제를 일으킬 수 있는 NULL 문자를 제거한다.
        normalized_line_endings = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")

        # 줄의 앞쪽 공백은 PDF 목록이나 들여쓰기 구조를 나타낼 수 있으므로
        # 유지하고, 줄 끝의 불필요한 공백만 제거한다.
        trimmed_lines = "\n".join(line.rstrip() for line in normalized_line_endings.split("\n"))

        # 문단 구분에 필요한 두 줄의 줄바꿈은 유지하고,
        # 세 줄 이상 연속된 불필요한 빈 줄만 축소한다.
        normalized_blank_lines = _EXCESSIVE_BLANK_LINES_PATTERN.sub(
            "\n\n",
            trimmed_lines,
        )

        return normalized_blank_lines.strip()
