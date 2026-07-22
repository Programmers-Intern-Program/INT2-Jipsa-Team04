"""애플리케이션 서버의 manifest 및 인제스트 완료 API를 호출한다."""

import asyncio
import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Final, Literal

import httpx2
from pydantic import ValidationError

from jipsa_rag.core.config import Settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.schemas.file_processing import (
    FileProcessingRequest,
)
from jipsa_rag.schemas.ingestion import (
    ChunkSynchronizationRequest,
    IngestCompleteRequest,
)

logger = logging.getLogger(__name__)


# 애플리케이션 서버의 내부 파일 API는 /api/v1 prefix를 사용하지 않는다.
#
# 백엔드 InternalFileController의 실제 경로와 동일하게 유지한다.
_INTERNAL_FILES_PATH: Final[str] = "/internal/files"

# RAG 서버가 백엔드의 /internal/** API를 호출할 때 사용하는
# 서비스 간 인증 헤더다.
_INTERNAL_TOKEN_HEADER_NAME: Final[str] = "X-Internal-Token"

# HTTP 메서드를 클라이언트가 실제 사용하는 값으로 제한한다.
HttpMethod = Literal[
    "GET",
    "POST",
]

# HTTP 408과 429는 일시적인 상태일 가능성이 있으므로 재시도한다.
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset(
    {
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.TOO_MANY_REQUESTS,
    }
)


class ApplicationServerIngestClient:
    """애플리케이션 서버의 RAG 내부 API를 호출하는 비동기 클라이언트."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """애플리케이션 서버 설정과 테스트용 transport를 주입받는다."""

        self._settings = settings
        self._transport = transport

    async def fetch_manifest(
        self,
        *,
        file_idx: int,
    ) -> FileProcessingRequest:
        """현재 파일 정보와 새로운 Presigned GET URL을 조회한다.

        POST /ingest에 포함된 manifest는 핸드오프 시점의 스냅샷이다.

        실제 파일 처리를 시작하기 직전에 백엔드 manifest API를 다시
        조회하여 파일명, 폴더, 파일 형식 및 Presigned URL의 최신 값을
        사용한다.
        """

        self._validate_file_idx(file_idx)

        response = await self._request(
            method="GET",
            path=(f"{_INTERNAL_FILES_PATH}/{file_idx}/manifest"),
            operation="manifest_fetch",
            file_idx=file_idx,
        )

        self._ensure_expected_status(
            response=response,
            expected_status=HTTPStatus.OK,
            operation="manifest_fetch",
            file_idx=file_idx,
        )

        try:
            payload: object = response.json()
        except ValueError as error:
            # 백엔드 응답 원문에는 내부 오류 정보나 Presigned URL이
            # 포함될 수 있으므로 예외 또는 로그에 본문을 저장하지 않는다.
            raise AppException(
                ErrorCode.INVALID_APPLICATION_SERVER_RESPONSE,
                log_context={
                    "operation": "manifest_fetch",
                    "file_idx": file_idx,
                    "validation": "invalid_json",
                },
            ) from error

        try:
            manifest = FileProcessingRequest.model_validate(payload)
        except ValidationError as error:
            # Pydantic ValidationError에는 입력값이 포함될 수 있으므로
            # 오류 상세를 외부 메시지나 로그 컨텍스트에 저장하지 않는다.
            raise AppException(
                ErrorCode.INVALID_APPLICATION_SERVER_RESPONSE,
                log_context={
                    "operation": "manifest_fetch",
                    "file_idx": file_idx,
                    "validation": "invalid_manifest_schema",
                },
            ) from error

        # 요청한 파일과 다른 파일의 manifest를 반환하는 경우
        # 다른 사용자의 파일을 잘못 처리할 수 있으므로 즉시 거부한다.
        if manifest.file_idx != file_idx:
            raise AppException(
                ErrorCode.INVALID_APPLICATION_SERVER_RESPONSE,
                log_context={
                    "operation": "manifest_fetch",
                    "file_idx": file_idx,
                    "received_file_idx": manifest.file_idx,
                    "validation": "file_idx_mismatch",
                },
            )

        return manifest

    async def notify_ingest_complete(
        self,
        *,
        file_idx: int,
        success: bool,
        index_version: int | None = None,
        chunks: tuple[
            ChunkSynchronizationRequest,
            ...,
        ]
        | None = None,
        error_message: str | None = None,
    ) -> None:
        """파일 인제스트의 최종 상태와 청크 동기화 데이터를 전달한다.

        기존 success-only 호출은 하위 호환성을 위해 허용한다.

        실제 인제스트 성공 경로에서는 index_version과 최신 활성 청크
        전체를 함께 전달한다. chunk_count는 별도 인자로 받지 않고
        실제 전송할 chunks 길이를 기준으로 자동 계산한다.

        실패 콜백에는 청크 데이터가 포함되지 않으며 외부 공개가 가능한
        error_message만 전달한다.
        """

        self._validate_file_idx(file_idx)

        # 호출 이후 외부에서 목록이 변경되지 않도록 불변 tuple로 정규화한다.
        normalized_chunks = tuple(chunks) if chunks is not None else None

        request_body = IngestCompleteRequest(
            success=success,
            index_version=index_version,
            # chunk_count를 호출자에게 별도로 받으면 실제 청크 배열 길이와
            # 달라질 수 있다. 전송 직전 확정된 tuple 길이를 기준으로 계산한다.
            chunk_count=(len(normalized_chunks) if normalized_chunks is not None else None),
            chunks=normalized_chunks,
            error_message=error_message,
        )

        # mode="json"을 사용하면 source_metadata 안의 tuple과 같은
        # Python 전용 값이 JSON 배열 등 직렬화 가능한 값으로 변환된다.
        serialized_body = request_body.model_dump(
            mode="json",
        )

        # exclude_none=True를 model_dump()에 직접 사용하면
        # 중첩 청크의 token_count=None까지 제거된다.
        #
        # 백엔드 계약상 token_count를 계산하지 않은 경우 null을 전달해야 하므로,
        # 최상위 선택 필드만 제거하고 청크 내부의 null 값은 그대로 유지한다.
        payload: dict[str, object] = {
            key: value for key, value in serialized_body.items() if value is not None
        }

        response = await self._request(
            method="POST",
            path=(f"{_INTERNAL_FILES_PATH}/{file_idx}/ingest-complete"),
            operation="ingest_complete_callback",
            file_idx=file_idx,
            json_body=payload,
        )

        self._ensure_expected_status(
            response=response,
            expected_status=HTTPStatus.NO_CONTENT,
            operation="ingest_complete_callback",
            file_idx=file_idx,
        )

    async def _request(
        self,
        *,
        method: HttpMethod,
        path: str,
        operation: str,
        file_idx: int,
        json_body: Mapping[
            str,
            object,
        ]
        | None = None,
    ) -> httpx2.Response:
        """인증과 재시도 정책을 적용하여 내부 HTTP 요청을 수행한다."""

        internal_token = self._get_internal_token()

        timeout = httpx2.Timeout(
            connect=(self._settings.app_server_connect_timeout_seconds),
            read=(self._settings.app_server_read_timeout_seconds),
            write=(self._settings.app_server_read_timeout_seconds),
            pool=(self._settings.app_server_connect_timeout_seconds),
        )

        async with httpx2.AsyncClient(
            base_url=self._settings.app_server_base_url,
            timeout=timeout,
            follow_redirects=False,
            # 시스템 HTTP_PROXY 또는 HTTPS_PROXY가 내부 서비스 통신에
            # 의도하지 않게 개입하지 않도록 한다.
            trust_env=False,
            transport=self._transport,
            headers={
                _INTERNAL_TOKEN_HEADER_NAME: internal_token,
                "Accept": "application/json",
            },
        ) as client:
            for attempt_number in range(
                1,
                self._settings.app_server_max_attempts + 1,
            ):
                try:
                    response = await client.request(
                        method,
                        path,
                        json=json_body,
                    )

                except httpx2.TimeoutException as error:
                    if attempt_number >= self._settings.app_server_max_attempts:
                        raise AppException(
                            ErrorCode.APPLICATION_SERVER_TIMEOUT,
                            log_context={
                                "operation": operation,
                                "file_idx": file_idx,
                                "attempt_number": attempt_number,
                                "exception_type": (type(error).__name__),
                            },
                        ) from error

                    await self._wait_before_retry(
                        operation=operation,
                        file_idx=file_idx,
                        attempt_number=attempt_number,
                        reason="timeout",
                    )
                    continue

                except httpx2.RequestError as error:
                    if attempt_number >= self._settings.app_server_max_attempts:
                        raise AppException(
                            ErrorCode.APPLICATION_SERVER_UNAVAILABLE,
                            log_context={
                                "operation": operation,
                                "file_idx": file_idx,
                                "attempt_number": attempt_number,
                                "exception_type": (type(error).__name__),
                            },
                        ) from error

                    await self._wait_before_retry(
                        operation=operation,
                        file_idx=file_idx,
                        attempt_number=attempt_number,
                        reason=type(error).__name__,
                    )
                    continue

                status_code = response.status_code

                if self._is_retryable_status(status_code):
                    if attempt_number >= self._settings.app_server_max_attempts:
                        error_code = (
                            ErrorCode.APPLICATION_SERVER_TIMEOUT
                            if status_code == HTTPStatus.REQUEST_TIMEOUT
                            else (ErrorCode.APPLICATION_SERVER_UNAVAILABLE)
                        )

                        raise AppException(
                            error_code,
                            log_context={
                                "operation": operation,
                                "file_idx": file_idx,
                                "attempt_number": attempt_number,
                                "upstream_status_code": (status_code),
                            },
                        )

                    await self._wait_before_retry(
                        operation=operation,
                        file_idx=file_idx,
                        attempt_number=attempt_number,
                        reason=f"http_{status_code}",
                    )
                    continue

                return response

        # 설정상 최소 시도 횟수가 1이므로 정상적으로는 도달할 수 없다.
        raise AppException(
            ErrorCode.INTERNAL_SERVER_ERROR,
            log_context={
                "operation": operation,
                "file_idx": file_idx,
                "reason": ("request_loop_completed_without_response"),
            },
        )

    def _get_internal_token(self) -> str:
        """애플리케이션 서버 호출에 사용할 내부 토큰을 반환한다."""

        configured_token = self._settings.internal_token

        if configured_token is None:
            # 토큰 미설정 상태에서 요청을 보내면 인증이 우회되거나
            # 반복적인 401 응답이 발생할 수 있으므로 네트워크 요청 전에
            # fail-closed 방식으로 중단한다.
            raise AppException(
                ErrorCode.SERVICE_UNAVAILABLE,
                log_context={
                    "operation": ("application_server_authentication"),
                    "reason": ("internal_token_not_configured"),
                },
            )

        return configured_token.get_secret_value()

    async def _wait_before_retry(
        self,
        *,
        operation: str,
        file_idx: int,
        attempt_number: int,
        reason: str,
    ) -> None:
        """지수 증가 지연을 적용한 뒤 다음 요청을 시도한다."""

        delay_seconds = min(
            (self._settings.app_server_retry_initial_delay_seconds * (2 ** (attempt_number - 1))),
            (self._settings.app_server_retry_max_delay_seconds),
        )

        # 내부 토큰, 요청 본문, Presigned URL 및 응답 본문은 기록하지 않는다.
        logger.warning(
            "Application server request will be retried.",
            extra={
                "event": ("application_server_request_retry"),
                "operation": operation,
                "file_idx": file_idx,
                "attempt_number": attempt_number,
                "next_attempt_number": (attempt_number + 1),
                "retry_delay_seconds": delay_seconds,
                "reason": reason,
            },
        )

        await asyncio.sleep(delay_seconds)

    @staticmethod
    def _is_retryable_status(
        status_code: int,
    ) -> bool:
        """일시적인 오류로 판단하여 재시도할 상태 코드인지 확인한다."""

        return (
            status_code in _RETRYABLE_STATUS_CODES
            or status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        )

    @staticmethod
    def _ensure_expected_status(
        *,
        response: httpx2.Response,
        expected_status: HTTPStatus,
        operation: str,
        file_idx: int,
    ) -> None:
        """백엔드 응답 상태가 API 계약과 일치하는지 검증한다."""

        status_code = response.status_code

        if status_code == expected_status:
            return

        if status_code == HTTPStatus.NOT_FOUND:
            raise AppException(
                ErrorCode.RESOURCE_NOT_FOUND,
                public_message=("The requested file was not found."),
                log_context={
                    "operation": operation,
                    "file_idx": file_idx,
                    "upstream_status_code": status_code,
                },
            )

        # 내부 토큰 불일치, IP allowlist 차단, 잘못된 요청 및
        # 예상하지 못한 2xx/3xx 응답을 모두 잘못된 upstream 계약으로 처리한다.
        #
        # 백엔드 응답 본문은 외부 응답이나 로그에 포함하지 않는다.
        raise AppException(
            ErrorCode.APPLICATION_SERVER_REQUEST_REJECTED,
            log_context={
                "operation": operation,
                "file_idx": file_idx,
                "expected_status_code": int(expected_status),
                "upstream_status_code": status_code,
            },
        )

    @staticmethod
    def _validate_file_idx(
        file_idx: int,
    ) -> None:
        """내부 파일 API 경로에 사용할 File 식별자를 검증한다."""

        if isinstance(file_idx, bool) or not isinstance(file_idx, int) or file_idx <= 0:
            raise ValueError("file_idx must be a positive integer.")
