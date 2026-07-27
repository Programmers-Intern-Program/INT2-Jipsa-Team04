"""HTTP 다운로더가 다섯 지원 형식의 MIME과 Magic Byte를 검증하는지 테스트한다."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx2
import pytest

from jipsa_rag.core.config import Settings
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader

_FILE_URL = (
    "https://example-bucket.s3.ap-northeast-2.amazonaws.com/files/document?X-Amz-Signature=test"
)


def _ooxml_bytes(required_member: str) -> bytes:
    """다운로더의 ZIP 시그니처 검증에 사용할 최소 OOXML 계열 바이트를 만든다."""

    stream = BytesIO()
    with ZipFile(stream, "w") as package:
        package.writestr("[Content_Types].xml", "<Types />")
        package.writestr(required_member, "<root />")
    return stream.getvalue()


@pytest.fixture
def settings() -> Settings:
    """파일 다운로드 테스트에 필요한 최소 설정을 생성한다."""

    return Settings(
        app_env="test",
        database_host="127.0.0.1",
        database_name="Jipsa_Local_RAG",
        database_user="test_user",
        database_password="test_password",
        file_download_allowed_host_suffixes=".amazonaws.com",
        file_download_connect_timeout_seconds=5.0,
        file_download_read_timeout_seconds=30.0,
        file_download_max_size_bytes=1024 * 1024,
        _env_file=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        pytest.param("application/pdf", b"%PDF-1.7\n%%EOF", id="pdf"),
        pytest.param(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _ooxml_bytes("word/document.xml"),
            id="docx",
        ),
        pytest.param(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            _ooxml_bytes("ppt/presentation.xml"),
            id="pptx",
        ),
        pytest.param(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _ooxml_bytes("xl/workbook.xml"),
            id="xlsx",
        ),
        pytest.param("text/plain; charset=utf-8", "줄 단위 문서".encode(), id="txt"),
    ],
)
async def test_downloads_each_supported_document_family(
    settings: Settings,
    tmp_path: Path,
    content_type: str,
    payload: bytes,
) -> None:
    async def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={"Content-Type": content_type},
            stream=httpx2.ByteStream(payload),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )
    downloaded_path: Path | None = None

    async with downloader.download_and_validate(
        file_url=_FILE_URL,
        users_idx=1,
        file_idx=2,
    ) as downloaded_file:
        downloaded_path = downloaded_file.path
        assert downloaded_file.path.read_bytes() == payload
        assert downloaded_file.content_type == content_type.partition(";")[0]

    assert downloaded_path is not None
    assert not downloaded_path.exists()


@pytest.mark.asyncio
async def test_rejects_ooxml_mime_for_another_ooxml_package(
    settings: Settings,
    tmp_path: Path,
) -> None:
    payload = _ooxml_bytes("ppt/presentation.xml")

    async def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            },
            stream=httpx2.ByteStream(payload),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(AppException) as exception_info:
        async with downloader.download_and_validate(
            file_url=_FILE_URL,
            users_idx=1,
            file_idx=2,
        ):
            pass

    assert exception_info.value.code == "INVALID_FILE"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_ooxml_mime_with_pdf_magic(
    settings: Settings,
    tmp_path: Path,
) -> None:
    async def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code=200,
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            },
            stream=httpx2.ByteStream(b"%PDF-1.7\n%%EOF"),
        )

    downloader = HttpFileDownloader(
        settings,
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    with pytest.raises(AppException) as exception_info:
        async with downloader.download_and_validate(
            file_url=_FILE_URL,
            users_idx=1,
            file_idx=2,
        ):
            pass

    assert exception_info.value.code == "INVALID_FILE"
    assert list(tmp_path.iterdir()) == []
