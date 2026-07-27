"""RAG 인증정보와 내부 접속 정보가 JSON 로그에 노출되지 않는지 검증한다.

질문·청크·OCR 원문·프롬프트는 서비스가 로그 필드에 전달하지 않는 방식으로
E2E에서 검증한다. 이 파일은 로그 Formatter가 구조화 필드와 일반 문자열 안의
내부 인증 토큰, Bearer 토큰, API Key 및 Presigned URL을 최종 출력 전에
마스킹하는 방어 계층을 독립적으로 검증한다.
"""

import json
import logging
import sys

from jipsa_rag.core.logging import SensitiveDataJsonFormatter


def _format_record(record: logging.LogRecord) -> dict[str, object]:
    """운영과 같은 민감정보 Formatter를 적용하고 JSON 객체로 반환한다."""

    formatter = SensitiveDataJsonFormatter(
        [
            "levelname",
            "name",
            "message",
            "internal_token",
            "authorization",
            "download_url",
            "anthropic_api_key",
        ]
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    if not isinstance(parsed, dict):
        raise AssertionError("The JSON log formatter must return an object.")
    return parsed


def test_authentication_and_presigned_url_values_are_redacted() -> None:
    """구조화 필드와 message 문자열 양쪽의 인증정보 원문을 모두 제거한다."""

    internal_token = "INTERNAL-TOKEN-SECRET-ISSUE123-A1"
    bearer_token = "BEARER-TOKEN-SECRET-ISSUE123-B2"
    api_key = "ANTHROPIC-KEY-SECRET-ISSUE123-C3"
    presigned_url = (
        "https://storage.example.invalid/private/document.pdf"
        "?X-Amz-Credential=credential-secret"
        "&X-Amz-Signature=signature-secret"
    )
    message = (
        f"internal_token={internal_token} "
        f"Authorization: Bearer {bearer_token} "
        f"api_key={api_key} download_url={presigned_url}"
    )
    record = logging.LogRecord(
        name="jipsa_rag.security_test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.__dict__["internal_token"] = internal_token
    record.__dict__["authorization"] = f"Bearer {bearer_token}"
    record.__dict__["download_url"] = presigned_url
    record.__dict__["anthropic_api_key"] = api_key

    payload = _format_record(record)
    serialized = json.dumps(payload, ensure_ascii=False)

    for secret in (
        internal_token,
        bearer_token,
        api_key,
        "credential-secret",
        "signature-secret",
    ):
        assert secret not in serialized

    assert "[REDACTED]" in serialized
    assert "[REDACTED_PRESIGNED_URL]" in serialized


def test_authentication_value_is_redacted_inside_exception_traceback() -> None:
    """예외 메시지와 traceback에 포함된 내부 토큰도 Formatter가 제거한다."""

    internal_token = "TRACEBACK-INTERNAL-TOKEN-ISSUE123-D4"

    exception_info = None
    try:
        raise RuntimeError(f"x_internal_token={internal_token}")
    except RuntimeError:
        exception_info = sys.exc_info()

    if exception_info is None:
        raise AssertionError("The test exception traceback must be available.")

    record = logging.LogRecord(
        name="jipsa_rag.security_test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="RAG operation failed safely.",
        args=(),
        exc_info=exception_info,
    )
    payload = _format_record(record)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert internal_token not in serialized
    assert "[REDACTED]" in serialized
