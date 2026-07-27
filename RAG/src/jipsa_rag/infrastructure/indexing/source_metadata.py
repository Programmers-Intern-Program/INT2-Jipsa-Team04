"""Local RAG DB와 Qdrant가 공유하는 source metadata 직렬화 계약.

문서 파서는 불변 tuple을 사용해 위치 목록을 보존하지만 JSON은 배열을 list로
표현한다. 또한 NaN과 Infinity는 Python의 ``json.dumps`` 기본 동작에서는 출력될
수 있으나 MySQL JSON과 Qdrant payload의 이식 가능한 값이 아니다. 이 모듈은 두
저장소가 같은 규칙으로 메타데이터를 검증·변환하도록 단일 직렬화 경계를 제공한다.
"""

import json
import math
from collections.abc import Mapping, Sequence

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def normalize_source_metadata(metadata: Mapping[str, object]) -> dict[str, JsonValue]:
    """파서/청커 메타데이터를 MySQL과 Qdrant가 공유할 JSON 객체로 변환한다.

    키는 비어 있지 않은 문자열만 허용한다. 값은 JSON 스칼라, mapping 또는
    문자열이 아닌 sequence만 재귀적으로 허용한다. 지원되지 않는 임의 객체를
    ``str()``로 조용히 바꾸지 않아 저장 계약 위반을 조기에 발견한다.
    """

    normalized: dict[str, JsonValue] = {}

    for raw_key, raw_value in metadata.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("source metadata keys must be non-empty strings.")
        normalized[raw_key] = _normalize_json_value(raw_value)

    return normalized


def dump_source_metadata_json(metadata: Mapping[str, object]) -> str:
    """MySQL JSON 컬럼에 바인딩할 결정적인 UTF-8 JSON 문자열을 반환한다.

    ``sort_keys=True``는 같은 메타데이터의 저장 문자열을 실행마다 동일하게 만들고,
    ``allow_nan=False``는 비표준 부동소수 값을 명시적으로 거부한다.
    """

    normalized = normalize_source_metadata(metadata)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def metadata_int(
    metadata: Mapping[str, object],
    key: str,
    *,
    minimum: int,
) -> int | None:
    """메타데이터에서 bool이 아닌 지정 최솟값 이상의 정수를 읽는다."""

    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def metadata_float(metadata: Mapping[str, object], key: str) -> float | None:
    """메타데이터에서 유한한 실수 값을 읽는다."""

    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def metadata_text(
    metadata: Mapping[str, object],
    key: str,
    *,
    maximum_length: int | None = None,
) -> str | None:
    """메타데이터에서 비어 있지 않은 문자열을 읽고 선택적으로 길이를 제한한다."""

    value = metadata.get(key)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if maximum_length is not None:
        return normalized[:maximum_length]
    return normalized


def _normalize_json_value(value: object) -> JsonValue:
    """임의 값을 JSON 표준 값으로 재귀 변환한다."""

    if value is None or isinstance(value, str | bool | int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("source metadata floats must be finite.")
        return value

    if isinstance(value, Mapping):
        nested: dict[str, JsonValue] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ValueError("nested source metadata keys must be non-empty strings.")
            nested[raw_key] = _normalize_json_value(raw_value)
        return nested

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize_json_value(item) for item in value]

    raise ValueError(
        "source metadata values must be JSON-compatible scalars, mappings, or sequences."
    )
