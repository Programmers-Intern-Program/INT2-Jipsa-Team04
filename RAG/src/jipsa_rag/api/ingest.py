"""애플리케이션 서버가 전달한 파일 인제스트 요청을 수신한다."""

from http import HTTPStatus

from fastapi import APIRouter, Depends

from jipsa_rag.api.internal_auth import verify_rag_ingest_token
from jipsa_rag.api.v1.endpoints.file_processing import (
    ChunkEmbedderDependency,
    DocumentChunkerDependency,
    DocumentParserFactoryDependency,
    FileDownloaderDependency,
    FileIndexingServiceDependency,
    process_file_processing_request,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
)
from jipsa_rag.schemas.file_processing import (
    FileProcessingCompletedResponse,
    FileProcessingRequest,
)

# 백엔드 RagIngestClient는 RAG_BASE_URL 뒤에 "/ingest"를 직접 붙여 호출한다.
#
# 따라서 이 라우터에는 /api/v1 prefix를 적용하지 않고
# main.py에서 애플리케이션 루트 라우터로 직접 등록한다.
#
# 라우터 공통 dependency로 내부 토큰 검증을 지정하여
# 요청 본문 처리나 파일 다운로드가 시작되기 전에 인증을 수행한다.
router = APIRouter(
    tags=["Ingestion"],
    dependencies=[
        Depends(verify_rag_ingest_token),
    ],
)


@router.post(
    "/ingest",
    status_code=HTTPStatus.OK,
    response_model=ApiResponse[FileProcessingCompletedResponse],
    summary="RAG 파일 인제스트",
    description=(
        "애플리케이션 서버가 전달한 파일 메타데이터와 Presigned GET URL을 "
        "사용하여 원본 파일 다운로드, 문서 파싱, 청킹, 임베딩 생성, "
        "Local RAG DB 저장 및 Qdrant 색인을 수행한다. "
        "요청에는 X-Internal-Token 헤더가 필요하다."
    ),
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": ApiResponse[None],
            "description": "내부 토큰 누락 또는 불일치",
        },
        HTTPStatus.BAD_REQUEST: {
            "model": ApiResponse[None],
            "description": "다운로드 URL 검증 실패",
        },
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE: {
            "model": ApiResponse[None],
            "description": "최대 허용 파일 크기 초과",
        },
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: {
            "model": ApiResponse[None],
            "description": "지원하지 않는 MIME 유형 또는 문서 파서",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": ("요청값, 문서 형식, 문서 텍스트 또는 검색 가능 청크 검증 실패"),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": ("원본 파일 다운로드, 임베딩 서비스 응답 또는 VectorDB 저장 실패"),
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": ("내부 인제스트 토큰 미설정 또는 외부 의존 서비스 사용 불가"),
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 또는 임베딩 요청 시간 초과",
        },
        HTTPStatus.INTERNAL_SERVER_ERROR: {
            "model": ApiResponse[None],
            "description": ("문서 읽기, 청킹, 임베딩, Local RAG DB 저장 또는 내부 처리 실패"),
        },
    },
)
async def ingest_file(
    request: FileProcessingRequest,
    file_downloader: FileDownloaderDependency,
    document_parser_factory: DocumentParserFactoryDependency,
    document_chunker: DocumentChunkerDependency,
    chunk_embedder: ChunkEmbedderDependency,
    file_indexing_service: FileIndexingServiceDependency,
) -> ApiResponse[FileProcessingCompletedResponse]:
    """기존 파일 처리 파이프라인을 재사용하여 인제스트를 수행한다.

    POST /ingest와 기존 POST /api/v1/files/process가 서로 다른
    다운로드, 파싱, 청킹 또는 저장 구현을 갖게 되면 예외 처리와
    데이터 저장 결과가 달라질 수 있다.

    따라서 실제 처리 로직은 기존 process_file_processing_request()에
    위임하고 이 엔드포인트는 내부 인증과 외부 경로 계약만 담당한다.
    """

    return await process_file_processing_request(
        request=request,
        file_downloader=file_downloader,
        document_parser_factory=document_parser_factory,
        document_chunker=document_chunker,
        chunk_embedder=chunk_embedder,
        file_indexing_service=file_indexing_service,
    )
