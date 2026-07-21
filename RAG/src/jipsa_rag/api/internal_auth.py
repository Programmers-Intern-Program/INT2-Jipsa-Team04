"""RAG 인제스트 요청의 내부 서비스 인증을 제공한다."""

from secrets import compare_digest
from typing import Annotated, Final

from fastapi import Depends, Header

from jipsa_rag.core.config import Settings, get_settings
from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException

# 애플리케이션 서버와 RAG 서버가 서비스 간 인증에 사용하는
# HTTP 요청 헤더 이름이다.
INTERNAL_TOKEN_HEADER_NAME: Final[str] = "X-Internal-Token"


# get_settings()는 현재 실행 환경의 Settings 객체를 생성하고 캐싱한다.
#
# 동일 프로세스의 요청마다 dotenv 파일을 다시 읽지 않는다.
SettingsDependency = Annotated[
    Settings,
    Depends(get_settings),
]


# Header alias를 명시하여 다음 두 동작을 보장한다.
#
# 1. 실제 HTTP 요청에서는 X-Internal-Token이라는 이름을 사용한다.
# 2. Swagger UI와 OpenAPI 스키마에도 동일한 헤더 이름이 표시된다.
#
# 헤더가 누락된 경우 FastAPI의 자동 422 오류로 처리하지 않고
# 인증 의존성에서 명시적인 401 응답으로 변환하기 위해 None을 허용한다.
InternalTokenHeader = Annotated[
    str | None,
    Header(
        alias=INTERNAL_TOKEN_HEADER_NAME,
        convert_underscores=False,
    ),
]


def verify_rag_ingest_token(
    settings: SettingsDependency,
    provided_token: InternalTokenHeader = None,
) -> None:
    """X-Internal-Token 공유 시크릿을 검증한다.

    서버에 내부 토큰이 설정되지 않은 경우 인증을 우회하지 않고
    서비스 사용 불가 오류를 반환한다.

    요청 토큰이 누락되거나 설정된 토큰과 일치하지 않는 경우에는
    외부 응답에서 동일한 인증 실패로 처리한다.

    토큰 비교는 처리 시간 차이를 이용한 값 추측 가능성을 줄이기 위해
    secrets.compare_digest()를 사용한다.
    """

    configured_token = settings.rag_ingest_token

    if configured_token is None:
        # 내부 토큰이 설정되지 않은 상태는 요청자의 인증 오류가 아니라
        # RAG 서버의 배포 또는 실행 설정 오류에 해당한다.
        #
        # 설정 누락 상태에서 요청을 허용하면 인증이 완전히 우회되므로
        # 반드시 실패하도록 fail-closed 방식으로 처리한다.
        #
        # 로그 컨텍스트에는 토큰 원문을 포함하지 않는다.
        raise AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            log_context={
                "authentication_type": "internal_token",
                "authentication_failure_reason": "token_not_configured",
            },
        )

    # secrets.compare_digest()는 같은 타입의 두 값을 비교해야 한다.
    #
    # str 비교는 ASCII 문자만 지원하므로 환경 변수에 비 ASCII 문자가
    # 포함된 경우에도 안전하게 비교할 수 있도록 UTF-8 bytes로 변환한다.
    expected_token_bytes = configured_token.get_secret_value().encode("utf-8")
    provided_token_bytes = (provided_token or "").encode("utf-8")

    if not compare_digest(
        provided_token_bytes,
        expected_token_bytes,
    ):
        # 헤더 누락과 토큰 불일치는 외부 응답에서 동일한 401 오류로 반환한다.
        #
        # 이를 통해 외부 호출자가 서버의 토큰 설정 여부나
        # 부분 일치 여부를 응답 메시지로 추측하지 못하도록 한다.
        #
        # 내부 로그에는 원문 토큰 대신 실패 유형만 기록한다.
        failure_reason = "token_missing" if provided_token is None else "token_mismatch"

        raise AppException(
            ErrorCode.UNAUTHORIZED,
            log_context={
                "authentication_type": "internal_token",
                "authentication_failure_reason": failure_reason,
            },
        )
