"""Presigned GET URL에서 원본 문서를 스트리밍 다운로드하고 형식을 검증한다.

이 다운로더는 Local RAG 서버가 AWS 자격 증명이나 ``boto3`` 없이 애플리케이션
서버가 발급한 Presigned GET URL만 사용한다. 다운로드한 파일은 임시 경로에
저장되며 ``async with`` 블록 안에서만 유효하다.

처리 순서는 다음과 같다.

1. URL의 HTTPS, 포트, 사용자 정보, fragment와 허용 호스트를 검증한다.
2. HTTP 응답 상태, Content-Length, Content-Encoding과 MIME Type을 검증한다.
3. 본문을 64 KiB 단위로 임시 파일에 스트리밍한다.
4. 실제 수신 바이트 기준으로 최대 파일 크기를 다시 제한한다.
5. 다운로드와 동시에 SHA-256을 계산하고 형식 판별용 선두 8 KiB만 보관한다.
6. MIME Type, Magic Byte와 OOXML 패키지 루트를 교차 검증한다.
7. 선택적으로 기준 SHA-256과 일정 시간 비교를 수행한다.
8. 호출 블록이 종료되면 성공·실패와 관계없이 임시 파일을 삭제한다.

Presigned URL, 쿼리 문자열, 파일 원문, 해시와 임시 파일 전체 경로는 로그에
남기지 않는다. 운영 진단에는 사용자·파일 식별자와 안전한 검증 단계만 기록한다.
"""

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
from jipsa_rag.infrastructure.file.format_validation import (
    FORMAT_SNIFF_BYTES,
    validate_content_type_and_magic,
    validate_ooxml_package_and_mime,
    validate_supported_content_type,
)

logger = logging.getLogger(__name__)

# 파일 전체를 메모리에 올리지 않고 64 KiB 단위로 네트워크와 디스크 사이를 이동한다.
# 너무 작은 값은 시스템 호출 수를 늘리고, 지나치게 큰 값은 요청당 메모리 사용량을
# 높이므로 일반적인 스트리밍 단위인 64 KiB를 사용한다.
DOWNLOAD_CHUNK_SIZE_BYTES: Final[int] = 64 * 1024


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    """다운로드와 검증이 끝난 임시 원본 파일의 안전한 결과 모델.

    ``path``는 다운로더의 ``async with`` 블록 안에서만 유효하다. 컨텍스트가 끝난
    뒤 파싱 결과만 메모리에 남기고, 원본 임시 파일 경로를 저장하거나 재사용해서는
    안 된다.

    Attributes:
        path:
            검증이 완료된 임시 파일 경로다.
        size_bytes:
            HTTP 헤더가 아니라 실제로 수신하고 기록한 원본 바이트 수다.
        sha256:
            다운로드 원본 바이트에서 직접 계산한 소문자 SHA-256 16진 문자열이다.
        content_type:
            파라미터가 제거되고 소문자로 정규화된 MIME Type이다. 응답에 헤더가
            없거나 빈 값이면 ``None``이다.
    """

    path: Path
    size_bytes: int
    sha256: str
    content_type: str | None


class HttpFileDownloader:
    """HTTP 스트리밍 기반 원본 파일 다운로드와 공통 형식 검증을 수행한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        temp_directory: Path | None = None,
    ) -> None:
        """다운로드 설정과 테스트용 의존성을 주입받는다.

        Args:
            settings:
                허용 호스트 suffix, 연결·읽기 timeout과 최대 파일 크기를 포함한다.
            transport:
                실제 네트워크 대신 ``MockTransport``를 사용할 단위 테스트용 주입점이다.
            temp_directory:
                임시 파일을 생성할 디렉터리다. 생략하면 OS 기본 임시 디렉터리를 쓴다.
        """

        self._settings = settings
        self._transport = transport
        self._temp_directory = temp_directory

    @asynccontextmanager
    async def download_and_validate(
        self,
        *,
        file_url: str,
        users_idx: int,
        file_idx: int,
        expected_sha256: str | None = None,
    ) -> AsyncIterator[DownloadedFile]:
        """원본 파일을 다운로드하고 검증한 뒤 임시 파일을 제한적으로 제공한다.

        공개 인자 계약은 기존 PDF 인제스트 흐름과 동일하게 유지한다. 파일명의
        확장자와 요청 ``file_type`` 교차 검증은 ``FileProcessingRequest``가 담당하고,
        이 계층은 HTTP 응답과 실제 바이트를 기준으로 형식을 검증한다.

        ``yield`` 이후 호출자의 파서가 실행되는 동안만 임시 파일이 존재한다. 파서가
        예외를 발생시키거나 후속 코드가 중단되어도 ``finally``에서 파일을 삭제한다.

        Args:
            file_url:
                애플리케이션 서버가 발급한 HTTPS Presigned GET URL 원문이다.
            users_idx:
                오류 로그 범위를 제한하는 사용자 식별자다.
            file_idx:
                오류 로그 범위를 제한하는 파일 식별자다.
            expected_sha256:
                선택적 기준 해시다. 새 파일 처리 API는 보통 전달하지 않으며, 값이
                있는 기존 호출 경로에서는 다운로드 결과와 비교한다.

        Yields:
            검증이 완료된 ``DownloadedFile``이다.
        """

        # 네트워크 요청과 임시 파일 생성을 시작하기 전에 URL을 먼저 검증한다.
        # 허용되지 않은 호스트나 포트에 대한 SSRF 가능성을 조기에 차단한다.
        self._validate_download_url(file_url)

        try:
            temp_path = self._create_temp_path()
        except OSError as error:
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                public_message="A temporary file could not be created.",
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
                users_idx=users_idx,
                file_idx=file_idx,
                temp_path=temp_path,
                expected_sha256=expected_sha256,
            )

            # 이 지점부터 호출자의 async with 블록이 끝날 때까지 파일이 존재한다.
            yield downloaded_file
        finally:
            try:
                # 다운로드 중 실패해 파일이 일부만 작성되었거나 이미 삭제된 경우에도
                # missing_ok=True로 정리 동작을 멱등하게 유지한다.
                temp_path.unlink(missing_ok=True)
            except OSError:
                # cleanup 실패를 이유로 원래 파싱·다운로드 예외를 덮어쓰지 않는다.
                # 또한 임시 경로, URL과 해시는 민감할 수 있으므로 로그에 포함하지 않는다.
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
        users_idx: int,
        file_idx: int,
        temp_path: Path,
        expected_sha256: str | None,
    ) -> DownloadedFile:
        """HTTP 응답 본문을 임시 파일에 스트리밍하고 검증 결과를 반환한다."""

        content_type: str | None = None
        size_bytes = 0
        sha256 = hashlib.sha256()

        # 파일 전체를 메모리에 저장하지 않고 형식 sniff에 필요한 앞부분만 보관한다.
        leading_bytes = bytearray()

        timeout = httpx2.Timeout(
            connect=self._settings.file_download_connect_timeout_seconds,
            read=self._settings.file_download_read_timeout_seconds,
            # GET 요청은 요청 본문이 없지만 httpx의 완전한 timeout 구성을 명시한다.
            write=self._settings.file_download_read_timeout_seconds,
            pool=self._settings.file_download_connect_timeout_seconds,
        )

        try:
            async with (
                httpx2.AsyncClient(
                    timeout=timeout,
                    # Presigned URL이 다른 호스트로 redirect되면 최초 허용 호스트 검증을
                    # 우회할 수 있으므로 자동 redirect를 따라가지 않는다.
                    follow_redirects=False,
                    # 시스템 HTTP_PROXY/HTTPS_PROXY가 로컬 RAG 다운로드 경로에
                    # 개입하지 않도록 환경 proxy 설정을 무시한다.
                    trust_env=False,
                    transport=self._transport,
                ) as client,
                client.stream(
                    "GET",
                    file_url,
                    headers={
                        # 압축 디코딩이 적용되면 S3 원본 바이트와 실제 해시 입력이 달라질
                        # 수 있다. 원본 바이트 그대로 받기 위해 identity를 요청한다.
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
                    # aiter_raw()는 Content-Encoding 디코딩 전 원본 응답 바이트를 제공한다.
                    async for chunk in response.aiter_raw(
                        chunk_size=DOWNLOAD_CHUNK_SIZE_BYTES,
                    ):
                        if not chunk:
                            continue

                        size_bytes += len(chunk)

                        # Content-Length는 누락되거나 실제보다 작게 전달될 수 있으므로
                        # 실제 수신 누적 바이트를 기준으로 최대 크기를 다시 확인한다.
                        if (
                            size_bytes
                            > self._settings.file_download_max_size_bytes
                        ):
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

                        # 처음 FORMAT_SNIFF_BYTES까지만 별도 메모리에 보관한다. 마지막
                        # chunk가 한도를 넘으면 필요한 앞부분만 slice한다.
                        remaining_sniff_bytes = (
                            FORMAT_SNIFF_BYTES - len(leading_bytes)
                        )
                        if remaining_sniff_bytes > 0:
                            leading_bytes.extend(
                                chunk[:remaining_sniff_bytes]
                            )

                        # 다운로드와 동시에 해시를 계산하여 완료 후 파일 전체를 다시
                        # 읽는 추가 I/O를 피한다.
                        sha256.update(chunk)
                        file_handle.write(chunk)

        except AppException:
            # 이미 안전한 공통 오류로 변환된 검증 실패는 그대로 전달한다.
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
            # 임시 파일 열기, 쓰기, flush 등 파일 시스템 오류를 공통 내부 오류로 바꾼다.
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                public_message="The downloaded file could not be stored.",
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
                public_message="The downloaded file is empty.",
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "validation": "empty_file",
                },
            )

        detected_family = validate_content_type_and_magic(
            content_type=content_type,
            leading_bytes=bytes(leading_bytes),
            users_idx=users_idx,
            file_idx=file_idx,
        )

        if detected_family == "OOXML":
            # DOCX, PPTX, XLSX는 모두 ZIP Magic Byte를 공유한다. 임시 파일의 중앙
            # 디렉터리를 확인하여 지원 OOXML 루트가 정확히 하나인지, 구체 MIME Type과
            # 실제 패키지 형식이 일치하는지 검증한다.
            validate_ooxml_package_and_mime(
                file_path=temp_path,
                content_type=content_type,
                users_idx=users_idx,
                file_idx=file_idx,
            )

        calculated_sha256 = sha256.hexdigest()

        if expected_sha256 is not None and not hmac.compare_digest(
            calculated_sha256,
            expected_sha256.lower(),
        ):
            # 일반 == 비교 대신 일정 시간 비교 함수를 사용한다. 해시 자체는 로그나
            # 외부 응답에 넣지 않고 파일 범위와 실제 크기만 기록한다.
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

    def _validate_download_url(self, file_url: str) -> None:
        """다운로드 URL의 스킴, 포트, 사용자 정보와 허용 호스트를 검증한다."""

        try:
            parsed = urlsplit(file_url)
            hostname = parsed.hostname
            # malformed port는 속성 접근 시 ValueError를 발생시킬 수 있다.
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
                public_message="The file URL must use HTTPS.",
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

        # https://user:pass@host 형태는 사용자 정보가 URL에 포함되고 해석 혼동이나
        # 로그 노출 위험이 있으므로 허용하지 않는다.
        if parsed.username is not None or parsed.password is not None:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_user_information",
                },
            )

        # Fragment는 HTTP 요청에 전송되지 않지만 동일 URL의 해석 차이를 만들 수 있어
        # Presigned URL 계약에서 허용하지 않는다.
        if parsed.fragment:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_fragment",
                },
            )

        # 허용 호스트의 다른 내부 서비스 포트로 요청되는 것을 막기 위해 HTTPS 기본
        # 포트 443만 허용한다. 포트가 생략된 경우도 443으로 해석한다.
        if parsed_port is not None and parsed_port != 443:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                log_context={
                    "validation": "url_port",
                    "port": parsed_port,
                },
            )

        normalized_hostname = hostname.lower()
        allowed_suffixes = (
            self._settings.parsed_file_download_allowed_host_suffixes
        )

        # suffix가 ".amazonaws.com"이면 정확한 amazonaws.com 자체와
        # bucket.s3.ap-northeast-2.amazonaws.com 같은 점 경계 하위 도메인을 허용한다.
        # malicious-amazonaws.com은 점 경계가 없어 endswith(".amazonaws.com")가
        # 거짓이므로 허용되지 않는다.
        is_allowed_host = any(
            normalized_hostname == suffix.removeprefix(".")
            or normalized_hostname.endswith(suffix)
            for suffix in allowed_suffixes
        )

        if not is_allowed_host:
            raise AppException(
                ErrorCode.INVALID_FILE_URL,
                public_message="The file URL host is not allowed.",
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
        """HTTP 상태, 선언 크기, 인코딩과 MIME Type 헤더를 검증한다."""

        if not 200 <= response.status_code < 300:
            # Presigned URL 만료, 권한 오류, 객체 없음 등 외부 상태 코드는 내부 로그
            # 컨텍스트에만 기록하고 응답 본문은 읽거나 외부에 노출하지 않는다.
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

            if (
                content_length
                > self._settings.file_download_max_size_bytes
            ):
                # 본문을 읽기 전에 명백한 초과 파일을 조기 거부한다. 스트리밍 중에도
                # 실제 바이트 수를 별도로 검사하므로 잘못된 작은 헤더로 우회할 수 없다.
                raise AppException(
                    ErrorCode.FILE_TOO_LARGE,
                    log_context={
                        "users_idx": users_idx,
                        "file_idx": file_idx,
                        "content_length": content_length,
                        "maximum_size_bytes": (
                            self._settings.file_download_max_size_bytes
                        ),
                    },
                )

        content_encoding = response.headers.get(
            "content-encoding",
            "identity",
        ).strip().lower()

        if content_encoding not in {"", "identity"}:
            # gzip 등의 전송 인코딩을 허용하면 원본 S3 바이트와 다운로드 후 바이트가
            # 달라져 SHA-256 기준이 흔들릴 수 있으므로 거부한다.
            raise AppException(
                ErrorCode.INVALID_FILE,
                public_message="Encoded file responses are not supported.",
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "content_encoding": content_encoding,
                },
            )

        # 지원하지 않는 MIME Type은 본문 수신 전에 차단한다. 헤더가 없거나 일반
        # 바이너리 타입이면 Magic Byte와 파서 구조 검증에서 최종 판단한다.
        return validate_supported_content_type(
            content_type=response.headers.get("content-type"),
            users_idx=users_idx,
            file_idx=file_idx,
        )

    def _create_temp_path(self) -> Path:
        """외부 파일명을 사용하지 않는 안전한 임시 파일 경로를 생성한다."""

        if self._temp_directory is not None:
            self._temp_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

        # 요청의 file_name을 임시 경로에 사용하지 않는다. 경로 구분자, 예약 파일명,
        # 이름 충돌과 로그 노출 가능성을 피하고 OS가 생성한 무작위 이름을 사용한다.
        #
        # suffix를 .pdf/.docx처럼 실제 형식으로 두지 않는 이유는 다운로더가 요청의
        # file_type을 인자로 받지 않고 기존 PDF 호출 계약을 유지하기 때문이다. 실제
        # 형식은 MIME Type, Magic Byte, OOXML 내부 구조와 형식별 파서가 판별한다.
        file_descriptor, path_value = tempfile.mkstemp(
            prefix="jipsa-rag-",
            suffix=".document",
            dir=self._temp_directory,
        )

        # mkstemp()는 열린 저수준 파일 descriptor를 반환한다. 이를 닫지 않으면
        # Windows에서 같은 경로를 다시 열거나 삭제할 때 파일 잠금 문제가 생길 수 있다.
        os.close(file_descriptor)

        return Path(path_value)
