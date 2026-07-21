"""색인 실행 소유권을 검증하며 Local RAG 최종 상태를 갱신한다."""

from datetime import UTC, datetime
from typing import Final

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.indexing.exceptions import (
    IndexRunOwnershipLostError,
    LocalRagStorageError,
)
from jipsa_rag.infrastructure.indexing.local_repository import (
    LocalRagIndexRepository,
)

# 성공 전환은 같은 사용자·파일 범위에서 더 큰 실행 PK가 없을 때만 허용한다.
# RAG_Index_Run_IDX는 AUTO_INCREMENT이므로 큰 값이 더 나중에 시작된 실행이다.
_MARK_RUN_SUCCESS_IF_CURRENT: Final = text(
    """
    UPDATE `RAG_Index_Run` AS `current_run`
    LEFT JOIN `RAG_Index_Run` AS `newer_run`
      ON `newer_run`.`Users_IDX` = `current_run`.`Users_IDX`
     AND `newer_run`.`File_IDX` = `current_run`.`File_IDX`
     AND `newer_run`.`RAG_Index_Run_IDX` > `current_run`.`RAG_Index_Run_IDX`
    SET `current_run`.`Status` = 'SUCCESS',
        `current_run`.`Finished_At` = :finished_at,
        `current_run`.`Error_Message` = NULL
    WHERE `current_run`.`RAG_Index_Run_IDX` = :rag_index_run_idx
      AND `current_run`.`RAG_Document_IDX` = :rag_document_idx
      AND `current_run`.`Status` = 'RUNNING'
      AND `newer_run`.`RAG_Index_Run_IDX` IS NULL
    """
)

# 문서 성공 상태는 해당 문서의 SUCCESS 실행이 파일 범위의 최신 실행일 때만 확정한다.
_MARK_DOCUMENT_INDEXED_FOR_CURRENT_SUCCESS: Final = text(
    """
    UPDATE `RAG_Document` AS `document`
    INNER JOIN `RAG_Index_Run` AS `owner_run`
      ON `owner_run`.`RAG_Document_IDX` = `document`.`RAG_Document_IDX`
     AND `owner_run`.`Status` = 'SUCCESS'
    LEFT JOIN `RAG_Index_Run` AS `newer_run`
      ON `newer_run`.`Users_IDX` = `owner_run`.`Users_IDX`
     AND `newer_run`.`File_IDX` = `owner_run`.`File_IDX`
     AND `newer_run`.`RAG_Index_Run_IDX` > `owner_run`.`RAG_Index_Run_IDX`
    SET `document`.`Index_Status` = 'INDEXED',
        `document`.`Updated_At` = CURRENT_TIMESTAMP(6),
        `document`.`Deleted_At` = NULL
    WHERE `document`.`RAG_Document_IDX` = :rag_document_idx
      AND `newer_run`.`RAG_Index_Run_IDX` IS NULL
    """
)

# 이전 문서는 현재 문서가 파일 범위의 최신 SUCCESS 실행을 소유할 때만
# soft delete한다. 오래된 실행은 최신 성공 문서를 대체할 수 없다.
_SOFT_DELETE_SUPERSEDED_DOCUMENTS_IF_CURRENT: Final = text(
    """
    UPDATE `RAG_Document` AS `superseded_document`
    INNER JOIN `RAG_Document` AS `current_document`
      ON `current_document`.`RAG_Document_IDX` = :current_rag_document_idx
    INNER JOIN `RAG_Index_Run` AS `owner_run`
      ON `owner_run`.`RAG_Document_IDX` = `current_document`.`RAG_Document_IDX`
     AND `owner_run`.`Status` = 'SUCCESS'
    LEFT JOIN `RAG_Index_Run` AS `newer_run`
      ON `newer_run`.`Users_IDX` = `owner_run`.`Users_IDX`
     AND `newer_run`.`File_IDX` = `owner_run`.`File_IDX`
     AND `newer_run`.`RAG_Index_Run_IDX` > `owner_run`.`RAG_Index_Run_IDX`
    SET `superseded_document`.`Deleted_At` = CURRENT_TIMESTAMP(6),
        `superseded_document`.`Updated_At` = CURRENT_TIMESTAMP(6)
    WHERE `superseded_document`.`RAG_Document_IDX` IN :rag_document_idxs
      AND `superseded_document`.`RAG_Document_IDX` <> :current_rag_document_idx
      AND `superseded_document`.`Users_IDX` = `current_document`.`Users_IDX`
      AND `superseded_document`.`File_IDX` = `current_document`.`File_IDX`
      AND `superseded_document`.`Index_Status` = 'INDEXED'
      AND `superseded_document`.`Deleted_At` IS NULL
      AND `current_document`.`Index_Status` = 'INDEXED'
      AND `current_document`.`Deleted_At` IS NULL
      AND `newer_run`.`RAG_Index_Run_IDX` IS NULL
    """
).bindparams(
    bindparam(
        "rag_document_idxs",
        expanding=True,
    )
)

# 실행 자체는 자신의 RUNNING 상태에서만 FAILED로 전환한다.
# 이미 SUCCESS 또는 FAILED인 실행을 늦은 예외가 다시 덮어쓰지 못한다.
_MARK_RUN_FAILED_IF_RUNNING: Final = text(
    """
    UPDATE `RAG_Index_Run`
    SET `Status` = 'FAILED',
        `Finished_At` = :finished_at,
        `Error_Message` = :error_message
    WHERE `RAG_Index_Run_IDX` = :rag_index_run_idx
      AND `Status` = 'RUNNING'
    """
)

# 문서 실패 상태는 실패한 실행이 같은 문서의 최신 실행이고,
# 문서가 아직 INDEXING일 때만 반영한다. 같은 문서를 재사용한 최신 성공의
# INDEXED 상태는 보존되며, 다른 문서의 최신 성공에도 영향을 주지 않는다.
_MARK_DOCUMENT_FAILED_FOR_CURRENT_RUN: Final = text(
    """
    UPDATE `RAG_Document` AS `document`
    INNER JOIN `RAG_Index_Run` AS `failed_run`
      ON `failed_run`.`RAG_Document_IDX` = `document`.`RAG_Document_IDX`
     AND `failed_run`.`RAG_Index_Run_IDX` = :rag_index_run_idx
     AND `failed_run`.`Status` = 'FAILED'
    LEFT JOIN `RAG_Index_Run` AS `newer_document_run`
      ON `newer_document_run`.`RAG_Document_IDX` =
         `failed_run`.`RAG_Document_IDX`
     AND `newer_document_run`.`RAG_Index_Run_IDX` >
         `failed_run`.`RAG_Index_Run_IDX`
    SET `document`.`Index_Status` = 'FAILED',
        `document`.`Updated_At` = CURRENT_TIMESTAMP(6)
    WHERE `document`.`RAG_Document_IDX` = :rag_document_idx
      AND `document`.`Index_Status` = 'INDEXING'
      AND `newer_document_run`.`RAG_Index_Run_IDX` IS NULL
    """
)


class ConcurrentSafeLocalRagIndexRepository(LocalRagIndexRepository):
    """기본 저장 동작에 최신 실행 소유권 기반 최종 상태 갱신을 적용한다.

    prepare_indexing()은 부모 구현을 그대로 사용한다. 최종 SUCCESS/FAILED
    전환만 조건부 SQL로 재정의하여 오래된 실행이 최신 실행의 문서 상태를
    덮어쓰지 못하게 한다.
    """

    def __init__(self, session: AsyncSession) -> None:
        """요청 범위 AsyncSession을 부모 저장소와 공유한다."""

        super().__init__(session)

    async def mark_indexed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        superseded_rag_document_idxs: tuple[int, ...],
    ) -> None:
        """최신 RUNNING 실행만 SUCCESS와 INDEXED 상태를 확정한다."""

        try:
            async with self._session.begin():
                run_result = await self._session.execute(
                    _MARK_RUN_SUCCESS_IF_CURRENT,
                    {
                        "rag_document_idx": rag_document_idx,
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
                    },
                )
                _require_single_updated_row(
                    run_result,
                    operation="mark_indexed_run_ownership",
                )

                document_result = await self._session.execute(
                    _MARK_DOCUMENT_INDEXED_FOR_CURRENT_SUCCESS,
                    {
                        "rag_document_idx": rag_document_idx,
                    },
                )
                _require_single_updated_row(
                    document_result,
                    operation="mark_indexed_document_ownership",
                )

                if superseded_rag_document_idxs:
                    await self._session.execute(
                        _SOFT_DELETE_SUPERSEDED_DOCUMENTS_IF_CURRENT,
                        {
                            "rag_document_idxs": superseded_rag_document_idxs,
                            "current_rag_document_idx": rag_document_idx,
                        },
                    )

        except IndexRunOwnershipLostError:
            raise
        except SQLAlchemyError as error:
            raise LocalRagStorageError("mark_indexed") from error

    async def mark_failed(
        self,
        *,
        rag_document_idx: int,
        rag_index_run_idx: int,
        error_message: str,
    ) -> None:
        """실행은 실패 처리하되 최신 INDEXING 문서만 FAILED로 변경한다."""

        safe_error_message = _normalize_error_message(error_message)

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_RUN_FAILED_IF_RUNNING,
                    {
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
                        "error_message": safe_error_message,
                    },
                )
                await self._session.execute(
                    _MARK_DOCUMENT_FAILED_FOR_CURRENT_RUN,
                    {
                        "rag_document_idx": rag_document_idx,
                        "rag_index_run_idx": rag_index_run_idx,
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
        """기존 정상 문서를 보존하고 아직 RUNNING인 실행만 실패 처리한다."""

        safe_error_message = _normalize_error_message(error_message)

        try:
            async with self._session.begin():
                await self._session.execute(
                    _MARK_RUN_FAILED_IF_RUNNING,
                    {
                        "rag_index_run_idx": rag_index_run_idx,
                        "finished_at": _utc_now_without_timezone(),
                        "error_message": safe_error_message,
                    },
                )
        except SQLAlchemyError as error:
            raise LocalRagStorageError("mark_run_failed") from error


def _require_single_updated_row(
    result: object,
    *,
    operation: str,
) -> None:
    """조건부 UPDATE가 정확히 한 행을 소유권 있게 변경했는지 검증한다.

    일부 DB 드라이버는 rowcount를 -1 또는 None으로 보고할 수 있다.
    그 경우 SQL의 조건부 갱신 자체는 유지하되 애플리케이션 추가 검증만 생략한다.
    asyncmy/MariaDB의 일반 UPDATE 결과에서는 정수 rowcount가 제공된다.
    """

    raw_rowcount = getattr(result, "rowcount", None)

    if raw_rowcount is None or raw_rowcount == -1:
        return

    if isinstance(raw_rowcount, bool) or not isinstance(raw_rowcount, int):
        raise LocalRagStorageError(f"{operation}_rowcount")

    if raw_rowcount != 1:
        raise IndexRunOwnershipLostError(operation)


def _normalize_error_message(error_message: str) -> str:
    """실행 이력에 저장할 안전한 제한 길이 오류 메시지를 생성한다."""

    return error_message.strip()[:1000] or "INDEX_STORAGE_FAILED"


def _utc_now_without_timezone() -> datetime:
    """MySQL/MariaDB DATETIME(6)에 저장할 UTC 기준 naive datetime을 반환한다."""

    return datetime.now(UTC).replace(tzinfo=None)
