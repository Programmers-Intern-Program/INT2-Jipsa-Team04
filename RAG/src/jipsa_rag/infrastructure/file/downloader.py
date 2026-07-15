"""Presigned GET URL로 원본 파일을 내려받고 파일 유효성을 검증한다."""

import hashlib
import hmac
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import httpx2

from jipsa_rag.core.config import Settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException

logger = logging.getLogger(__name__)


# PDF 파일은 이 Magic Byte로 시작해야 한다.
#
# 확장자나 MIME 유형만 확인하면 실제 내용이 다른 파일을
# PDF로 위장할 수 있으므로 파일 본문도 함께 검증한다.
PDF_MAGIC_BYTES: Final[bytes] = b"%PDF-"


# 다운로드 파일을 한 번에 메모리에 적재하지 않고
# 64 KiB 단위로 읽어 임시 파일에 기록한다.
DOWNLOAD_CHUNK_SIZE_BYTES: Final[int] = 64 * 1024


# S3 객체의 Content-Type이 명확하게 설정되지 않은 경우
# 일반적인 바이너리 MIME 유형으로 반환될 수 있다.
#
# MIME 유형이 허용되더라도 최종 파일 형식은
# PDF Magic Byte로 다시 확인한다.
ALLOWED_PDF_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
        "binary/octet-stream",
    }
)


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    """다운로드와 검증이 완료된 임시 파일 정보."""

    path: Path
    size_bytes: int
    sha256: str
    content_type: str | None


class HttpFileDownloader:
    """HTTP 스트리밍 기반 원본 파일 다운로드와 검증을 수행한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        temp_directory: Path | None = None,
    ) -> None:
        """다운로드 설정과 테스트용 HTTP transport를 주입받는다."""

        self._settings = settings
        self._transport = transport
        self._temp_directory = temp_directory

    @asynccontextmanager
    async def download_and_validate(
        self,
        *,
        file_url: str,
        expected_sha256: str,
        users_idx: int,
        file_idx: int,
    ) -> AsyncIterator[DownloadedFile]:
        """원본 파일을 다운로드하고 PDF 형식과 해시를 검증한다.

        다운로드가 완료된 임시 파일은 async with 구문 안에서만
        사용할 수 있다.

        async with 블록이 정상 종료되거나 예외로 종료되더라도
        임시 파일은 finally 구문에서 삭제한다.
        """

        # 네트워크 요청을 수행하기 전에 URL의 HTTPS 스킴,
        # 포트 및 허용 호스트 여부를 먼저 검증한다.
        self._validate_download_url(file_url)

        try:
            temp_path = self._create_temp_path()
        except OSError as error:
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                public_message=("A temporary file could not be created."),
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "operation": "temporary_file_create",
                    "exception_type": type(error).__name__,
                },
            ) from error

        try:
            downloaded_file = await self._download_to_path(
                file_url=file_url,
                expected_sha256=expected_sha256,
                users_idx=users_idx,
                file_idx=file_idx,
                temp_path=temp_path,
            )

            # 후속 PDF 텍스트 추출은 이 yield 이후의
            # async with 블록 내부에서 실행해야 한다.
            yield downloaded_file

        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                # Presigned URL, URL Query String, 파일 해시 및
                # 임시 파일 전체 경로는 로그에 기록하지 않는다.
                logger.exception(
                    "Temporary file cleanup failed.",
                    extra={
                        "event": "temporary_file_cleanup_failed",
                        "users_idx": users_idx,
                        "file_idx": file_idx,
                    },
                )

    async def _download_to_path(
        self,
        *,
        file_url: str,
        expected_sha256: str,
        users_idx: int,
        file_idx: int,
        temp_path: Path,
    ) -> DownloadedFile:
        """파일을 지정된 임시 경로에 스트리밍하고 검증 결과를 반환한다."""

        content_type: str | None = None
        size_bytes = 0
        sha256 = hashlib.sha256()
        leading_bytes = bytearray()

        timeout = httpx2.Timeout(
            connect=(self._settings.file_download_connect_timeout_seconds),
            read=(self._settings.file_download_read_timeout_seconds),
            # GET 요청은 별도의 본문을 전송하지 않지만
            # HTTP 클라이언트의 전체 timeout 구성을 명시한다.
            write=(self._settings.file_download_read_timeout_seconds),
            pool=(self._settings.file_download_connect_timeout_seconds),
        )

        try:
            # HTTP 클라이언트와 스트리밍 응답의 생명주기를 하나의
            # async with 문에서 함께 관리한다.
            #
            # 각 Context Manager는 왼쪽부터 순서대로 진입하므로
            # 먼저 생성된 client를 다음 stream() 호출에서 사용할 수 있다.
            async with (
                httpx2.AsyncClient(
                    timeout=timeout,
                    # 다운로드 대상이 다른 호스트로 이동하지 않도록
                    # Redirect 응답을 자동으로 따라가지 않는다.
                    follow_redirects=False,
                    # 로컬 시스템의 HTTP_PROXY, HTTPS_PROXY 등의
                    # 환경 변수가 다운로드 경로에 개입하지 않도록 한다.
                    trust_env=False,
                    transport=self._transport,
                ) as client,
                client.stream(
                    "GET",
                    file_url,
                    headers={
                        # 서버가 gzip 등의 압축 응답을 반환하면
                        # 애플리케이션 서버가 계산한 원본 파일 해시와
                        # 수신 바이트 해시가 달라질 수 있다.
                        "Accept-Encoding": "identity",
                    },
                ) as response,
            ):
                content_type = self._validate_response_headers(
                    response,
                    users_idx=users_idx,
                    file_idx=file_idx,
                )

                with temp_path.open("wb") as file_handle:
                    # aiter_raw()를 사용하여 HTTP Content-Encoding
                    # 디코딩이 적용되지 않은 원본 응답 바이트를 읽는다.
                    async for chunk in response.aiter_raw(
                        chunk_size=DOWNLOAD_CHUNK_SIZE_BYTES,
                    ):
                        if not chunk:
                            continue

                        size_bytes += len(chunk)

                        # Content-Length가 없거나 실제 값보다 작더라도
                        # 수신한 실제 바이트 수를 기준으로 제한한다.
                        if size_bytes > self._settings.file_download_max_size_bytes:
                            raise AppException(
                                ErrorCode.FILE_TOO_LARGE,
                                log_context={
                                    "users_idx": users_idx,
                                    "file_idx": file_idx,
                                    "received_size_bytes": size_bytes,
                                    "maximum_size_bytes": (
                                        self._settings.file_download_max_size_bytes
                                    ),
                                },
                            )

                        # PDF Magic Byte 검증에 필요한 첫 5바이트만
                        # 별도 메모리에 보관한다.
                        remaining_magic_bytes = len(PDF_MAGIC_BYTES) - len(leading_bytes)

                        if remaining_magic_bytes > 0:
                            leading_bytes.extend(chunk[:remaining_magic_bytes])

                        # 다운로드와 동시에 SHA-256을 계산하여
                        # 파일 전체를 다시 읽지 않도록 한다.
                        sha256.update(chunk)
                        file_handle.write(chunk)

        except AppException:
            raise

        except httpx2.TimeoutException as error:
            raise AppException(
                ErrorCode.FILE_DOWNLOAD_TIMEOUT,
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "exception_type": type(error).__name__,
                },
            ) from error

        except httpx2.RequestError as error:
            raise AppException(
                ErrorCode.FILE_DOWNLOAD_FAILED,
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "exception_type": type(error).__name__,
                },
            ) from error

        except OSError as error:
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                public_message=("The downloaded file could not be stored."),
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "operation": "temporary_file_write",
                    "exception_type": type(error).__name__,
                },
            ) from error

        if size_bytes == 0:
            raise AppException(
                ErrorCode.INVALID_FILE,
                public_message=("The downloaded file is empty."),
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "validation": "empty_file",
                },
            )

        if bytes(leading_bytes) != PDF_MAGIC_BYTES:
            raise AppException(
                ErrorCode.INVALID_FILE,
                public_message=("The downloaded file is not a valid PDF file."),
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "validation": "pdf_magic_bytes",
                },
            )

        calculated_sha256 = sha256.hexdigest()

        # 해시 문자열 비교에는 일반 문자열 비교 대신
        # 일정 시간 비교 함수를 사용한다.
        if not hmac.compare_digest(
            calculated_sha256,
            expected_sha256.lower(),
        ):
            raise AppException(
                ErrorCode.FILE_HASH_MISMATCH,
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "file_size_bytes": size_bytes,
                },
            )

        return DownloadedFile(
            path=temp_path,
            size_bytes=size_bytes,
            sha256=calculated_sha256,
            content_type=content_type,
        )

    def _validate_download_url(
        self,
        file_url: str,
    ) -> None:
        """다운로드 URL의 스킴, 사용자 정보 및 호스트를 검증한다."""

        try:
            parsed = urlsplit(file_url)
            hostname = parsed.hostname
            parsed_port = parsed.port
        except ValueError as error:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_parse",
                },
            ) from error

        if parsed.scheme.lower() != "https":
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                public_message=("The file URL must use HTTPS."),
                log_context={
                    "validation": "url_scheme",
                },
            )

        if hostname is None:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_hostname",
                },
            )

        if parsed.username is not None or parsed.password is not None:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_user_information",
                },
            )

        if parsed.fragment:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_fragment",
                },
            )

        # HTTPS 기본 포트인 443만 허용한다.
        #
        # 별도의 포트를 허용하면 동일한 호스트 이름에서 실행되는
        # 다른 서비스로 요청이 전달될 가능성이 있다.
        if parsed_port is not None and parsed_port != 443:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_port",
                    "port": parsed_port,
                },
            )

        normalized_hostname = hostname.lower()
        allowed_suffixes = self._settings.parsed_file_download_allowed_host_suffixes

        # ".amazonaws.com"은 다음 호스트와 일치한다.
        # - amazonaws.com
        # - bucket.s3.ap-northeast-2.amazonaws.com
        #
        # "malicious-amazonaws.com"처럼 점 경계가 없는 호스트는
        # ".amazonaws.com"으로 끝나지 않으므로 허용되지 않는다.
        is_allowed_host = any(
            normalized_hostname == suffix.removeprefix(".") or normalized_hostname.endswith(suffix)
            for suffix in allowed_suffixes
        )

        if not is_allowed_host:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                public_message=("The file URL host is not allowed."),
                log_context={
                    "validation": "url_allowed_host",
                },
            )

    def _validate_response_headers(
        self,
        response: httpx2.Response,
        *,
        users_idx: int,
        file_idx: int,
    ) -> str | None:
        """HTTP 상태와 파일 크기 및 MIME 유형 헤더를 검증한다."""

        if not 200 <= response.status_code < 300:
            raise AppException(
                ErrorCode.FILE_DOWNLOAD_FAILED,
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "source_status_code": response.status_code,
                },
            )

        raw_content_length = response.headers.get("content-length")

        if raw_content_length is not None:
            try:
                content_length = int(raw_content_length)
            except ValueError as error:
                raise AppException(
                    ErrorCode.FILE_DOWNLOAD_FAILED,
                    log_context={
                        "users_idx": users_idx,
                        "file_idx": file_idx,
                        "validation": "content_length",
                    },
                ) from error

            if content_length < 0:
                raise AppException(
                    ErrorCode.FILE_DOWNLOAD_FAILED,
                    log_context={
                        "users_idx": users_idx,
                        "file_idx": file_idx,
                        "validation": "negative_content_length",
                    },
                )

            if content_length > self._settings.file_download_max_size_bytes:
                raise AppException(
                    ErrorCode.FILE_TOO_LARGE,
                    log_context={
                        "users_idx": users_idx,
                        "file_idx": file_idx,
                        "content_length": content_length,
                        "maximum_size_bytes": (self._settings.file_download_max_size_bytes),
                    },
                )

        content_encoding = (
            response.headers.get(
                "content-encoding",
                "identity",
            )
            .strip()
            .lower()
        )

        # 원본 파일 해시를 동일하게 계산하기 위해
        # 압축 또는 별도의 Content-Encoding 응답은 허용하지 않는다.
        if content_encoding not in {
            "",
            "identity",
        }:
            raise AppException(
                ErrorCode.INVALID_FILE,
                public_message=("Encoded file responses are not supported."),
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "content_encoding": content_encoding,
                },
            )

        raw_content_type = response.headers.get("content-type")

        # Content-Type이 없는 경우에도 Magic Byte 검증으로
        # 실제 PDF 여부를 확인할 수 있으므로 즉시 실패시키지 않는다.
        if raw_content_type is None:
            return None

        # application/pdf; charset=binary와 같은 값에서
        # MIME 유형 부분만 분리한다.
        content_type = raw_content_type.partition(";")[0].strip().lower()

        if content_type and content_type not in ALLOWED_PDF_CONTENT_TYPES:
            raise AppException(
                ErrorCode.UNSUPPORTED_FILE_MEDIA_TYPE,
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "content_type": content_type,
                },
            )

        return content_type or None

    def _create_temp_path(self) -> Path:
        """다운로드 파일을 저장할 안전한 임시 파일 경로를 생성한다."""

        if self._temp_directory is not None:
            self._temp_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

        # mkstemp()가 생성한 파일명은 외부 요청의 File_Name을
        # 사용하지 않으므로 경로 조작과 파일명 충돌을 방지할 수 있다.
        file_descriptor, path_value = tempfile.mkstemp(
            prefix="jipsa-rag-",
            suffix=".pdf",
            dir=self._temp_directory,
        )

        # Windows에서는 열린 파일 descriptor가 남아 있으면
        # 같은 경로를 다시 열거나 삭제하지 못할 수 있으므로 즉시 닫는다.
        os.close(file_descriptor)

        return Path(path_value)
