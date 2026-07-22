"""문서 및 벡터 저장 단계에서 사용하는 예외를 정의한다."""


class IndexStorageError(Exception):
    """색인 저장 계층에서 발생하는 모든 예외의 기준 클래스."""


class LocalRagStorageError(IndexStorageError):
    """Local RAG DB 저장 또는 상태 갱신에 실패한 경우 발생한다."""

    def __init__(self, operation: str) -> None:
        """실패한 저장 작업명을 보관한다.

        실제 SQL 문, 데이터베이스 접속 정보 및 원본 청크 내용은
        예외 메시지에 포함하지 않는다.
        """

        self.operation = operation
        super().__init__(f"Local RAG storage operation failed: {operation}")


class IndexRunOwnershipLostError(LocalRagStorageError):
    """현재 실행이 더 이상 해당 파일 색인의 최신 소유자가 아님을 나타낸다.

    오래된 실행은 이 예외가 발생한 뒤 문서 상태를 성공 또는 실패로
    확정하거나 다른 실행이 만든 Qdrant Point를 보상 삭제하면 안 된다.
    """


class VectorDatabaseError(IndexStorageError):
    """Qdrant 저장 계층에서 발생하는 모든 예외의 기준 클래스."""

    def __init__(
        self,
        operation: str,
        *,
        status_code: int | None = None,
    ) -> None:
        """실패 작업과 안전하게 기록할 수 있는 HTTP 상태만 보관한다."""

        self.operation = operation
        self.status_code = status_code
        super().__init__(f"Vector database operation failed: {operation}")


class VectorDatabaseUnavailableError(VectorDatabaseError):
    """Qdrant 연결 실패, 시간 초과 또는 일시적 장애를 나타낸다."""


class VectorDatabaseRejectedError(VectorDatabaseError):
    """Qdrant가 저장 요청을 영구적인 오류로 거부한 경우 발생한다."""


class VectorCollectionConfigurationError(VectorDatabaseError):
    """Collection 설정과 현재 임베딩 결과가 일치하지 않는 경우 발생한다."""
