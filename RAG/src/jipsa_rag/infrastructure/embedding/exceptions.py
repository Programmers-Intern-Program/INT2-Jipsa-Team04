"""임베딩 생성 계층에서 발생하는 예외를 정의한다."""


class EmbeddingError(Exception):
    """모든 임베딩 생성 예외의 기본 클래스."""


class EmbeddingServiceTimeoutError(EmbeddingError):
    """임베딩 서비스가 제한 시간 안에 응답하지 않은 경우."""

    def __init__(self) -> None:
        """외부 응답에 노출하지 않을 고정 내부 메시지를 설정한다."""

        super().__init__("The embedding service request timed out.")


class EmbeddingServiceUnavailableError(EmbeddingError):
    """임베딩 서비스에 연결할 수 없거나 일시적으로 사용할 수 없는 경우."""

    def __init__(
        self,
        *,
        status_code: int | None = None,
    ) -> None:
        """안전한 HTTP 상태 코드만 오류 컨텍스트로 보관한다."""

        self.status_code = status_code

        super().__init__("The embedding service is unavailable.")


class EmbeddingServiceRejectedError(EmbeddingError):
    """임베딩 서비스가 RAG 서버의 요청을 거부한 경우."""

    def __init__(
        self,
        *,
        status_code: int,
    ) -> None:
        """응답 본문은 보관하지 않고 상태 코드만 유지한다."""

        self.status_code = status_code

        super().__init__("The embedding service rejected the request.")


class InvalidEmbeddingResponseError(EmbeddingError):
    """임베딩 서비스 응답 구조나 벡터 값이 유효하지 않은 경우."""

    def __init__(
        self,
        *,
        reason: str,
        batch_start_index: int,
    ) -> None:
        """청크 원문이나 벡터 전체를 포함하지 않는 검증 정보를 보관한다."""

        self.reason = reason
        self.batch_start_index = batch_start_index

        super().__init__(f"Invalid embedding response: {reason}")
