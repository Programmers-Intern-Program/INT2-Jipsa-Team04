from contextvars import ContextVar, Token
from uuid import UUID, uuid4

REQUEST_ID_HEADER = "X-Request-ID"

_request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


def resolve_request_id(header_value: str | None) -> str:
    """요청 헤더의 UUID를 검증하거나 새로운 요청 식별자를 생성한다.

    애플리케이션 서버가 유효한 UUID 형식의 X-Request-ID를 전달하면
    서버 간 로그 추적을 위해 동일한 값을 사용한다.

    헤더가 없거나 UUID 형식이 아니면 외부 입력을 그대로 신뢰하지 않고
    RAG 서버에서 새로운 UUID를 생성한다.
    """

    if header_value is not None:
        normalized_value = header_value.strip()

        try:
            return str(UUID(normalized_value))
        except ValueError:
            pass

    return str(uuid4())


def set_request_id(request_id: str) -> Token[str | None]:
    """현재 비동기 실행 컨텍스트에 요청 식별자를 저장한다."""

    return _request_id_context.set(request_id)


def get_request_id() -> str | None:
    """현재 비동기 실행 컨텍스트의 요청 식별자를 반환한다."""

    return _request_id_context.get()


def reset_request_id(token: Token[str | None]) -> None:
    """요청 처리가 끝난 후 이전 실행 컨텍스트를 복원한다."""

    _request_id_context.reset(token)
