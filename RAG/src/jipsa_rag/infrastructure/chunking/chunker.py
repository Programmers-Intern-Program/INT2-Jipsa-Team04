"""문서 청커가 구현해야 하는 공통 인터페이스를 정의한다."""

from typing import Protocol

from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    ChunkingContext,
)
from jipsa_rag.infrastructure.document.models import ParsedDocument


class DocumentChunker(Protocol):
    """파싱 결과를 공통 ChunkedDocument로 변환하는 인터페이스."""

    async def chunk(
        self,
        *,
        document: ParsedDocument,
        context: ChunkingContext,
    ) -> ChunkedDocument:
        """파싱된 문서를 검색 및 임베딩용 텍스트 청크로 변환한다.

        Args:
            document:
                형식별 DocumentParser가 반환한 공통 문서 파싱 결과다.

            context:
                사용자, 파일, 파일 해시 및 색인 버전처럼
                결정적 Chunk ID 생성에 필요한 문서 식별 정보다.

        Returns:
            문서 전체의 TextChunk 목록과 원본 단위 개수를 포함하는
            ChunkedDocument 결과다.

        Notes:
            구현체는 PDF 페이지, PPTX 슬라이드, XLSX 시트 등
            ParsedDocumentUnit의 원본 위치 경계를 임의로 합치지 않아야 한다.

            각 청크는 원본 ParsedDocumentUnit의 source_metadata를
            유지해야 한다.
        """

        ...
