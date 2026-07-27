"""파일 범위의 최신 성공 색인과 전체 청크를 Local RAG DB에서 조회한다.

RAG_Chunk.Source_Metadata JSON에는 DOCX 문단, PPTX 도형, XLSX 셀 범위,
TXT 줄 위치처럼 정규화 전용 컬럼만으로 표현할 수 없는 전체 출처 정보가 저장된다.
성공 콜백은 이 JSON을 우선 복원하고, 이전 데이터와의 호환성을 위해 Page,
Slide_No, Sheet_Name, Section_Title 컬럼을 누락 필드의 fallback으로 사용한다.
"""

import json
import math
from collections.abc import Mapping, Sequence
from typing import Final, cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.infrastructure.indexing.chunk_snapshot_models import (
    IndexedChunkSnapshot,
    IndexedDocumentSnapshot,
    SnapshotMetadataScalar,
    SnapshotMetadataValue,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
)

# 같은 사용자·파일 범위에서 성공한 색인 실행 중 현재 활성 문서를 소유한
# 가장 최신 실행을 먼저 선택한 뒤, 해당 문서의 청크 전체를 조회한다.
#
# RAG_Index_Run_IDX는 AUTO_INCREMENT PK이므로 값이 클수록 더 나중에 시작된
# 색인 실행이다. 성공한 재색인이 존재하면 이전 요청이 늦게 콜백 단계에
# 도착하더라도 이 쿼리는 항상 가장 최신 SUCCESS 실행의 활성 문서를 선택한다.
#
# 조건의 목적은 다음과 같다.
#
# 1. Users_IDX와 File_IDX가 요청 범위와 일치해야 한다.
# 2. SUCCESS 실행이 소유한 INDEXED·미삭제 문서만 선택한다.
# 3. 청크의 Index_Version이 상위 문서의 Index_Version과 같아야 한다.
# 4. 최신 실행은 MAX(RAG_Index_Run_IDX)로 결정한다.
# 5. Chunk_Index 순서로 정렬하여 0부터 이어지는 전체 스냅샷을 구성한다.
# 6. Source_Metadata JSON을 조회하여 형식별 전체 원본 위치를 복원한다.
#
# 동일한 인제스트 요청이 반복되어 기존 RAG_Document를 멱등 재사용한 경우에도
# 새로운 SUCCESS 실행 이력은 생성된다. 이때 최신 실행이 같은 문서를 가리키므로
# 저장된 결정적 Chunk_ID 전체가 그대로 다시 반환된다.
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
        chunk.`Section_Title` AS `section_title`,
        chunk.`Source_Metadata` AS `source_metadata`
    FROM `RAG_Document` AS document
    INNER JOIN `RAG_Index_Run` AS latest_successful_run
        ON latest_successful_run.`RAG_Document_IDX`
            = document.`RAG_Document_IDX`
       AND latest_successful_run.`RAG_Index_Run_IDX` = (
           SELECT MAX(candidate_run.`RAG_Index_Run_IDX`)
           FROM `RAG_Index_Run` AS candidate_run
           INNER JOIN `RAG_Document` AS candidate_document
               ON candidate_document.`RAG_Document_IDX`
                   = candidate_run.`RAG_Document_IDX`
           WHERE candidate_run.`Users_IDX`
                     = :users_idx
             AND candidate_run.`File_IDX`
                     = :file_idx
             AND candidate_run.`Status`
                     = 'SUCCESS'
             AND candidate_document.`Users_IDX`
                     = :users_idx
             AND candidate_document.`File_IDX`
                     = :file_idx
             AND candidate_document.`Index_Status`
                     = 'INDEXED'
             AND candidate_document.`Deleted_At`
                     IS NULL
       )
    INNER JOIN `RAG_Chunk` AS chunk
        ON chunk.`RAG_Document_IDX`
            = document.`RAG_Document_IDX`
       AND chunk.`Index_Version`
            = document.`Index_Version`
    WHERE document.`Users_IDX`
              = :users_idx
      AND document.`File_IDX`
              = :file_idx
      AND document.`Index_Status`
              = 'INDEXED'
      AND document.`Deleted_At`
              IS NULL
    ORDER BY chunk.`Chunk_Index`
    """
)


class LocalRagActiveChunkRepository:
    """Local RAG DB에서 가장 최신인 성공 색인의 전체 청크를 조회한다."""

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
    ) -> IndexedDocumentSnapshot:
        """파일 범위에서 가장 최신인 성공 색인의 전체 청크를 반환한다.

        호출자가 특정 RAG_Document_IDX를 지정하지 않도록 설계했다.

        이전 인제스트 요청이 재색인 요청보다 늦게 콜백 단계에 도착해도
        저장소가 현재 최신 SUCCESS 실행의 활성 문서를 직접 선택하므로,
        이전 문서의 청크가 성공 payload에 포함되지 않는다.

        활성 성공 문서가 없거나 청크가 누락된 경우에는 완전한 성공 콜백을
        만들 수 없으므로 LocalRagStorageError를 발생시킨다.
        """

        _validate_positive_identifier(
            users_idx,
            field_name="users_idx",
        )
        _validate_positive_identifier(
            file_idx,
            field_name="file_idx",
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
            # DB 값이나 Source_Metadata JSON이 모델 계약과 다르면 일부 청크를
            # 전송하지 않고 저장소 일관성 오류로 처리한다.
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

        # Source_Metadata JSON은 파서와 구조 보존 청커가 만든 전체 위치 계약이다.
        # 정규화 컬럼만으로 다시 조립하면 DOCX paragraph_index, PPTX shape_path,
        # XLSX cell_range, TXT line_number 같은 필드가 사라지므로 JSON을 우선한다.
        source_metadata = _read_source_metadata(
            row,
            "source_metadata",
        )

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

        # Source_Metadata 컬럼이 추가되기 전에 저장된 기존 청크나 일부 키가 없는
        # 데이터도 콜백할 수 있도록 정규화 컬럼을 fallback으로 병합한다.
        # JSON에 이미 같은 키가 있으면 원본 메타데이터를 우선하여 덮어쓰지 않는다.
        if page_number is not None:
            source_metadata.setdefault(
                "page_number",
                page_number,
            )

        if slide_number is not None:
            source_metadata.setdefault(
                "slide_number",
                slide_number,
            )

        if sheet_name is not None:
            source_metadata.setdefault(
                "sheet_name",
                sheet_name,
            )

        if section_title is not None:
            source_metadata.setdefault(
                "section_title",
                section_title,
            )

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


def _read_source_metadata(
    row: Mapping[str, object],
    key: str,
) -> dict[str, SnapshotMetadataValue]:
    """MySQL JSON 값을 콜백 스냅샷의 불변 메타데이터 계약으로 복원한다.

    asyncmy와 SQLAlchemy 설정에 따라 JSON 컬럼은 JSON 문자열 또는 이미
    역직렬화된 Mapping으로 반환될 수 있으므로 두 형태를 모두 허용한다.
    NULL은 Source_Metadata 컬럼 도입 이전 데이터와의 호환성을 위해 빈 객체로
    처리하며, 이후 정규화 위치 컬럼이 fallback 값을 채운다.

    콜백 스키마는 JSON 스칼라와 스칼라 배열만 허용한다. 중첩 객체나 배열 안의
    객체를 조용히 문자열로 바꾸지 않고 즉시 거부하여 AWS DB에 손상된 출처
    계약이 동기화되는 것을 방지한다.
    """

    raw_value = row[key]

    if raw_value is None:
        return {}

    parsed_value: object

    if isinstance(raw_value, str):
        if not raw_value.strip():
            raise ValueError(f"{key} must not be an empty JSON string.")

        parsed_value = json.loads(
            raw_value,
            parse_constant=_reject_non_finite_json_constant,
        )
    elif isinstance(raw_value, Mapping):
        # DB 드라이버가 반환한 변경 가능한 dict를 그대로 스냅샷에 보관하지 않는다.
        parsed_value = dict(raw_value)
    else:
        raise TypeError(f"{key} must be a JSON object string, mapping, or null.")

    if not isinstance(parsed_value, Mapping):
        raise TypeError(f"{key} must contain a JSON object.")

    normalized: dict[str, SnapshotMetadataValue] = {}

    for raw_key, metadata_value in cast(
        Mapping[object, object],
        parsed_value,
    ).items():
        if not isinstance(raw_key, str):
            raise TypeError(f"{key} keys must be strings.")

        normalized_key = raw_key.strip()

        if not normalized_key:
            raise ValueError(f"{key} keys must not be empty.")

        if normalized_key in normalized:
            raise ValueError(f"{key} contains duplicate normalized keys.")

        normalized[normalized_key] = _normalize_snapshot_metadata_value(
            metadata_value,
        )

    return normalized


def _normalize_snapshot_metadata_value(
    value: object,
) -> SnapshotMetadataValue:
    """JSON 값을 성공 콜백이 허용하는 스칼라 또는 스칼라 tuple로 변환한다."""

    if value is None or isinstance(
        value,
        str | bool | int,
    ):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("source metadata floats must be finite.")

        return value

    if isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    ):
        normalized_items: list[SnapshotMetadataScalar] = []

        for item in value:
            if item is None or isinstance(
                item,
                str | bool | int,
            ):
                normalized_items.append(item)
                continue

            if isinstance(item, float):
                if not math.isfinite(item):
                    raise ValueError("source metadata array floats must be finite.")

                normalized_items.append(item)
                continue

            raise TypeError("source metadata arrays must contain only JSON scalar values.")

        return tuple(normalized_items)

    raise TypeError("source metadata values must be JSON scalars or scalar arrays.")


def _reject_non_finite_json_constant(
    value: str,
) -> object:
    """JSON의 NaN과 Infinity 확장을 거부한다."""

    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _validate_positive_identifier(
    value: int,
    *,
    field_name: str,
) -> None:
    """bool이 아닌 양의 정수 식별자인지 검증한다."""

    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise ValueError(f"{field_name} must be a positive integer.")


def _require_integer(
    row: Mapping[str, object],
    key: str,
) -> int:
    """필수 DB 값을 bool이 아닌 정수로 읽는다."""

    value = row[key]

    if isinstance(value, bool) or not isinstance(
        value,
        int,
    ):
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

    if isinstance(value, bool) or not isinstance(
        value,
        int,
    ):
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

    if not isinstance(
        value,
        str,
    ):
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

    if not isinstance(
        value,
        str,
    ):
        raise TypeError(f"{key} must be a string or null.")

    normalized_value = value.strip()

    return normalized_value or None
