"""RAG ``.env.local`` 파일을 외부 성능 측정 프로세스용 환경으로 읽는다.

성능 측정기는 RAG 설정 객체를 직접 가져오지 않는다. 대신 RAG 실행 스크립트와 같은
``KEY=VALUE`` 파일을 읽어 자식 프로세스에 전달한다. 이 모듈은 비밀값을 로그에 출력하지
않으며, 파일에 이미 선언된 값보다 호출 프로세스의 명시적 환경 변수를 우선한다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

_ENVIRONMENT_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_dotenv(path: Path) -> dict[str, str]:
    """UTF-8 dotenv 파일을 읽어 문자열 사전으로 반환한다.

    첫 번째 등호만 이름과 값의 경계로 사용한다. 따라서 API Key, DSN 또는 서명 문자열에
    추가 등호가 포함되어도 값을 손상하지 않는다. ``export KEY=value`` 형태도 허용하되,
    셸 확장이나 변수 치환은 실행하지 않는다.
    """

    if not path.is_file():
        raise FileNotFoundError(f"dotenv file does not exist: {path}")

    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].lstrip()

        separator_index = line.find("=")
        if separator_index <= 0:
            raise ValueError(f"Invalid dotenv assignment at line {line_number}.")

        name = line[:separator_index].strip()
        value = line[separator_index + 1 :].strip()
        if _ENVIRONMENT_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(f"Invalid dotenv variable name at line {line_number}.")

        value = _strip_matching_quotes(value)
        result[name] = value

    return result


def build_child_environment(
    dotenv_values: Mapping[str, str],
    *,
    overrides: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """현재 프로세스 환경과 dotenv를 병합한 자식 프로세스 환경을 만든다.

    우선순위는 ``dotenv < 현재 프로세스 환경 < overrides``다. 호출자가 현재 PowerShell에
    임시로 설정한 값은 ``.env.local``보다 우선하며, ``overrides``의 ``None`` 값은 해당
    변수를 자식 환경에서 제거한다.
    """

    child = dict(dotenv_values)
    child.update(os.environ)

    for name, value in (overrides or {}).items():
        if value is None:
            child.pop(name, None)
        else:
            child[name] = value

    return child


def get_required_secret(environment: Mapping[str, str], *names: str) -> str:
    """여러 호환 변수명 중 처음 발견한 비어 있지 않은 값을 반환한다."""

    for name in names:
        value = environment.get(name)
        if value is not None and value.strip():
            return value.strip()
    joined = ", ".join(names)
    raise ValueError(f"One of the following environment variables is required: {joined}")


def _strip_matching_quotes(value: str) -> str:
    """값 양끝의 동일한 단일·이중 따옴표만 제거한다."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
