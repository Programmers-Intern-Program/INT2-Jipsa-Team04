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

    파일 처리 API와 서비스 계층은 PdfDocumentParser와 같은 구체적인
    파서 구현을 직접 선택하지 않는다.

    호출자는 처리할 문서 형식만 Factory에 전달하고, Factory는 현재
    지원 정책에 따라 등록된 DocumentParser 구현체를 반환한다.

    현재 Local RAG 파일 처리 정책은 텍스트 레이어가 존재하는 PDF만
    지원한다. 따라서 기본 등록 목록에는 PdfDocumentParser만 포함한다.

    다음 형식은 현재 지원하지 않는다.

    - TXT
    - DOCX
    - XLSX
    - PPTX

    위 형식은 파서 구현과 지원 정책이 확정되기 전까지 기본 Factory에
    등록하지 않는다.
    """

    def __init__(
        self,
        parsers: Iterable[DocumentParser] | None = None,
    ) -> None:
        """기본 파서 또는 호출자가 전달한 파서 목록을 등록한다.

        Args:
            parsers:
                Factory에 등록할 DocumentParser 구현체 목록이다.

                값을 전달하지 않으면 현재 실제 지원 형식인 PDF 파서만
                자동으로 등록한다.

                호출자가 명시적인 목록을 전달하면 기본 파서를 추가하지
                않고 전달받은 목록만 등록한다.

                빈 tuple을 전달하면 등록된 파서가 없는 Factory를 만들 수
                있다. 테스트에서는 이를 이용해 등록 상태를 완전히 통제한다.

        Raises:
            DuplicateDocumentParserError:
                동일한 DocumentType을 처리하는 파서가 두 개 이상
                등록된 경우 발생한다.
        """

        registered_parsers: tuple[DocumentParser, ...]

        if parsers is not None:
            # 호출자가 파서 목록을 명시했다면 기본 파서를 추가하지 않는다.
            #
            # 빈 tuple도 유효한 명시적 입력이므로 truthy 여부가 아니라
            # None 여부로 기본 등록 여부를 판단한다.
            registered_parsers = tuple(parsers)
        else:
            # 현재 실제 파일 처리 정책에서 지원하는 형식은 PDF뿐이다.
            #
            # TXT, DOCX, XLSX 및 PPTX는 요청 스키마와 Factory 양쪽에서
            # 지원하지 않도록 유지한다.
            registered_parsers = (PdfDocumentParser(),)

        # 문서 형식을 Key로 사용하여 대응하는 파서를 저장한다.
        #
        # 조회 시 반복적인 if/elif 분기를 사용하지 않고 정규화된
        # DocumentType을 이용해 결정적으로 파서를 선택한다.
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
                동일한 file_type의 파서가 이미 등록된 경우 발생한다.
        """

        file_type = parser.file_type

        # 동일한 문서 형식에 여러 파서가 등록되면 등록 순서에 따라
        # 선택 결과가 달라질 수 있다.
        #
        # 파서 선택 결과를 결정적으로 유지하기 위해 동일 형식의
        # 중복 등록을 명시적으로 거부한다.
        if file_type in self._parsers:
            raise DuplicateDocumentParserError(file_type)

        self._parsers[file_type] = parser

    def get_parser(
        self,
        file_type: DocumentType | str,
    ) -> DocumentParser:
        """문서 형식을 정규화하고 등록된 파서를 반환한다.

        문자열 입력은 앞뒤 공백을 제거하고 대문자로 변환한다.

        다음 값은 모두 DocumentType.PDF로 정규화된다.

        - ``"PDF"``
        - ``"pdf"``
        - ``" Pdf "``

        DocumentType에 정의되어 있더라도 현재 Factory에 등록되지 않은
        TXT, DOCX, XLSX 및 PPTX는 UnsupportedDocumentTypeError로
        거부한다.

        Args:
            file_type:
                파서를 조회할 DocumentType 또는 문자열 값이다.

        Returns:
            요청한 문서 형식을 처리하는 DocumentParser 구현체다.

        Raises:
            UnsupportedDocumentTypeError:
                DocumentType에 정의되지 않은 값이거나 해당 형식의
                파서가 현재 Factory에 등록되지 않은 경우 발생한다.
        """

        normalized_file_type = self._normalize_file_type(file_type)

        try:
            return self._parsers[normalized_file_type]
        except KeyError as error:
            # 알려진 문서 형식이라도 현재 지원 정책상 파서가 등록되지
            # 않았다면 일반 KeyError를 노출하지 않는다.
            #
            # 문서 파서 계층의 공통 미지원 형식 예외로 변환하여 API가
            # 일관된 오류로 처리할 수 있도록 한다.
            raise UnsupportedDocumentTypeError(
                normalized_file_type,
            ) from error

    def supports(
        self,
        file_type: DocumentType | str,
    ) -> bool:
        """요청한 문서 형식의 파서가 등록되어 있는지 반환한다.

        DocumentType에 정의되지 않은 문자열이나 현재 지원하지 않는
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

        DocumentType 값은 그대로 반환한다. 문자열은 앞뒤 공백을 제거하고
        대문자로 변환한 뒤 공통 Enum 값으로 변환한다.

        이 메서드는 문서 형식 이름을 정규화할 뿐 지원 여부를 결정하지
        않는다. 실제 지원 여부는 해당 형식의 파서 등록 상태로 판단한다.

        Args:
            file_type:
                정규화할 DocumentType 또는 문자열 값이다.

        Returns:
            정규화가 완료된 DocumentType 값이다.

        Raises:
            UnsupportedDocumentTypeError:
                DocumentType에 정의된 값으로 변환할 수 없는 경우 발생한다.
        """

        if isinstance(file_type, DocumentType):
            return file_type

        normalized_value = file_type.strip().upper()

        try:
            return DocumentType(normalized_value)
        except ValueError as error:
            # Enum 변환 과정의 일반 ValueError를 외부로 노출하지 않고
            # 문서 파서 계층의 공통 미지원 형식 예외로 변환한다.
            raise UnsupportedDocumentTypeError(file_type) from error