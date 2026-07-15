from collections.abc import Mapping

from jipsa_rag.core.error_codes import ErrorCode

type LogContextValue = str | int | float | bool | None


class AppException(Exception):
    """서비스에서 의도적으로 발생시키는 공통 애플리케이션 예외."""

    def __init__(
        self,
        error_code: ErrorCode,
        *,
        public_message: str | None = None,
        log_context: Mapping[str, LogContextValue] | None = None,
    ) -> None:
        """공통 오류 코드와 로그 컨텍스트를 사용해 예외를 생성한다.

        Args:
            error_code:
                HTTP 상태 코드, 외부 응답 코드 및 기본 메시지를 포함하는
                공통 오류 코드다.
            public_message:
                외부 API 응답에 공개할 메시지다. 지정하지 않으면
                오류 코드에 정의된 기본 메시지를 사용한다.
            log_context:
                내부 로그 추적에만 사용할 추가 정보다. 외부 API 응답에는
                포함하지 않는다.
        """

        self.error_code = error_code
        self.public_message = public_message or error_code.message
        self.log_context = dict(log_context or {})

        super().__init__(self.public_message)

    @property
    def status_code(self) -> int:
        """예외에 대응하는 HTTP 상태 코드를 반환한다."""

        return self.error_code.status_code

    @property
    def code(self) -> str:
        """외부 API 응답에서 사용할 오류 코드를 반환한다."""

        return self.error_code.code
