"""동일 파일 색인이 동시에 실행되지 않도록 MySQL advisory lock을 관리한다."""

import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from jipsa_rag.infrastructure.indexing.exceptions import LocalRagStorageError

logger = logging.getLogger(__name__)

_LOCK_NAME_PREFIX: Final[str] = "jipsa:rag:file:"
_DEFAULT_WAIT_SLICE_SECONDS: Final[int] = 5

_ACQUIRE_FILE_LOCK: Final = text("SELECT GET_LOCK(:lock_name, :timeout_seconds)")
_RELEASE_FILE_LOCK: Final = text("SELECT RELEASE_LOCK(:lock_name)")


class NoOpFileIndexLock:
    """저장소 없는 단위 테스트에서 사용할 무동작 파일 lock 구현체."""

    def hold(
        self,
        *,
        file_idx: int,
    ) -> AbstractAsyncContextManager[None]:
        """입력값만 검증하고 즉시 임계 구역을 연다."""

        return self._hold(file_idx=file_idx)

    @asynccontextmanager
    async def _hold(
        self,
        *,
        file_idx: int,
    ) -> AsyncIterator[None]:
        """동기화 없이 호출자 코드를 그대로 실행한다."""

        _validate_file_idx(file_idx)
        yield


class MySqlAdvisoryFileIndexLock:
    """File_IDX별 MySQL/MariaDB named lock을 연결 단위로 보유한다.

    GET_LOCK은 DB 연결에 귀속되므로 lock을 보유하는 동안 전용 연결을
    유지한다. 동일 file_idx의 다른 요청은 짧은 대기 구간을 반복하면서
    앞선 실행이 완료될 때까지 기다린다.

    트랜잭션 row lock을 장시간 유지하지 않으므로 파일 다운로드나 Qdrant
    통신이 길어져도 RAG_Document 및 RAG_Index_Run 행을 잠그지 않는다.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        wait_slice_seconds: int = _DEFAULT_WAIT_SLICE_SECONDS,
    ) -> None:
        """공유 AsyncEngine과 GET_LOCK 단위 대기 시간을 저장한다."""

        if isinstance(wait_slice_seconds, bool) or not isinstance(
            wait_slice_seconds,
            int,
        ):
            raise TypeError("wait_slice_seconds must be an integer.")

        if wait_slice_seconds <= 0:
            raise ValueError("wait_slice_seconds must be greater than zero.")

        self._engine = engine
        self._wait_slice_seconds = wait_slice_seconds

    def hold(
        self,
        *,
        file_idx: int,
    ) -> AbstractAsyncContextManager[None]:
        """지정한 file_idx lock을 획득하고 해제하는 context를 반환한다."""

        return self._hold(file_idx=file_idx)

    @asynccontextmanager
    async def _hold(
        self,
        *,
        file_idx: int,
    ) -> AsyncIterator[None]:
        """동일 파일의 색인 임계 구역을 단일 DB 연결로 보호한다."""

        _validate_file_idx(file_idx)
        lock_name = _build_lock_name(file_idx)
        connection = await self._acquire_connection(lock_name=lock_name)
        body_error: BaseException | None = None

        try:
            yield
        except BaseException as error:
            # CancelledError도 포함하여 요청 취소 시에도 lock 해제를 시도한다.
            body_error = error
            raise
        finally:
            try:
                await self._release_connection(
                    connection=connection,
                    lock_name=lock_name,
                )
            except LocalRagStorageError:
                # 원래 색인 오류가 존재하면 lock 해제 오류가 이를 덮어쓰지 않게 한다.
                if body_error is not None:
                    logger.exception(
                        "Failed to release file indexing lock after processing error.",
                        extra={
                            "event": "file_index_lock_release_failed",
                            "file_idx": file_idx,
                        },
                    )
                else:
                    raise

    async def _acquire_connection(
        self,
        *,
        lock_name: str,
    ) -> AsyncConnection:
        """named lock을 획득한 전용 연결을 반환한다."""

        while True:
            try:
                connection = await self._engine.connect()
            except SQLAlchemyError as error:
                raise LocalRagStorageError("file_index_lock_connect") from error

            try:
                result = await connection.execute(
                    _ACQUIRE_FILE_LOCK,
                    {
                        "lock_name": lock_name,
                        "timeout_seconds": self._wait_slice_seconds,
                    },
                )
                lock_result = result.scalar_one()
            except SQLAlchemyError as error:
                await _invalidate_and_close(connection)
                raise LocalRagStorageError("file_index_lock_acquire") from error
            except BaseException:
                # 취소 또는 예상하지 못한 오류 중 연결이 풀로 반환되면
                # 획득 직후의 named lock이 남을 수 있으므로 연결을 폐기한다.
                await _invalidate_and_close(connection)
                raise

            if lock_result == 1:
                return connection

            if lock_result == 0:
                # 이번 대기 구간 안에 lock을 얻지 못했다.
                # 연결을 풀에 반환한 뒤 새 연결로 다시 대기하여 풀 고갈을 줄인다.
                await connection.close()
                continue

            await _invalidate_and_close(connection)
            raise LocalRagStorageError("file_index_lock_acquire_result")

    async def _release_connection(
        self,
        *,
        connection: AsyncConnection,
        lock_name: str,
    ) -> None:
        """현재 연결이 보유한 named lock을 해제하고 연결을 반환한다."""

        try:
            result = await connection.execute(
                _RELEASE_FILE_LOCK,
                {
                    "lock_name": lock_name,
                },
            )
            release_result = result.scalar_one()
        except SQLAlchemyError as error:
            await _invalidate_and_close(connection)
            raise LocalRagStorageError("file_index_lock_release") from error
        except BaseException:
            await _invalidate_and_close(connection)
            raise

        if release_result != 1:
            # 0은 현재 연결이 lock을 소유하지 않음을, NULL은 lock 이름이
            # 존재하지 않음을 의미한다. 어느 경우든 연결 상태를 신뢰하지 않는다.
            await _invalidate_and_close(connection)
            raise LocalRagStorageError("file_index_lock_release_result")

        await connection.close()


def _validate_file_idx(file_idx: int) -> None:
    """lock 식별자에 사용할 File_IDX가 bool이 아닌 양의 정수인지 검증한다."""

    if isinstance(file_idx, bool) or not isinstance(file_idx, int) or file_idx <= 0:
        raise ValueError("file_idx must be a positive integer.")


def _build_lock_name(file_idx: int) -> str:
    """MySQL의 64자 제한 안에서 결정적인 파일 lock 이름을 생성한다."""

    lock_name = f"{_LOCK_NAME_PREFIX}{file_idx}"

    if len(lock_name) > 64:
        # 양의 BIGINT File_IDX와 현재 prefix로는 도달하지 않지만,
        # 향후 prefix 변경 시 MySQL 제한을 조용히 초과하지 않도록 방어한다.
        raise ValueError("file index lock name must not exceed 64 characters.")

    return lock_name


async def _invalidate_and_close(connection: AsyncConnection) -> None:
    """named lock 누수를 막기 위해 연결을 풀에서 폐기한 뒤 닫는다."""

    try:
        await connection.invalidate()
    finally:
        await connection.close()
