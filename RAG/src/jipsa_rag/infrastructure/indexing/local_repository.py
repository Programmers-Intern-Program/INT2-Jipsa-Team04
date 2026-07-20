"""문서, 청크 및 색인 실행 이력을 Local RAG DB에 저장한다."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.indexing.exceptions import LocalRagStorageError
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)

# 동일한 파일, 파일 해시, 파서 버전, 임베딩 모델 및 색인 버전이
# 이미 존재하면 새 문서를 중복 생성하지 않고 기존 문서를 재사용한다.
#
# LAST_INSERT_ID(RAG_Document_IDX)를 사용하면 INSERT와 중복 UPDATE 양쪽에서
# 직후 SELECT LAST_INSERT_ID()로 동일한 문서 PK를 안전하게 가져올 수 있다.
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
        `Updated_At` = CURRENT_TIMESTAMP(6),
        `Deleted_At` = NULL
    """
)

# MySQL/MariaDB 연결 단위의 마지막 AUTO_INCREMENT 값을 조회한다.
# RAG_Document UPSERT의 중복 UPDATE 경로에서도 LAST_INSERT_ID(PK)를
# 명시했으므로 기존 문서 PK를 동일한 방식으로 읽을 수 있다.
_SELECT_LAST_INSERT_ID: Final = text("SELECT LAST_INSERT_ID()")

# 동일 문서와 동일 색인 버전을 다시 처리하는 경우
# 이전 청크를 먼저 제거하여 더 이상 생성되지 않는 잔여 청크가
# Local RAG DB에 남지 않도록 한다.
_DELETE_DOCUMENT_CHUNKS: Final = text(
    """
    DELETE FROM `RAG_Chunk`
    WHERE `RAG_Document_IDX` = :rag_document_idx
      AND `Index_Version` = :index_version
    """
)

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
        `Index_Version`
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
        :index_version
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
        'FULL',
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
        `Updated_At` = CURRENT_TIMESTAMP(6)
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
    """Local RAG DB의 문서, 청크 및 실행 상태를 트랜잭션 단위로 관리한다.

    S3_Key는 AWS 서버 DB의 File 테이블에서만 관리한다.
    이 저장소는 File_IDX를 외부 참조값으로 보관하며 S3 객체 위치를
    Local RAG DB에 복제하지 않는다.
    """

    def __init__(self, session: AsyncSession) -> None:
        """FastAPI 요청 범위의 비동기 SQLAlchemy 세션을 주입받는다."""

        self._session = session

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        """문서와 청크를 저장하고 색인 실행 상태를 RUNNING으로 기록한다.

        문서 UPSERT, 기존 청크 삭제, 새 청크 INSERT 및 실행 이력 INSERT는
        하나의 Local RAG DB 트랜잭션에서 처리한다.

        Qdrant는 별도 데이터베이스이므로 이 트랜잭션에 포함되지 않는다.
        Qdrant 저장 성공 또는 실패에 따른 최종 상태는 mark_indexed()와
        mark_failed()에서 별도 트랜잭션으로 갱신한다.
        """

        started_at = _utc_now_without_timezone()

        document_parameters: dict[str, object] = {
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

        try:
            async with self._session.begin():
                await self._session.execute(
                    _DOCUMENT_UPSERT,
                    document_parameters,
                )
                rag_document_idx = await self._read_last_insert_id(
                    operation="document_upsert",
                )

                await self._session.execute(
                    _DELETE_DOCUMENT_CHUNKS,
                    {
                        "rag_document_idx": rag_document_idx,
                        "index_version": metadata.index_version,
                    },
                )

                # SQLAlchemy execute()에 파라미터 목록을 전달하면
                # asyncmy 드라이버가 executemany 방식으로 청크를 저장한다.
                chunk_parameters = [
                    _build_chunk_parameters(
                        rag_document_idx=rag_document_idx,
                        metadata=metadata,
                        embedding_model=embedded_document.embedding_model,
                        embedded_chunk=embedded_chunk,
                    )
                    for embedded_chunk in embedded_document.chunks
                ]

                await self._session.execute(
                    _INSERT_CHUNK,
                    chunk_parameters,
                )

                await self._session.execute(
                    _INSERT_INDEX_RUN,
                    {
                        "rag_document_idx": rag_document_idx,
                        "file_idx": metadata.file_idx,
                        "users_idx": metadata.users_idx,
                        "parser_type": metadata.parser_type,
                        "parser_version": metadata.parser_version,
                        "embedding_model": embedded_document.embedding_model,
                        "chunk_count": embedded_document.chunk_count,
                        "started_at": started_at,
                    },
                )
                rag_index_run_idx = await self._read_last_insert_id(
                    operation="index_run_insert",
                )

        except LocalRagStorageError:
            # PK 검증처럼 저장소가 이미 의미 있는 예외로 변환한 경우
            # 동일 예외를 다시 감싸지 않고 상위 서비스에 전달한다.
            raise
        except SQLAlchemyError as error:
            # SQL 문과 DB 드라이버 예외 원문은 외부 계층에 노출하지 않는다.
            raise LocalRagStorageError("prepare_indexing") from error

        return PreparedLocalIndex(
            rag_document_idx=rag_document_idx,
            rag_index_run_idx=rag_index_run_idx,
            chunk_ids=tuple(embedded_chunk.chunk_id for embedded_chunk in embedded_document.chunks),
        )

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
    ) -> None:
        """Qdrant 적재가 끝난 문서와 실행 이력을 성공 상태로 변경한다."""

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_DOCUMENT_INDEXED,
                    {
                        "rag_document_idx": rag_document_idx,
                    },
                )
                await self._session.execute(
                    _MARK_RUN_SUCCESS,
                    {
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
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
        """Qdrant 적재가 실패한 문서와 실행 이력을 실패 상태로 변경한다.

        Error_Message에는 예외 타입이나 안전한 작업 코드만 전달해야 한다.
        파일 원문, 청크 내용, 임베딩 벡터, Presigned URL 및 DB 접속 정보는
        이 메서드에 전달하거나 저장하지 않는다.
        """

        safe_error_message = error_message.strip()[:1000] or "INDEX_STORAGE_FAILED"

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_DOCUMENT_FAILED,
                    {
                        "rag_document_idx": rag_document_idx,
                    },
                )
                await self._session.execute(
                    _MARK_RUN_FAILED,
                    {
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
                        "error_message": safe_error_message,
                    },
                )
        except SQLAlchemyError as error:
            raise LocalRagStorageError("mark_failed") from error

    async def _read_last_insert_id(
        self,
        *,
        operation: str,
    ) -> int:
        """현재 DB 연결에서 마지막 INSERT 또는 UPSERT PK를 읽는다."""

        result = await self._session.execute(_SELECT_LAST_INSERT_ID)
        raw_identifier = result.scalar_one()

        if isinstance(raw_identifier, bool) or not isinstance(raw_identifier, int):
            raise LocalRagStorageError(operation)

        if raw_identifier <= 0:
            raise LocalRagStorageError(operation)

        return raw_identifier


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
        "page": _metadata_int(
            source_metadata,
            "page_number",
            minimum=1,
        ),
        "slide_no": (
            _metadata_int(
                source_metadata,
                "slide_number",
                minimum=1,
            )
            or _metadata_int(
                source_metadata,
                "slide_no",
                minimum=1,
            )
        ),
        "sheet_name": _metadata_text(
            source_metadata,
            "sheet_name",
            maximum_length=100,
        ),
        "section_title": _metadata_text(
            source_metadata,
            "section_title",
            maximum_length=255,
        ),
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "content_hash": chunk.content_hash,
        "embedding_model": embedding_model,
        "index_version": metadata.index_version,
    }


def _metadata_int(
    metadata: Mapping[str, object],
    key: str,
    *,
    minimum: int,
) -> int | None:
    """메타데이터에서 지정한 최솟값 이상의 정수를 읽는다."""

    value = metadata.get(key)

    if isinstance(value, bool) or not isinstance(value, int):
        return None

    if value < minimum:
        return None

    return value


def _metadata_text(
    metadata: Mapping[str, object],
    key: str,
    *,
    maximum_length: int,
) -> str | None:
    """메타데이터에서 비어 있지 않은 제한 길이 문자열을 읽는다."""

    value = metadata.get(key)

    if not isinstance(value, str):
        return None

    normalized_value = value.strip()

    if not normalized_value:
        return None

    return normalized_value[:maximum_length]


def _utc_now_without_timezone() -> datetime:
    """MySQL/MariaDB DATETIME(6)에 저장할 UTC 기준 naive datetime을 반환한다."""

    return datetime.now(UTC).replace(tzinfo=None)
