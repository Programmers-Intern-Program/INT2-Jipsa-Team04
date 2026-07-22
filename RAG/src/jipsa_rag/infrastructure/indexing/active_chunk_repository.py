"""최종 활성 문서와 전체 청크를 Local RAG DB에서 조회한다."""

from collections.abc import Mapping, Sequence
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.indexing.chunk_snapshot_models import (
    IndexedChunkSnapshot,
    IndexedDocumentSnapshot,
    SnapshotMetadataValue,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
)

# 색인 서비스가 성공으로 반환한 RAG_Document_IDX가 여전히 해당 파일의
# 유일한 최신 활성 문서인지 확인한 뒤 그 문서의 청크 전체를 조회한다.
#
# 조건의 목적은 다음과 같다.
# 1. RAG_Document_IDX, Users_IDX, File_IDX가 모두 일치해야 한다.
# 2. INDEXED이면서 논리 삭제되지 않은 문서만 허용한다.
# 3. 청크의 Index_Version이 상위 문서의 Index_Version과 같아야 한다.
# 4. 같은 사용자·파일에 다른 활성 INDEXED 문서가 존재하면 오래된 실행의
#    콜백일 수 있으므로 결과를 반환하지 않는다.
# 5. Chunk_Index 순서로 정렬하여 0부터 이어지는 최종 스냅샷을 구성한다.
_SELECT_LATEST_ACTIVE_CHUNKS: Final = text(
    """
    SELECT
        document.`RAG_Document_IDX` AS `rag_document_idx`,
        document.`Users_IDX` AS `users_idx`,
        document.`File_IDX` AS `file_idx`,
        document.`Index_Version` AS `index_version`,
        document.`Chunk_Count` AS `chunk_count`,
        chunk.`Chunk_ID` AS `chunk_id`,
        chunk.`Chunk_Index` AS `chunk_index`,
        chunk.`Content` AS `content`,
        chunk.`Content_Hash` AS `content_hash`,
        chunk.`Token_Count` AS `token_count`,
        chunk.`Page` AS `page`,
        chunk.`Slide_No` AS `slide_no`,
        chunk.`Sheet_Name` AS `sheet_name`,
        chunk.`Section_Title` AS `section_title`
    FROM `RAG_Document` AS document
    INNER JOIN `RAG_Chunk` AS chunk
        ON chunk.`RAG_Document_IDX`
            = document.`RAG_Document_IDX`
       AND chunk.`Index_Version`
            = document.`Index_Version`
    WHERE document.`RAG_Document_IDX`
            = :rag_document_idx
      AND document.`Users_IDX`
            = :users_idx
      AND document.`File_IDX`
            = :file_idx
      AND document.`Index_Status`
            = 'INDEXED'
      AND document.`Deleted_At`
            IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM `RAG_Document` AS other_active_document
          WHERE other_active_document.`Users_IDX`
                    = document.`Users_IDX`
            AND other_active_document.`File_IDX`
                    = document.`File_IDX`
            AND other_active_document.`RAG_Document_IDX`
                    <> document.`RAG_Document_IDX`
            AND other_active_document.`Index_Status`
                    = 'INDEXED'
            AND other_active_document.`Deleted_At`
                    IS NULL
      )
    ORDER BY chunk.`Chunk_Index`
    """
)


class LocalRagActiveChunkRepository:
    """Local RAG DB에서 성공한 최종 색인의 전체 청크를 조회한다."""

    def __init__(
        self,
        database_session: AsyncSession,
    ) -> None:
        """요청 범위의 비동기 DB 세션을 저장한다."""

        self._database_session = database_session

    async def fetch_latest_active_chunk_snapshot(
        self,
        *,
        users_idx: int,
        file_idx: int,
        rag_document_idx: int,
    ) -> IndexedDocumentSnapshot:
        """지정한 최종 문서가 최신 활성 색인이면 전체 청크를 반환한다.

        조회 대상 문서가 삭제되었거나, 아직 INDEXED가 아니거나,
        다른 활성 문서가 존재하거나, 청크가 누락된 경우에는 정상적인
        성공 콜백을 만들 수 없으므로 LocalRagStorageError를 발생시킨다.
        """

        _validate_positive_identifier(
            users_idx,
            field_name="users_idx",
        )
        _validate_positive_identifier(
            file_idx,
            field_name="file_idx",
        )
        _validate_positive_identifier(
            rag_document_idx,
            field_name="rag_document_idx",
        )

        try:
            # 읽기 전용 조회이지만 동일한 AsyncSession의 트랜잭션 경계를
            # 명확하게 닫아 다음 요청이나 세션 종료 시 암묵적 rollback에
            # 의존하지 않도록 한다.
            async with self._database_session.begin():
                result = await self._database_session.execute(
                    _SELECT_LATEST_ACTIVE_CHUNKS,
                    {
                        "users_idx": users_idx,
                        "file_idx": file_idx,
                        "rag_document_idx": rag_document_idx,
                    },
                )

                raw_rows = result.mappings().all()

        except SQLAlchemyError as error:
            # SQL, 연결 문자열, 청크 원문은 예외 메시지에 포함하지 않는다.
            raise LocalRagStorageError("fetch_latest_active_chunk_snapshot") from error

        # SQLAlchemy RowMapping은 런타임에서는 Mapping처럼 동작하지만
        # 정적 타입에서는 Mapping[str, object]와 정확히 일치하지 않는다.
        #
        # DB 드라이버 전용 타입을 서비스 모델 생성 함수 밖으로 전달하지 않고
        # 일반 dict로 복사하여 저장소 계층의 타입 경계를 명확하게 만든다.
        rows: tuple[Mapping[str, object], ...] = tuple(dict(row) for row in raw_rows)

        if not rows:
            raise LocalRagStorageError("latest_active_chunk_snapshot_not_found")

        try:
            return _build_document_snapshot(rows)
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            # DB 값이 모델 계약과 다르면 일부 청크를 전송하지 않고
            # 저장소 일관성 오류로 처리한다.
            raise LocalRagStorageError("validate_latest_active_chunk_snapshot") from error


def _build_document_snapshot(
    rows: Sequence[Mapping[str, object]],
) -> IndexedDocumentSnapshot:
    """DB 조회 행을 검증하여 하나의 최종 문서 스냅샷으로 조립한다."""

    if not rows:
        raise ValueError("rows must contain at least one active chunk.")

    first_row = rows[0]

    rag_document_idx = _require_integer(
        first_row,
        "rag_document_idx",
    )
    users_idx = _require_integer(
        first_row,
        "users_idx",
    )
    file_idx = _require_integer(
        first_row,
        "file_idx",
    )
    index_version = _require_integer(
        first_row,
        "index_version",
    )
    chunk_count = _require_integer(
        first_row,
        "chunk_count",
    )

    chunks: list[IndexedChunkSnapshot] = []

    for row in rows:
        # JOIN 결과의 모든 행이 같은 상위 문서 정보를 가리켜야 한다.
        # 하나라도 다르면 잘못된 JOIN 또는 손상된 데이터 상태이므로 거부한다.
        if (
            _require_integer(
                row,
                "rag_document_idx",
            )
            != rag_document_idx
        ):
            raise ValueError("rag_document_idx must be identical for all rows.")

        if (
            _require_integer(
                row,
                "users_idx",
            )
            != users_idx
        ):
            raise ValueError("users_idx must be identical for all rows.")

        if (
            _require_integer(
                row,
                "file_idx",
            )
            != file_idx
        ):
            raise ValueError("file_idx must be identical for all rows.")

        if (
            _require_integer(
                row,
                "index_version",
            )
            != index_version
        ):
            raise ValueError("index_version must be identical for all rows.")

        if (
            _require_integer(
                row,
                "chunk_count",
            )
            != chunk_count
        ):
            raise ValueError("chunk_count must be identical for all rows.")

        source_metadata: dict[
            str,
            SnapshotMetadataValue,
        ] = {}

        page_number = _optional_integer(
            row,
            "page",
        )
        slide_number = _optional_integer(
            row,
            "slide_no",
        )
        sheet_name = _optional_string(
            row,
            "sheet_name",
        )
        section_title = _optional_string(
            row,
            "section_title",
        )

        # DB의 형식별 위치 컬럼을 외부 계약에서 사용하는 명시적인 키로
        # 변환한다. 값이 없는 형식의 필드는 payload에 포함하지 않는다.
        if page_number is not None:
            source_metadata["page_number"] = page_number

        if slide_number is not None:
            source_metadata["slide_number"] = slide_number

        if sheet_name is not None:
            source_metadata["sheet_name"] = sheet_name

        if section_title is not None:
            source_metadata["section_title"] = section_title

        chunks.append(
            IndexedChunkSnapshot(
                chunk_id=_require_string(
                    row,
                    "chunk_id",
                ),
                chunk_index=_require_integer(
                    row,
                    "chunk_index",
                ),
                content=_require_string(
                    row,
                    "content",
                    preserve_whitespace=True,
                ),
                content_hash=_require_string(
                    row,
                    "content_hash",
                ),
                token_count=_optional_integer(
                    row,
                    "token_count",
                ),
                source_metadata=source_metadata,
            )
        )

    return IndexedDocumentSnapshot(
        rag_document_idx=rag_document_idx,
        users_idx=users_idx,
        file_idx=file_idx,
        index_version=index_version,
        chunk_count=chunk_count,
        chunks=tuple(chunks),
    )


def _validate_positive_identifier(
    value: int,
    *,
    field_name: str,
) -> None:
    """bool이 아닌 양의 정수 식별자인지 검증한다."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _require_integer(
    row: Mapping[str, object],
    key: str,
) -> int:
    """필수 DB 값을 bool이 아닌 정수로 읽는다."""

    value = row[key]

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer.")

    return value


def _optional_integer(
    row: Mapping[str, object],
    key: str,
) -> int | None:
    """NULL을 허용하는 DB 값을 bool이 아닌 정수로 읽는다."""

    value = row[key]

    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer or null.")

    return value


def _require_string(
    row: Mapping[str, object],
    key: str,
    *,
    preserve_whitespace: bool = False,
) -> str:
    """필수 문자열 DB 값을 읽고 빈 문자열을 거부한다."""

    value = row[key]

    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string.")

    if preserve_whitespace:
        if not value:
            raise ValueError(f"{key} must not be empty.")

        return value

    normalized_value = value.strip()

    if not normalized_value:
        raise ValueError(f"{key} must not be empty.")

    return normalized_value


def _optional_string(
    row: Mapping[str, object],
    key: str,
) -> str | None:
    """NULL 또는 비어 있는 선택 문자열 DB 값을 정규화한다."""

    value = row[key]

    if value is None:
        return None

    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null.")

    normalized_value = value.strip()

    return normalized_value or None
