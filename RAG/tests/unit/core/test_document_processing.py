"""다중 형식 문서 처리 설정의 기본값과 교차 필드 검증을 테스트한다."""

import pytest
from pydantic import ValidationError

from jipsa_rag.core.document_processing import DocumentProcessingSettings


def test_document_processing_defaults_preserve_existing_chunk_contract() -> None:
    """환경 변수가 없을 때 기존 1,000/200 문자 및 색인 버전 2를 유지한다."""

    settings = DocumentProcessingSettings(_env_file=None)

    assert settings.chunk_size_chars == 1_000
    assert settings.chunk_overlap_chars == 200
    assert settings.index_version == 2


def test_document_processing_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """문서 처리 값은 다른 RAG 설정과 동일한 JIPSA_RAG_ 접두사를 사용한다."""

    monkeypatch.setenv("JIPSA_RAG_CHUNK_SIZE_CHARS", "1200")
    monkeypatch.setenv("JIPSA_RAG_CHUNK_OVERLAP_CHARS", "120")
    monkeypatch.setenv("JIPSA_RAG_INDEX_VERSION", "7")

    settings = DocumentProcessingSettings(_env_file=None)

    assert settings.chunk_size_chars == 1_200
    assert settings.chunk_overlap_chars == 120
    assert settings.index_version == 7


def test_document_processing_rejects_overlap_not_smaller_than_chunk_size() -> None:
    """중첩 크기가 청크 크기 이상이면 시작 단계에서 설정 오류로 거부한다."""

    with pytest.raises(ValidationError):
        DocumentProcessingSettings(
            chunk_size_chars=100,
            chunk_overlap_chars=100,
            _env_file=None,
        )


def test_document_processing_rejects_member_limit_larger_than_total_limit() -> None:
    """OOXML 단일 엔트리 한계가 전체 해제 한계보다 클 수 없다."""

    with pytest.raises(ValidationError):
        DocumentProcessingSettings(
            ooxml_max_member_uncompressed_bytes=2 * 1024 * 1024,
            ooxml_max_total_uncompressed_bytes=1024 * 1024,
            _env_file=None,
        )
