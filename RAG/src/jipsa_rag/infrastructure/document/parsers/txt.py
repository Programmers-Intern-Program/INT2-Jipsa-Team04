import asyncio
from pathlib import Path
from typing import Final

from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentReadError,
    DocumentTextNotFoundError,
)
from jipsa_rag.infrastructure.document.models import (
    DocumentType,
    ParsedDocument,
    ParsedDocumentUnit,
)

_TXT_PARSER_TYPE: Final[str] = "TXT_PLAIN"
_TXT_PARSER_VERSION: Final[str] = "1.0.0"
_DECODE_CANDIDATES: Final[tuple[str, ...]] = ("utf-8-sig", "utf-8", "cp949")


class TxtDocumentParser:

    @property
    def file_type(self) -> DocumentType:
        return DocumentType.TXT

    @property
    def parser_type(self) -> str:
        return _TXT_PARSER_TYPE

    @property
    def parser_version(self) -> str:
        return _TXT_PARSER_VERSION

    async def parse(self, file_path: Path) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: Path) -> ParsedDocument:
        if not file_path.exists() or not file_path.is_file():
            raise DocumentFileNotFoundError(file_path)

        try:
            raw_bytes = file_path.read_bytes()
        except OSError as error:
            raise DocumentReadError(file_path) from error

        text = self._decode(raw_bytes)

        if not text.strip():
            raise DocumentTextNotFoundError(self.file_type)

        unit = ParsedDocumentUnit(
            text=text,
            source_metadata={"line_start": 1},
        )

        return ParsedDocument(
            file_type=self.file_type,
            units=(unit,),
            document_metadata={"line_count": text.count("\n") + 1},
        )

    @staticmethod
    def _decode(raw_bytes: bytes) -> str:
        for encoding in _DECODE_CANDIDATES:
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")