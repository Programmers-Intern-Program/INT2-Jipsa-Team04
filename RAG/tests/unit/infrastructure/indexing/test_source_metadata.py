"""Local RAG DB와 Qdrant가 공유하는 source_metadata 정규화를 테스트한다."""

import json
import math

import pytest

from jipsa_rag.infrastructure.indexing.source_metadata import (
    dump_source_metadata_json,
    normalize_source_metadata,
)


def test_normalize_source_metadata_converts_nested_values_to_json_types() -> None:
    """불변 tuple과 중첩 Mapping을 JSON 배열과 객체로 변환한다."""

    normalized = normalize_source_metadata(
        {
            "sheet_name": "매출",
            "cell_coordinates": ("A1", "B1"),
            "geometry": {"left": 100, "ratio": 0.25},
            "flags": (True, None),
        }
    )

    assert normalized == {
        "sheet_name": "매출",
        "cell_coordinates": ["A1", "B1"],
        "geometry": {"left": 100, "ratio": 0.25},
        "flags": [True, None],
    }


def test_dump_source_metadata_is_deterministic_and_unicode_safe() -> None:
    """키 입력 순서가 달라도 DB JSON 문자열은 동일하고 한글을 보존한다."""

    first = dump_source_metadata_json({"나": 2, "가": 1})
    second = dump_source_metadata_json({"가": 1, "나": 2})

    assert first == second
    assert "가" in first
    assert json.loads(first) == {"가": 1, "나": 2}


@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_normalize_source_metadata_rejects_non_finite_float(value: float) -> None:
    """Qdrant와 JSON에서 의미가 불명확한 NaN과 Infinity를 거부한다."""

    with pytest.raises(ValueError):
        normalize_source_metadata({"invalid": value})


def test_normalize_source_metadata_rejects_unsupported_object() -> None:
    """임의 객체를 문자열로 암묵 변환하지 않아 payload 계약을 고정한다."""

    with pytest.raises(ValueError):
        normalize_source_metadata({"invalid": object()})
