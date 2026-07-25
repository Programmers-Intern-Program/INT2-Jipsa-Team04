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


class DocumentParserFactory:
    """등록된 문서 형식별 파서를 조회하고 관리한다.

    파일 처리 API와 서비스 계층은 PdfDocumentParser 같은 구체 구현체를
    직접 선택하지 않는다.

    호출자는 처리할 문서 형식만 Factory에 전달하고, Factory는 해당 형식에
    등록된 DocumentParser 구현체를 반환한다.

    현재 프로젝트의 공식 지원 범위는 PDF뿐이다.

    따라서 기본 Factory에는 PdfDocumentParser만 등록하며 다음 형식은
    DocumentType 열거형에 존재하더라도 의도적으로 등록하지 않는다.

    - TXT
    - DOCX
    - XLSX
    - PPTX

    열거형에 형식이 존재하는 것과 실제 파일 처리 기능이 지원되는 것은
    서로 다른 개념이다. 형식 이름이 내부 모델에 정의되어 있더라도 파서가
    등록되지 않았다면 UnsupportedDocumentTypeError를 발생시킨다.
    """

    def __init__(
        self,
        parsers: Iterable[DocumentParser] | None = None,
    ) -> None:
        """기본 파서 또는 호출자가 전달한 파서 목록을 등록한다.

        Args:
            parsers:
                Factory에 등록할 DocumentParser 구현체 목록이다.

                값을 전달하지 않으면 현재 공식 지원 파서인
                PdfDocumentParser만 자동으로 등록한다.

                호출자가 명시적인 목록을 전달하면 기본 파서를 추가하지
                않고 전달받은 목록만 등록한다.

                빈 tuple을 전달하면 등록된 파서가 전혀 없는 Factory를
                만들 수 있다. 테스트에서는 이 특성을 이용하여 등록 상태를
                완전히 통제할 수 있다.

        Raises:
            DuplicateDocumentParserError:
                동일한 DocumentType을 처리하는 파서가 두 개 이상
                등록된 경우 발생한다.
        """

        registered_parsers: tuple[DocumentParser, ...]

        if parsers is not None:
            # 호출자가 파서 목록을 직접 전달했다면 PDF 기본 파서를
            # 암묵적으로 추가하지 않는다.
            #
            # 이를 통해 테스트나 향후 별도 실행 프로필에서 등록할 파서를
            # 명시적으로 통제할 수 있다.
            registered_parsers = tuple(parsers)
        else:
            # 현재 사용자 요구사항과 파일 처리 계약은 PDF만 지원한다.
            #
            # TXT 모듈이 존재하더라도 실제 파서 구현이 완료되지 않았으므로
            # 기본 등록 목록에 포함해서는 안 된다.
            registered_parsers = (
                PdfDocumentParser(),
            )

        # 문서 형식을 Key로 사용하여 대응하는 파서를 저장한다.
        #
        # 반복적인 if/elif 분기 대신 정규화된 DocumentType을 이용해
        # 결정적으로 파서를 조회한다.
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
                동일한 file_type을 처리하는 파서가 이미 등록된 경우
                발생한다.
        """

        file_type = parser.file_type

        # 동일한 문서 형식에 여러 파서가 등록되면 등록 순서에 따라
        # 조회 결과가 달라질 수 있다.
        #
        # 파서 선택 결과를 결정적으로 유지하기 위해 같은 DocumentType의
        # 중복 등록을 명시적으로 거부한다.
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

        TXT, DOCX, XLSX, PPTX 값도 DocumentType으로 정규화될 수는 있지만
        현재 Factory에 파서가 등록되어 있지 않으므로 조회 단계에서
        UnsupportedDocumentTypeError가 발생한다.

        Args:
            file_type:
                파서를 조회할 DocumentType 또는 문자열 값이다.

        Returns:
            요청한 문서 형식을 처리하는 DocumentParser 구현체다.

        Raises:
            UnsupportedDocumentTypeError:
                DocumentType에 정의되지 않은 값이거나 해당 형식의
                파서가 현재 등록되어 있지 않은 경우 발생한다.
        """

        normalized_file_type = self._normalize_file_type(file_type)

        try:
            return self._parsers[normalized_file_type]
        except KeyError as error:
            # DocumentType에 정의된 형식이라도 실제 파서가 등록되지
            # 않았다면 현재 서비스가 지원하는 형식이 아니다.
            #
            # 일반 KeyError를 외부로 전달하지 않고 문서 파서 계층의
            # 명확한 미지원 형식 예외로 변환한다.
            raise UnsupportedDocumentTypeError(
                normalized_file_type,
            ) from error

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

        DocumentType 값은 그대로 반환한다.

        문자열은 앞뒤 공백을 제거하고 대문자로 변환한 뒤 공통 Enum 값으로
        변환한다.

        이 메서드는 형식 이름이 DocumentType에 정의되어 있는지만 확인한다.
        실제 지원 여부는 get_parser() 또는 supports()가 등록된 파서를
        기준으로 판단한다.

        Args:
            file_type:
                정규화할 DocumentType 또는 문자열 값이다.

        Returns:
            정규화가 완료된 DocumentType 값이다.

        Raises:
            UnsupportedDocumentTypeError:
                PDF, TXT, DOCX, XLSX 및 PPTX 중 어느 값으로도
                변환할 수 없는 경우 발생한다.
        """

        if isinstance(file_type, DocumentType):
            return file_type

        normalized_value = file_type.strip().upper()

        try:
            return DocumentType(normalized_value)
        except ValueError as error:
            # Enum 변환 과정의 일반 ValueError에는 입력 문자열이
            # 포함될 수 있다.
            #
            # 상위 계층이 문서 파서 공통 예외만 처리할 수 있도록
            # UnsupportedDocumentTypeError로 변환한다.
            raise UnsupportedDocumentTypeError(file_type) from error