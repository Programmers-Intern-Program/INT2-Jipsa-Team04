"""OCR 텍스트의 공백, 줄바꿈 및 비정상 문자를 결정적으로 정규화한다."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_ZERO_WIDTH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\u200B-\u200D\u2060\uFEFF]")
_HORIZONTAL_SPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^\S\n]+")
_EXCESSIVE_BLANK_LINES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")
_DEHYPHENATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<=[0-9A-Za-z가-힣])-\n(?=[0-9A-Za-z가-힣])"
)
_SPACE_BEFORE_PUNCTUATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+([,.;:!?%\)\]\}])")
_SPACE_AFTER_OPENING_PATTERN: Final[re.Pattern[str]] = re.compile(r"([\(\[\{])\s+")
_REPEATED_REPLACEMENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"�+")


def normalize_ocr_text(text: str, *, maximum_chars: int) -> str:
    """OCR 결과를 검색·임베딩에 안정적인 UTF-8 텍스트로 변환한다.

    처리 순서가 바뀌면 결과 Content Hash와 Chunk ID가 달라질 수 있으므로 아래
    순서를 유지한다.

    1. CRLF/CR을 LF로 통일한다.
    2. Unicode NFKC로 호환 문자를 표준화한다.
    3. NUL, 제어 문자, zero-width 문자와 replacement character를 제거한다.
    4. 줄 끝 하이픈으로 분리된 단어를 결합한다.
    5. 수평 공백과 과도한 빈 줄을 축약한다.
    6. 문장 부호 주변의 OCR 특유 공백을 정리한다.
    """

    if maximum_chars <= 0:
        raise ValueError("maximum_chars must be greater than zero.")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ")
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = _ZERO_WIDTH_PATTERN.sub("", normalized)
    normalized = _remove_control_and_unassigned_characters(normalized)
    normalized = _REPEATED_REPLACEMENT_PATTERN.sub("", normalized)
    normalized = _DEHYPHENATION_PATTERN.sub("", normalized)

    lines: list[str] = []
    for raw_line in normalized.split("\n"):
        line = _HORIZONTAL_SPACE_PATTERN.sub(" ", raw_line).strip()
        line = _SPACE_BEFORE_PUNCTUATION_PATTERN.sub(r"\1", line)
        line = _SPACE_AFTER_OPENING_PATTERN.sub(r"\1", line)
        lines.append(line)

    normalized = "\n".join(lines)
    normalized = _EXCESSIVE_BLANK_LINES_PATTERN.sub("\n\n", normalized)
    normalized = normalized.strip()

    if len(normalized) <= maximum_chars:
        return normalized

    # UTF-8 다중 바이트 문자를 중간에서 자르지 않도록 Python 문자열 단위로 제한한다.
    return normalized[:maximum_chars].rstrip()


def _remove_control_and_unassigned_characters(text: str) -> str:
    """줄바꿈과 탭을 제외한 제어·서로게이트·미할당 문자를 제거한다."""

    output: list[str] = []
    for character in text:
        if character in ("\n", "\t"):
            output.append(character)
            continue

        category = unicodedata.category(character)
        if category in {"Cc", "Cs", "Cn"}:
            continue
        output.append(character)

    return "".join(output)
