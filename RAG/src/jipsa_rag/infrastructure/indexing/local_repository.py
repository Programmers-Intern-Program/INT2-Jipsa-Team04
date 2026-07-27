"""문서, 청크와 색인 실행 이력을 Local RAG DB에 저장한다.

RAG_Chunk.Source_Metadata JSON 컬럼은 PDF 페이지 이외의 위치 계약을 보존하는
핵심 컬럼이다. 기존 구현은 Page, Slide_No, Sheet_Name, Section_Title만 저장하여
PPTX 도형 좌표, XLSX 셀 범위와 TXT 문자 범위를 잃을 수 있었다. 이 저장소는
검색·표시용 정규화 컬럼을 계속 채우면서 전체 ``TextChunk.source_metadata``를
결정적인 JSON 문자열로 함께 저장한다.

문서 유일성에는 File_Hash, Parser_Version, Embedding_Model과 Index_Version이
포함된다. 따라서 파서 버전이 바뀌면 기존 INDEXED 문서를 멱등 재사용하지 않고
새 문서를 준비한다. 새 색인이 성공한 뒤에만 이전 문서가 soft delete되므로
재파싱·재색인 도중 장애가 발생해도 기존 검색 결과가 유지된다.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.embedding.models import EmbeddedChunk, EmbeddedDocument
from jipsa_rag.infrastructure.indexing.exceptions import LocalRagStorageError
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)
from jipsa_rag.infrastructure.indexing.source_metadata import (
    dump_source_metadata_json,
    metadata_int,
    metadata_text,
)

_SELECT_EXACT_DOCUMENT: Final = text(
    """
    SELECT
        `RAG_Document_IDX` AS `rag_document_idx`,
        `Index_Status` AS `index_status`
    FROM `RAG_Document`
    WHERE `File_IDX` = :file_idx
      AND `Users_IDX` = :users_idx
      AND `File_Hash` = :file_hash
      AND `Parser_Version` = :parser_version
      AND `Embedding_Model` = :embedding_model
      AND `Index_Version` = :index_version
      AND `Deleted_At` IS NULL
    LIMIT 1
    FOR UPDATE
    """
)

_SELECT_DOCUMENT_CHUNK_IDS: Final = text(
    """
    SELECT `Chunk_ID`
    FROM `RAG_Chunk`
    WHERE `RAG_Document_IDX` = :rag_document_idx
      AND `Index_Version` = :index_version
    ORDER BY `Chunk_Index`
    """
)

_DOCUMENT_UPSERT: Final = text(
    """
    INSERT INTO `RAG_Document` (
        `File_IDX`,
        `Users_IDX`,
        `Folder_IDX`,
        `File_Name`,
        `File_Type`,
        `File_Hash`,
        `Index_Version`,
        `Parse_Status`,
        `Index_Status`,
        `Chunk_Count`,
        `Parser_Type`,
        `Parser_Version`,
        `Embedding_Model`
    )
    VALUES (
        :file_idx,
        :users_idx,
        :folder_idx,
        :file_name,
        :file_type,
        :file_hash,
        :index_version,
        'PARSED',
        'INDEXING',
        :chunk_count,
        :parser_type,
        :parser_version,
        :embedding_model
    )
    ON DUPLICATE KEY UPDATE
        `RAG_Document_IDX` = LAST_INSERT_ID(`RAG_Document_IDX`),
        `Users_IDX` = :users_idx,
        `Folder_IDX` = :folder_idx,
        `File_Name` = :file_name,
        `File_Type` = :file_type,
        `Parse_Status` = 'PARSED',
        `Index_Status` = 'INDEXING',
        `Chunk_Count` = :chunk_count,
        `Parser_Type` = :parser_type,
        `Parser_Version` = :parser_version,
        `Embedding_Model` = :embedding_model,
        `Updated_At` = CURRENT_TIMESTAMP(6),
        `Deleted_At` = NULL
    """
)

_UPDATE_INDEXED_DOCUMENT_SNAPSHOT: Final = text(
    """
    UPDATE `RAG_Document`
    SET `Users_IDX` = :users_idx,
        `Folder_IDX` = :folder_idx,
        `File_Name` = :file_name,
        `File_Type` = :file_type,
        `Parse_Status` = 'PARSED',
        `Chunk_Count` = :chunk_count,
        `Parser_Type` = :parser_type,
        `Parser_Version` = :parser_version,
        `Embedding_Model` = :embedding_model,
        `Updated_At` = CURRENT_TIMESTAMP(6)
    WHERE `RAG_Document_IDX` = :rag_document_idx
      AND `Index_Status` = 'INDEXED'
      AND `Deleted_At` IS NULL
    """
)

_SELECT_LAST_INSERT_ID: Final = text("SELECT LAST_INSERT_ID()")

_SELECT_PREVIOUS_INDEXED_DOCUMENT_IDS: Final = text(
    """
    SELECT `RAG_Document_IDX`
    FROM `RAG_Document`
    WHERE `Users_IDX` = :users_idx
      AND `File_IDX` = :file_idx
      AND `RAG_Document_IDX` <> :rag_document_idx
      AND `Index_Status` = 'INDEXED'
      AND `Deleted_At` IS NULL
    ORDER BY `RAG_Document_IDX`
    FOR UPDATE
    """
)

_DELETE_DOCUMENT_CHUNKS: Final = text(
    """
    DELETE FROM `RAG_Chunk`
    WHERE `RAG_Document_IDX` = :rag_document_idx
      AND `Index_Version` = :index_version
    """
)

# Source_Metadata는 문자열로 직렬화하여 text() 기반 SQL에서도 asyncmy가 안정적으로
# 바인딩할 수 있게 한다. MySQL JSON 컬럼은 유효한 JSON 문자열을 저장 시 검증한다.
_INSERT_CHUNK: Final = text(
    """
    INSERT INTO `RAG_Chunk` (
        `Chunk_ID`,
        `RAG_Document_IDX`,
        `File_IDX`,
        `Users_IDX`,
        `Folder_IDX`,
        `Chunk_Index`,
        `Content`,
        `Token_Count`,
        `Page`,
        `Slide_No`,
        `Sheet_Name`,
        `Section_Title`,
        `Start_Offset`,
        `End_Offset`,
        `Content_Hash`,
        `Embedding_Model`,
        `Index_Version`,
        `Source_Metadata`
    )
    VALUES (
        :chunk_id,
        :rag_document_idx,
        :file_idx,
        :users_idx,
        :folder_idx,
        :chunk_index,
        :content,
        :token_count,
        :page,
        :slide_no,
        :sheet_name,
        :section_title,
        :start_offset,
        :end_offset,
        :content_hash,
        :embedding_model,
        :index_version,
        :source_metadata
    )
    """
)

_INSERT_INDEX_RUN: Final = text(
    """
    INSERT INTO `RAG_Index_Run` (
        `RAG_Document_IDX`,
        `Server_Job_IDX`,
        `File_IDX`,
        `Users_IDX`,
        `Run_Type`,
        `Status`,
        `Parser_Type`,
        `Parser_Version`,
        `Embedding_Model`,
        `Chunk_Count`,
        `Started_At`
    )
    VALUES (
        :rag_document_idx,
        NULL,
        :file_idx,
        :users_idx,
        :run_type,
        'RUNNING',
        :parser_type,
        :parser_version,
        :embedding_model,
        :chunk_count,
        :started_at
    )
    """
)

_MARK_DOCUMENT_INDEXED: Final = text(
    """
    UPDATE `RAG_Document`
    SET `Index_Status` = 'INDEXED',
        `Updated_At` = CURRENT_TIMESTAMP(6),
        `Deleted_At` = NULL
    WHERE `RAG_Document_IDX` = :rag_document_idx
    """
)

_MARK_RUN_SUCCESS: Final = text(
    """
    UPDATE `RAG_Index_Run`
    SET `Status` = 'SUCCESS',
        `Finished_At` = :finished_at,
        `Error_Message` = NULL
    WHERE `RAG_Index_Run_IDX` = :rag_index_run_idx
    """
)

_SOFT_DELETE_SUPERSEDED_DOCUMENTS: Final = text(
    """
    UPDATE `RAG_Document`
    SET `Deleted_At` = CURRENT_TIMESTAMP(6),
        `Updated_At` = CURRENT_TIMESTAMP(6)
    WHERE `RAG_Document_IDX` IN :rag_document_idxs
      AND `RAG_Document_IDX` <> :current_rag_document_idx
      AND `Index_Status` = 'INDEXED'
      AND `Deleted_At` IS NULL
    """
).bindparams(bindparam("rag_document_idxs", expanding=True))

_MARK_DOCUMENT_FAILED: Final = text(
    """
    UPDATE `RAG_Document`
    SET `Index_Status` = 'FAILED',
        `Updated_At` = CURRENT_TIMESTAMP(6)
    WHERE `RAG_Document_IDX` = :rag_document_idx
    """
)

_MARK_RUN_FAILED: Final = text(
    """
    UPDATE `RAG_Index_Run`
    SET `Status` = 'FAILED',
        `Finished_At` = :finished_at,
        `Error_Message` = :error_message
    WHERE `RAG_Index_Run_IDX` = :rag_index_run_idx
    """
)


class LocalRagIndexRepository:
    """Local RAG 문서·청크·실행 상태를 트랜잭션 단위로 관리한다."""

    def __init__(self, session: AsyncSession) -> None:
        """FastAPI 요청 범위의 비동기 SQLAlchemy 세션을 주입받는다."""

        self._session = session

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        """새 색인을 준비하거나 정확히 같은 기존 정상 색인을 재사용한다.

        Parser_Version이 달라지면 ``_read_exact_document``가 기존 문서를 찾지 못한다.
        이 경우 새 RAG_Document와 RAG_Chunk를 만들고 같은 사용자·파일의 기존 정상
        문서를 ``previous_rag_document_idxs``로 반환한다. 서비스 계층은 새 Qdrant
        Point가 모두 활성화된 뒤에만 이 이전 문서들을 비활성화한다.
        """

        started_at = _utc_now_without_timezone()
        incoming_chunk_ids = tuple(
            embedded_chunk.chunk_id for embedded_chunk in embedded_document.chunks
        )
        document_parameters = _build_document_parameters(
            metadata=metadata,
            embedded_document=embedded_document,
        )

        try:
            async with self._session.begin():
                existing_document = await self._read_exact_document(
                    metadata=metadata,
                    embedding_model=embedded_document.embedding_model,
                )

                if existing_document is not None and existing_document[1] == "INDEXED":
                    rag_document_idx = existing_document[0]
                    await self._assert_existing_chunk_identity(
                        rag_document_idx=rag_document_idx,
                        index_version=metadata.index_version,
                        expected_chunk_ids=incoming_chunk_ids,
                    )
                    await self._session.execute(
                        _UPDATE_INDEXED_DOCUMENT_SNAPSHOT,
                        {
                            **document_parameters,
                            "rag_document_idx": rag_document_idx,
                        },
                    )
                    previous_document_ids = await self._read_previous_indexed_document_ids(
                        users_idx=metadata.users_idx,
                        file_idx=metadata.file_idx,
                        current_rag_document_idx=rag_document_idx,
                    )
                    rag_index_run_idx = await self._insert_index_run(
                        rag_document_idx=rag_document_idx,
                        metadata=metadata,
                        embedded_document=embedded_document,
                        started_at=started_at,
                        run_type=("REINDEX" if previous_document_ids else "FULL"),
                    )

                    return PreparedLocalIndex(
                        rag_document_idx=rag_document_idx,
                        rag_index_run_idx=rag_index_run_idx,
                        chunk_ids=incoming_chunk_ids,
                        previous_rag_document_idxs=previous_document_ids,
                        reuses_existing_index=True,
                    )

                await self._session.execute(_DOCUMENT_UPSERT, document_parameters)
                rag_document_idx = await self._read_last_insert_id(operation="document_upsert")
                previous_document_ids = await self._read_previous_indexed_document_ids(
                    users_idx=metadata.users_idx,
                    file_idx=metadata.file_idx,
                    current_rag_document_idx=rag_document_idx,
                )
                await self._session.execute(
                    _DELETE_DOCUMENT_CHUNKS,
                    {
                        "rag_document_idx": rag_document_idx,
                        "index_version": metadata.index_version,
                    },
                )

                chunk_parameters = [
                    _build_chunk_parameters(
                        rag_document_idx=rag_document_idx,
                        metadata=metadata,
                        embedding_model=embedded_document.embedding_model,
                        embedded_chunk=embedded_chunk,
                    )
                    for embedded_chunk in embedded_document.chunks
                ]
                await self._session.execute(_INSERT_CHUNK, chunk_parameters)

                rag_index_run_idx = await self._insert_index_run(
                    rag_document_idx=rag_document_idx,
                    metadata=metadata,
                    embedded_document=embedded_document,
                    started_at=started_at,
                    run_type=("REINDEX" if previous_document_ids else "FULL"),
                )
        except LocalRagStorageError:
            raise
        except ValueError as error:
            # JSON 비호환 메타데이터는 DB 드라이버 오류가 되기 전에 명확한 저장 계약
            # 위반으로 변환한다. 원문 메타데이터 값은 예외 메시지에 포함하지 않는다.
            raise LocalRagStorageError("serialize_source_metadata") from error
        except SQLAlchemyError as error:
            raise LocalRagStorageError("prepare_indexing") from error

        return PreparedLocalIndex(
            rag_document_idx=rag_document_idx,
            rag_index_run_idx=rag_index_run_idx,
            chunk_ids=incoming_chunk_ids,
            previous_rag_document_idxs=previous_document_ids,
            reuses_existing_index=False,
        )

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        superseded_rag_document_idxs: tuple[int, ...],
    ) -> None:
        """현재 문서·실행을 성공 처리하고 대체된 이전 문서를 soft delete한다."""

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_DOCUMENT_INDEXED,
                    {"rag_document_idx": rag_document_idx},
                )
                await self._session.execute(
                    _MARK_RUN_SUCCESS,
                    {
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
                    },
                )
                if superseded_rag_document_idxs:
                    await self._session.execute(
                        _SOFT_DELETE_SUPERSEDED_DOCUMENTS,
                        {
                            "rag_document_idxs": superseded_rag_document_idxs,
                            "current_rag_document_idx": rag_document_idx,
                        },
                    )
        except SQLAlchemyError as error:
            raise LocalRagStorageError("mark_indexed") from error

    async def mark_failed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """신규 문서와 실행 이력을 실패 상태로 변경한다."""

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_DOCUMENT_FAILED,
                    {"rag_document_idx": rag_document_idx},
                )
                await self._session.execute(
                    _MARK_RUN_FAILED,
                    {
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
                        "error_message": _normalize_error_message(error_message),
                    },
                )
        except SQLAlchemyError as error:
            raise LocalRagStorageError("mark_failed") from error

    async def mark_run_failed(
        self,
        *,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """기존 INDEXED 문서는 유지하고 이번 실행 이력만 실패 처리한다."""

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_RUN_FAILED,
                    {
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
                        "error_message": _normalize_error_message(error_message),
                    },
                )
        except SQLAlchemyError as error:
            raise LocalRagStorageError("mark_run_failed") from error

    async def _read_exact_document(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedding_model: str,
    ) -> tuple[int, str] | None:
        """현재 색인 식별자와 정확히 일치하는 활성 문서를 잠그고 읽는다."""

        result = await self._session.execute(
            _SELECT_EXACT_DOCUMENT,
            {
                "file_idx": metadata.file_idx,
                "users_idx": metadata.users_idx,
                "file_hash": metadata.file_hash,
                "parser_version": metadata.parser_version,
                "embedding_model": embedding_model,
                "index_version": metadata.index_version,
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None

        return (
            _require_positive_int(row["rag_document_idx"], operation="select_exact_document"),
            _require_non_empty_text(row["index_status"], operation="select_exact_document"),
        )

    async def _assert_existing_chunk_identity(
        self,
        *,
        rag_document_idx: int,
        index_version: int,
        expected_chunk_ids: tuple[str, ...],
    ) -> None:
        """기존 청크와 이번 실행의 결정적 Chunk ID가 정확히 같은지 확인한다."""

        result = await self._session.execute(
            _SELECT_DOCUMENT_CHUNK_IDS,
            {
                "rag_document_idx": rag_document_idx,
                "index_version": index_version,
            },
        )
        existing_chunk_ids = tuple(
            _require_non_empty_text(value, operation="select_document_chunk_ids")
            for value in result.scalars().all()
        )
        if existing_chunk_ids != expected_chunk_ids:
            raise LocalRagStorageError("indexed_chunk_identity_mismatch")

    async def _read_previous_indexed_document_ids(
        self,
        *,
        users_idx: int,
        file_idx: int,
        current_rag_document_idx: int,
    ) -> tuple[int, ...]:
        """현재 문서가 성공 후 대체할 이전 정상 문서 식별자를 잠그고 반환한다."""

        result = await self._session.execute(
            _SELECT_PREVIOUS_INDEXED_DOCUMENT_IDS,
            {
                "users_idx": users_idx,
                "file_idx": file_idx,
                "rag_document_idx": current_rag_document_idx,
            },
        )
        document_ids = tuple(
            _require_positive_int(value, operation="select_previous_indexed_documents")
            for value in result.scalars().all()
        )
        if len(set(document_ids)) != len(document_ids):
            raise LocalRagStorageError("select_previous_indexed_documents")
        return document_ids

    async def _insert_index_run(
        self,
        *,
        rag_document_idx: int,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
        started_at: datetime,
        run_type: str,
    ) -> int:
        """이번 색인 시도를 FULL 또는 REINDEX 실행 이력으로 저장한다."""

        normalized_run_type = run_type.strip().upper()
        if normalized_run_type not in {"FULL", "REINDEX"}:
            raise LocalRagStorageError("invalid_index_run_type")

        await self._session.execute(
            _INSERT_INDEX_RUN,
            {
                "rag_document_idx": rag_document_idx,
                "file_idx": metadata.file_idx,
                "users_idx": metadata.users_idx,
                "run_type": normalized_run_type,
                "parser_type": metadata.parser_type,
                "parser_version": metadata.parser_version,
                "embedding_model": embedded_document.embedding_model,
                "chunk_count": embedded_document.chunk_count,
                "started_at": started_at,
            },
        )
        return await self._read_last_insert_id(operation="index_run_insert")

    async def _read_last_insert_id(self, *, operation: str) -> int:
        """현재 DB 연결의 마지막 INSERT 또는 UPSERT PK를 읽는다."""

        result = await self._session.execute(_SELECT_LAST_INSERT_ID)
        return _require_positive_int(result.scalar_one(), operation=operation)


def _build_document_parameters(
    *,
    metadata: DocumentIndexMetadata,
    embedded_document: EmbeddedDocument,
) -> dict[str, object]:
    """RAG_Document UPSERT 파라미터를 생성한다."""

    return {
        "file_idx": metadata.file_idx,
        "users_idx": metadata.users_idx,
        "folder_idx": metadata.folder_idx,
        "file_name": metadata.file_name,
        "file_type": metadata.file_type.value,
        "file_hash": metadata.file_hash,
        "index_version": metadata.index_version,
        "chunk_count": embedded_document.chunk_count,
        "parser_type": metadata.parser_type,
        "parser_version": metadata.parser_version,
        "embedding_model": embedded_document.embedding_model,
    }


def _build_chunk_parameters(
    *,
    rag_document_idx: int,
    metadata: DocumentIndexMetadata,
    embedding_model: str,
    embedded_chunk: EmbeddedChunk,
) -> dict[str, object]:
    """EmbeddedChunk를 RAG_Chunk INSERT 파라미터로 변환한다."""

    chunk = embedded_chunk.chunk
    source_metadata: Mapping[str, object] = chunk.source_metadata

    return {
        "chunk_id": chunk.chunk_id,
        "rag_document_idx": rag_document_idx,
        "file_idx": metadata.file_idx,
        "users_idx": metadata.users_idx,
        "folder_idx": metadata.folder_idx,
        "chunk_index": chunk.chunk_index,
        "content": chunk.content,
        "token_count": chunk.token_count,
        "page": metadata_int(source_metadata, "page_number", minimum=1),
        "slide_no": (
            metadata_int(source_metadata, "slide_number", minimum=1)
            or metadata_int(source_metadata, "slide_no", minimum=1)
        ),
        "sheet_name": metadata_text(
            source_metadata,
            "sheet_name",
            maximum_length=100,
        ),
        "section_title": metadata_text(
            source_metadata,
            "section_title",
            maximum_length=255,
        ),
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "content_hash": chunk.content_hash,
        "embedding_model": embedding_model,
        "index_version": metadata.index_version,
        "source_metadata": dump_source_metadata_json(source_metadata),
    }


def _require_positive_int(value: object, *, operation: str) -> int:
    """DB 결과가 bool이 아닌 양의 정수인지 검증한다."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LocalRagStorageError(operation)
    return value


def _require_non_empty_text(value: object, *, operation: str) -> str:
    """DB 결과가 비어 있지 않은 문자열인지 검증한다."""

    if not isinstance(value, str) or not value.strip():
        raise LocalRagStorageError(operation)
    return value.strip()


def _normalize_error_message(error_message: str) -> str:
    """실행 이력에 저장할 안전한 제한 길이 오류 메시지를 생성한다."""

    return error_message.strip()[:1000] or "INDEX_STORAGE_FAILED"


def _utc_now_without_timezone() -> datetime:
    """MySQL DATETIME(6)에 저장할 UTC 기준 naive datetime을 반환한다."""

    return datetime.now(UTC).replace(tzinfo=None)
