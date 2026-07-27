"""다중 형식 문서 파싱과 구조화 청킹의 환경 설정을 관리한다.

기존 ``core.config.Settings``에 문서 처리 옵션을 계속 추가하면 애플리케이션,
DB, 외부 서비스 설정과 파서 안전 정책의 책임이 섞인다. 이 모듈은 동일한
``.env.local``/``.env.development``/``.env.test`` 선택 규칙을 재사용하면서 문서
처리 전용 값만 별도 모델로 검증한다.
"""

from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jipsa_rag.core.config import resolve_env_file, resolve_environment


class DocumentProcessingSettings(BaseSettings):
    """구조화 청킹 버전과 OOXML 압축 안전 한계를 정의한다."""

    # 기존 CharacterTextChunker 기본값과 같게 유지하여 PDF 청크 경계와 결정적
    # Chunk ID가 환경 변수 미설정 상태에서 달라지지 않게 한다.
    chunk_size_chars: int = Field(default=1_000, ge=1, le=100_000)
    chunk_overlap_chars: int = Field(default=200, ge=0, le=99_999)

    # 현재 운영 중인 결정적 Chunk ID 계약 버전이다. 구조 메타데이터만 확장하고
    # content/hash/ID 입력을 바꾸지 않으므로 기본값 2를 유지한다.
    index_version: int = Field(default=2, ge=1, le=2_147_483_647)

    # OOXML ZIP 중앙 디렉터리 한계다. 다운로드 압축 파일 크기와 별개로 압축 해제
    # 후 자원 사용량을 제한한다.
    ooxml_max_member_count: int = Field(default=20_000, ge=10, le=100_000)
    ooxml_max_total_uncompressed_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1024 * 1024,
        le=4 * 1024 * 1024 * 1024,
    )
    ooxml_max_member_uncompressed_bytes: int = Field(
        default=128 * 1024 * 1024,
        ge=1024,
        le=2 * 1024 * 1024 * 1024,
    )
    ooxml_max_compression_ratio: float = Field(default=200.0, gt=1.0, le=10_000.0)

    model_config = SettingsConfigDict(
        env_prefix="JIPSA_RAG_",
        case_sensitive=False,
        extra="ignore",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def validate_cross_field_limits(self) -> Self:
        """청크 overlap과 OOXML 개별/전체 크기 관계를 검증한다."""

        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("청크 overlap은 청크 크기보다 작아야 합니다.")

        if self.ooxml_max_member_uncompressed_bytes > self.ooxml_max_total_uncompressed_bytes:
            raise ValueError("OOXML 단일 엔트리 한계는 전체 해제 크기 한계보다 클 수 없습니다.")

        return self


@lru_cache(maxsize=1)
def get_document_processing_settings() -> DocumentProcessingSettings:
    """현재 실행 환경의 문서 처리 설정 객체를 생성하고 재사용한다."""

    environment = resolve_environment()
    return DocumentProcessingSettings(_env_file=resolve_env_file(environment))
