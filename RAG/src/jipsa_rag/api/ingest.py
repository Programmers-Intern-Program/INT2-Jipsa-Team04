"""애플리케이션 서버가 전달한 파일 인제스트 요청을 수신한다."""

import logging
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from jipsa_rag.api.internal_auth import verify_rag_ingest_token
from jipsa_rag.api.v1.endpoints.file_processing import (
    ChunkEmbedderDependency,
    DatabaseSessionDependency,
    DocumentChunkerDependency,
    DocumentParserFactoryDependency,
    FileDownloaderDependency,
    FileIndexingServiceDependency,
    FileIndexLockDependency,
    process_file_processing_request,
)
from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException
from jipsa_rag.infrastructure.app_server.ingest_client import (
    ApplicationServerIngestClient,
)
from jipsa_rag.infrastructure.indexing.active_chunk_repository import (
    LocalRagActiveChunkRepository,
)
from jipsa_rag.infrastructure.indexing.chunk_snapshot_models import (
    IndexedDocumentSnapshot,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    LocalRagStorageError,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
)
from jipsa_rag.schemas.file_processing import (
    FileProcessingCompletedResponse,
    FileProcessingRequest,
)
from jipsa_rag.schemas.ingestion import (
    ChunkSynchronizationRequest,
)
from jipsa_rag.services.active_chunk_snapshot import (
    ActiveChunkSnapshotService,
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
    tags=[
        "Ingestion",
    ],
    dependencies=[
        Depends(
            verify_rag_ingest_token,
        ),
    ],
)


# 현재 환경의 애플리케이션 서버 설정을 주입받는다.
SettingsDependency = Annotated[
    Settings,
    Depends(
        get_settings,
    ),
]


def get_application_server_ingest_client(
    settings: SettingsDependency,
) -> ApplicationServerIngestClient:
    """현재 환경 설정이 적용된 애플리케이션 서버 클라이언트를 생성한다."""

    return ApplicationServerIngestClient(
        settings,
    )


# API 테스트에서는 이 의존성을 교체하여 실제 백엔드 서버 없이
# manifest 재조회와 완료 콜백 흐름을 검증할 수 있다.
ApplicationServerIngestClientDependency = Annotated[
    ApplicationServerIngestClient,
    Depends(
        get_application_server_ingest_client,
    ),
]


def get_active_chunk_snapshot_service(
    database_session: DatabaseSessionDependency,
    file_index_lock: FileIndexLockDependency,
) -> ActiveChunkSnapshotService:
    """Local RAG DB와 File_IDX lock이 적용된 스냅샷 서비스를 생성한다.

    FileIndexingServiceDependency와 동일한 하위 의존성을 사용한다.

    FastAPI는 동일 요청 안에서 기본적으로 의존성 결과를 캐시하므로
    DB 세션과 File_IDX advisory lock 객체를 불필요하게 중복 생성하지 않고
    같은 요청 범위의 객체를 재사용한다.

    활성 청크 조회부터 성공 콜백 전송이 끝날 때까지 같은 File_IDX lock을
    유지하여 재색인과 성공 콜백 스냅샷이 서로 교차하지 않게 한다.
    """

    return ActiveChunkSnapshotService(
        repository=LocalRagActiveChunkRepository(
            database_session,
        ),
        file_lock=file_index_lock,
    )


# API 단위 테스트에서는 이 의존성을 Stub으로 교체하여 실제 MySQL 없이
# 최신 활성 청크 조회와 성공 콜백 연결을 검증할 수 있다.
ActiveChunkSnapshotServiceDependency = Annotated[
    ActiveChunkSnapshotService,
    Depends(
        get_active_chunk_snapshot_service,
    ),
]


def _build_callback_error_message(
    error: Exception,
) -> str:
    """백엔드 실패 콜백에 전달할 안전한 오류 메시지를 생성한다.

    예외 원문에는 SQL, 파일 경로, Presigned URL, 내부 호스트 주소 또는
    라이브러리 오류 상세가 포함될 수 있으므로 그대로 전달하지 않는다.
    """

    if isinstance(
        error,
        AppException,
    ):
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
    """원본 처리 예외를 보존하면서 청크 없는 실패 콜백을 전송한다.

    실패 콜백은 ``success``와 외부 공개용 ``error_message``만 전달한다.

    ``index_version``, ``chunk_count`` 및 ``chunks``를 인자로 넘기지 않으므로
    부분 생성되었거나 이전 색인에 속한 청크가 실패 payload에 포함되지 않는다.

    실패 콜백 자체가 실패해도 원래 파일 처리 예외를 덮어쓰지 않는다.
    """

    try:
        await client.notify_ingest_complete(
            file_idx=file_idx,
            success=False,
            error_message=_build_callback_error_message(
                processing_error,
            ),
        )

    except Exception as callback_error:
        # 콜백 오류를 다시 발생시키면 원래 다운로드, 파싱, 청킹,
        # 임베딩, 저장 또는 스냅샷 조회 오류가 유실될 수 있다.
        #
        # 따라서 콜백 오류는 안전한 진단 정보만 로그로 남기고
        # 상위 호출자에게는 원래 처리 오류를 전달한다.
        logger.exception(
            "Failed to report ingestion failure to the application server.",
            extra={
                "event": "ingest_failure_callback_failed",
                "file_idx": file_idx,
                "callback_error_type": (
                    type(
                        callback_error,
                    ).__name__
                ),
            },
        )


def _build_chunk_synchronization_requests(
    snapshot: IndexedDocumentSnapshot,
) -> tuple[
    ChunkSynchronizationRequest,
    ...,
]:
    """최신 활성 청크 스냅샷을 성공 콜백 DTO로 변환한다.

    전달 필드는 Chunk ID, 문서 내 순번, 원문, 해시, 토큰 수와
    출처 메타데이터로 제한한다.

    임베딩 벡터, Presigned URL, 내부 인증 토큰 및 DB 접속 정보는
    IndexedDocumentSnapshot에 존재하지 않으므로 payload에 포함될 수 없다.
    """

    callback_chunks: list[ChunkSynchronizationRequest] = []

    try:
        for chunk in snapshot.chunks:
            callback_chunks.append(
                ChunkSynchronizationRequest(
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.chunk_index,
                    # 원문과 Content_Hash의 일치를 보존하기 위해
                    # strip()이나 줄바꿈 정규화를 수행하지 않는다.
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    token_count=chunk.token_count,
                    # 스냅샷의 읽기 전용 Mapping을 DTO가 소유하는 일반 dict로
                    # 복사한다. 이후 원본 스냅샷은 변경되지 않는다.
                    source_metadata=dict(
                        chunk.source_metadata,
                    ),
                )
            )

    except ValidationError:
        # Pydantic ValidationError는 입력값에 청크 원문을 포함할 수 있다.
        #
        # 원문이 예외 체인이나 로그에 노출되지 않도록 원인 예외를 연결하지
        # 않고 안전한 애플리케이션 오류로 변환한다.
        raise AppException(
            ErrorCode.LOCAL_RAG_STORAGE_FAILED,
            log_context={
                "operation": ("build_chunk_synchronization_requests"),
                "users_idx": snapshot.users_idx,
                "file_idx": snapshot.file_idx,
                "rag_document_idx": snapshot.rag_document_idx,
                "validation": ("invalid_active_chunk_callback_schema"),
            },
        ) from None

    normalized_chunks = tuple(
        callback_chunks,
    )

    # 모델과 저장소 계층에서 이미 개수를 검증하지만 API 경계에서도
    # 한 번 더 확인하여 DTO 변환 과정에서 청크가 누락되는 회귀를 차단한다.
    if len(normalized_chunks) != snapshot.chunk_count:
        raise AppException(
            ErrorCode.LOCAL_RAG_STORAGE_FAILED,
            log_context={
                "operation": ("build_chunk_synchronization_requests"),
                "users_idx": snapshot.users_idx,
                "file_idx": snapshot.file_idx,
                "rag_document_idx": snapshot.rag_document_idx,
                "declared_chunk_count": snapshot.chunk_count,
                "actual_chunk_count": len(
                    normalized_chunks,
                ),
                "validation": ("chunk_count_mismatch_after_conversion"),
            },
        )

    return normalized_chunks


def _validate_snapshot_scope(
    *,
    snapshot: IndexedDocumentSnapshot,
    users_idx: int,
    file_idx: int,
) -> None:
    """조회된 최신 스냅샷이 현재 파일 범위와 일치하는지 검증한다."""

    if snapshot.users_idx == users_idx and snapshot.file_idx == file_idx:
        return

    raise AppException(
        ErrorCode.LOCAL_RAG_STORAGE_FAILED,
        log_context={
            "operation": ("validate_latest_active_snapshot_scope"),
            "users_idx": users_idx,
            "file_idx": file_idx,
            "snapshot_users_idx": snapshot.users_idx,
            "snapshot_file_idx": snapshot.file_idx,
            "snapshot_rag_document_idx": (snapshot.rag_document_idx),
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
            "description": ("지원하지 않는 MIME 유형 또는 문서 파서"),
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
            "description": (
                "문서 읽기, 청킹, 임베딩, Local RAG DB 저장, 활성 청크 조회 또는 내부 처리 실패"
            ),
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
    active_chunk_snapshot_service: (ActiveChunkSnapshotServiceDependency),
    application_server_client: (ApplicationServerIngestClientDependency),
) -> ApiResponse[FileProcessingCompletedResponse]:
    """최신 manifest로 파일을 처리하고 최신 활성 청크 전체를 통지한다.

    POST /ingest 요청 본문은 파일 처리 시작을 알리는 핸드오프 역할을 한다.

    실제 처리 직전에는 file_idx를 사용하여 백엔드에서 manifest를
    다시 조회한다. 이를 통해 핸드오프 이후 파일명, 폴더 또는
    Presigned URL이 갱신된 경우 최신 값을 사용할 수 있다.

    성공 콜백 단계에서는 특정 처리 실행의 문서를 다시 읽지 않는다.
    파일 범위의 최신 SUCCESS 실행이 소유한 활성 문서와 전체 청크를 조회한다.

    따라서 이전 요청이 재색인보다 늦게 콜백 단계에 도착하더라도
    이전 청크가 아니라 콜백 시점의 최신 청크 전체가 전달된다.
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
        # process_file_processing_request()의 반환값은
        # FileProcessingCompletedResponse 자체가 아니라
        # ApiResponse[FileProcessingCompletedResponse]다.
        processing_response = await process_file_processing_request(
            request=latest_manifest,
            file_downloader=file_downloader,
            document_parser_factory=(document_parser_factory),
            document_chunker=document_chunker,
            chunk_embedder=chunk_embedder,
            file_indexing_service=(file_indexing_service),
        )

        # 실제 색인 결과 필드는 ApiResponse.data 안에 존재한다.
        #
        # data가 없는 성공 응답은 내부 계약 위반이므로 콜백 전에 거부한다.
        processing_data = processing_response.data

        if processing_data is None:
            # 원본 파일 정보나 청크 내용을 로그에 남기지 않고
            # 안전한 식별 정보와 검증 사유만 기록한다.
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                log_context={
                    "operation": ("validate_file_processing_response"),
                    "users_idx": (latest_manifest.user_idx),
                    "file_idx": (latest_manifest.file_idx),
                    "validation": ("missing_processing_response_data"),
                },
            )

    except Exception as processing_error:
        # 다운로드 이후 파싱, 청킹, 임베딩 또는 저장이 실패하면
        # 백엔드 File 상태를 FAILED로 전환할 수 있도록 실패 콜백을 보낸다.
        #
        # 실패 콜백에는 index_version, chunk_count 또는 chunks를 전달하지 않는다.
        await _notify_ingest_failure_safely(
            client=application_server_client,
            file_idx=latest_manifest.file_idx,
            processing_error=processing_error,
        )

        raise

    # 성공 콜백 호출이 시작된 뒤 발생한 오류에는 실패 콜백을 추가로 보내지 않는다.
    #
    # 성공 콜백 네트워크 오류 뒤 실패 콜백을 보내면 백엔드가 성공 요청을
    # 실제로 반영했는지 알 수 없는 상태에서 FAILED로 덮어쓸 수 있기 때문이다.
    success_callback_started = False

    try:
        try:
            # 최신 활성 청크 조회와 성공 콜백 전송을 같은 File_IDX lock 안에서
            # 수행한다. 이 블록이 끝날 때까지 같은 파일의 다음 재색인은
            # 색인 임계 구역에 진입할 수 없다.
            async with active_chunk_snapshot_service.hold_latest_active_snapshot(
                users_idx=latest_manifest.user_idx,
                file_idx=latest_manifest.file_idx,
            ) as active_snapshot:
                _validate_snapshot_scope(
                    snapshot=active_snapshot,
                    users_idx=latest_manifest.user_idx,
                    file_idx=latest_manifest.file_idx,
                )

                # 저장소는 특정 processing_data.rag_document_idx가 아니라
                # 파일 범위의 최신 SUCCESS 실행을 기준으로 스냅샷을 반환한다.
                #
                # 따라서 더 최신 재색인이 먼저 완료된 경우에도 이전 실행의
                # 청크 수나 문서 식별자와 비교하지 않고 최신 스냅샷 자체를
                # 성공 payload의 단일 진실 공급원으로 사용한다.
                callback_chunks = _build_chunk_synchronization_requests(
                    active_snapshot,
                )

                # chunk_count는 ApplicationServerIngestClient가
                # callback_chunks의 실제 길이로 계산하며
                # IngestCompleteRequest가 다시 검증한다.
                #
                # 동일 인제스트 요청은 결정적 UUIDv5 Chunk_ID를 재사용하므로
                # 같은 최신 스냅샷을 여러 번 전달해도 식별자는 변하지 않는다.
                success_callback_started = True

                await application_server_client.notify_ingest_complete(
                    file_idx=(latest_manifest.file_idx),
                    success=True,
                    index_version=(active_snapshot.index_version),
                    chunks=callback_chunks,
                )

        except LocalRagStorageError as error:
            # SQL, 연결 정보 또는 청크 원문을 노출하지 않고 기존 공통 오류로
            # 변환한다. 성공 콜백이 아직 시작되지 않았다면 아래 외부 except에서
            # 청크 없는 실패 콜백을 전달한다.
            raise AppException(
                ErrorCode.LOCAL_RAG_STORAGE_FAILED,
                log_context={
                    "operation": error.operation,
                    "users_idx": (latest_manifest.user_idx),
                    "file_idx": (latest_manifest.file_idx),
                    "processing_rag_document_idx": (processing_data.rag_document_idx),
                },
            ) from error

    except Exception as synchronization_error:
        if not success_callback_started:
            # 최신 활성 스냅샷 조회 또는 payload 생성 실패는 처리 실패로
            # 통지하되, 실패 payload에는 청크 동기화 필드를 포함하지 않는다.
            await _notify_ingest_failure_safely(
                client=application_server_client,
                file_idx=latest_manifest.file_idx,
                processing_error=synchronization_error,
            )

        # 성공 콜백 자체가 실패한 경우에는 같은 서버에 실패 콜백을
        # 재전송하지 않고 원래 콜백 오류를 호출자에게 전달한다.
        raise

    # 기존 /ingest 응답 계약은 그대로 유지한다.
    #
    # 내부 성공 콜백은 콜백 시점의 최신 활성 스냅샷을 사용하지만,
    # API 호출자에게는 현재 요청의 파일 처리 응답을 그대로 반환한다.
    return processing_response
