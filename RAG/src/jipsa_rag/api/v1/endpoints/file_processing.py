"""애플리케이션 서버에서 전달한 파일 처리 요청을 접수한다."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.infrastructure.file.downloader import (
    HttpFileDownloader,
)
from jipsa_rag.schemas.common import (
    ApiResponse,
    ValidationErrorData,
)
from jipsa_rag.schemas.file_processing import (
    FileProcessingAcceptedResponse,
    FileProcessingRequest,
)

router = APIRouter(
    prefix="/files",
    tags=["File Processing"],
)


# 파일 처리 엔드포인트에서 사용할 Settings 의존성이다.
#
# get_settings()는 환경 설정 객체를 캐싱하므로
# 요청마다 dotenv 파일을 다시 읽지 않는다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


def get_file_downloader(
    settings: SettingsDependency,
) -> HttpFileDownloader:
    """현재 환경 설정이 적용된 파일 다운로더를 생성한다."""

    return HttpFileDownloader(settings)


# 테스트에서는 get_file_downloader 의존성을 교체하여
# 실제 외부 네트워크 요청 없이 API 동작을 검증한다.
FileDownloaderDependency = Annotated[
    HttpFileDownloader,
    Depends(get_file_downloader),
]


@router.post(
    "/process",
    status_code=HTTPStatus.ACCEPTED,
    response_model=ApiResponse[FileProcessingAcceptedResponse],
    summary="RAG 파일 처리 요청 접수",
    description=(
        "애플리케이션 서버에서 파일 URL과 파일 정보를 전달받아 "
        "원본 PDF 파일을 다운로드하고 파일 형식과 "
        "SHA-256 해시를 검증한다."
    ),
    responses={
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
            "description": "지원하지 않는 MIME 유형",
        },
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ApiResponse[ValidationErrorData | None],
            "description": ("요청값, PDF 형식 또는 파일 해시 검증 실패"),
        },
        HTTPStatus.BAD_GATEWAY: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 실패",
        },
        HTTPStatus.GATEWAY_TIMEOUT: {
            "model": ApiResponse[None],
            "description": "원본 파일 다운로드 시간 초과",
        },
    },
)
async def accept_file_processing_request(
    request: FileProcessingRequest,
    file_downloader: FileDownloaderDependency,
) -> ApiResponse[FileProcessingAcceptedResponse]:
    """원본 파일을 다운로드하고 유효성과 SHA-256 해시를 검증한다."""

    async with file_downloader.download_and_validate(
        # Presigned URL은 변환하거나 로그에 남기지 않고
        # 애플리케이션 서버가 전달한 원문을 그대로 사용한다.
        file_url=request.file_url,
        expected_sha256=request.file_hash,
        users_idx=request.users_idx,
        file_idx=request.file_idx,
    ) as downloaded_file:
        # 다음 단계의 PDF 텍스트 추출은 이 context 내부에 연결한다.
        #
        # context가 종료되면 다운로드한 임시 파일은
        # 성공 여부와 관계없이 HttpFileDownloader에서 삭제한다.
        response_data = FileProcessingAcceptedResponse(
            users_idx=request.users_idx,
            file_idx=request.file_idx,
            folder_idx=request.folder_idx,
            file_name=request.file_name,
            file_type=request.file_type,
            file_size_bytes=downloaded_file.size_bytes,
        )

    # Presigned URL, 실제 파일 해시 및 임시 파일 경로는
    # 외부 응답에 포함하지 않는다.
    return ApiResponse[FileProcessingAcceptedResponse](
        success=True,
        code="FILE_VALIDATION_COMPLETED",
        message="File download and validation completed.",
        data=response_data,
    )
