"""애플리케이션 서버에서 전달한 다중 형식 문서를 처리하고 색인 결과를 반환한다."""

import logging
from http import HTTPStatus
from time import perf_counter
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.document_processing import (
    DocumentProcessingSettings,
    get_document_processing_settings,
)
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.chunking.exceptions import (
    DocumentChunkingError,
    InvalidChunkingConfigurationError,
    NoDocumentChunksError,
)
from jipsa_rag.infrastructure.chunking.models import (
    ChunkedDocument,
    ChunkingContext,
)
from jipsa_rag.infrastructure.chunking.structured import StructuredDocumentChunker
from jipsa_rag.infrastructure.database.session import engine, get_db_session
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
from jipsa_rag.infrastructure.document.models import ParsedDocument
from jipsa_rag.infrastructure.document.parser_factory import DocumentParserFactory
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingError,
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.embedding.tei import TeiChunkEmbedder
from jipsa_rag.infrastructure.file.downloader import HttpFileDownloader
from jipsa_rag.infrastructure.indexing.concurrent_repository import (
    ConcurrentSafeLocalRagIndexRepository,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
    VectorCollectionConfigurationError,
    VectorDatabaseError,
    VectorDatabaseRejectedError,
    VectorDatabaseUnavailableError,
)
from jipsa_rag.infrastructure.indexing.file_lock import MySqlAdvisoryFileIndexLock
from jipsa_rag.infrastructure.indexing.models import DocumentIndexMetadata
from jipsa_rag.infrastructure.indexing.qdrant_store import (
    QdrantChunkVectorStore,
    get_qdrant_vector_store,
)
from jipsa_rag.schemas.common import ApiResponse, ValidationErrorData
from jipsa_rag.schemas.file_processing import (
    FileProcessingCompletedResponse,
    FileProcessingRequest,
)
from jipsa_rag.services.file_indexing import FileIndexingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["File Processing"])

SettingsDependency = Annotated[Settings, Depends(get_settings)]
DocumentProcessingSettingsDependency = Annotated[
    DocumentProcessingSettings,
    Depends(get_document_processing_settings),
]
DatabaseSessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_file_downloader(settings: SettingsDependency) -> HttpFileDownloader:
    """현재 환경 설정이 적용된 스트리밍 파일 다운로더를 생성한다."""

    return HttpFileDownloader(settings)


FileDownloaderDependency = Annotated[
    HttpFileDownloader,
    Depends(get_file_downloader),
]


def get_document_parser_factory(request: Request) -> DocumentParserFactory:
    """현재 FastAPI lifespan이 소유한 공유 문서 Parser Factory를 반환한다.

    Factory를 요청마다 새로 만들면 EasyOCR Reader, CUDA 모델과 OCR semaphore도 요청별로
    복제된다. lifespan에서 생성해 ``app.state``에 저장한 단일 Factory를 반환하여 PDF,
    DOCX, PPTX, XLSX의 OCR worker pool과 동시성 제한을 모든 요청이 공유하게 한다.

    lifespan 밖에서 dependency를 직접 호출한 잘못된 실행은 새 Factory를 묵시적으로
    생성하지 않고 실패시킨다. 이를 통해 종료되지 않는 CUDA worker가 생기는 경로를
    차단한다. API 테스트는 기존과 동일하게 dependency override로 Stub Factory를 주입할
    수 있다.
    """

    factory = getattr(request.app.state, "document_parser_factory", None)
    if not isinstance(factory, DocumentParserFactory):
        raise RuntimeError("DocumentParserFactory is unavailable outside application lifespan.")
    return factory


DocumentParserFactoryDependency = Annotated[
    DocumentParserFactory,
    Depends(get_document_parser_factory),
]


class DocumentChunker(Protocol):
    """파일 처리 API가 요구하는 최소 비동기 청킹 계약이다.

    운영 의존성은 ``StructuredDocumentChunker``를 반환하지만 기존 PDF 회귀 테스트와
    일부 내부 호출은 ``CharacterTextChunker``를 주입한다. 두 구현체 모두 동일한
    ``chunk()`` 시그니처를 제공하므로 구체 클래스 대신 Protocol을 사용하여 기존
    주입 가능성과 다중 형식 구조화 청킹을 함께 보존한다.
    """

    async def chunk(
        self,
        *,
        document: ParsedDocument,
        context: ChunkingContext,
    ) -> ChunkedDocument:
        """파싱 문서를 검색 가능한 결정적 청크 집합으로 변환한다."""

        ...


def get_document_chunker(
    processing_settings: DocumentProcessingSettingsDependency,
) -> StructuredDocumentChunker:
    """형식별 구조 전략과 기존 결정적 ID 규칙을 결합한 청커를 생성한다."""

    return StructuredDocumentChunker(
        chunk_size_chars=processing_settings.chunk_size_chars,
        chunk_overlap_chars=processing_settings.chunk_overlap_chars,
    )


DocumentChunkerDependency = Annotated[
    DocumentChunker,
    Depends(get_document_chunker),
]


def get_chunk_embedder(settings: SettingsDependency) -> TeiChunkEmbedder:
    """현재 환경 설정이 적용된 CUDA TEI 청크 임베더를 생성한다."""

    return TeiChunkEmbedder(settings)


ChunkEmbedderDependency = Annotated[
    TeiChunkEmbedder,
    Depends(get_chunk_embedder),
]

QdrantVectorStoreDependency = Annotated[
    QdrantChunkVectorStore,
    Depends(get_qdrant_vector_store),
]


def get_file_index_lock() -> MySqlAdvisoryFileIndexLock:
    """프로세스 간에 공유되는 File_IDX별 MySQL advisory lock을 생성한다."""

    return MySqlAdvisoryFileIndexLock(engine)


FileIndexLockDependency = Annotated[
    MySqlAdvisoryFileIndexLock,
    Depends(get_file_index_lock),
]


def get_file_indexing_service(
    database_session: DatabaseSessionDependency,
    vector_store: QdrantVectorStoreDependency,
    file_lock: FileIndexLockDependency,
) -> FileIndexingService:
    """최신 실행 소유권과 파일 lock이 적용된 색인 서비스를 생성한다."""

    return FileIndexingService(
        local_repository=ConcurrentSafeLocalRagIndexRepository(database_session),
        vector_store=vector_store,
        file_lock=file_lock,
    )


FileIndexingServiceDependency = Annotated[
    FileIndexingService,
    Depends(get_file_indexing_service),
]


def _convert_document_parser_error(
    error: DocumentParserError,
    *,
    users_idx: int,
    file_idx: int,
) -> AppException:
    """문서 파서 예외를 민감 정보가 없는 공통 애플리케이션 예외로 변환한다."""

    log_context: dict[str, str | int] = {
        "users_idx": users_idx,
        "file_idx": file_idx,
        "document_error_type": type(error).__name__,
    }

    if isinstance(error, UnsupportedDocumentTypeError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.UNSUPPORTED_DOCUMENT_TYPE
    elif isinstance(error, EncryptedDocumentError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.ENCRYPTED_DOCUMENT
    elif isinstance(error, InvalidDocumentError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.INVALID_DOCUMENT
    elif isinstance(error, DocumentTextExtractionError):
        log_context["file_type"] = str(error.file_type)
        _copy_safe_document_location(
            source_metadata=error.source_metadata,
            log_context=log_context,
        )
        error_code = ErrorCode.DOCUMENT_TEXT_EXTRACTION_FAILED
    elif isinstance(error, DocumentTextNotFoundError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.DOCUMENT_TEXT_NOT_FOUND
    elif isinstance(error, DocumentFileNotFoundError | DocumentReadError):
        # 임시 파일 전체 경로나 Presigned URL은 로그에 넣지 않는다.
        error_code = ErrorCode.DOCUMENT_READ_FAILED
    else:
        error_code = ErrorCode.INTERNAL_SERVER_ERROR

    return AppException(error_code, log_context=log_context)


def _copy_safe_document_location(
    *,
    source_metadata: dict[str, str | int | float | bool | None],
    log_context: dict[str, str | int],
) -> None:
    """문서 내용 없이 장애 위치를 재현할 수 있는 안전한 필드만 로그에 복사한다."""

    integer_keys = (
        "page_number",
        "slide_number",
        "shape_index",
        "sheet_index",
        "row_number",
        "line_number",
        "section_index",
        "paragraph_index",
        "table_index",
    )
    text_keys = (
        "sheet_name",
        "shape_path",
        "cell_range",
    )

    for key in integer_keys:
        value = source_metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            log_context[key] = value

    for key in text_keys:
        value = source_metadata.get(key)
        if isinstance(value, str) and value.strip():
            # 파일명·문서 내용이 실수로 섞여도 로그 크기가 무한히 증가하지 않게 제한한다.
            log_context[key] = value.strip()[:255]


def _convert_document_chunking_error(
    error: DocumentChunkingError,
    *,
    users_idx: int,
    file_idx: int,
) -> AppException:
    """문서 청킹 예외를 공통 애플리케이션 예외로 변환한다."""

    log_context: dict[str, str | int] = {
        "users_idx": users_idx,
        "file_idx": file_idx,
        "chunking_error_type": type(error).__name__,
    }

    if isinstance(error, NoDocumentChunksError):
        log_context["file_type"] = str(error.file_type)
        error_code = ErrorCode.DOCUMENT_CHUNKS_NOT_FOUND
    elif isinstance(error, InvalidChunkingConfigurationError):
        log_context["chunk_size_chars"] = error.chunk_size_chars
        log_context["chunk_overlap_chars"] = error.chunk_overlap_chars
        error_code = ErrorCode.DOCUMENT_CHUNKING_FAILED
    else:
        error_code = ErrorCode.DOCUMENT_CHUNKING_FAILED

    return AppException(error_code, log_context=log_context)


def _convert_embedding_error(
    error: EmbeddingError,
    *,
    users_idx: int,
    file_idx: int,
) -> AppException:
    """TEI 오류를 응답 본문과 벡터를 노출하지 않는 공통 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "users_idx": users_idx,
        "file_idx": file_idx,
        "embedding_error_type": type(error).__name__,
    }

    if isinstance(error, EmbeddingServiceTimeoutError):
        error_code = ErrorCode.EMBEDDING_SERVICE_TIMEOUT
    elif isinstance(error, EmbeddingServiceUnavailableError):
        if error.status_code is not None:
            log_context["embedding_status_code"] = error.status_code
        error_code = ErrorCode.EMBEDDING_SERVICE_UNAVAILABLE
    elif isinstance(error, EmbeddingServiceRejectedError):
        log_context["embedding_status_code"] = error.status_code
        error_code = ErrorCode.EMBEDDING_REQUEST_REJECTED
    elif isinstance(error, InvalidEmbeddingResponseError):
        log_context["embedding_response_reason"] = error.reason
        log_context["embedding_batch_start_index"] = error.batch_start_index
        error_code = ErrorCode.INVALID_EMBEDDING_RESPONSE
    else:
        error_code = ErrorCode.EMBEDDING_GENERATION_FAILED

    return AppException(error_code, log_context=log_context)


def _convert_index_storage_error(
    error: LocalRagStorageError | VectorDatabaseError,
    *,
    users_idx: int,
    file_idx: int,
) -> AppException:
    """Local RAG DB와 Qdrant 저장 예외를 공통 API 오류로 변환한다."""

    log_context: dict[str, str | int] = {
        "users_idx": users_idx,
        "file_idx": file_idx,
        "index_storage_error_type": type(error).__name__,
        "storage_operation": error.operation,
    }

    if isinstance(error, LocalRagStorageError):
        error_code = ErrorCode.LOCAL_RAG_STORAGE_FAILED
    elif isinstance(error, VectorDatabaseUnavailableError):
        if error.status_code is not None:
            log_context["vector_status_code"] = error.status_code
        error_code = ErrorCode.VECTOR_DATABASE_UNAVAILABLE
    elif isinstance(error, VectorDatabaseRejectedError | VectorCollectionConfigurationError):
        if error.status_code is not None:
            log_context["vector_status_code"] = error.status_code
        error_code = ErrorCode.VECTOR_STORAGE_FAILED
    else:
        if error.status_code is not None:
            log_context["vector_status_code"] = error.status_code
        error_code = ErrorCode.VECTOR_STORAGE_FAILED

    return AppException(error_code, log_context=log_context)


def _calculate_duration_ms(started_at: float) -> float:
    """단계별 처리 시간을 밀리초 단위로 계산한다."""

    return round(
        (perf_counter() - started_at) * 1000,
        3,
    )


@router.post(
    "/process",
    status_code=HTTPStatus.OK,
    response_model=ApiResponse[FileProcessingCompletedResponse],
    summary="다중 형식 RAG 파일 처리",
    description=(
        "애플리케이션 서버에서 PDF, DOCX, PPTX, TXT 또는 XLSX의 다운로드 URL과 "
        "파일 정보를 전달받아 확장자·MIME Type·Magic Byte·OOXML 구조를 검증한다. "
        "문서 구조와 위치를 보존해 파싱·청킹하고 CUDA TEI 임베딩 생성 후 Local RAG "
        "DB와 Qdrant에 동일한 source metadata 계약으로 색인한다."
    ),
    responses={
        HTTPStatus.BAD_REQUEST: {
            "model": ApiResponse[None],
            "description": "다운로드 URL 또는 요청 파일 정보 검증 실패",
        },
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE: {
            "model": ApiResponse[None],
            "description": "최대 허용 파일 크기 초과",
        },
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: {
            "model": ApiResponse[None],
            "description": "지원하지 않는 확장자, MIME 유형 또는 문서 파서",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": ("요청값, 문서 구조, 암호화 여부, 추출 텍스트 또는 청크 검증 실패"),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": "다운로드, 임베딩 응답 또는 VectorDB 저장 실패",
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": "임베딩 서비스 또는 VectorDB 일시적 사용 불가",
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 또는 임베딩 요청 시간 초과",
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": "문서 읽기, 청킹, Local RAG DB 저장 또는 내부 처리 실패",
        },
    },
)
async def process_file_processing_request(
    request: FileProcessingRequest,
    file_downloader: FileDownloaderDependency,
    document_parser_factory: DocumentParserFactoryDependency,
    document_chunker: DocumentChunkerDependency,
    chunk_embedder: ChunkEmbedderDependency,
    file_indexing_service: FileIndexingServiceDependency,
) -> ApiResponse[FileProcessingCompletedResponse]:
    """다운로드부터 구조화 파싱, 임베딩, 이중 저장까지 완료한다.

    문서 처리 설정은 ``get_document_chunker()``와 같은 캐시된 설정 공급자에서
    읽는다. 이 함수는 FastAPI 엔드포인트뿐 아니라 ``POST /ingest`` 내부에서도
    직접 호출되므로, 추가 위치 인자를 강제하지 않아 기존 내부 호출 계약을
    유지한다.

    INFO 로그는 다운로드, 파싱/OCR, 청킹의 완료 시점에만 한 줄씩 기록한다.
    청크 원문, 파일명, Presigned URL 및 임베딩 벡터는 기록하지 않고 식별자,
    개수, 파일 유형과 처리 시간 같은 작은 스칼라 값만 사용하여 로그 비용을 제한한다.
    """

    # 색인 버전은 Chunk ID와 Local RAG 문서 정체성에 사용된다. 환경별 dotenv를
    # 읽는 공급자는 캐시되므로 요청마다 파일을 다시 파싱하지 않는다.
    processing_settings = get_document_processing_settings()

    try:
        # 지원하지 않는 형식은 네트워크 요청 전에 실패시켜 임시 파일과 외부 트래픽을
        # 만들지 않는다. Factory는 다섯 형식의 구체 구현 선택을 캡슐화한다.
        document_parser = document_parser_factory.get_parser(request.file_type)

        download_started_at = perf_counter()

        async with file_downloader.download_and_validate(
            file_url=request.download_url,
            users_idx=request.user_idx,
            file_idx=request.file_idx,
        ) as downloaded_file:
            # async context 진입 시점에는 URL 검증, 재시도, 스트리밍 다운로드,
            # 크기 제한과 SHA-256 계산이 모두 끝난 상태다. 이 지점에서 측정해야
            # 파싱 시간과 분리된 실제 다운로드 단계 시간을 얻을 수 있다.
            file_size_bytes = downloaded_file.size_bytes
            calculated_file_hash = downloaded_file.sha256

            logger.info(
                "File download completed.",
                extra={
                    "event": "file_download_completed",
                    "users_idx": request.user_idx,
                    "file_idx": request.file_idx,
                    "file_type": str(request.file_type),
                    "size_bytes": file_size_bytes,
                    "duration_ms": _calculate_duration_ms(download_started_at),
                },
            )

            parsing_started_at = perf_counter()
            parsed_document = await document_parser.parse(downloaded_file.path)

            # PDF, DOCX, PPTX, XLSX의 이미지 OCR은 각 Parser 내부의 동일 parse 호출에
            # 포함된다. 따라서 Parser가 반환된 직후의 시간은 텍스트 추출과 활성화된
            # OCR 보강 작업을 모두 포함하며, 별도 이미지별 INFO 로그를 만들지 않는다.
            logger.info(
                "Document parsing and OCR phase completed.",
                extra={
                    "event": "document_parsing_ocr_completed",
                    "users_idx": request.user_idx,
                    "file_idx": request.file_idx,
                    "file_type": str(parsed_document.file_type),
                    "parser_type": document_parser.parser_type,
                    "parser_version": document_parser.parser_version,
                    "ocr_enabled": processing_settings.ocr_enabled,
                    "structure_unit_count": parsed_document.unit_count,
                    "text_unit_count": parsed_document.text_unit_count,
                    "duration_ms": _calculate_duration_ms(parsing_started_at),
                },
            )

        # 임시 파일은 파싱 직후 정리하고, 이후 단계는 불변 메모리 모델만 사용한다.
        chunking_started_at = perf_counter()

        chunked_document = await document_chunker.chunk(
            document=parsed_document,
            context=ChunkingContext(
                users_idx=request.user_idx,
                file_idx=request.file_idx,
                file_hash=calculated_file_hash,
                parser_version=document_parser.parser_version,
                embedding_model=chunk_embedder.embedding_model,
                index_version=processing_settings.index_version,
            ),
        )

        logger.info(
            "Document chunking completed.",
            extra={
                "event": "document_chunking_completed",
                "users_idx": request.user_idx,
                "file_idx": request.file_idx,
                "file_type": str(chunked_document.file_type),
                "structure_unit_count": chunked_document.source_unit_count,
                "text_unit_count": chunked_document.text_unit_count,
                "chunk_count": chunked_document.chunk_count,
                "duration_ms": _calculate_duration_ms(chunking_started_at),
            },
        )

        embedded_document = await chunk_embedder.embed(document=chunked_document)

        indexing_result = await file_indexing_service.index(
            metadata=DocumentIndexMetadata(
                users_idx=request.user_idx,
                file_idx=request.file_idx,
                folder_idx=request.folder_idx,
                file_name=request.file_name,
                file_type=parsed_document.file_type,
                file_hash=calculated_file_hash,
                index_version=processing_settings.index_version,
                parser_type=document_parser.parser_type,
                parser_version=document_parser.parser_version,
            ),
            embedded_document=embedded_document,
        )

        response_data = FileProcessingCompletedResponse(
            rag_document_idx=indexing_result.rag_document_idx,
            file_idx=request.file_idx,
            user_idx=request.user_idx,
            folder_idx=request.folder_idx,
            file_name=request.file_name,
            file_type=request.file_type,
            file_size_bytes=file_size_bytes,
            # 기존 API 필드명은 하위 호환성을 위해 page_count로 유지한다. PDF는 실제
            # 페이지 수이며 다른 형식은 파서가 생성한 원본 구조 unit 수다.
            page_count=parsed_document.unit_count,
            text_unit_count=parsed_document.text_unit_count,
            chunk_count=indexing_result.chunk_count,
            embedding_model=embedded_document.embedding_model,
            embedding_dim=embedded_document.embedding_dim,
            processing_status="INDEXED",
        )
    except DocumentParserError as error:
        raise _convert_document_parser_error(
            error,
            users_idx=request.user_idx,
            file_idx=request.file_idx,
        ) from error
    except DocumentChunkingError as error:
        raise _convert_document_chunking_error(
            error,
            users_idx=request.user_idx,
            file_idx=request.file_idx,
        ) from error
    except EmbeddingError as error:
        raise _convert_embedding_error(
            error,
            users_idx=request.user_idx,
            file_idx=request.file_idx,
        ) from error
    except (LocalRagStorageError, VectorDatabaseError) as error:
        raise _convert_index_storage_error(
            error,
            users_idx=request.user_idx,
            file_idx=request.file_idx,
        ) from error

    return ApiResponse[FileProcessingCompletedResponse](
        success=True,
        code="FILE_INDEXING_COMPLETED",
        message="File download, parsing, chunking, embedding, and indexing completed.",
        data=response_data,
    )
