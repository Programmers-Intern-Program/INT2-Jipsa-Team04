"""RAG ``.env.local``을 읽어 외부 성능 테스트 실행 정보를 구성한다.

성능 테스트 전용 설정에 Token, DB Password 또는 API Key를 복사하지 않는다. 실행 시점에
기존 ``RAG/.env.local``을 읽고 필요한 값만 Process Memory에 유지한다. 공개 보고서에는
Origin, Collection, 모델명처럼 비밀정보가 아닌 값만 기록한다.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast
from urllib.parse import urlsplit

TargetEnvironment = Literal["test", "staging", "production"]

_ENVIRONMENT_VALUES: Final[set[str]] = {"test", "staging", "production"}
_ENV_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class RagEnvironmentSettings:
    """외부 RAG와 데이터 선택 Source에 필요한 런타임 설정."""

    source_path: Path
    external_base_url: str
    target_environment: TargetEnvironment
    api_v1_prefix: str
    ingest_token: str
    qdrant_url: str
    qdrant_collection: str
    qdrant_api_key: str | None
    database_host: str | None
    database_port: int | None
    database_name: str | None
    database_user: str | None
    database_password: str | None
    embedding_model: str | None
    embedding_dimension: int | None

    @property
    def target_origin(self) -> str:
        """Path와 Query를 제거한 외부 RAG Origin을 반환한다."""

        parsed = urlsplit(self.external_base_url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{host}{port}"

    @property
    def health_path(self) -> str:
        return f"{self.api_v1_prefix}/health/live"

    @property
    def readiness_path(self) -> str:
        return f"{self.api_v1_prefix}/health/ready"

    @property
    def search_path(self) -> str:
        return f"{self.api_v1_prefix}/chunks/search"

    def to_public_dict(self) -> dict[str, object]:
        """비밀값을 제외한 실행 설정을 반환한다."""

        return {
            "source_path_name": self.source_path.name,
            "target_origin": self.target_origin,
            "target_environment": self.target_environment,
            "api_v1_prefix": self.api_v1_prefix,
            "qdrant_origin": _origin_only(self.qdrant_url),
            "qdrant_collection": self.qdrant_collection,
            "database_host_configured": self.database_host is not None,
            "database_name": self.database_name,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "ingest_token_loaded": bool(self.ingest_token),
            "qdrant_api_key_loaded": bool(self.qdrant_api_key),
            "database_password_loaded": bool(self.database_password),
        }


def load_rag_environment(path: Path) -> RagEnvironmentSettings:
    """RAG 환경 파일을 읽고 성능 테스트에 필요한 값을 엄격하게 검증한다."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"RAG environment file was not found: {resolved}")

    values = parse_dotenv(resolved)
    external_base_url = _required(values, "JIPSA_RAG_EXTERNAL_BASE_URL").rstrip("/")
    _validate_origin(external_base_url, "JIPSA_RAG_EXTERNAL_BASE_URL")

    api_v1_prefix = _normalize_api_prefix(values.get("JIPSA_RAG_API_V1_PREFIX", "/api/v1"))
    environment_raw = values.get("JIPSA_RAG_APP_ENV", "test").strip().lower()
    if environment_raw not in _ENVIRONMENT_VALUES:
        raise ValueError("JIPSA_RAG_APP_ENV must be test, staging, or production when it is set.")

    qdrant_url = _required(values, "JIPSA_RAG_QDRANT_URL").rstrip("/")
    _validate_origin(qdrant_url, "JIPSA_RAG_QDRANT_URL")

    return RagEnvironmentSettings(
        source_path=resolved,
        external_base_url=external_base_url,
        target_environment=cast(TargetEnvironment, environment_raw),
        api_v1_prefix=api_v1_prefix,
        ingest_token=_required(values, "RAG_INGEST_TOKEN"),
        qdrant_url=qdrant_url,
        qdrant_collection=_required(values, "JIPSA_RAG_QDRANT_COLLECTION"),
        qdrant_api_key=_optional(values, "JIPSA_RAG_QDRANT_API_KEY"),
        database_host=_optional(values, "JIPSA_RAG_DATABASE_HOST"),
        database_port=_optional_positive_int(values, "JIPSA_RAG_DATABASE_PORT"),
        database_name=_optional(values, "JIPSA_RAG_DATABASE_NAME"),
        database_user=_optional(values, "JIPSA_RAG_DATABASE_USER"),
        database_password=_optional(values, "JIPSA_RAG_DATABASE_PASSWORD"),
        embedding_model=_optional(values, "JIPSA_RAG_EMBEDDING_MODEL"),
        embedding_dimension=_optional_positive_int(values, "JIPSA_RAG_EMBEDDING_DIM"),
    )


def parse_dotenv(path: Path) -> dict[str, str]:
    """주석·따옴표를 처리하면서 dotenv Key/Value를 읽는다.

    변수 확장은 수행하지 않는다. 성능 테스트는 기존 RAG 실행 환경의 최종 문자열을 그대로
    사용하며, 환경 파일의 값을 다른 파일에 다시 기록하지 않는다.
    """

    parsed: dict[str, str] = {}
    for line_number, original_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = original_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid dotenv line {line_number}: missing '='.")

        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Invalid dotenv key at line {line_number}: {key!r}")
        parsed[key] = _decode_dotenv_value(raw_value.strip(), line_number)
    return parsed


def _decode_dotenv_value(raw_value: str, line_number: int) -> str:
    if not raw_value:
        return ""
    if raw_value[0] not in {'"', "'"}:
        return _strip_unquoted_comment(raw_value).strip()

    quote = raw_value[0]
    escaped = False
    characters: list[str] = []
    closing_index: int | None = None
    for index, character in enumerate(raw_value[1:], start=1):
        if quote == '"' and escaped:
            translations = {"n": "\n", "r": "\r", "t": "\t"}
            characters.append(translations.get(character, character))
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character == quote:
            closing_index = index
            break
        characters.append(character)

    if closing_index is None:
        raise ValueError(f"Unclosed quoted dotenv value at line {line_number}.")
    remainder = raw_value[closing_index + 1 :].strip()
    if remainder and not remainder.startswith("#"):
        raise ValueError(f"Unexpected text after dotenv value at line {line_number}.")
    return "".join(characters)


def _strip_unquoted_comment(value: str) -> str:
    for index, character in enumerate(value):
        if character == "#" and index > 0 and value[index - 1].isspace():
            return value[:index]
    return value


def _normalize_api_prefix(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    normalized = normalized.rstrip("/")
    if not normalized or "?" in normalized or "#" in normalized:
        raise ValueError("JIPSA_RAG_API_V1_PREFIX must be a valid absolute API path.")
    return normalized


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ValueError(f"Required RAG environment variable is missing: {key}")
    return value


def _optional(values: dict[str, str], key: str) -> str | None:
    value = values.get(key, "").strip()
    return value or None


def _optional_positive_int(values: dict[str, str], key: str) -> int | None:
    raw = _optional(values, key)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{key} must be an integer.") from error
    if value <= 0:
        raise ValueError(f"{key} must be positive.")
    return value


def _validate_origin(value: str, key: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{key} must be an HTTP or HTTPS origin.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{key} must not contain user information.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(f"{key} must not contain an API path, query, or fragment.")


def _origin_only(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        host_address = ipaddress.ip_address(host)
        if host_address.version == 6:
            host = f"[{host}]"
    except ValueError:
        pass
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}"
