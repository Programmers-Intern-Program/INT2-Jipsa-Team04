"""문서, 청크 및 색인 실행 이력을 Local RAG DB에 저장한다."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
)
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
    PreparedLocalIndex,
)

# DB의 문서 유일성 기준과 동일한 식별 정보로 기존 문서를 잠근다.
#
# 동일 식별자의 문서가 이미 INDEXED라면 정상 문서를 INDEXING 또는 FAILED로
# 되돌리지 않고 멱등 재사용 경로로 처리해야 한다.
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

# 이미 INDEXED인 동일 문서를 재사용할 때 Local RAG DB의 청크 집합이
# 이번 실행이 생성한 결정적 Chunk ID와 정확히 같은지 확인한다.
#
# 다르면 동일한 Index_Version 아래에서 청킹 정책 또는 Chunk ID 규칙이
# 변경된 상태이므로 기존 정상 문서를 덮어쓰지 않고 명시적으로 실패시킨다.
_SELECT_DOCUMENT_CHUNK_IDS: Final = text(
    """
    SELECT `Chunk_ID`
    FROM `RAG_Chunk`
    WHERE `RAG_Document_IDX` = :rag_document_idx
      AND `Index_Version` = :index_version
    ORDER BY `Chunk_Index`
    """
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
        `Parser_Version` = :parser_version,
        `Embedding_Model` = :embedding_model,
        `Updated_At` = CURRENT_TIMESTAMP(6),
        `Deleted_At` = NULL
    """
)

# 기존 정상 문서를 멱등 재사용하는 경우 상태는 INDEXED로 유지하고
# 이름, 폴더 및 표시용 메타데이터만 최신 manifest 값으로 갱신한다.
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

# MySQL/MariaDB 연결 단위의 마지막 AUTO_INCREMENT 값을 조회한다.
# RAG_Document UPSERT의 중복 UPDATE 경로에서도 LAST_INSERT_ID(PK)를
# 명시했으므로 기존 문서 PK를 동일한 방식으로 읽을 수 있다.
_SELECT_LAST_INSERT_ID: Final = text("SELECT LAST_INSERT_ID()")

# 같은 사용자·파일 범위에서 현재까지 정상 검색 대상으로 남아 있는
# 이전 문서를 잠그고 식별자를 조회한다.
#
# 신규 색인이 완전히 성공하기 전까지 이 문서들의 Qdrant Point와
# Local RAG 상태는 변경하지 않는다.
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

# 동일 문서와 동일 색인 버전을 다시 준비하는 경우 이전 청크를 먼저
# 제거한다. 단, 기존 문서가 이미 INDEXED인 멱등 재사용 경로에서는
# 이 SQL을 실행하지 않으므로 정상 청크가 신규 실행 실패로 유실되지 않는다.
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

# Qdrant에서 이전 문서 Point를 비활성화한 뒤 Local RAG에서도
# 이전 문서를 검색 대상에서 제외한다. 청크와 실행 이력은 감사·복구를 위해
# 물리 삭제하지 않고 문서에 Deleted_At만 기록한다.
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
).bindparams(
    bindparam(
        "rag_document_idxs",
        expanding=True,
    )
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

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """FastAPI 요청 범위의 비동기 SQLAlchemy 세션을 주입받는다."""

        self._session = session

    async def prepare_indexing(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> PreparedLocalIndex:
        """신규 색인을 준비하거나 동일한 기존 정상 색인을 멱등 재사용한다.

        신규 문서 경로에서는 문서 UPSERT, 기존 실패 청크 정리, 새 청크 저장 및
        실행 이력 생성을 하나의 Local RAG DB 트랜잭션으로 처리한다.

        동일 식별자의 문서가 이미 INDEXED이면 정상 문서와 청크를 변경하지 않고
        청크 ID 일치 여부를 검증한 뒤 새로운 실행 이력만 RUNNING으로 생성한다.
        이 분기를 통해 Qdrant 재업서트가 실패해도 기존 정상 문서가 FAILED로
        덮어써지거나 기존 청크가 삭제되지 않는다.
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
                    embedding_model=(embedded_document.embedding_model),
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
                            "users_idx": metadata.users_idx,
                            "folder_idx": metadata.folder_idx,
                            "file_name": metadata.file_name,
                            "file_type": metadata.file_type.value,
                            "chunk_count": (embedded_document.chunk_count),
                            "parser_type": metadata.parser_type,
                            "parser_version": (metadata.parser_version),
                            "embedding_model": (embedded_document.embedding_model),
                            "rag_document_idx": rag_document_idx,
                        },
                    )

                    previous_document_ids = await self._read_previous_indexed_document_ids(
                        users_idx=metadata.users_idx,
                        file_idx=metadata.file_idx,
                        current_rag_document_idx=(rag_document_idx),
                    )

                    rag_index_run_idx = await self._insert_index_run(
                        rag_document_idx=rag_document_idx,
                        metadata=metadata,
                        embedded_document=embedded_document,
                        started_at=started_at,
                    )

                    return PreparedLocalIndex(
                        rag_document_idx=rag_document_idx,
                        rag_index_run_idx=rag_index_run_idx,
                        chunk_ids=incoming_chunk_ids,
                        previous_rag_document_idxs=(previous_document_ids),
                        reuses_existing_index=True,
                    )

                await self._session.execute(
                    _DOCUMENT_UPSERT,
                    document_parameters,
                )
                rag_document_idx = await self._read_last_insert_id(
                    operation="document_upsert",
                )

                previous_document_ids = await self._read_previous_indexed_document_ids(
                    users_idx=metadata.users_idx,
                    file_idx=metadata.file_idx,
                    current_rag_document_idx=(rag_document_idx),
                )

                await self._session.execute(
                    _DELETE_DOCUMENT_CHUNKS,
                    {
                        "rag_document_idx": rag_document_idx,
                        "index_version": (metadata.index_version),
                    },
                )

                # SQLAlchemy execute()에 파라미터 목록을 전달하면
                # asyncmy 드라이버가 executemany 방식으로 청크를 저장한다.
                chunk_parameters = [
                    _build_chunk_parameters(
                        rag_document_idx=rag_document_idx,
                        metadata=metadata,
                        embedding_model=(embedded_document.embedding_model),
                        embedded_chunk=embedded_chunk,
                    )
                    for embedded_chunk in (embedded_document.chunks)
                ]

                await self._session.execute(
                    _INSERT_CHUNK,
                    chunk_parameters,
                )

                rag_index_run_idx = await self._insert_index_run(
                    rag_document_idx=rag_document_idx,
                    metadata=metadata,
                    embedded_document=embedded_document,
                    started_at=started_at,
                )

        except LocalRagStorageError:
            # PK 및 청크 식별자 검증처럼 저장소가 이미 의미 있는 예외로
            # 변환한 경우 동일 예외를 다시 감싸지 않고 상위 서비스에 전달한다.
            raise

        except SQLAlchemyError as error:
            # SQL 문과 DB 드라이버 예외 원문은 외부 계층에 노출하지 않는다.
            raise LocalRagStorageError("prepare_indexing") from error

        return PreparedLocalIndex(
            rag_document_idx=rag_document_idx,
            rag_index_run_idx=rag_index_run_idx,
            chunk_ids=incoming_chunk_ids,
            previous_rag_document_idxs=(previous_document_ids),
            reuses_existing_index=False,
        )

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        superseded_rag_document_idxs: tuple[int, ...],
    ) -> None:
        """현재 문서·실행을 성공 처리하고 대체된 이전 문서를 soft delete한다.

        이 메서드는 Qdrant에서 신규 Point 활성화와 이전 Point 비활성화가
        모두 완료된 뒤 호출된다. 세 Local RAG 상태 변경은 하나의 DB
        트랜잭션으로 확정한다.
        """

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_DOCUMENT_INDEXED,
                    {
                        "rag_document_idx": (rag_document_idx),
                    },
                )
                await self._session.execute(
                    _MARK_RUN_SUCCESS,
                    {
                        "rag_index_run_idx": (rag_index_run_idx),
                        "finished_at": (_utc_now_without_timezone()),
                    },
                )

                if superseded_rag_document_idxs:
                    await self._session.execute(
                        _SOFT_DELETE_SUPERSEDED_DOCUMENTS,
                        {
                            "rag_document_idxs": (superseded_rag_document_idxs),
                            "current_rag_document_idx": (rag_document_idx),
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
        """신규 문서와 실행 이력을 실패 상태로 변경한다.

        이 메서드는 이번 실행이 새 문서 색인을 준비한 경우에만 사용한다.
        이미 INDEXED인 동일 문서를 재사용한 실행에는 mark_run_failed()를
        사용하여 기존 정상 문서 상태를 보존한다.
        """

        safe_error_message = _normalize_error_message(error_message)

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_DOCUMENT_FAILED,
                    {
                        "rag_document_idx": (rag_document_idx),
                    },
                )
                await self._session.execute(
                    _MARK_RUN_FAILED,
                    {
                        "rag_index_run_idx": (rag_index_run_idx),
                        "finished_at": (_utc_now_without_timezone()),
                        "error_message": (safe_error_message),
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
        """기존 INDEXED 문서를 유지하고 이번 실행 이력만 FAILED 처리한다."""

        safe_error_message = _normalize_error_message(error_message)

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_RUN_FAILED,
                    {
                        "rag_index_run_idx": (rag_index_run_idx),
                        "finished_at": (_utc_now_without_timezone()),
                        "error_message": (safe_error_message),
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
                "parser_version": (metadata.parser_version),
                "embedding_model": embedding_model,
                "index_version": metadata.index_version,
            },
        )
        row = result.mappings().one_or_none()

        if row is None:
            return None

        rag_document_idx = _require_positive_int(
            row["rag_document_idx"],
            operation="select_exact_document",
        )
        index_status = _require_non_empty_text(
            row["index_status"],
            operation="select_exact_document",
        )

        return (
            rag_document_idx,
            index_status,
        )

    async def _assert_existing_chunk_identity(
        self,
        *,
        rag_document_idx: int,
        index_version: int,
        expected_chunk_ids: tuple[str, ...],
    ) -> None:
        """기존 정상 청크와 이번 실행의 결정적 Chunk ID가 같은지 검증한다."""

        result = await self._session.execute(
            _SELECT_DOCUMENT_CHUNK_IDS,
            {
                "rag_document_idx": rag_document_idx,
                "index_version": index_version,
            },
        )
        raw_chunk_ids = result.scalars().all()

        existing_chunk_ids: list[str] = []

        for raw_chunk_id in raw_chunk_ids:
            existing_chunk_ids.append(
                _require_non_empty_text(
                    raw_chunk_id,
                    operation=("select_document_chunk_ids"),
                )
            )

        if tuple(existing_chunk_ids) != expected_chunk_ids:
            # 동일한 문서 식별자 아래에서 Chunk ID가 달라졌다면
            # 청킹 정책 또는 ID 생성 규칙 변경 시 Index_Version을
            # 올리지 않은 상태다.
            #
            # 기존 정상 청크를 지우지 않고 명시적인 저장소 오류로 중단한다.
            raise LocalRagStorageError("indexed_chunk_identity_mismatch")

    async def _read_previous_indexed_document_ids(
        self,
        *,
        users_idx: int,
        file_idx: int,
        current_rag_document_idx: int,
    ) -> tuple[int, ...]:
        """현재 문서가 대체할 이전 정상 문서 식별자를 잠그고 반환한다."""

        result = await self._session.execute(
            _SELECT_PREVIOUS_INDEXED_DOCUMENT_IDS,
            {
                "users_idx": users_idx,
                "file_idx": file_idx,
                "rag_document_idx": (current_rag_document_idx),
            },
        )
        raw_document_ids = result.scalars().all()

        document_ids = tuple(
            _require_positive_int(
                raw_document_id,
                operation=("select_previous_indexed_documents"),
            )
            for raw_document_id in raw_document_ids
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
    ) -> int:
        """이번 색인 시도를 RUNNING 실행 이력으로 저장하고 PK를 반환한다."""

        await self._session.execute(
            _INSERT_INDEX_RUN,
            {
                "rag_document_idx": rag_document_idx,
                "file_idx": metadata.file_idx,
                "users_idx": metadata.users_idx,
                "parser_type": metadata.parser_type,
                "parser_version": (metadata.parser_version),
                "embedding_model": (embedded_document.embedding_model),
                "chunk_count": (embedded_document.chunk_count),
                "started_at": started_at,
            },
        )

        return await self._read_last_insert_id(
            operation="index_run_insert",
        )

    async def _read_last_insert_id(
        self,
        *,
        operation: str,
    ) -> int:
        """현재 DB 연결에서 마지막 INSERT 또는 UPSERT PK를 읽는다."""

        result = await self._session.execute(_SELECT_LAST_INSERT_ID)
        raw_identifier = result.scalar_one()

        return _require_positive_int(
            raw_identifier,
            operation=operation,
        )


def _build_document_parameters(
    *,
    metadata: DocumentIndexMetadata,
    embedded_document: EmbeddedDocument,
) -> dict[str, object]:
    """문서 UPSERT와 기존 INDEXED 문서 스냅샷 갱신 파라미터를 생성한다."""

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
        "embedding_model": (embedded_document.embedding_model),
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


def _require_positive_int(
    value: object,
    *,
    operation: str,
) -> int:
    """DB 결과가 bool이 아닌 양의 정수인지 검증한다."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LocalRagStorageError(operation)

    return value


def _require_non_empty_text(
    value: object,
    *,
    operation: str,
) -> str:
    """DB 결과가 비어 있지 않은 문자열인지 검증한다."""

    if not isinstance(value, str):
        raise LocalRagStorageError(operation)

    normalized_value = value.strip()

    if not normalized_value:
        raise LocalRagStorageError(operation)

    return normalized_value


def _normalize_error_message(
    error_message: str,
) -> str:
    """실행 이력에 저장할 안전한 제한 길이 오류 메시지를 생성한다."""

    return error_message.strip()[:1000] or "INDEX_STORAGE_FAILED"


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
