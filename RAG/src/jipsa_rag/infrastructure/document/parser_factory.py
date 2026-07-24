"""문서 형식에 맞는 파서를 선택하는 Factory를 제공한다."""

from collections.abc import Iterable

from jipsa_rag.infrastructure.document.exceptions import (
    DuplicateDocumentParserError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser import DocumentParser
from jipsa_rag.infrastructure.document.parsers.pdf import (
    PdfDocumentParser,
)
from jipsa_rag.infrastructure.document.parsers.txt import (
    TxtDocumentParser,
)


class DocumentParserFactory:
    """등록된 문서 형식별 파서를 조회하고 관리한다.

    파일 처리 API와 서비스 계층은 PDF, DOCX, XLSX, PPTX의
    구체적인 파서 클래스를 직접 선택하지 않는다.

    호출자는 처리할 문서 형식만 Factory에 전달하고,
    Factory는 해당 형식에 등록된 DocumentParser 구현체를 반환한다.

    현재 단계에서는 PDF 파서만 구현되어 있으므로 기본 등록 목록에는
    PdfDocumentParser만 포함한다.

    DOCX, XLSX 및 PPTX 파서 구현이 완료되면 기본 등록 목록에
    각 파서를 순차적으로 추가한다.
    """

    def __init__(
        self,
        parsers: Iterable[DocumentParser] | None = None,
    ) -> None:
        """기본 파서 또는 호출자가 전달한 파서 목록을 등록한다.

        Args:
            parsers:
                Factory에 등록할 DocumentParser 구현체 목록이다.

                값을 전달하지 않으면 현재 기본 구현체인
                PdfDocumentParser를 자동으로 등록한다.

                테스트에서는 이 인수를 사용하여 실제 파서 대신
                테스트 전용 가짜 파서를 등록할 수 있다.

        Raises:
            DuplicateDocumentParserError:
                동일한 DocumentType을 처리하는 파서가
                두 개 이상 등록된 경우 발생한다.
        """

        # 호출자가 명시적인 파서 목록을 전달했다면
        # 기본 PDF 파서를 별도로 추가하지 않는다.
        #
        # 이를 통해 단위 테스트에서 Factory에 등록되는 파서를
        # 완전히 통제할 수 있다.
        registered_parsers: tuple[DocumentParser, ...] = (
            tuple(parsers) if parsers is not None else (PdfDocumentParser(), TxtDocumentParser())
        )

        # 문서 형식을 Key로 사용하여 대응하는 파서를 저장한다.
        #
        # 이후 파서 조회 시 if/elif 분기를 반복하지 않고
        # DocumentType을 이용해 한 번에 조회할 수 있다.
        self._parsers: dict[DocumentType, DocumentParser] = {}

        for parser in registered_parsers:
            self.register(parser)

    def register(
        self,
        parser: DocumentParser,
    ) -> None:
        """문서 형식별 파서를 중복 없이 등록한다.

        Args:
            parser:
                Factory에 등록할 DocumentParser 구현체다.

        Raises:
            DuplicateDocumentParserError:
                동일한 file_type의 파서가 이미 등록되어 있는 경우
                발생한다.
        """

        file_type = parser.file_type

        # 동일한 문서 형식에 여러 파서가 등록되면
        # 등록 순서에 따라 선택 결과가 달라질 수 있다.
        #
        # 파서 선택을 결정적으로 유지하기 위해
        # 동일한 DocumentType의 중복 등록을 거부한다.
        if file_type in self._parsers:
            raise DuplicateDocumentParserError(file_type)

        self._parsers[file_type] = parser

    def get_parser(
        self,
        file_type: DocumentType | str,
    ) -> DocumentParser:
        """문서 형식을 정규화하고 대응하는 파서를 반환한다.

        문자열 입력은 앞뒤 공백을 제거하고 대문자로 변환한다.

        다음 값은 모두 DocumentType.PDF로 처리된다.

        - "PDF"
        - "pdf"
        - " Pdf "

        Args:
            file_type:
                파서를 조회할 DocumentType 또는 문자열 값이다.

        Returns:
            요청한 문서 형식을 처리하는 DocumentParser 구현체다.

        Raises:
            UnsupportedDocumentTypeError:
                DocumentType에 정의되지 않은 값이거나,
                해당 형식의 파서가 아직 등록되지 않은 경우 발생한다.
        """

        normalized_file_type = self._normalize_file_type(file_type)

        try:
            return self._parsers[normalized_file_type]
        except KeyError as error:
            # DOCX, XLSX 및 PPTX는 DocumentType에는 정의되어 있어도
            # 실제 파서 구현 전에는 Factory에 등록되지 않는다.
            #
            # 일반 KeyError를 외부로 전달하지 않고
            # 문서 파서 계층의 명확한 예외로 변환한다.
            raise UnsupportedDocumentTypeError(normalized_file_type) from error

    def supports(
        self,
        file_type: DocumentType | str,
    ) -> bool:
        """요청한 문서 형식의 파서가 현재 등록되어 있는지 반환한다.

        DocumentType에 정의되지 않은 문자열이나 아직 구현되지 않은
        문서 형식은 예외를 외부로 전달하지 않고 False를 반환한다.

        Args:
            file_type:
                지원 여부를 확인할 DocumentType 또는 문자열 값이다.

        Returns:
            대응하는 파서가 등록되어 있으면 True,
            등록되어 있지 않으면 False다.
        """

        try:
            normalized_file_type = self._normalize_file_type(file_type)
        except UnsupportedDocumentTypeError:
            return False

        return normalized_file_type in self._parsers

    @property
    def registered_file_types(self) -> frozenset[DocumentType]:
        """현재 등록된 문서 형식을 읽기 전용 집합으로 반환한다.

        내부 dict의 Key View를 직접 노출하지 않고 frozenset으로
        변환하여 호출자가 Factory 내부 등록 상태를 변경하지 못하게 한다.

        Returns:
            현재 파서가 등록된 DocumentType의 읽기 전용 집합이다.
        """

        return frozenset(self._parsers)

    @staticmethod
    def _normalize_file_type(
        file_type: DocumentType | str,
    ) -> DocumentType:
        """DocumentType 또는 문자열 입력을 DocumentType으로 통일한다.

        Args:
            file_type:
                정규화할 DocumentType 또는 문자열 값이다.

        Returns:
            정규화가 완료된 DocumentType 값이다.

        Raises:
            UnsupportedDocumentTypeError:
                PDF, DOCX, XLSX 및 PPTX 중 어느 값으로도
                변환할 수 없는 경우 발생한다.
        """

        if isinstance(file_type, DocumentType):
            return file_type

        normalized_value = file_type.strip().upper()

        try:
            return DocumentType(normalized_value)
        except ValueError as error:
            raise UnsupportedDocumentTypeError(file_type) from error
