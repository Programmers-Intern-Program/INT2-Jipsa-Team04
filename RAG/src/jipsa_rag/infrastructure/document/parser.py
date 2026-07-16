"""문서 형식별 파서가 구현해야 하는 공통 인터페이스를 정의한다."""

from pathlib import Path
from typing import Protocol

from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
)


class DocumentParser(Protocol):
    """원본 문서를 공통 ParsedDocument 모델로 변환하는 파서 인터페이스.

    PDF, DOCX, XLSX 및 PPTX 형식별 파서는 모두 이 인터페이스와
    동일한 속성과 메서드를 제공해야 한다.

    Protocol은 명시적인 상속보다 객체의 구조를 기준으로 타입을 판단한다.
    따라서 형식별 파서가 DocumentParser를 직접 상속하지 않더라도
    file_type 속성과 parse() 메서드를 동일한 타입으로 구현하면
    DocumentParser 구현체로 사용할 수 있다.

    이 인터페이스를 통해 파일 처리 API와 서비스 계층은
    PdfDocumentParser, DocxDocumentParser와 같은 구체 클래스에
    직접 의존하지 않고 모든 문서 파서를 동일한 방식으로 호출한다.
    """

    @property
    def file_type(self) -> DocumentType:
        """현재 파서가 처리할 수 있는 문서 형식을 반환한다.

        각 형식별 파서는 자신이 담당하는 DocumentType을 반환해야 한다.

        예:
            PdfDocumentParser는 DocumentType.PDF를 반환한다.
            DocxDocumentParser는 DocumentType.DOCX를 반환한다.
            XlsxDocumentParser는 DocumentType.XLSX를 반환한다.
            PptxDocumentParser는 DocumentType.PPTX를 반환한다.

        Returns:
            현재 파서가 처리하는 공통 문서 형식이다.
        """

        ...

    async def parse(
        self,
        file_path: Path,
    ) -> ParsedDocument:
        """원본 문서를 읽어 공통 문서 파싱 결과로 변환한다.

        형식별 파서는 원본 문서의 위치 단위를 유지하면서
        ParsedDocumentUnit 목록을 생성해야 한다.

        각 문서 형식의 원본 위치 정보는 ParsedDocumentUnit의
        source_metadata에 저장한다.

        원본 위치 메타데이터 예:
            PDF:
                page_number

            DOCX:
                paragraph_index
                table_index

            XLSX:
                sheet_name
                cell_range

            PPTX:
                slide_number
                shape_index

        PDF, DOCX, XLSX 및 PPTX 파싱 라이브러리는 대부분
        동기식 파일 입출력을 수행한다. 각 구현체는 필요한 경우
        asyncio.to_thread()를 사용하여 FastAPI 이벤트 루프를
        직접 차단하지 않도록 구현한다.

        Args:
            file_path:
                다운로드와 유효성 검증이 완료된 임시 원본 파일 경로다.

                HttpFileDownloader가 반환한 임시 파일은
                download_and_validate()의 async with 블록이 종료되면
                삭제된다. 따라서 문서 파싱은 반드시 해당 컨텍스트가
                유지되는 동안 실행해야 한다.

        Returns:
            문서 형식, 원본 위치별 텍스트 단위 및 문서 메타데이터를
            포함하는 공통 ParsedDocument 결과다.
        """

        ...
