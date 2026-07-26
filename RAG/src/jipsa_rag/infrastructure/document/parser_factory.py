"""요청한 문서 형식에 대응하는 파서를 선택하는 Factory를 제공한다.

문서 처리 API는 ``if file_type == ...`` 형태의 분기문으로 구체 파서를 직접
생성하지 않는다. 이 모듈의 ``DocumentParserFactory``가 지원 파서를 한곳에서
등록하고 조회함으로써 API, 인제스트, 청킹 및 저장 계층이 구체 라이브러리에
결합되지 않도록 한다.
"""

from collections.abc import Iterable

from jipsa_rag.infrastructure.document.exceptions import (
    DuplicateDocumentParserError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import DocumentType
from jipsa_rag.infrastructure.document.parser import DocumentParser
from jipsa_rag.infrastructure.document.parsers import (
    DocxDocumentParser,
    PdfDocumentParser,
    PptxDocumentParser,
    TxtDocumentParser,
    XlsxDocumentParser,
)


class DocumentParserFactory:
    """등록된 형식별 파서를 중복 없이 관리하고 조회한다.

    기본 생성자는 현재 정식 지원 범위인 다음 다섯 개 파서를 모두 등록한다.

    - ``PdfDocumentParser``: PDF 텍스트 레이어와 페이지 위치 추출
    - ``DocxDocumentParser``: DOCX 문단, 제목, 목록, 표와 섹션 위치 추출
    - ``PptxDocumentParser``: PPTX 도형 텍스트, 표와 발표자 노트 추출
    - ``TxtDocumentParser``: TXT 인코딩 감지, 줄 단위 추출과 바이너리 거부
    - ``XlsxDocumentParser``: XLSX 시트, 행, 표, 병합 셀과 수식 캐시 결과 추출

    Factory는 파서를 생성하는 역할뿐 아니라, 동일 ``DocumentType``의 파서가
    두 번 등록되어 선택 결과가 모호해지는 상황도 차단한다.
    """

    def __init__(
        self,
        parsers: Iterable[DocumentParser] | None = None,
    ) -> None:
        """기본 파서 또는 호출자가 전달한 파서 목록을 등록한다.

        ``parsers``를 생략하면 운영 기본 파서 다섯 개를 등록한다. 반대로 호출자가
        명시적인 Iterable을 전달하면 기본 파서를 자동으로 추가하지 않고 전달받은
        목록만 사용한다.

        이 동작은 테스트 격리에 중요하다. 예를 들어 API 단위 테스트에서는 실제
        ``python-docx``나 ``openpyxl``을 실행하지 않고, 하나의 Stub 파서만 등록하여
        파서 선택과 후속 단계 호출 여부를 정확하게 검증할 수 있다.

        빈 tuple도 유효한 명시적 설정이다. ``DocumentParserFactory(parsers=())``는
        등록 파서가 없는 Factory를 만들며, 기본 파서를 다시 추가하지 않는다.

        Args:
            parsers:
                등록할 ``DocumentParser`` 구현체 Iterable이다. ``None``이면 운영
                기본 파서를 사용하고, 값이 있으면 그 목록만 사용한다.

        Raises:
            DuplicateDocumentParserError:
                두 구현체가 동일한 ``DocumentType``을 반환한 경우 발생한다.
        """

        if parsers is None:
            # 등록 순서는 기능적 선택 결과에는 영향을 주지 않는다. 실제 조회는
            # DocumentType을 키로 하는 dict를 사용한다. 다만 테스트와 문서에서
            # 읽기 쉬운 순서인 PDF, DOCX, PPTX, TXT, XLSX로 명시한다.
            registered_parsers: tuple[DocumentParser, ...] = (
                PdfDocumentParser(),
                DocxDocumentParser(),
                PptxDocumentParser(),
                TxtDocumentParser(),
                XlsxDocumentParser(),
            )
        else:
            # Generator를 그대로 보관하면 한 번 순회한 뒤 사라질 수 있다. 생성 시점에
            # tuple로 확정하여 등록 과정이 입력 Iterable의 생명주기에 영향받지 않게 한다.
            # 빈 tuple은 "등록 파서 없음"이라는 호출자의 명시적 의도로 유지한다.
            registered_parsers = tuple(parsers)

        # 반복적인 if/elif 분기 대신 정규화된 DocumentType을 사전 키로 사용한다.
        # 이 구조에서는 " PDF ", "pdf", DocumentType.PDF가 모두 같은 파서를 가리킨다.
        self._parsers: dict[DocumentType, DocumentParser] = {}

        for parser in registered_parsers:
            self.register(parser)

    def register(self, parser: DocumentParser) -> None:
        """파서가 담당하는 형식을 키로 사용하여 중복 없이 등록한다.

        동일 형식에 여러 파서가 필요한 경우에도 이 기본 Factory에 동시에 등록해서는
        안 된다. 예를 들어 PDF 텍스트 파서와 PDF OCR 파서가 함께 필요해지면 요청 정책,
        우선순위 또는 별도 전략 계층을 먼저 정의해야 한다. 아무 규칙 없이 두 파서를
        등록하면 같은 요청에서 어느 구현을 선택할지 결정할 수 없기 때문이다.

        Args:
            parser:
                ``file_type``, ``parser_type``, ``parser_version``, ``parse()`` 계약을
                만족하는 파서 구현체다.

        Raises:
            DuplicateDocumentParserError:
                같은 ``DocumentType`` 키가 이미 등록되어 있는 경우 발생한다.
        """

        file_type = parser.file_type

        # 기존 값을 조용히 덮어쓰면 환경이나 import 순서에 따라 실제 파서가 바뀔 수
        # 있다. 운영 결과의 비결정성을 막기 위해 중복 등록은 즉시 실패시킨다.
        if file_type in self._parsers:
            raise DuplicateDocumentParserError(file_type)

        self._parsers[file_type] = parser

    def get_parser(
        self,
        file_type: DocumentType | str,
    ) -> DocumentParser:
        """문서 형식을 정규화한 뒤 대응하는 파서를 반환한다.

        문자열 입력은 앞뒤 공백을 제거하고 대문자로 변환한 뒤 ``DocumentType``으로
        해석한다. 따라서 ``"PDF"``, ``"pdf"``, ``" Pdf "``는 모두 동일한
        ``PdfDocumentParser``를 반환한다.

        Args:
            file_type:
                공통 ``DocumentType`` 또는 대소문자가 섞일 수 있는 문자열이다.

        Returns:
            정규화된 형식을 담당하는 등록 파서다.

        Raises:
            UnsupportedDocumentTypeError:
                공통 Enum에 존재하지 않는 값이거나, 해당 형식의 파서가 현재 Factory에
                등록되어 있지 않은 경우 발생한다.
        """

        normalized_file_type = self._normalize_file_type(file_type)

        try:
            return self._parsers[normalized_file_type]
        except KeyError as error:
            # dict의 KeyError를 그대로 노출하면 상위 계층이 "잘못된 내부 키"와
            # "지원하지 않는 문서 형식"을 구분하기 어렵다. 문서 계층의 명시적인
            # 예외로 변환하여 API 경계에서 일관된 오류 코드로 매핑할 수 있게 한다.
            raise UnsupportedDocumentTypeError(normalized_file_type) from error

    def supports(
        self,
        file_type: DocumentType | str,
    ) -> bool:
        """요청한 형식의 파서가 현재 등록되어 있는지 반환한다.

        이 메서드는 지원 여부 확인용이므로 알 수 없는 문자열이 들어와도 예외를
        전파하지 않고 ``False``를 반환한다. 실제 파서를 반드시 얻어야 하는 호출자는
        ``get_parser()``를 사용하여 명확한 예외를 받는다.
        """

        try:
            normalized_file_type = self._normalize_file_type(file_type)
        except UnsupportedDocumentTypeError:
            return False

        return normalized_file_type in self._parsers

    @property
    def registered_file_types(self) -> frozenset[DocumentType]:
        """현재 등록된 문서 형식을 외부 수정이 불가능한 집합으로 반환한다.

        내부 dict의 키 view를 직접 반환하지 않고 ``frozenset``으로 복사한다. 호출자가
        반환값을 변경하여 Factory의 등록 상태를 우회 수정하는 상황을 방지하고,
        테스트에서 지원 형식 집합을 명확하게 비교할 수 있게 한다.
        """

        return frozenset(self._parsers)

    @staticmethod
    def _normalize_file_type(
        file_type: DocumentType | str,
    ) -> DocumentType:
        """Enum 또는 문자열 형식 입력을 ``DocumentType``으로 통일한다.

        정규화와 실제 등록 여부 확인은 별개의 책임이다. 이 메서드는 문자열이 공통
        ``DocumentType``에 정의되어 있는지만 확인하고, 파서 등록 여부는
        ``get_parser()`` 또는 ``supports()``가 판단한다.

        Args:
            file_type:
                이미 정규화된 ``DocumentType`` 또는 사용자·API 입력에서 전달된 문자열이다.

        Returns:
            공통 ``DocumentType`` 값이다.

        Raises:
            UnsupportedDocumentTypeError:
                문자열이 ``DocumentType``에 정의되지 않은 경우 발생한다.
        """

        if isinstance(file_type, DocumentType):
            # 이미 타입 안전한 Enum이면 문자열 변환을 거치지 않고 그대로 사용한다.
            return file_type

        normalized_value = file_type.strip().upper()

        try:
            return DocumentType(normalized_value)
        except ValueError as error:
            # 원래 입력 문자열을 예외에 보존하여 진단 시 어떤 값이 들어왔는지 알 수
            # 있게 하되, API 응답에는 내부 예외 원문을 직접 노출하지 않는다.
            raise UnsupportedDocumentTypeError(file_type) from error
