"""문서 형식에 맞는 파서를 선택하는 Factory를 제공한다."""

from collections.abc import Iterable

from jipsa_rag.infrastructure.document.exceptions import (
    DuplicateDocumentParserError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser import DocumentParser
from jipsa_rag.infrastructure.document.parsers.pdf import PdfDocumentParser


class DocumentParserFactory:
    """등록된 문서 형식별 파서를 조회하고 관리한다.

    파일 처리 API와 서비스 계층은 구체적인 파서 구현을 직접 선택하지 않고
    이 Factory에 원본 문서 형식만 전달한다.

    현재 제품 정책과 실제 구현 범위는 PDF 전용이다.

    - 기본 등록 파서: ``PdfDocumentParser``
    - 명시적 미지원 형식: TXT, DOCX, XLSX, PPTX
    - 이미지 기반 스캔 PDF: OCR을 지원하지 않으므로 PDF 파서에서
      ``DocumentTextNotFoundError``로 처리한다.

    향후 다른 문서 형식을 지원할 때는 해당 파서 구현, API 요청 Enum,
    저장·검색 메타데이터 및 회귀 테스트를 같은 변경 단위에서 함께 확장한다.
    """

    def __init__(
        self,
        parsers: Iterable[DocumentParser] | None = None,
    ) -> None:
        """기본 파서 또는 호출자가 전달한 파서 목록을 등록한다.

        Args:
            parsers:
                Factory에 등록할 ``DocumentParser`` 구현체 목록이다.

                값을 전달하지 않으면 현재 운영에서 지원하는
                ``PdfDocumentParser``만 자동 등록한다.

                호출자가 명시적인 목록을 전달하면 기본 파서를 추가하지 않고
                전달받은 목록만 등록한다. 테스트에서는 이 동작을 이용하여
                Factory의 등록 상태를 완전히 통제할 수 있다.

        Raises:
            DuplicateDocumentParserError:
                동일한 ``DocumentType``을 처리하는 파서가 두 개 이상
                등록된 경우 발생한다.
        """

        if parsers is None:
            # 현 단계의 RAG 인제스트는 PDF만 지원한다.
            #
            # TXT, DOCX, XLSX, PPTX는 DocumentType에 남겨 두어 외부 입력을
            # 정확한 미지원 형식으로 식별하지만 기본 파서로 등록하지 않는다.
            registered_parsers: tuple[DocumentParser, ...] = (
                PdfDocumentParser(),
            )
        else:
            # 빈 tuple도 "등록 파서 없음"이라는 명시적인 설정으로 취급한다.
            registered_parsers = tuple(parsers)

        # 반복적인 if/elif 분기 대신 정규화된 DocumentType으로 조회하여
        # 파서 선택 결과가 등록 순서나 문자열 대소문자에 흔들리지 않게 한다.
        self._parsers: dict[DocumentType, DocumentParser] = {}

        for parser in registered_parsers:
            self.register(parser)

    def register(
        self,
        parser: DocumentParser,
    ) -> None:
        """문서 형식별 파서를 중복 없이 등록한다."""

        file_type = parser.file_type

        # 동일한 형식에 여러 파서가 등록되면 선택 결과가 모호해지므로 거부한다.
        if file_type in self._parsers:
            raise DuplicateDocumentParserError(file_type)

        self._parsers[file_type] = parser

    def get_parser(
        self,
        file_type: DocumentType | str,
    ) -> DocumentParser:
        """문서 형식을 정규화하고 등록된 파서를 반환한다.

        ``"PDF"``, ``"pdf"``, ``" Pdf "``는 모두 ``DocumentType.PDF``로
        정규화된다.

        Raises:
            UnsupportedDocumentTypeError:
                공통 ``DocumentType``에 정의되지 않은 값이거나,
                TXT·DOCX·XLSX·PPTX처럼 현재 파서가 등록되지 않은
                형식인 경우 발생한다.
        """

        normalized_file_type = self._normalize_file_type(file_type)

        try:
            return self._parsers[normalized_file_type]
        except KeyError as error:
            # 일반 KeyError를 외부로 노출하지 않고 문서 계층의
            # 명확한 미지원 형식 예외로 변환한다.
            raise UnsupportedDocumentTypeError(
                normalized_file_type
            ) from error

    def supports(
        self,
        file_type: DocumentType | str,
    ) -> bool:
        """요청한 문서 형식의 파서가 현재 등록되어 있는지 반환한다."""

        try:
            normalized_file_type = self._normalize_file_type(file_type)
        except UnsupportedDocumentTypeError:
            return False

        return normalized_file_type in self._parsers

    @property
    def registered_file_types(self) -> frozenset[DocumentType]:
        """현재 등록된 문서 형식을 읽기 전용 집합으로 반환한다."""

        return frozenset(self._parsers)

    @staticmethod
    def _normalize_file_type(
        file_type: DocumentType | str,
    ) -> DocumentType:
        """DocumentType 또는 문자열 입력을 DocumentType으로 통일한다.

        ``DocumentType``에는 향후 지원 후보 형식도 남아 있다. 실제 지원 여부는
        정규화 단계가 아니라 등록 파서 조회 단계에서 결정한다.
        """

        if isinstance(file_type, DocumentType):
            return file_type

        normalized_value = file_type.strip().upper()

        try:
            return DocumentType(normalized_value)
        except ValueError as error:
            raise UnsupportedDocumentTypeError(file_type) from error