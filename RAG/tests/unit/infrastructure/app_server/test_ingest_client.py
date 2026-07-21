"""애플리케이션 서버 manifest 및 완료 콜백 클라이언트를 테스트한다."""

import json

import httpx2
import pytest
from pydantic import SecretStr

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)

_TEST_INTERNAL_TOKEN = "test-application-internal-token-0123456789abcdef"


def _create_settings(
    *,
    max_attempts: int = 1,
) -> Settings:
    """실제 백엔드 서버에 의존하지 않는 테스트 설정을 생성한다."""

    return get_settings().model_copy(
        update={
            "internal_token": SecretStr(_TEST_INTERNAL_TOKEN),
            "app_server_base_url": "http://application.test",
            "app_server_connect_timeout_seconds": 1.0,
            "app_server_read_timeout_seconds": 1.0,
            "app_server_max_attempts": max_attempts,
            # 재시도 테스트가 불필요하게 지연되지 않도록 최소값을 사용한다.
            "app_server_retry_initial_delay_seconds": 0.0,
            "app_server_retry_max_delay_seconds": 0.0,
        }
    )


def _manifest_payload(
    *,
    file_idx: int = 123,
) -> dict[str, object]:
    """백엔드 manifest API의 정상 응답 본문을 생성한다."""

    return {
        "file_idx": file_idx,
        "user_idx": 45,
        "folder_idx": 9,
        "file_name": "meeting.pdf",
        "file_type": "pdf",
        "download_url": (
            "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
            "files/example.pdf?X-Amz-Signature=test"
        ),
        "url_expires_in": 900,
    }


@pytest.mark.asyncio
async def test_fetch_manifest_sends_internal_token_and_parses_response() -> None:
    """manifest 요청에 내부 토큰을 전달하고 응답을 검증해야 한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.path == "/internal/files/123/manifest"
        assert request.headers["X-Internal-Token"] == _TEST_INTERNAL_TOKEN

        return httpx2.Response(
            status_code=200,
            json=_manifest_payload(),
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    manifest = await client.fetch_manifest(
        file_idx=123,
    )

    assert manifest.file_idx == 123
    assert manifest.user_idx == 45
    assert manifest.folder_idx == 9
    assert manifest.file_name == "meeting.pdf"
    assert manifest.file_type == "pdf"
    assert manifest.url_expires_in == 900


@pytest.mark.asyncio
async def test_success_callback_sends_only_success_field() -> None:
    """성공 콜백에는 error_message를 포함하지 않아야 한다."""

    received_payloads: list[object] = []

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        assert request.method == "POST"
        assert request.url.path == "/internal/files/123/ingest-complete"
        assert request.headers["X-Internal-Token"] == _TEST_INTERNAL_TOKEN

        payload: object = json.loads(
            request.content.decode("utf-8"),
        )
        received_payloads.append(payload)

        return httpx2.Response(
            status_code=204,
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    await client.notify_ingest_complete(
        file_idx=123,
        success=True,
    )

    assert received_payloads == [
        {
            "success": True,
        }
    ]


@pytest.mark.asyncio
async def test_failure_callback_sends_error_message() -> None:
    """실패 콜백에는 외부 공개용 오류 메시지를 포함해야 한다."""

    received_payloads: list[object] = []

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        payload: object = json.loads(
            request.content.decode("utf-8"),
        )
        received_payloads.append(payload)

        return httpx2.Response(
            status_code=204,
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    await client.notify_ingest_complete(
        file_idx=123,
        success=False,
        error_message=("INVALID_DOCUMENT: The document structure is invalid."),
    )

    assert received_payloads == [
        {
            "success": False,
            "error_message": ("INVALID_DOCUMENT: The document structure is invalid."),
        }
    ]


@pytest.mark.asyncio
async def test_fetch_manifest_rejects_invalid_json() -> None:
    """manifest 응답이 JSON이 아니면 upstream 계약 오류로 처리해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            content=b"not-json",
            headers={
                "Content-Type": "text/plain",
            },
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AppException) as exception_info:
        await client.fetch_manifest(
            file_idx=123,
        )

    assert exception_info.value.error_code is ErrorCode.INVALID_APPLICATION_SERVER_RESPONSE


@pytest.mark.asyncio
async def test_fetch_manifest_rejects_file_idx_mismatch() -> None:
    """요청한 파일과 다른 manifest를 반환하면 거부해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            json=_manifest_payload(
                file_idx=999,
            ),
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AppException) as exception_info:
        await client.fetch_manifest(
            file_idx=123,
        )

    assert exception_info.value.error_code is ErrorCode.INVALID_APPLICATION_SERVER_RESPONSE


@pytest.mark.asyncio
async def test_client_rejects_missing_internal_token_before_request() -> None:
    """INTERNAL_TOKEN이 없으면 네트워크 요청을 실행하지 않아야 한다."""

    request_count = 0

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        nonlocal request_count
        request_count += 1

        return httpx2.Response(
            status_code=200,
            json=_manifest_payload(),
        )

    settings = _create_settings().model_copy(
        update={
            "internal_token": None,
        }
    )

    client = ApplicationServerIngestClient(
        settings,
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AppException) as exception_info:
        await client.fetch_manifest(
            file_idx=123,
        )

    assert exception_info.value.error_code is ErrorCode.SERVICE_UNAVAILABLE
    assert request_count == 0


@pytest.mark.asyncio
async def test_client_maps_timeout_to_application_server_timeout() -> None:
    """최종 요청 시간 초과를 504 애플리케이션 오류로 변환해야 한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        raise httpx2.ReadTimeout(
            "test timeout",
            request=request,
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AppException) as exception_info:
        await client.fetch_manifest(
            file_idx=123,
        )

    assert exception_info.value.error_code is ErrorCode.APPLICATION_SERVER_TIMEOUT


@pytest.mark.asyncio
async def test_client_retries_temporary_server_failure() -> None:
    """백엔드 5xx 응답은 설정된 횟수만큼 재시도해야 한다."""

    request_count = 0

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        nonlocal request_count
        request_count += 1

        if request_count < 3:
            return httpx2.Response(
                status_code=503,
            )

        return httpx2.Response(
            status_code=200,
            json=_manifest_payload(),
        )

    client = ApplicationServerIngestClient(
        _create_settings(
            max_attempts=3,
        ),
        transport=httpx2.MockTransport(handler),
    )

    manifest = await client.fetch_manifest(
        file_idx=123,
    )

    assert manifest.file_idx == 123
    assert request_count == 3


@pytest.mark.asyncio
async def test_client_maps_authentication_rejection_to_bad_gateway() -> None:
    """백엔드의 내부 인증 거부를 upstream 요청 거부로 변환해야 한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=401,
        )

    client = ApplicationServerIngestClient(
        _create_settings(),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(AppException) as exception_info:
        await client.fetch_manifest(
            file_idx=123,
        )

    assert exception_info.value.error_code is ErrorCode.APPLICATION_SERVER_REQUEST_REJECTED
