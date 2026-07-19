"""문서 청킹 단계에서 발생하는 도메인 예외를 정의한다."""


class DocumentChunkingError(Exception):
    """문서 청킹 계층에서 발생하는 모든 예외의 기본 클래스."""


class InvalidChunkingConfigurationError(DocumentChunkingError):
    """청크 크기 또는 중첩 크기 설정이 올바르지 않은 경우의 예외."""

    def __init__(
        self,
        *,
        chunk_size_chars: int,
        chunk_overlap_chars: int,
    ) -> None:
        """검증에 실패한 청킹 설정을 보관한다."""

        self.chunk_size_chars = chunk_size_chars
        self.chunk_overlap_chars = chunk_overlap_chars

        super().__init__(
            "Chunk size must be greater than zero, and chunk overlap "
            "must be greater than or equal to zero and smaller than chunk size."
        )


class NoDocumentChunksError(DocumentChunkingError):
    """파싱 결과에서 생성 가능한 텍스트 청크가 없는 경우의 예외."""

    def __init__(
        self,
        file_type: object,
    ) -> None:
        """청크를 만들지 못한 문서 형식을 보관한다."""

        self.file_type = file_type

        super().__init__(f"No text chunks could be created from the {file_type!s} document.")
