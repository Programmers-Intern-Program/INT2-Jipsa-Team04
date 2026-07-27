"""OCR 텍스트 정규화의 결정성과 검색 적합성을 검증한다."""

import pytest

from jipsa_rag.infrastructure.ocr.normalization import normalize_ocr_text


def test_normalize_ocr_text_removes_abnormal_characters_and_repairs_layout() -> None:
    """호환 문자, zero-width, 제어 문자, 줄 하이픈과 문장부호 공백을 정리한다."""

    raw_text = "  \uff21\uff22\uff23\u200b  \r\n테스-\r\n트  ,  \x00��\n\n\n끝  "

    result = normalize_ocr_text(raw_text, maximum_chars=1_000)

    assert result == "ABC\n테스트,\n\n끝"


def test_normalize_ocr_text_preserves_single_paragraph_break() -> None:
    """의미 있는 두 줄 문단 구분은 유지하고 수평 공백만 축약한다."""

    result = normalize_ocr_text(
        "첫째   줄\n\n둘째\t줄",
        maximum_chars=1_000,
    )

    assert result == "첫째 줄\n\n둘째 줄"


def test_normalize_ocr_text_applies_character_limit_after_normalization() -> None:
    """정규화 완료 후 Python 문자열 문자 단위로 최대 길이를 제한한다."""

    result = normalize_ocr_text("가나다라마바사", maximum_chars=4)

    assert result == "가나다라"


def test_normalize_ocr_text_rejects_non_positive_limit() -> None:
    """잘못된 길이 설정을 조용히 무시하지 않고 즉시 실패시킨다."""

    with pytest.raises(ValueError, match="maximum_chars"):
        normalize_ocr_text("텍스트", maximum_chars=0)
