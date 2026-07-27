"""모든 문서 형식 파서가 따라야 하는 공통 구조적 인터페이스를 정의한다.

이 모듈은 PDF, DOCX, PPTX, TXT, XLSX처럼 내부 구조가 서로 다른 파일을
서비스 계층에서 동일한 방식으로 다루기 위한 경계다. 구체 파서 구현은 각
라이브러리의 객체와 예외를 이 계층 안에 가두고, 최종적으로 공통
``ParsedDocument`` 모델만 반환해야 한다.

중요한 설계 원칙은 다음과 같다.

1. API와 서비스 계층은 구체 파서 클래스를 직접 선택하지 않는다.
2. 파서 구현체는 원본 위치를 ``ParsedDocumentUnit.source_metadata``에 보존한다.
3. 동기식 문서 라이브러리는 ``asyncio.to_thread()``를 사용하여 FastAPI의
   이벤트 루프를 직접 차단하지 않는다.
4. ``parser_type``과 ``parser_version``은 Local RAG DB의 재색인 판단과
   결정적 Chunk ID 생성에 사용되므로 임의로 변경하지 않는다.
5. 파서가 반환한 문자열과 메타데이터는 임시 파일이 삭제된 뒤에도 사용할 수
   있어야 하므로, 파일 핸들이나 라이브러리 객체를 결과에 포함하지 않는다.
"""

from pathlib import Path
from typing import Protocol

from jipsa_rag.infrastructure.document.models import DocumentType, ParsedDocument


class DocumentParser(Protocol):
    """원본 문서를 공통 ``ParsedDocument``로 변환하는 파서 계약.

    ``Protocol`` 기반 구조적 타이핑을 사용하므로 형식별 파서가 이 클래스를
    명시적으로 상속할 필요는 없다. 아래 속성과 메서드를 동일한 타입으로
    제공하면 ``DocumentParser`` 구현체로 사용할 수 있다.

    이 방식은 다음 장점이 있다.

    - 구체 파서가 공통 추상 클래스의 내부 구현에 불필요하게 결합되지 않는다.
    - 테스트에서는 작은 Stub 객체만으로 Parser Factory와 API 흐름을 검증할 수 있다.
    - 새 형식을 추가할 때 API와 인제스트 서비스 코드를 수정하지 않고 Factory에만
      등록할 수 있다.
    """

    @property
    def file_type(self) -> DocumentType:
        """현재 파서가 책임지는 원본 문서 형식을 반환한다.

        반환값은 Parser Factory의 사전 키로 사용된다. 따라서 하나의 파서 인스턴스는
        정확히 하나의 ``DocumentType``만 담당해야 하며, 같은 형식을 처리하는 파서가
        중복 등록되면 ``DuplicateDocumentParserError``가 발생한다.

        Returns:
            구현체가 처리하는 ``DocumentType`` 값이다.
        """

        ...

    @property
    def parser_type(self) -> str:
        """Local RAG DB에 저장할 실제 추출 방식 식별자를 반환한다.

        이 값은 단순한 파일 확장자가 아니라, 동일 형식 안에서도 추출 방식을 구분할
        수 있어야 한다. 예를 들어 텍스트 레이어 기반 PDF는 ``PDF_TEXT``를 사용하고,
        향후 OCR 파서가 추가된다면 ``PDF_OCR``처럼 별도 값을 사용해야 한다.

        파서 종류가 달라지면 같은 원본 파일에서도 생성되는 텍스트와 메타데이터가
        달라질 수 있으므로 색인 이력과 운영 진단에서 반드시 구분해야 한다.

        Returns:
            저장 및 진단에 사용할 안정적인 파서 종류 문자열이다.
        """

        ...

    @property
    def parser_version(self) -> str:
        """파싱 결과 호환성을 식별하는 버전을 반환한다.

        다음 변경으로 기존 색인 결과와 호환되지 않게 되면 이 버전을 증가시켜야 한다.

        - 텍스트 정규화 규칙 변경
        - 문단, 슬라이드, 행 등 원본 단위 경계 변경
        - 표 직렬화 형식 변경
        - 원본 위치 메타데이터 키 또는 의미 변경
        - 수식 결과 선택 정책 변경
        - 목록이나 제목 판별 정책 변경

        단순 주석 보강이나 결과에 영향을 주지 않는 내부 리팩터링만으로는 버전을
        증가시키지 않는다.

        Returns:
            재파싱 및 Chunk ID 입력에 사용할 파서 호환 버전 문자열이다.
        """

        ...

    async def parse(self, file_path: Path) -> ParsedDocument:
        """검증된 임시 원본 파일을 공통 파싱 결과로 변환한다.

        호출 시점의 ``file_path``는 ``HttpFileDownloader``가 생성한 임시 파일이다.
        해당 파일은 ``download_and_validate()``의 ``async with`` 블록이 끝나면
        삭제되므로, 파싱은 반드시 그 컨텍스트가 유지되는 동안 완료되어야 한다.

        형식별 원본 위치는 각 ``ParsedDocumentUnit.source_metadata``에 기록한다.
        대표 키는 다음과 같다.

        - PDF: ``page_number``
        - DOCX: ``section_index``, ``block_index``, ``paragraph_index``,
          ``table_index``, ``heading_level``
        - PPTX: ``slide_number``, ``shape_index``, ``shape_path``, ``notes_index``
        - TXT: ``line_number``, ``encoding``
        - XLSX: ``sheet_index``, ``sheet_name``, ``row_number``, ``cell_range``

        형식별 라이브러리의 내부 객체, 열린 파일 핸들, XML 노드 또는 Workbook 객체는
        반환값에 포함하지 않는다. 반환 결과는 불변 데이터 모델만으로 구성하여 임시
        파일 삭제 이후 청킹, 임베딩, Local RAG DB 저장 및 Qdrant 색인에서 안전하게
        사용할 수 있어야 한다.

        Args:
            file_path:
                다운로드, 크기 제한, MIME Type 및 Magic Byte 검증이 완료된 임시
                원본 파일 경로다.

        Returns:
            문서 형식, 원본 위치별 텍스트 단위와 문서 전체 메타데이터를 포함하는
            ``ParsedDocument`` 결과다.
        """

        ...
