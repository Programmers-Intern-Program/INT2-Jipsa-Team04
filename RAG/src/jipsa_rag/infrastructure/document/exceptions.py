"""문서 파싱 단계에서 발생하는 도메인 예외를 정의한다."""

from collections.abc import Mapping
from pathlib import Path

type DocumentSourceMetadataValue = str | int | float | bool | None


class DocumentParserError(Exception):
    """문서 파서 계층에서 발생하는 모든 예외의 기본 클래스."""


class UnsupportedDocumentTypeError(DocumentParserError):
    """요청한 문서 형식에 대응하는 파서가 등록되지 않은 경우의 예외."""

    def __init__(self, file_type: object) -> None:
        """지원하지 않는 문서 형식 값을 보관한다."""

        self.file_type = file_type
        super().__init__(f"No document parser is registered for file type: {file_type!s}")


class DuplicateDocumentParserError(DocumentParserError):
    """동일한 문서 형식의 파서가 두 번 등록된 경우의 예외."""

    def __init__(self, file_type: object) -> None:
        """중복 등록된 문서 형식 값을 보관한다."""

        self.file_type = file_type
        super().__init__(f"A document parser is already registered for file type: {file_type!s}")


class DocumentFileNotFoundError(DocumentParserError):
    """파싱 대상 파일이 존재하지 않거나 일반 파일이 아닌 경우의 예외."""

    def __init__(self, file_path: Path) -> None:
        """조회에 실패한 파일 경로를 보관한다."""

        self.file_path = file_path
        super().__init__("The document file does not exist or is not a regular file.")


class DocumentReadError(DocumentParserError):
    """파일 시스템에서 문서 바이트를 읽지 못한 경우의 예외."""

    def __init__(self, file_path: Path) -> None:
        """읽기에 실패한 파일 경로를 보관한다."""

        self.file_path = file_path
        super().__init__("The document file could not be read.")


class InvalidDocumentError(DocumentParserError):
    """문서 구조가 손상되었거나 형식으로 해석할 수 없는 경우의 예외."""

    def __init__(self, file_type: object) -> None:
        """구조 검증에 실패한 문서 형식을 보관한다."""

        self.file_type = file_type
        super().__init__(f"The file is not a valid {file_type!s} document.")


class EncryptedDocumentError(DocumentParserError):
    """암호화 문서를 현재 파서 정책으로 처리할 수 없는 경우의 예외."""

    def __init__(self, file_type: object) -> None:
        """암호화가 감지된 문서 형식을 보관한다."""

        self.file_type = file_type
        super().__init__(f"Encrypted {file_type!s} documents are not supported.")


class DocumentTextExtractionError(DocumentParserError):
    """특정 원본 위치에서 텍스트 추출에 실패한 경우의 예외."""

    def __init__(
        self,
        *,
        file_type: object,
        source_metadata: Mapping[
            str,
            DocumentSourceMetadataValue,
        ],
    ) -> None:
        """실패한 문서 형식과 원본 위치 메타데이터를 보관한다."""

        self.file_type = file_type
        self.source_metadata = dict(source_metadata)
        super().__init__(f"Text could not be extracted from the {file_type!s} document.")


class DocumentTextNotFoundError(DocumentParserError):
    """파싱은 완료되었지만 추출 가능한 텍스트가 없는 경우의 예외."""

    def __init__(self, file_type: object) -> None:
        """텍스트가 발견되지 않은 문서 형식을 보관한다."""

        self.file_type = file_type
        super().__init__(f"No extractable text was found in the {file_type!s} document.")
