"""원본 위치 단위 경계를 유지하는 문자 기반 문서 청커를 제공한다."""

import asyncio
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final
from uuid import UUID, uuid5

from jipsa_rag.infrastructure.chunking.exceptions import (
    InvalidChunkingConfigurationError,
    NoDocumentChunksError,
)
from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    ChunkingContext,
    TextChunk,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    SourceMetadataValue,
)

# ParsedDocument.text는 텍스트가 있는 원본 단위 사이를
# 두 줄의 줄바꿈으로 연결한다.
#
# 문서 전체 Start/End Offset을 계산할 때 반드시 동일한 구분자를 사용해야
# 실제 ParsedDocument.text 문자열 위치와 청크 오프셋이 일치한다.
_DOCUMENT_UNIT_SEPARATOR: Final[str] = "\n\n"


# Chunk ID는 Local RAG DB와 Qdrant에서 동일하게 사용하는 UUID다.
#
# UUIDv5 Namespace는 한번 사용하기 시작하면 변경하면 안 된다.
# Namespace가 변경되면 동일한 파일과 동일한 청크에서도
# 서로 다른 Chunk ID가 생성되기 때문이다.
_CHUNK_ID_NAMESPACE: Final[UUID] = UUID("2a9ab273-ec4b-5df7-bcb4-cd1ed3171094")


# 최대 문자 수에 도달하기 전에 다음 구분자를 우선순위대로 탐색한다.
#
# 문단, 줄, 문장, 구 및 단어 경계를 우선 사용하고
# 적절한 경계가 없으면 최대 문자 수 위치에서 강제로 분할한다.
_PREFERRED_SEPARATORS: Final[tuple[str, ...]] = (
    "\n\n",
    "\n",
    ". ",
    "! ",
    "? ",
    "; ",
    ", ",
    " ",
)


@dataclass(frozen=True, slots=True)
class _ChunkSlice:
    """원본 단위 문자열 안에서 선택한 단일 청크 구간."""

    start_offset: int
    end_offset: int
    content: str


class CharacterTextChunker:
    """원본 위치 단위를 넘지 않는 문자 기반 텍스트 청커.

    한 PDF 페이지, DOCX 문단·표, XLSX 시트·셀 범위 또는
    PPTX 슬라이드·도형과 같은 ParsedDocumentUnit을 독립적으로 처리한다.

    따라서 한 청크가 서로 다른 PDF 페이지나 PPTX 슬라이드에 걸쳐
    생성되지 않으며, 각 청크는 원본 단위의 source_metadata를 유지한다.

    청크 크기는 문자 수를 기준으로 제한한다.

    정확한 토큰 수는 실제 임베딩 모델 토크나이저에 따라 달라지므로
    현재 단계에서는 계산하지 않는다.
    """

    def __init__(
        self,
        *,
        chunk_size_chars: int = 1000,
        chunk_overlap_chars: int = 200,
    ) -> None:
        """최대 청크 문자 수와 인접 청크 중첩 문자 수를 설정한다."""

        if (
            chunk_size_chars <= 0
            or chunk_overlap_chars < 0
            or chunk_overlap_chars >= chunk_size_chars
        ):
            raise InvalidChunkingConfigurationError(
                chunk_size_chars=chunk_size_chars,
                chunk_overlap_chars=chunk_overlap_chars,
            )

        self._chunk_size_chars = chunk_size_chars
        self._chunk_overlap_chars = chunk_overlap_chars

    @property
    def chunk_size_chars(self) -> int:
        """청크 하나에 허용되는 최대 문자 수를 반환한다."""

        return self._chunk_size_chars

    @property
    def chunk_overlap_chars(self) -> int:
        """인접 청크 사이의 최대 중첩 문자 수를 반환한다."""

        return self._chunk_overlap_chars

    async def chunk(
        self,
        *,
        document: ParsedDocument,
        context: ChunkingContext,
    ) -> ChunkedDocument:
        """문서 청킹을 별도의 작업 스레드에서 수행한다.

        문서가 매우 크면 문자열 탐색과 SHA-256 계산도 일정 시간 동안
        CPU를 사용할 수 있다.

        FastAPI 이벤트 루프에서 직접 처리하지 않고 asyncio.to_thread()로
        이동하여 다른 비동기 요청 처리를 차단하지 않도록 한다.
        """

        return await asyncio.to_thread(
            self._chunk_sync,
            document,
            context,
        )

    def _chunk_sync(
        self,
        document: ParsedDocument,
        context: ChunkingContext,
    ) -> ChunkedDocument:
        """원본 위치 단위별 텍스트 청크와 메타데이터를 생성한다."""

        chunks: list[TextChunk] = []

        # ParsedDocument.text 기준의 전역 문자 위치를 계산한다.
        #
        # 텍스트가 없는 빈 페이지는 ParsedDocument.text 결합에서 제외되므로
        # document_offset도 빈 단위에서는 증가시키지 않는다.
        document_offset = 0
        has_previous_text_unit = False

        for source_unit_index, unit in enumerate(document.units):
            if not unit.text:
                # 빈 페이지도 ParsedDocument에는 남아 있지만
                # 임베딩할 텍스트가 없으므로 청크는 생성하지 않는다.
                continue

            if has_previous_text_unit:
                document_offset += len(_DOCUMENT_UNIT_SEPARATOR)

            unit_document_start_offset = document_offset

            for chunk_slice in self._split_text(unit.text):
                chunk_index = len(chunks)

                content_hash = hashlib.sha256(chunk_slice.content.encode("utf-8")).hexdigest()

                chunk_id = self._create_chunk_id(
                    context=context,
                    file_type=document.file_type,
                    chunk_index=chunk_index,
                    content_hash=content_hash,
                )

                # 파서가 생성한 page_number, slide_number, sheet_name 등의
                # 원본 메타데이터를 그대로 복사한다.
                source_metadata: dict[str, SourceMetadataValue] = dict(unit.source_metadata)

                # Start/End Offset은 문서 전체 기준으로 저장하지만,
                # 특정 페이지·슬라이드·시트 안에서의 위치도 추적할 수 있도록
                # 단위 내부 오프셋을 Source Metadata에 함께 저장한다.
                source_metadata.update(
                    {
                        "source_unit_index": source_unit_index,
                        "unit_start_offset": chunk_slice.start_offset,
                        "unit_end_offset": chunk_slice.end_offset,
                    }
                )

                chunks.append(
                    TextChunk(
                        chunk_id=chunk_id,
                        chunk_index=chunk_index,
                        content=chunk_slice.content,
                        content_hash=content_hash,
                        start_offset=(unit_document_start_offset + chunk_slice.start_offset),
                        end_offset=(unit_document_start_offset + chunk_slice.end_offset),
                        source_metadata=source_metadata,
                    )
                )

            document_offset += len(unit.text)
            has_previous_text_unit = True

        if not chunks:
            raise NoDocumentChunksError(document.file_type)

        return ChunkedDocument(
            file_type=document.file_type,
            chunks=tuple(chunks),
            source_unit_count=document.unit_count,
            text_unit_count=document.text_unit_count,
        )

    def _split_text(
        self,
        text: str,
    ) -> Iterator[_ChunkSlice]:
        """원본 문자열을 최대 크기와 중첩 범위에 따라 분할한다."""

        text_length = len(text)
        start_offset = 0

        while start_offset < text_length:
            candidate_end_offset = min(
                start_offset + self._chunk_size_chars,
                text_length,
            )

            end_offset = self._find_split_end(
                text=text,
                start_offset=start_offset,
                candidate_end_offset=candidate_end_offset,
            )

            content_start_offset, content_end_offset = self._trim_whitespace_bounds(
                text=text,
                start_offset=start_offset,
                end_offset=end_offset,
            )

            if content_start_offset < content_end_offset:
                yield _ChunkSlice(
                    start_offset=content_start_offset,
                    end_offset=content_end_offset,
                    content=text[content_start_offset:content_end_offset],
                )

            if end_offset >= text_length:
                break

            # 다음 청크는 현재 분할 위치에서 overlap만큼 되돌아간다.
            #
            # 분할 경계가 예상보다 앞쪽에서 선택되더라도 반드시
            # start_offset보다 큰 값으로 이동하여 무한 반복을 방지한다.
            next_start_offset = end_offset - self._chunk_overlap_chars

            if next_start_offset <= start_offset:
                next_start_offset = start_offset + 1

            start_offset = next_start_offset

    def _find_split_end(
        self,
        *,
        text: str,
        start_offset: int,
        candidate_end_offset: int,
    ) -> int:
        """최대 크기 안에서 가장 적절한 텍스트 경계를 찾는다."""

        if candidate_end_offset >= len(text):
            return len(text)

        # 너무 앞쪽의 구분자를 선택하면 매우 작은 청크가 만들어질 수 있다.
        #
        # 최소한 청크 크기의 절반 이상을 사용하고,
        # overlap보다 한 문자 이상 전진한 범위에서만 경계를 탐색한다.
        minimum_boundary_distance = max(
            self._chunk_overlap_chars + 1,
            self._chunk_size_chars // 2,
        )

        boundary_search_start = min(
            candidate_end_offset,
            start_offset + minimum_boundary_distance,
        )

        for separator in _PREFERRED_SEPARATORS:
            separator_index = text.rfind(
                separator,
                boundary_search_start,
                candidate_end_offset,
            )

            if separator_index >= 0:
                # 마침표와 같은 구분 문자는 앞 청크에 포함하고,
                # 뒤따르는 공백은 이후 trim 단계에서 제거한다.
                return separator_index + len(separator)

        # 문단, 문장 또는 공백 경계를 찾지 못한 긴 문자열은
        # 최대 문자 수 위치에서 강제로 분할한다.
        return candidate_end_offset

    @staticmethod
    def _trim_whitespace_bounds(
        *,
        text: str,
        start_offset: int,
        end_offset: int,
    ) -> tuple[int, int]:
        """청크 양 끝의 공백을 제거하면서 실제 오프셋을 보정한다."""

        normalized_start_offset = start_offset
        normalized_end_offset = end_offset

        while (
            normalized_start_offset < normalized_end_offset
            and text[normalized_start_offset].isspace()
        ):
            normalized_start_offset += 1

        while (
            normalized_end_offset > normalized_start_offset
            and text[normalized_end_offset - 1].isspace()
        ):
            normalized_end_offset -= 1

        return (
            normalized_start_offset,
            normalized_end_offset,
        )

    @staticmethod
    def _create_chunk_id(
        *,
        context: ChunkingContext,
        file_type: DocumentType,
        chunk_index: int,
        content_hash: str,
    ) -> str:
        """문서와 청크 정보로 결정적 UUIDv5 Chunk ID를 생성한다.

        같은 사용자, 파일, 파일 해시, 색인 버전, 청크 순번 및
        청크 내용이면 항상 같은 UUID가 생성된다.

        폴더 위치와 파일명은 Chunk ID 입력에서 제외한다.
        파일 이동이나 이름 변경만으로 기존 Vector Point ID가
        불필요하게 변경되지 않도록 하기 위한 정책이다.
        """

        canonical_chunk_identity = "\x1f".join(
            (
                str(context.users_idx),
                str(context.file_idx),
                context.file_hash,
                file_type.value,
                str(context.index_version),
                str(chunk_index),
                content_hash,
            )
        )

        return str(
            uuid5(
                _CHUNK_ID_NAMESPACE,
                canonical_chunk_identity,
            )
        )
