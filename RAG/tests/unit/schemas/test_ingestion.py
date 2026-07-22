"""RAG 인제스트 완료 및 청크 동기화 요청 스키마를 테스트한다."""

import re

import pytest
from pydantic import ValidationError

from jipsa_rag.schemas.ingestion import (
    ChunkSynchronizationRequest,
    IngestCompleteRequest,
)

_TEST_CHUNK_ID = "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612"
_TEST_CONTENT_HASH = "A" * 64


def _create_chunk_request() -> ChunkSynchronizationRequest:
    """정상 청크 동기화 요청 DTO를 생성한다."""

    return ChunkSynchronizationRequest(
        chunk_id=_TEST_CHUNK_ID,
        chunk_index=0,
        # content_hash는 이 원문을 기준으로 계산되므로
        # DTO 검증 과정에서 앞뒤 공백이나 줄바꿈을 제거하면 안 된다.
        content="  원문 앞뒤 공백과 줄바꿈을 보존한다.\n",
        content_hash=_TEST_CONTENT_HASH,
        token_count=None,
        source_metadata={
            "page_number": 1,
            "section_path": (
                "1장",
                "개요",
            ),
            "continued": False,
            "optional_label": None,
        },
    )


def test_chunk_synchronization_request_preserves_content_and_metadata() -> None:
    """청크 원문과 JSON 직렬화 가능한 출처 메타데이터를 보존해야 한다."""

    request = _create_chunk_request()

    assert request.chunk_id == _TEST_CHUNK_ID
    assert request.chunk_index == 0
    assert request.content == "  원문 앞뒤 공백과 줄바꿈을 보존한다.\n"
    assert request.content_hash == _TEST_CONTENT_HASH.lower()
    assert request.token_count is None

    serialized = request.model_dump(
        mode="json",
    )

    assert serialized == {
        "chunk_id": _TEST_CHUNK_ID,
        "chunk_index": 0,
        "content": "  원문 앞뒤 공백과 줄바꿈을 보존한다.\n",
        "content_hash": _TEST_CONTENT_HASH.lower(),
        "token_count": None,
        "source_metadata": {
            "page_number": 1,
            # Local RAG 내부 모델의 tuple 메타데이터는
            # HTTP JSON payload에서 배열로 직렬화되어야 한다.
            "section_path": [
                "1장",
                "개요",
            ],
            "continued": False,
            "optional_label": None,
        },
    }


def test_ingest_complete_request_accepts_full_chunk_synchronization_payload() -> None:
    """성공 콜백은 색인 버전과 청크 데이터를 포함할 수 있어야 한다."""

    request = IngestCompleteRequest(
        success=True,
        index_version=2,
        chunk_count=1,
        chunks=(_create_chunk_request(),),
    )

    assert request.success is True
    assert request.index_version == 2
    assert request.chunk_count == 1
    assert request.chunks is not None
    assert request.chunks[0].chunk_id == _TEST_CHUNK_ID
    assert request.error_message is None


def test_ingest_complete_request_rejects_partial_chunk_synchronization_payload() -> None:
    """청크 동기화 필드는 일부만 전달할 수 없어야 한다."""

    # pytest의 match 인자는 일반 문자열 비교가 아니라 정규식으로 처리된다.
    #
    # 오류 메시지 끝의 마침표와 같은 정규식 메타 문자를 문자 그대로
    # 비교하도록 re.escape()를 적용한다.
    with pytest.raises(
        ValidationError,
        match=re.escape("Chunk synchronization requires index_version, chunk_count, and chunks."),
    ):
        IngestCompleteRequest(
            success=True,
            index_version=2,
        )


def test_failed_ingest_request_rejects_chunk_synchronization_payload() -> None:
    """실패 콜백에는 청크 원문이나 색인 버전을 포함하지 않아야 한다."""

    # 정규식 메타 문자가 오류 메시지 매칭 결과에 영향을 주지 않도록
    # 검증할 전체 메시지를 re.escape()로 이스케이프한다.
    with pytest.raises(
        ValidationError,
        match=re.escape("Failed ingestion must not include chunk synchronization data."),
    ):
        IngestCompleteRequest(
            success=False,
            index_version=2,
            chunk_count=1,
            chunks=(_create_chunk_request(),),
            error_message=("VECTOR_STORAGE_FAILED: Vector storage failed."),
        )
