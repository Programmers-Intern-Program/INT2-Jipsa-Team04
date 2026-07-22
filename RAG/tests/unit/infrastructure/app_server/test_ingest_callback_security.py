"""AWS 애플리케이션 서버 인제스트 완료 payload의 보안 계약을 테스트한다."""

import json
from collections.abc import Mapping, Sequence
from typing import cast

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.schemas.ingestion import ChunkSynchronizationRequest

_TEST_INTERNAL_TOKEN = "test-application-internal-token-0123456789abcdef"
_TEST_CHUNK_ID = "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612"
_TEST_CONTENT_HASH = "a" * 64

# AWS 애플리케이션 서버와 동기화하는 JSON payload에
# 존재해서는 안 되는 Local RAG 및 VectorDB 전용 필드명이다.
#
# 필드명 검사는 대소문자와 하이픈 표기 차이의 영향을 받지 않도록
# 수집 단계에서 소문자 snake_case 형태로 정규화한다.
_FORBIDDEN_VECTOR_FIELD_NAMES = frozenset(
    {
        "embedding",
        "embeddings",
        "embedding_vector",
        "embedding_vectors",
        "vector",
        "vectors",
    }
)


def _create_settings() -> Settings:
    """실제 AWS 서버에 연결하지 않는 인제스트 클라이언트 설정을 생성한다."""

    return get_settings().model_copy(
        update={
            "internal_token": SecretStr(
                _TEST_INTERNAL_TOKEN,
            ),
            "app_server_base_url": "http://application.test",
            "app_server_connect_timeout_seconds": 1.0,
            "app_server_read_timeout_seconds": 1.0,
            "app_server_max_attempts": 1,
            # 재시도 지연은 이 테스트의 검증 대상이 아니므로
            # 테스트가 불필요하게 대기하지 않도록 0초를 사용한다.
            "app_server_retry_initial_delay_seconds": 0.0,
            "app_server_retry_max_delay_seconds": 0.0,
        }
    )


def _create_chunk_request() -> ChunkSynchronizationRequest:
    """성공 동기화 payload에 사용할 단일 청크 DTO를 생성한다."""

    return ChunkSynchronizationRequest(
        chunk_id=_TEST_CHUNK_ID,
        chunk_index=0,
        content="AWS 서버와 동기화할 청크 원문",
        content_hash=_TEST_CONTENT_HASH,
        token_count=17,
        source_metadata={
            "page_number": 1,
            "section_path": (
                "1장",
                "개요",
            ),
        },
    )


def _decode_json_object(request: httpx2.Request) -> dict[str, object]:
    """MockTransport가 수신한 요청 본문을 JSON 객체로 검증하여 반환한다."""

    decoded_payload: object = json.loads(
        request.content.decode(
            "utf-8",
        )
    )

    # ingest-complete API의 최상위 요청 본문은
    # 반드시 JSON object여야 한다.
    if not isinstance(decoded_payload, dict):
        raise AssertionError("The ingest-complete payload must be a JSON object.")

    return cast(
        dict[str, object],
        decoded_payload,
    )


def _normalize_field_name(field_name: str) -> str:
    """payload 필드명을 비교 가능한 소문자 snake_case로 정규화한다."""

    return field_name.strip().lower().replace("-", "_")


def _collect_payload_field_names(value: object) -> set[str]:
    """중첩 JSON 객체와 배열에 존재하는 모든 필드명을 재귀적으로 수집한다.

    임베딩 필드가 최상위가 아니라 chunks 내부나 source_metadata 안에
    잘못 포함되는 회귀도 탐지할 수 있도록 전체 payload를 순회한다.
    """

    collected_field_names: set[str] = set()

    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if isinstance(key, str):
                collected_field_names.add(
                    _normalize_field_name(key),
                )

            collected_field_names.update(
                _collect_payload_field_names(nested_value),
            )

        return collected_field_names

    # str과 bytes도 Sequence이지만 각 문자나 바이트를 순회할 필요가 없으므로
    # JSON 배열에 대응하는 실제 컬렉션만 재귀적으로 검사한다.
    if isinstance(value, Sequence) and not isinstance(
        value,
        (
            str,
            bytes,
            bytearray,
        ),
    ):
        for item in value:
            collected_field_names.update(
                _collect_payload_field_names(item),
            )

    return collected_field_names


def test_chunk_synchronization_schema_rejects_embedding_vector_field() -> None:
    """청크 DTO는 임베딩 벡터 필드를 입력 단계에서 허용하지 않아야 한다."""

    with pytest.raises(ValidationError) as exception_info:
        ChunkSynchronizationRequest.model_validate(
            {
                "chunk_id": _TEST_CHUNK_ID,
                "chunk_index": 0,
                "content": "AWS 서버와 동기화할 청크 원문",
                "content_hash": _TEST_CONTENT_HASH,
                "token_count": 17,
                "source_metadata": {
                    "page_number": 1,
                },
                # 임베딩 벡터는 Local RAG와 Qdrant 사이에서만 사용해야 한다.
                #
                # 외부 콜백 DTO는 extra="forbid" 계약을 사용하므로
                # 정의되지 않은 embedding_vector 필드를 즉시 거부해야 한다.
                "embedding_vector": [
                    0.11,
                    0.22,
                    0.33,
                ],
            }
        )

    validation_errors = exception_info.value.errors()

    # 단순히 ValidationError 발생 여부만 확인하지 않고,
    # 실제로 embedding_vector 추가 필드 때문에 거부되었는지 확인한다.
    assert any(
        error.get("type") == "extra_forbidden"
        and tuple(error.get("loc", ())) == ("embedding_vector",)
        for error in validation_errors
    )


@pytest.mark.asyncio
async def test_successful_chunk_synchronization_payload_excludes_embedding_vectors() -> None:
    """성공 payload는 청크 정보만 보내고 임베딩 벡터는 보내지 않아야 한다."""

    received_payloads: list[dict[str, object]] = []

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        """Mock AWS 서버가 실제 HTTP 요청 계약을 검증하고 본문을 수집한다."""

        assert request.method == "POST"
        assert request.url.path == "/internal/files/123/ingest-complete"

        # 내부 인증 토큰은 HTTP 헤더에는 필요하지만
        # JSON payload 안에는 포함되어서는 안 된다.
        assert request.headers["X-Internal-Token"] == _TEST_INTERNAL_TOKEN

        received_payloads.append(
            _decode_json_object(request),
        )

        return httpx2.Response(
            status_code=204,
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(
            handler,
        ),
    )

    await client.notify_ingest_complete(
        file_idx=123,
        success=True,
        index_version=2,
        chunks=(_create_chunk_request(),),
    )

    # 실제 HTTP 클라이언트가 직렬화하여 전송한 JSON을
    # 허용된 전체 계약과 정확하게 비교한다.
    assert received_payloads == [
        {
            "success": True,
            "index_version": 2,
            "chunk_count": 1,
            "chunks": [
                {
                    "chunk_id": _TEST_CHUNK_ID,
                    "chunk_index": 0,
                    "content": "AWS 서버와 동기화할 청크 원문",
                    "content_hash": _TEST_CONTENT_HASH,
                    "token_count": 17,
                    "source_metadata": {
                        "page_number": 1,
                        "section_path": [
                            "1장",
                            "개요",
                        ],
                    },
                }
            ],
        }
    ]

    payload_field_names = _collect_payload_field_names(
        received_payloads[0],
    )

    # 최상위뿐 아니라 chunks 및 source_metadata 내부를 포함한
    # 전체 JSON payload에 임베딩 관련 필드가 없어야 한다.
    assert payload_field_names.isdisjoint(
        _FORBIDDEN_VECTOR_FIELD_NAMES,
    )

    # 내부 인증 토큰은 헤더 전용 값이며 payload 필드로 전달하면 안 된다.
    assert "internal_token" not in payload_field_names
    assert "x_internal_token" not in payload_field_names


@pytest.mark.asyncio
async def test_failed_chunk_synchronization_payload_excludes_all_chunk_fields() -> None:
    """실패 payload는 오류만 보내고 청크와 색인 정보를 포함하지 않아야 한다."""

    received_payloads: list[dict[str, object]] = []

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        """Mock AWS 서버가 실패 callback의 실제 요청 본문을 수집한다."""

        assert request.method == "POST"
        assert request.url.path == "/internal/files/123/ingest-complete"
        assert request.headers["X-Internal-Token"] == _TEST_INTERNAL_TOKEN

        received_payloads.append(
            _decode_json_object(request),
        )

        return httpx2.Response(
            status_code=204,
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(
            handler,
        ),
    )

    await client.notify_ingest_complete(
        file_idx=123,
        success=False,
        error_message=("VECTOR_STORAGE_FAILED: The vector storage operation failed."),
    )

    # 실패 callback에는 성공 여부와 외부 공개용 오류 메시지만 존재해야 한다.
    assert received_payloads == [
        {
            "success": False,
            "error_message": ("VECTOR_STORAGE_FAILED: The vector storage operation failed."),
        }
    ]

    payload_field_names = _collect_payload_field_names(
        received_payloads[0],
    )

    # 실패한 처리에서 생성된 일부 청크나 이전 활성 청크가
    # AWS 서버 DB에 동기화되지 않도록 모든 동기화 필드를 차단한다.
    assert payload_field_names.isdisjoint(
        _FORBIDDEN_VECTOR_FIELD_NAMES
        | {
            "index_version",
            "chunk_count",
            "chunks",
        }
    )

    # 내부 토큰은 성공 여부와 관계없이 헤더에만 존재해야 한다.
    assert "internal_token" not in payload_field_names
    assert "x_internal_token" not in payload_field_names
