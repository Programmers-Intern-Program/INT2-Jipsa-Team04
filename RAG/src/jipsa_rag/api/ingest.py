"""애플리케이션 서버가 전달한 파일 인제스트 요청을 수신한다."""

import logging
from http import HTTPStatus
from typing import Annotated, Final

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
from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
)
from jipsa_rag.schemas.file_processing import (
    FileProcessingCompletedResponse,
    FileProcessingRequest,
)

logger = logging.getLogger(__name__)


# 백엔드 File.Error_Message는 TEXT 컬럼이지만 지나치게 긴 내부 메시지를
# 콜백으로 전송하지 않도록 애플리케이션 수준에서 길이를 제한한다.
_MAX_CALLBACK_ERROR_MESSAGE_LENGTH: Final[int] = 4000


# 백엔드 RagIngestClient는 RAG_BASE_URL 뒤에 "/ingest"를 직접 붙여 호출한다.
#
# 따라서 이 라우터에는 /api/v1 prefix를 적용하지 않고
# main.py에서 애플리케이션 루트 라우터로 직접 등록한다.
#
# 라우터 공통 dependency로 내부 토큰 검증을 지정하여
# 요청 본문 처리나 외부 API 호출이 시작되기 전에 인증을 수행한다.
router = APIRouter(
    tags=["Ingestion"],
    dependencies=[
        Depends(verify_rag_ingest_token),
    ],
)


# 현재 환경의 애플리케이션 서버 설정을 주입받는다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


def get_application_server_ingest_client(
    settings: SettingsDependency,
) -> ApplicationServerIngestClient:
    """현재 환경 설정이 적용된 애플리케이션 서버 클라이언트를 생성한다."""

    return ApplicationServerIngestClient(settings)


# API 테스트에서는 이 의존성을 교체하여 실제 백엔드 서버 없이
# manifest 재조회와 완료 콜백 흐름을 검증할 수 있다.
ApplicationServerIngestClientDependency = Annotated[
    ApplicationServerIngestClient,
    Depends(get_application_server_ingest_client),
]


def _build_callback_error_message(
    error: Exception,
) -> str:
    """백엔드 실패 콜백에 전달할 안전한 오류 메시지를 생성한다.

    예외 원문에는 SQL, 파일 경로, Presigned URL, 내부 호스트 주소 또는
    라이브러리 오류 상세가 포함될 수 있으므로 그대로 전달하지 않는다.
    """

    if isinstance(error, AppException):
        # AppException.public_message는 외부 API 응답에 공개할 수 있도록
        # 명시적으로 분리된 메시지다.
        #
        # 백엔드가 오류 종류를 구분할 수 있도록 안전한 ErrorCode 문자열을
        # 메시지 앞에 함께 포함한다.
        message = f"{error.code}: {error.public_message}"
    else:
        # 처리되지 않은 예외는 내부 상세를 제거하고 고정 메시지로 변환한다.
        message = "INTERNAL_SERVER_ERROR: An internal RAG processing error occurred."

    return message[:_MAX_CALLBACK_ERROR_MESSAGE_LENGTH]


async def _notify_ingest_failure_safely(
    *,
    client: ApplicationServerIngestClient,
    file_idx: int,
    processing_error: Exception,
) -> None:
    """원본 처리 예외를 보존하면서 실패 콜백을 전송한다.

    실패 콜백 자체가 실패해도 원래 파일 처리 예외를 덮어쓰지 않는다.
    """

    try:
        await client.notify_ingest_complete(
            file_idx=file_idx,
            success=False,
            error_message=_build_callback_error_message(processing_error),
        )

    except Exception as callback_error:
        # 콜백 오류를 다시 발생시키면 원래 다운로드, 파싱, 청킹,
        # 임베딩 또는 저장 오류가 유실될 수 있다.
        #
        # 따라서 콜백 오류는 안전한 진단 정보만 로그로 남기고
        # 상위 호출자에게는 원래 처리 오류를 전달한다.
        logger.exception(
            "Failed to report ingestion failure to the application server.",
            extra={
                "event": "ingest_failure_callback_failed",
                "file_idx": file_idx,
                "callback_error_type": type(callback_error).__name__,
            },
        )


@router.post(
    "/ingest",
    status_code=HTTPStatus.OK,
    response_model=ApiResponse[FileProcessingCompletedResponse],
    summary="RAG 파일 인제스트",
    description=(
        "애플리케이션 서버가 전달한 파일 식별자를 사용하여 최신 manifest를 "
        "재조회한 뒤 원본 파일 다운로드, 문서 파싱, 청킹, 임베딩 생성, "
        "Local RAG DB 저장 및 Qdrant 색인을 수행한다. 처리 결과는 "
        "애플리케이션 서버의 ingest-complete API로 통지한다."
    ),
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": ApiResponse[None],
            "description": "내부 토큰 누락 또는 불일치",
        },
        HTTPStatus.NOT_FOUND: {
            "model": ApiResponse[None],
            "description": "백엔드에서 처리 대상 파일을 찾지 못함",
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
            "description": (
                "애플리케이션 서버 계약 오류, 원본 파일 다운로드, "
                "임베딩 서비스 응답 또는 VectorDB 저장 실패"
            ),
        },
        HTTPStatus.SERVICE_UNAVAILABLE: {
            "model": ApiResponse[None],
            "description": ("내부 토큰 미설정 또는 외부 의존 서비스 사용 불가"),
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": ("애플리케이션 서버, 원본 파일 다운로드 또는 임베딩 요청 시간 초과"),
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
    application_server_client: ApplicationServerIngestClientDependency,
) -> ApiResponse[FileProcessingCompletedResponse]:
    """최신 manifest로 파일을 처리하고 최종 결과를 백엔드에 통지한다.

    POST /ingest 요청 본문은 파일 처리 시작을 알리는 핸드오프 역할을 한다.

    실제 처리 직전에는 file_idx를 사용하여 백엔드에서 manifest를
    다시 조회한다. 이를 통해 핸드오프 이후 파일명, 폴더 또는
    Presigned URL이 갱신된 경우 최신 값을 사용할 수 있다.
    """

    # manifest 조회 실패는 아직 파일 다운로드나 색인 처리가
    # 시작되지 않은 상태다.
    #
    # 동일한 백엔드에 실패 콜백을 다시 전송해도 같은 인증 또는
    # 네트워크 오류가 반복될 가능성이 높으므로 manifest 조회 오류는
    # 그대로 호출자에게 전달한다.
    latest_manifest = await application_server_client.fetch_manifest(
        file_idx=request.file_idx,
    )

    try:
        processing_response = await process_file_processing_request(
            request=latest_manifest,
            file_downloader=file_downloader,
            document_parser_factory=document_parser_factory,
            document_chunker=document_chunker,
            chunk_embedder=chunk_embedder,
            file_indexing_service=file_indexing_service,
        )

    except Exception as processing_error:
        # 파일 다운로드 이후 파싱, 청킹, 임베딩 또는 저장이 실패하면
        # 백엔드 File 상태를 FAILED로 전환할 수 있도록 실패 콜백을 보낸다.
        #
        # 실패 콜백 오류는 원래 처리 예외를 덮어쓰지 않는다.
        await _notify_ingest_failure_safely(
            client=application_server_client,
            file_idx=latest_manifest.file_idx,
            processing_error=processing_error,
        )

        raise

    # Local RAG DB 및 Qdrant 저장이 모두 완료된 뒤에만
    # 백엔드 File 상태를 READY로 전환하는 성공 콜백을 보낸다.
    #
    # 성공 콜백이 실패하면 백엔드 상태가 PROCESSING에 남을 수 있으므로
    # 오류를 숨기지 않고 호출자에게 전달한다.
    await application_server_client.notify_ingest_complete(
        file_idx=latest_manifest.file_idx,
        success=True,
    )

    return processing_response
