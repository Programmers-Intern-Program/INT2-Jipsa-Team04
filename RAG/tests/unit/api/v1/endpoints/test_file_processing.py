"""파일 처리 API의 다운로드부터 Local RAG·Qdrant 저장까지 테스트한다."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from jipsa_rag.api.v1.endpoints.file_processing import (
    get_chunk_embedder,
    get_document_chunker,
    get_document_parser_factory,
    get_file_downloader,
    get_file_indexing_service,
)
from jipsa_rag.core.config import get_settings
from jipsa_rag.infrastructure.chunking.exceptions import (
    DocumentChunkingError,
    InvalidChunkingConfigurationError,
    NoDocumentChunksError,
)
from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    ChunkingContext,
    TextChunk,
)
from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentParserError,
    DocumentReadError,
    DocumentTextExtractionError,
    DocumentTextNotFoundError,
    EncryptedDocumentError,
    InvalidDocumentError,
    UnsupportedDocumentTypeError,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)
from jipsa_rag.infrastructure.document.parser_factory import (
    DocumentParserFactory,
)
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingError,
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.embedding.models import (
    EmbeddedChunk,
    EmbeddedDocument,
)
from jipsa_rag.infrastructure.file.downloader import (
    HttpFileDownloader,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
    VectorCollectionConfigurationError,
    VectorDatabaseError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.infrastructure.indexing.models import (
    DocumentIndexMetadata,
)
from jipsa_rag.services.file_indexing import FileIndexingResult

# API 계층 테스트에서는 실제 pypdf 파싱, TEI 통신,
# Local RAG DB 연결 또는 Qdrant 통신을 수행하지 않는다.
#
# 다운로더의 PDF Magic Byte 검증과 SHA-256 계산에 필요한
# 최소 바이트를 사용하고 파싱, 청킹, 임베딩 및 저장 결과는
# 각 Stub 객체가 반환한다.
#
# 실제 구현의 세부 동작은 다음 단위 테스트에서 각각 검증한다.
# - PdfDocumentParser
# - CharacterTextChunker
# - TeiChunkEmbedder
# - LocalRagIndexRepository
# - QdrantChunkVectorStore
# - FileIndexingService
PDF_CONTENT = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
PDF_SHA256 = hashlib.sha256(PDF_CONTENT).hexdigest()

TEST_EMBEDDING_MODEL = "test/embedding-model"
TEST_EMBEDDING_DIM = 3

VALID_FILE_PROCESSING_REQUEST: dict[str, object] = {
    "file_idx": 123,
    "user_idx": 45,
    "folder_idx": 9,
    "file_name": "2026 Q3 회의록.pdf",
    "file_type": "pdf",
    "download_url": (
        "https://example-bucket.s3.ap-northeast-2.amazonaws.com/"
        "files/example-file.pdf?X-Amz-Signature=example"
    ),
    "url_expires_in": 900,
}


class StubPdfDocumentParser:
    """파일 처리 API 테스트에서 사용하는 PDF 파서 대역."""

    def __init__(self) -> None:
        """호출 경로와 선택적으로 발생시킬 예외를 초기화한다."""

        self.error: DocumentParserError | None = None
        self.received_file_paths: list[Path] = []

    @property
    def file_type(self) -> DocumentType:
        """Factory에 등록할 문서 형식으로 PDF를 반환한다."""

        return DocumentType.PDF

    @property
    def parser_type(self) -> str:
        """Local RAG 저장 테스트에 사용할 파서 종류를 반환한다."""

        return "PDF_TEXT"

    @property
    def parser_version(self) -> str:
        """Local RAG 저장 테스트에 사용할 파서 버전을 반환한다."""

        return "test-1.0.0"

    async def parse(
        self,
        file_path: Path,
    ) -> ParsedDocument:
        """임시 파일 생명주기를 확인하고 고정된 파싱 결과를 반환한다."""

        self.received_file_paths.append(file_path)

        # 파싱은 HttpFileDownloader의 async with 내부에서 실행되어야 한다.
        #
        # 이 시점에 파일이 존재하지 않으면 엔드포인트가 context 밖에서
        # 파서를 호출한 것이므로 테스트를 즉시 실패시킨다.
        assert file_path.exists()
        assert file_path.is_file()

        if self.error is not None:
            raise self.error

        return ParsedDocument(
            file_type=self.file_type,
            units=(
                ParsedDocumentUnit(
                    text="First page text.",
                    source_metadata={
                        "page_number": 1,
                    },
                ),
                # 빈 페이지도 원본 위치 보존을 위해 단위로 유지한다.
                ParsedDocumentUnit(
                    text="",
                    source_metadata={
                        "page_number": 2,
                    },
                ),
            ),
            document_metadata={
                "page_count": 2,
            },
        )


class StubDocumentChunker:
    """파일 처리 API 테스트에서 사용하는 문서 청커 대역."""

    def __init__(self) -> None:
        """호출 입력과 선택적으로 발생시킬 예외를 초기화한다."""

        self.error: DocumentChunkingError | None = None
        self.received_documents: list[ParsedDocument] = []
        self.received_contexts: list[ChunkingContext] = []

    async def chunk(
        self,
        *,
        document: ParsedDocument,
        context: ChunkingContext,
    ) -> ChunkedDocument:
        """고정된 단일 청크를 포함한 청킹 결과를 반환한다."""

        self.received_documents.append(document)
        self.received_contexts.append(context)

        if self.error is not None:
            raise self.error

        content = document.units[0].text
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        return ChunkedDocument(
            file_type=document.file_type,
            chunks=(
                TextChunk(
                    chunk_id=("11111111-1111-1111-1111-111111111111"),
                    chunk_index=0,
                    content=content,
                    content_hash=content_hash,
                    start_offset=0,
                    end_offset=len(content),
                    source_metadata={
                        "page_number": 1,
                        "source_unit_index": 0,
                        "unit_start_offset": 0,
                        "unit_end_offset": len(content),
                    },
                ),
            ),
            source_unit_count=document.unit_count,
            text_unit_count=document.text_unit_count,
        )


class StubChunkEmbedder:
    """파일 처리 API 테스트에서 사용하는 청크 임베딩 생성기 대역."""

    def __init__(self) -> None:
        """호출 입력과 선택적으로 발생시킬 예외를 초기화한다."""

        self.error: EmbeddingError | None = None
        self.received_documents: list[ChunkedDocument] = []

    async def embed(
        self,
        *,
        document: ChunkedDocument,
    ) -> EmbeddedDocument:
        """모든 입력 청크에 고정된 테스트 벡터를 할당한다."""

        self.received_documents.append(document)

        if self.error is not None:
            raise self.error

        embedded_chunks = tuple(
            EmbeddedChunk(
                chunk=chunk,
                embedding=(
                    0.1,
                    0.2,
                    0.3,
                ),
            )
            for chunk in document.chunks
        )

        return EmbeddedDocument(
            embedding_model=TEST_EMBEDDING_MODEL,
            embedding_dim=TEST_EMBEDDING_DIM,
            chunks=embedded_chunks,
        )


class StubFileIndexingService:
    """Local RAG DB와 Qdrant를 호출하지 않는 파일 색인 서비스 대역."""

    def __init__(self) -> None:
        """호출 입력과 선택적으로 발생시킬 저장 예외를 초기화한다."""

        self.error: LocalRagStorageError | VectorDatabaseError | None = None
        self.received_metadata: list[DocumentIndexMetadata] = []
        self.received_documents: list[EmbeddedDocument] = []

    async def index(
        self,
        *,
        metadata: DocumentIndexMetadata,
        embedded_document: EmbeddedDocument,
    ) -> FileIndexingResult:
        """고정된 Local RAG 식별자를 포함한 저장 완료 결과를 반환한다."""

        self.received_metadata.append(metadata)
        self.received_documents.append(embedded_document)

        if self.error is not None:
            raise self.error

        return FileIndexingResult(
            rag_document_idx=100,
            rag_index_run_idx=200,
            chunk_count=embedded_document.chunk_count,
        )


@pytest.fixture
def pdf_document_parser() -> StubPdfDocumentParser:
    """파일 처리 API에 주입할 테스트 전용 PDF 파서를 반환한다."""

    return StubPdfDocumentParser()


@pytest.fixture
def document_chunker() -> StubDocumentChunker:
    """파일 처리 API에 주입할 테스트 전용 문서 청커를 반환한다."""

    return StubDocumentChunker()


@pytest.fixture
def chunk_embedder() -> StubChunkEmbedder:
    """파일 처리 API에 주입할 테스트 전용 청크 임베더를 반환한다."""

    return StubChunkEmbedder()


@pytest.fixture
def file_indexing_service() -> StubFileIndexingService:
    """파일 처리 API에 주입할 테스트 전용 색인 서비스를 반환한다."""

    return StubFileIndexingService()


@pytest.fixture
def file_processing_client(
    client: TestClient,
    tmp_path: Path,
    pdf_document_parser: StubPdfDocumentParser,
    document_chunker: StubDocumentChunker,
    chunk_embedder: StubChunkEmbedder,
    file_indexing_service: StubFileIndexingService,
) -> Iterator[TestClient]:
    """외부 네트워크와 저장소 없이 전체 파일 처리 API 흐름을 테스트한다."""

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
        get_settings(),
        transport=httpx2.MockTransport(handler),
        temp_directory=tmp_path,
    )

    parser_factory = DocumentParserFactory(
        parsers=(pdf_document_parser,),
    )

    # conftest의 client fixture에서 사용하는 실제 애플리케이션과
    # 동일한 객체에 다운로드, 파서, 청커, 임베더 및 색인 서비스
    # 의존성 override를 적용한다.
    from jipsa_rag.main import app

    app.dependency_overrides[get_file_downloader] = lambda: downloader
    app.dependency_overrides[get_document_parser_factory] = lambda: parser_factory
    app.dependency_overrides[get_document_chunker] = lambda: document_chunker
    app.dependency_overrides[get_chunk_embedder] = lambda: chunk_embedder
    app.dependency_overrides[get_file_indexing_service] = lambda: file_indexing_service

    try:
        yield client
    finally:
        # 다른 API 테스트에 테스트용 인프라 객체가 남지 않도록
        # fixture 종료 시 이번 테스트에서 등록한 override를 모두 제거한다.
        app.dependency_overrides.pop(
            get_file_downloader,
            None,
        )
        app.dependency_overrides.pop(
            get_document_parser_factory,
            None,
        )
        app.dependency_overrides.pop(
            get_document_chunker,
            None,
        )
        app.dependency_overrides.pop(
            get_chunk_embedder,
            None,
        )
        app.dependency_overrides.pop(
            get_file_indexing_service,
            None,
        )


def test_file_processing_request_returns_completed_response(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
    document_chunker: StubDocumentChunker,
    chunk_embedder: StubChunkEmbedder,
    file_indexing_service: StubFileIndexingService,
) -> None:
    """유효한 요청이 색인 완료 상태와 생성된 청크 수를 반환한다."""

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": "FILE_INDEXING_COMPLETED",
        "message": ("File download, parsing, chunking, embedding, and indexing completed."),
        "data": {
            "rag_document_idx": 100,
            "file_idx": 123,
            "user_idx": 45,
            "folder_idx": 9,
            "file_name": "2026 Q3 회의록.pdf",
            "file_type": "pdf",
            "file_size_bytes": len(PDF_CONTENT),
            "page_count": 2,
            "text_unit_count": 1,
            "chunk_count": 1,
            "embedding_model": TEST_EMBEDDING_MODEL,
            "embedding_dim": TEST_EMBEDDING_DIM,
            "processing_status": "INDEXED",
        },
    }

    response_data = response.json()["data"]

    # 애플리케이션 서버가 별도의 상태 조회 없이
    # 색인 완료 여부와 생성된 청크 개수를 확인할 수 있어야 한다.
    assert response_data["processing_status"] == "INDEXED"
    assert response_data["chunk_count"] == 1

    # 파서는 다운로드된 임시 파일 경로를 정확히 한 번 전달받아야 한다.
    assert len(pdf_document_parser.received_file_paths) == 1
    parsed_file_path = pdf_document_parser.received_file_paths[0]

    # API 응답이 반환되기 전에 다운로드 context가 종료되어
    # 임시 파일이 삭제되어야 한다.
    assert not parsed_file_path.exists()

    # 파서가 반환한 ParsedDocument가 청커에 그대로 전달되어야 한다.
    assert len(document_chunker.received_documents) == 1
    chunking_context = document_chunker.received_contexts[0]

    # 외부 요청의 user_idx는 내부 모델의 users_idx로 변환되어야 한다.
    assert chunking_context.users_idx == 45
    assert chunking_context.file_idx == 123

    # 요청에는 file_hash가 없지만 다운로더가 원본 바이트에서
    # 계산한 SHA-256이 결정적 Chunk ID 생성 컨텍스트에 전달되어야 한다.
    assert chunking_context.file_hash == PDF_SHA256
    assert chunking_context.index_version == 1

    # 청커가 반환한 ChunkedDocument가 임베더에 그대로 전달되어야 한다.
    assert len(chunk_embedder.received_documents) == 1
    assert chunk_embedder.received_documents[0].chunk_count == 1
    assert chunk_embedder.received_documents[0].chunks[0].content == "First page text."

    # 임베딩 결과와 문서 메타데이터가 색인 서비스에 전달되어야 한다.
    assert len(file_indexing_service.received_metadata) == 1
    assert len(file_indexing_service.received_documents) == 1

    indexing_metadata = file_indexing_service.received_metadata[0]

    assert indexing_metadata.users_idx == 45
    assert indexing_metadata.file_idx == 123
    assert indexing_metadata.folder_idx == 9
    assert indexing_metadata.file_name == "2026 Q3 회의록.pdf"
    assert indexing_metadata.file_type is DocumentType.PDF

    # Local RAG DB에는 다운로드 바이트에서 계산한 SHA-256을 저장한다.
    assert indexing_metadata.file_hash == PDF_SHA256
    assert indexing_metadata.index_version == 1
    assert indexing_metadata.parser_type == "PDF_TEXT"
    assert indexing_metadata.parser_version == "test-1.0.0"

    # Presigned URL과 S3_Key는 색인 메타데이터에 포함하지 않는다.
    assert not hasattr(indexing_metadata, "download_url")
    assert not hasattr(indexing_metadata, "file_url")
    assert not hasattr(indexing_metadata, "s3_key")


def test_file_processing_request_allows_null_folder_idx(
    file_processing_client: TestClient,
    file_indexing_service: StubFileIndexingService,
) -> None:
    """루트 경로 파일은 folder_idx 없이 저장할 수 있다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["folder_idx"] = None

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    assert response.status_code == 200
    assert response.json()["data"]["folder_idx"] is None
    assert response.json()["data"]["processing_status"] == "INDEXED"
    assert response.json()["data"]["chunk_count"] == 1
    assert file_indexing_service.received_metadata[0].folder_idx is None


def test_file_processing_request_rejects_legacy_payload_fields(
    file_processing_client: TestClient,
) -> None:
    """이전 요청 계약의 필드명과 외부 파일 해시를 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()

    # 새 필드를 제거한 뒤 이전 계약의 필드를 추가한다.
    request_body["file_url"] = request_body.pop("download_url")
    request_body["users_idx"] = request_body.pop("user_idx")
    request_body["file_hash"] = PDF_SHA256

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()
    invalid_fields = {error["field"] for error in body["data"]["errors"]}

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["message"] == "Request validation failed."

    # 새 계약의 필수 필드가 없다는 오류가 포함되어야 한다.
    assert "body.download_url" in invalid_fields
    assert "body.user_idx" in invalid_fields

    # extra="forbid" 설정으로 이전 계약 필드도 거부해야 한다.
    assert "body.file_url" in invalid_fields
    assert "body.users_idx" in invalid_fields
    assert "body.file_hash" in invalid_fields


@pytest.mark.parametrize(
    (
        "parser_error",
        "expected_status_code",
        "expected_code",
        "expected_message",
    ),
    [
        (
            UnsupportedDocumentTypeError(DocumentType.DOCX),
            415,
            "UNSUPPORTED_DOCUMENT_TYPE",
            "The document type is not supported.",
        ),
        (
            InvalidDocumentError(DocumentType.PDF),
            422,
            "INVALID_DOCUMENT",
            "The document structure is invalid.",
        ),
        (
            EncryptedDocumentError(DocumentType.PDF),
            422,
            "ENCRYPTED_DOCUMENT",
            "Encrypted documents are not supported.",
        ),
        (
            DocumentTextExtractionError(
                file_type=DocumentType.PDF,
                source_metadata={
                    "page_number": 2,
                },
            ),
            422,
            "DOCUMENT_TEXT_EXTRACTION_FAILED",
            "Text could not be extracted from the document.",
        ),
        (
            DocumentTextNotFoundError(DocumentType.PDF),
            422,
            "DOCUMENT_TEXT_NOT_FOUND",
            "No extractable text was found in the document.",
        ),
        (
            DocumentFileNotFoundError(Path("missing-document.pdf")),
            500,
            "DOCUMENT_READ_FAILED",
            "The document could not be read.",
        ),
        (
            DocumentReadError(Path("unreadable-document.pdf")),
            500,
            "DOCUMENT_READ_FAILED",
            "The document could not be read.",
        ),
    ],
    ids=[
        "unsupported-document-type",
        "invalid-document",
        "encrypted-document",
        "text-extraction-failed",
        "text-not-found",
        "document-file-not-found",
        "document-read-failed",
    ],
)
def test_file_processing_request_maps_document_parser_error(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
    document_chunker: StubDocumentChunker,
    chunk_embedder: StubChunkEmbedder,
    file_indexing_service: StubFileIndexingService,
    parser_error: DocumentParserError,
    expected_status_code: int,
    expected_code: str,
    expected_message: str,
) -> None:
    """문서 파서 예외를 공통 API 오류 응답으로 변환한다."""

    pdf_document_parser.error = parser_error

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == expected_status_code
    assert response.json() == {
        "success": False,
        "code": expected_code,
        "message": expected_message,
        "data": None,
    }

    # 파싱 실패 이후 단계는 실행하지 않아야 한다.
    assert document_chunker.received_documents == []
    assert chunk_embedder.received_documents == []
    assert file_indexing_service.received_documents == []

    assert len(pdf_document_parser.received_file_paths) == 1
    parsed_file_path = pdf_document_parser.received_file_paths[0]

    # 파서가 예외를 발생시켜도 다운로더의 finally가 실행되어
    # 다운로드한 임시 파일이 남지 않아야 한다.
    assert not parsed_file_path.exists()


@pytest.mark.parametrize(
    (
        "chunking_error",
        "expected_status_code",
        "expected_code",
        "expected_message",
    ),
    [
        (
            NoDocumentChunksError(DocumentType.PDF),
            422,
            "DOCUMENT_CHUNKS_NOT_FOUND",
            ("No searchable text chunks could be created from the document."),
        ),
        (
            InvalidChunkingConfigurationError(
                chunk_size_chars=0,
                chunk_overlap_chars=0,
            ),
            500,
            "DOCUMENT_CHUNKING_FAILED",
            "The document could not be chunked.",
        ),
        (
            DocumentChunkingError("Unexpected chunking failure."),
            500,
            "DOCUMENT_CHUNKING_FAILED",
            "The document could not be chunked.",
        ),
    ],
    ids=[
        "no-document-chunks",
        "invalid-chunking-configuration",
        "unexpected-chunking-error",
    ],
)
def test_file_processing_request_maps_document_chunking_error(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
    document_chunker: StubDocumentChunker,
    chunk_embedder: StubChunkEmbedder,
    file_indexing_service: StubFileIndexingService,
    chunking_error: DocumentChunkingError,
    expected_status_code: int,
    expected_code: str,
    expected_message: str,
) -> None:
    """문서 청킹 예외를 공통 API 오류 응답으로 변환한다."""

    document_chunker.error = chunking_error

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == expected_status_code
    assert response.json() == {
        "success": False,
        "code": expected_code,
        "message": expected_message,
        "data": None,
    }

    # 청킹은 실행되지만 실패 이후 임베딩과 저장 단계는 실행하지 않아야 한다.
    assert len(document_chunker.received_documents) == 1
    assert chunk_embedder.received_documents == []
    assert file_indexing_service.received_documents == []

    # 청킹은 다운로드 context 종료 후 실행되므로
    # 청킹 오류가 발생하더라도 임시 파일은 이미 삭제되어 있어야 한다.
    assert len(pdf_document_parser.received_file_paths) == 1
    assert not pdf_document_parser.received_file_paths[0].exists()


@pytest.mark.parametrize(
    (
        "embedding_error",
        "expected_status_code",
        "expected_code",
        "expected_message",
    ),
    [
        (
            EmbeddingServiceTimeoutError(),
            504,
            "EMBEDDING_SERVICE_TIMEOUT",
            "The embedding service request timed out.",
        ),
        (
            EmbeddingServiceUnavailableError(),
            503,
            "EMBEDDING_SERVICE_UNAVAILABLE",
            "The embedding service is temporarily unavailable.",
        ),
        (
            EmbeddingServiceUnavailableError(
                status_code=503,
            ),
            503,
            "EMBEDDING_SERVICE_UNAVAILABLE",
            "The embedding service is temporarily unavailable.",
        ),
        (
            EmbeddingServiceRejectedError(
                status_code=422,
            ),
            502,
            "EMBEDDING_REQUEST_REJECTED",
            "The embedding service rejected the request.",
        ),
        (
            InvalidEmbeddingResponseError(
                reason="vector dimension mismatch",
                batch_start_index=0,
            ),
            502,
            "INVALID_EMBEDDING_RESPONSE",
            "The embedding service returned an invalid response.",
        ),
        (
            EmbeddingError("Unexpected embedding failure."),
            500,
            "EMBEDDING_GENERATION_FAILED",
            "The document embeddings could not be generated.",
        ),
    ],
    ids=[
        "embedding-timeout",
        "embedding-connection-failed",
        "embedding-service-unavailable",
        "embedding-request-rejected",
        "invalid-embedding-response",
        "unexpected-embedding-error",
    ],
)
def test_file_processing_request_maps_embedding_error(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
    document_chunker: StubDocumentChunker,
    chunk_embedder: StubChunkEmbedder,
    file_indexing_service: StubFileIndexingService,
    embedding_error: EmbeddingError,
    expected_status_code: int,
    expected_code: str,
    expected_message: str,
) -> None:
    """임베딩 계층 예외를 공통 API 오류 응답으로 변환한다."""

    chunk_embedder.error = embedding_error

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == expected_status_code
    assert response.json() == {
        "success": False,
        "code": expected_code,
        "message": expected_message,
        "data": None,
    }

    # 임베딩 오류 시점에는 파싱과 청킹이 모두 완료되어야 한다.
    assert len(document_chunker.received_documents) == 1
    assert len(chunk_embedder.received_documents) == 1
    assert file_indexing_service.received_documents == []

    # TEI 통신은 다운로드 context 종료 후 수행하므로
    # 임베딩 오류가 발생해도 임시 파일이 남지 않아야 한다.
    assert len(pdf_document_parser.received_file_paths) == 1
    assert not pdf_document_parser.received_file_paths[0].exists()


@pytest.mark.parametrize(
    (
        "storage_error",
        "expected_status_code",
        "expected_code",
        "expected_message",
    ),
    [
        (
            LocalRagStorageError("prepare_indexing"),
            500,
            "LOCAL_RAG_STORAGE_FAILED",
            ("The document index could not be stored in the Local RAG database."),
        ),
        (
            VectorDatabaseUnavailableError(
                "upsert_document",
                status_code=503,
            ),
            503,
            "VECTOR_DATABASE_UNAVAILABLE",
            "The vector database is temporarily unavailable.",
        ),
        (
            VectorDatabaseRejectedError(
                "upsert_document",
                status_code=400,
            ),
            502,
            "VECTOR_STORAGE_FAILED",
            "The document vectors could not be stored.",
        ),
        (
            VectorCollectionConfigurationError("embedding_dim_mismatch"),
            502,
            "VECTOR_STORAGE_FAILED",
            "The document vectors could not be stored.",
        ),
    ],
    ids=[
        "local-rag-storage-failed",
        "vector-database-unavailable",
        "vector-database-rejected",
        "vector-collection-configuration-invalid",
    ],
)
def test_file_processing_request_maps_index_storage_error(
    file_processing_client: TestClient,
    pdf_document_parser: StubPdfDocumentParser,
    document_chunker: StubDocumentChunker,
    chunk_embedder: StubChunkEmbedder,
    file_indexing_service: StubFileIndexingService,
    storage_error: LocalRagStorageError | VectorDatabaseError,
    expected_status_code: int,
    expected_code: str,
    expected_message: str,
) -> None:
    """Local RAG DB와 Qdrant 저장 예외를 공통 API 오류로 변환한다."""

    file_indexing_service.error = storage_error

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=VALID_FILE_PROCESSING_REQUEST,
    )

    assert response.status_code == expected_status_code
    assert response.json() == {
        "success": False,
        "code": expected_code,
        "message": expected_message,
        "data": None,
    }

    # 저장 오류는 파싱, 청킹 및 임베딩이 완료된 뒤 발생한다.
    assert len(document_chunker.received_documents) == 1
    assert len(chunk_embedder.received_documents) == 1
    assert len(file_indexing_service.received_documents) == 1

    # 저장 단계에서도 Presigned URL의 임시 파일은 이미 삭제되어야 한다.
    assert len(pdf_document_parser.received_file_paths) == 1
    assert not pdf_document_parser.received_file_paths[0].exists()


def test_file_processing_request_rejects_non_positive_url_expires_in(
    file_processing_client: TestClient,
) -> None:
    """0 이하의 Presigned URL 유효 시간을 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["url_expires_in"] = 0

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["message"] == "Request validation failed."
    assert body["data"]["errors"][0]["field"] == "body.url_expires_in"


def test_file_processing_request_rejects_non_https_url(
    file_processing_client: TestClient,
) -> None:
    """HTTPS가 아닌 다운로드 URL을 요청 검증 단계에서 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["download_url"] = "http://example-bucket.s3.ap-northeast-2.amazonaws.com/file.pdf"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["data"]["errors"][0]["field"] == "body.download_url"


def test_file_processing_request_rejects_unsupported_file_type(
    file_processing_client: TestClient,
) -> None:
    """현재 요청 스키마에서 허용하지 않는 파일 타입을 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_name"] = "project-guide.docx"
    request_body["file_type"] = "docx"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["data"]["errors"][0]["field"] == "body.file_type"


def test_file_processing_request_normalizes_uppercase_pdf_type(
    file_processing_client: TestClient,
) -> None:
    """대문자 PDF 파일 타입을 외부 응답의 소문자 pdf로 정규화한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_type"] = "PDF"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    assert response.status_code == 200
    assert response.json()["data"]["file_type"] == "pdf"


def test_file_processing_request_rejects_pdf_without_pdf_extension(
    file_processing_client: TestClient,
) -> None:
    """PDF 타입과 파일명 확장자가 일치하지 않으면 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_name"] = "project-guide.txt"
    request_body["file_type"] = "pdf"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"


def test_file_processing_request_rejects_file_name_with_path(
    file_processing_client: TestClient,
) -> None:
    """디렉터리 경로가 포함된 파일명을 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["file_name"] = "../project-guide.pdf"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["data"]["errors"][0]["field"] == "body.file_name"


def test_file_processing_request_rejects_non_positive_identifiers(
    file_processing_client: TestClient,
) -> None:
    """0 이하의 사용자 및 파일 식별자를 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["user_idx"] = 0
    request_body["file_idx"] = -1

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()
    invalid_fields = {error["field"] for error in body["data"]["errors"]}

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert "body.user_idx" in invalid_fields
    assert "body.file_idx" in invalid_fields


def test_file_processing_request_rejects_unknown_field(
    file_processing_client: TestClient,
) -> None:
    """API 계약에 정의되지 않은 요청 필드를 거부한다."""

    request_body = VALID_FILE_PROCESSING_REQUEST.copy()
    request_body["unknown_field"] = "unexpected-value"

    response = file_processing_client.post(
        "/api/v1/files/process",
        json=request_body,
    )

    body = response.json()

    assert response.status_code == 422
    assert body["success"] is False
    assert body["code"] == "REQUEST_VALIDATION_FAILED"
    assert body["data"]["errors"][0]["field"] == "body.unknown_field"
