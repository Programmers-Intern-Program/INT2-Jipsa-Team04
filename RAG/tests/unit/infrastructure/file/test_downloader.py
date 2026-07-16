"""HTTP 파일 다운로드와 파일 유효성 검증 기능을 테스트한다."""

import hashlib
from pathlib import Path

import httpx2
import pytest

from jipsa_rag.core.config import Settings
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.file.downloader import (
    HttpFileDownloader,
)

PDF_CONTENT = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"

PDF_SHA256 = hashlib.sha256(PDF_CONTENT).hexdigest()

FILE_URL = (
    "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
    "files/example-file.pdf?X-Amz-Signature=example"
)


@pytest.fixture
def settings() -> Settings:
    """파일 다운로드 테스트에 사용할 최소 환경 설정을 생성한다."""

    return Settings(
        app_env="test",
        database_host="127.0.0.1",
        database_name="Jipsa_Local_RAG",
        database_user="test_user",
        database_password="test_password",
        file_download_allowed_host_suffixes=(".amazonaws.com"),
        file_download_connect_timeout_seconds=5.0,
        file_download_read_timeout_seconds=30.0,
        file_download_max_size_bytes=1024,
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_downloads_and_calculates_pdf_hash_without_expected_hash(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """기준 해시 없이 PDF를 다운로드하고 SHA-256을 계산한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        # 원본 파일 바이트를 그대로 받기 위해
        # identity 인코딩을 요청했는지 확인한다.
        assert request.headers["accept-encoding"] == "identity"

        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            # stream API 테스트에서는 이미 소비된 content 대신
            # ByteStream을 사용하여 응답 본문을 제공한다.
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    downloaded_path: Path | None = None

    # 새 파일 처리 API에는 file_hash가 없으므로
    # expected_sha256을 전달하지 않는 호출 경로를 검증한다.
    async with downloader.download_and_validate(
        file_url=FILE_URL,
        users_idx=45,
        file_idx=123,
    ) as downloaded_file:
        downloaded_path = downloaded_file.path

        assert downloaded_file.path.exists()
        assert downloaded_file.path.read_bytes() == PDF_CONTENT
        assert downloaded_file.size_bytes == len(PDF_CONTENT)
        assert downloaded_file.sha256 == PDF_SHA256
        assert downloaded_file.content_type == "application/pdf"

    # context 종료 후 임시 파일이 삭제되어야 한다.
    assert downloaded_path is not None
    assert not downloaded_path.exists()


@pytest.mark.asyncio
async def test_accepts_matching_expected_file_hash(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """선택적으로 전달한 기준 해시가 실제 파일 해시와 같으면 허용한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    downloaded_path: Path | None = None

    async with downloader.download_and_validate(
        file_url=FILE_URL,
        users_idx=1,
        file_idx=10,
        expected_sha256=PDF_SHA256,
    ) as downloaded_file:
        downloaded_path = downloaded_file.path
        assert downloaded_file.sha256 == PDF_SHA256

    assert downloaded_path is not None
    assert not downloaded_path.exists()


@pytest.mark.asyncio
async def test_deletes_temporary_file_when_caller_raises_error(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """후속 처리에서 예외가 발생해도 임시 파일을 삭제한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    downloaded_path: Path | None = None

    with pytest.raises(
        RuntimeError,
        match="parser failed",
    ):
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ) as downloaded_file:
            downloaded_path = downloaded_file.path

            # 후속 PDF 파서에서 오류가 발생한 상황을 모의한다.
            raise RuntimeError("parser failed")

    assert downloaded_path is not None
    assert not downloaded_path.exists()


@pytest.mark.asyncio
async def test_rejects_file_hash_mismatch_when_expected_hash_is_given(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """선택적으로 전달한 기준 해시와 실제 파일 해시가 다르면 거부한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
            expected_sha256="0" * 64,
        ):
            pass

    assert exception_info.value.code == "FILE_HASH_MISMATCH"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_file_larger_than_limit(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """최대 크기를 초과한 파일을 거부하고 임시 파일을 삭제한다."""

    oversized_content = PDF_CONTENT + b"x" * 1024

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            stream=httpx2.ByteStream(oversized_content),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "FILE_TOO_LARGE"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_content_length_larger_than_limit(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """Content-Length가 최대 크기를 초과하면 본문 수신 전에 거부한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": "2048",
            },
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "FILE_TOO_LARGE"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_empty_file(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """응답 본문이 비어 있는 파일을 거부한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            stream=httpx2.ByteStream(b""),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "INVALID_FILE"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_non_pdf_magic_bytes(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """MIME 유형이 PDF여도 실제 내용이 PDF가 아니면 거부한다."""

    invalid_content = b"this is not a pdf"

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
            },
            stream=httpx2.ByteStream(invalid_content),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "INVALID_FILE"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_unsupported_content_type(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """명시적으로 PDF와 다른 MIME 유형이 반환되면 거부한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "text/plain",
            },
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "UNSUPPORTED_FILE_MEDIA_TYPE"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_encoded_response(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """원본 해시가 달라질 수 있는 압축 응답을 거부한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Encoding": "gzip",
            },
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "INVALID_FILE"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_unallowed_download_host(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """허용 목록에 없는 호스트로 네트워크 요청을 보내지 않는다."""

    request_was_sent = False

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        nonlocal request_was_sent
        request_was_sent = True

        return httpx2.Response(
            status_code=200,
            stream=httpx2.ByteStream(PDF_CONTENT),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url="https://files.example.com/example-file.pdf",
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "INVALID_FILE_URL"
    assert request_was_sent is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_non_https_download_url(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """HTTP 스킴의 다운로드 URL을 거부한다."""

    downloader = HttpFileDownloader(
        settings,
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=("http://example-bucket.s3.ap-northeast-2.amazonaws.com/file.pdf"),
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "INVALID_FILE_URL"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_converts_http_error_to_download_failure(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """Presigned URL 만료나 권한 오류를 다운로드 실패로 변환한다."""

    async def handler(
        _: httpx2.Request,
    ) -> httpx2.Response:
        return httpx2.Response(
            status_code=403,
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "FILE_DOWNLOAD_FAILED"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_converts_timeout_to_download_timeout(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """다운로드 연결 제한 시간 초과를 공통 예외로 변환한다."""

    async def handler(
        request: httpx2.Request,
    ) -> httpx2.Response:
        raise httpx2.ConnectTimeout(
            "Connection timed out.",
            request=request,
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(
        AppException,
    ) as exception_info:
        async with downloader.download_and_validate(
            file_url=FILE_URL,
            users_idx=1,
            file_idx=10,
        ):
            pass

    assert exception_info.value.code == "FILE_DOWNLOAD_TIMEOUT"
    assert list(tmp_path.iterdir()) == []
