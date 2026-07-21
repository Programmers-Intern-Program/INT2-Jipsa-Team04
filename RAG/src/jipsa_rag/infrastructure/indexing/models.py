"""Local RAG DB와 VectorDB 저장에 사용하는 공통 색인 모델을 정의한다."""

import re
from dataclasses import dataclass
from typing import Final

from jipsa_rag.infrastructure.document.models import DocumentType

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DocumentIndexMetadata:
    """문서 하나를 Local RAG DB와 VectorDB에 저장할 때 필요한 메타데이터.

    S3 객체 위치는 AWS 서버 DB의 File.S3_Key가 기준 데이터이므로
    Local RAG 저장 모델에는 포함하지 않는다.

    Local RAG 서비스는 File_IDX를 외부 참조값으로 보관하고,
    원본 파일을 다시 내려받아야 할 때 애플리케이션 서버를 통해
    새로운 Presigned GET URL을 전달받는다.
    """

    users_idx: int
    file_idx: int
    folder_idx: int | None
    file_name: str
    file_type: DocumentType
    file_hash: str
    index_version: int
    parser_type: str
    parser_version: str

    def __post_init__(self) -> None:
        """식별자, 파일 해시 및 파서 메타데이터 형식을 검증한다."""

        if self.users_idx <= 0:
            raise ValueError("users_idx must be greater than zero.")

        if self.file_idx <= 0:
            raise ValueError("file_idx must be greater than zero.")

        if self.folder_idx is not None and self.folder_idx <= 0:
            raise ValueError("folder_idx must be greater than zero when provided.")

        if self.index_version <= 0:
            raise ValueError("index_version must be greater than zero.")

        normalized_file_name = self.file_name.strip()
        normalized_file_hash = self.file_hash.strip().lower()
        normalized_parser_type = self.parser_type.strip()
        normalized_parser_version = self.parser_version.strip()

        if not normalized_file_name:
            raise ValueError("file_name must not be empty.")

        if _SHA256_PATTERN.fullmatch(normalized_file_hash) is None:
            raise ValueError("file_hash must be a 64-character SHA-256 hexadecimal string.")

        if not normalized_parser_type:
            raise ValueError("parser_type must not be empty.")

        if not normalized_parser_version:
            raise ValueError("parser_version must not be empty.")

        object.__setattr__(
            self,
            "file_name",
            normalized_file_name,
        )
        object.__setattr__(
            self,
            "file_hash",
            normalized_file_hash,
        )
        object.__setattr__(
            self,
            "parser_type",
            normalized_parser_type,
        )
        object.__setattr__(
            self,
            "parser_version",
            normalized_parser_version,
        )


@dataclass(frozen=True, slots=True)
class PreparedLocalIndex:
    """Qdrant 적재 전에 Local RAG DB에 준비된 문서 색인 정보.

    previous_rag_document_idxs는 같은 사용자·파일에서 현재까지 정상적으로
    검색되던 이전 INDEXED 문서 식별자다. 신규 색인이 완전히 성공한 뒤에만
    해당 Qdrant Point를 비활성화하고 Local RAG 문서를 soft delete한다.

    reuses_existing_index가 True이면 동일한 파일 해시·파서 버전·임베딩 모델·
    색인 버전의 정상 문서를 재사용하는 멱등 실행이다. 이 경우 실패하더라도
    기존 정상 문서와 Qdrant Point를 삭제하거나 FAILED로 바꾸면 안 된다.
    """

    rag_document_idx: int
    rag_index_run_idx: int
    chunk_ids: tuple[str, ...]
    previous_rag_document_idxs: tuple[int, ...] = ()
    reuses_existing_index: bool = False

    def __post_init__(self) -> None:
        """Local RAG PK, 청크 ID 및 이전 정상 문서 식별자를 검증한다."""

        if self.rag_document_idx <= 0:
            raise ValueError("rag_document_idx must be greater than zero.")

        if self.rag_index_run_idx <= 0:
            raise ValueError("rag_index_run_idx must be greater than zero.")

        normalized_chunk_ids = tuple(self.chunk_ids)
        normalized_previous_document_ids = tuple(self.previous_rag_document_idxs)

        if not normalized_chunk_ids:
            raise ValueError("chunk_ids must contain at least one value.")

        if any(not chunk_id for chunk_id in normalized_chunk_ids):
            raise ValueError("chunk_ids must not contain empty values.")

        if len(set(normalized_chunk_ids)) != len(normalized_chunk_ids):
            raise ValueError("chunk_ids must be unique.")

        if any(
            isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0
            for document_id in normalized_previous_document_ids
        ):
            raise ValueError("previous_rag_document_idxs must contain positive integers.")

        if len(set(normalized_previous_document_ids)) != len(normalized_previous_document_ids):
            raise ValueError("previous_rag_document_idxs must be unique.")

        if self.rag_document_idx in normalized_previous_document_ids:
            raise ValueError("The current document must not be included in previous documents.")

        object.__setattr__(
            self,
            "chunk_ids",
            normalized_chunk_ids,
        )
        object.__setattr__(
            self,
            "previous_rag_document_idxs",
            normalized_previous_document_ids,
        )
