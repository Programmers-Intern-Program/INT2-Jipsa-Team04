"""텍스트 생성 공급자 호출과 응답 변환 과정에서 사용하는 예외를 정의한다."""


class GenerationError(Exception):
    """텍스트 생성 계층에서 발생하는 모든 예외의 기준 클래스."""


class GenerationProviderError(GenerationError):
    """외부 생성 공급자 요청이 실패한 경우 발생하는 기준 예외.

    외부 SDK의 원본 예외 메시지와 응답 본문에는 요청 정보나 공급자 내부 정보가
    포함될 수 있으므로 애플리케이션 경계 밖으로 그대로 전달하지 않는다.
    대신 공급자 이름, HTTP 상태 코드 및 요청 추적 ID처럼 안전하게 활용할 수 있는
    최소 메타데이터만 보관한다.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        """안전한 오류 메시지와 공급자 메타데이터를 저장한다."""

        self.provider = provider
        self.status_code = status_code
        self.request_id = request_id

        super().__init__(message)


class GenerationAuthenticationError(GenerationProviderError):
    """API Key가 유효하지 않거나 인증에 실패한 경우 발생한다."""


class GenerationRateLimitError(GenerationProviderError):
    """생성 공급자의 요청 제한을 초과한 경우 발생한다."""


class GenerationTimeoutError(GenerationProviderError):
    """생성 공급자 요청이 제한 시간 안에 완료되지 않은 경우 발생한다."""


class GenerationServerError(GenerationProviderError):
    """생성 공급자의 5xx 또는 과부하 오류로 요청이 실패한 경우 발생한다."""


class InvalidGenerationResponseError(GenerationProviderError):
    """공급자 응답을 내부 생성 결과로 안전하게 변환할 수 없는 경우 발생한다."""

    def __init__(
        self,
        *,
        provider: str,
        reason: str,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        """민감한 응답 원문 대신 내부에서 정의한 안전한 실패 사유를 보관한다."""

        self.reason = reason

        super().__init__(
            "Generation provider returned an invalid response.",
            provider=provider,
            status_code=status_code,
            request_id=request_id,
        )
