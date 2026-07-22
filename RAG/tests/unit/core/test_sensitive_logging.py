"""구조화 로그의 민감 정보 마스킹 동작을 검증한다."""

import json
import logging
from io import StringIO
from typing import cast

from jipsa_rag.core.logging import SensitiveDataJsonFormatter

_TEST_INTERNAL_TOKEN = "test-internal-token-value-that-must-never-be-logged"
_TEST_DATABASE_HOST = "private-rag-db.internal.example"
_TEST_DATABASE_PORT = 44077
_TEST_DATABASE_NAME = "private_jipsa_rag"
_TEST_DATABASE_USER = "private_rag_user"
_TEST_DATABASE_PASSWORD = "private-rag-database-password"
_TEST_DATABASE_DSN = (
    "mysql+asyncmy://"
    f"{_TEST_DATABASE_USER}:{_TEST_DATABASE_PASSWORD}"
    f"@{_TEST_DATABASE_HOST}:{_TEST_DATABASE_PORT}/{_TEST_DATABASE_NAME}"
)
_TEST_PRESIGNED_URL = (
    "https://private-bucket.s3.ap-northeast-2.amazonaws.com/files/test.pdf?"
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
    "X-Amz-Credential=temporary-credential&"
    "X-Amz-Signature=temporary-signature-value"
)


def _create_test_logger(stream: StringIO) -> logging.Logger:
    """민감 정보 Formatter만 적용된 독립 테스트 로거를 생성한다.

    전역 루트 로거를 사용하면 다른 테스트의 configure_logging() 실행 여부에 따라
    핸들러와 포맷이 달라질 수 있다.

    따라서 테스트 전용 Logger와 StreamHandler를 직접 생성하여
    SensitiveDataJsonFormatter의 동작만 독립적으로 검증한다.
    """

    formatter = SensitiveDataJsonFormatter(
        [
            "asctime",
            "levelname",
            "name",
            "message",
            "request_id",
            "exc_info",
        ]
    )

    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    # logging.getLogger()를 사용하면 다른 테스트가 구성한 루트 핸들러로
    # 레코드가 전파될 수 있으므로 독립 Logger 인스턴스를 직접 생성한다.
    logger = logging.Logger("sensitive-data-test")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)

    return logger


def _read_single_json_log(stream: StringIO) -> dict[str, object]:
    """테스트 스트림의 단일 JSON 로그를 일반 dict로 반환한다."""

    parsed_log: object = json.loads(stream.getvalue())

    if not isinstance(parsed_log, dict):
        raise AssertionError("The formatted log must be a JSON object.")

    return cast(
        dict[str, object],
        parsed_log,
    )


def test_formatter_redacts_structured_sensitive_fields_and_message_values() -> None:
    """구조화 필드와 일반 메시지의 인증 및 DB 정보를 모두 제거해야 한다."""

    stream = StringIO()
    logger = _create_test_logger(stream)

    logger.info(
        "Callback metadata: download_url=%s, internal_token=%s, database_url=%s",
        _TEST_PRESIGNED_URL,
        _TEST_INTERNAL_TOKEN,
        _TEST_DATABASE_DSN,
        extra={
            "internal_token": _TEST_INTERNAL_TOKEN,
            "download_url": _TEST_PRESIGNED_URL,
            "context": {
                "database_host": _TEST_DATABASE_HOST,
                "database_port": _TEST_DATABASE_PORT,
                "database_name": _TEST_DATABASE_NAME,
                "database_user": _TEST_DATABASE_USER,
                "database_password": _TEST_DATABASE_PASSWORD,
                "database_dsn": _TEST_DATABASE_DSN,
                # 파일 식별자는 장애 추적에 필요한 비민감 값이므로
                # 마스킹 과정에서도 원본을 유지해야 한다.
                "file_idx": 123,
            },
        },
    )

    raw_log = stream.getvalue()

    # 실제 로그 문자열 전체에서 원본 민감값이 하나라도 발견되면
    # 구조화 필드 또는 일반 메시지 마스킹이 누락된 것이다.
    for sensitive_value in (
        _TEST_INTERNAL_TOKEN,
        _TEST_DATABASE_HOST,
        str(_TEST_DATABASE_PORT),
        _TEST_DATABASE_NAME,
        _TEST_DATABASE_USER,
        _TEST_DATABASE_PASSWORD,
        _TEST_DATABASE_DSN,
        _TEST_PRESIGNED_URL,
        "temporary-credential",
        "temporary-signature-value",
    ):
        assert sensitive_value not in raw_log

    log_payload = _read_single_json_log(stream)

    # 명시적인 민감 필드명은 값의 원래 형식과 관계없이
    # 고정된 마스킹 문자열로 교체되어야 한다.
    assert log_payload["internal_token"] == "[REDACTED]"
    assert log_payload["download_url"] == "[REDACTED]"

    context = log_payload["context"]

    assert isinstance(context, dict)
    assert context["database_host"] == "[REDACTED]"
    assert context["database_port"] == "[REDACTED]"
    assert context["database_name"] == "[REDACTED]"
    assert context["database_user"] == "[REDACTED]"
    assert context["database_password"] == "[REDACTED]"
    assert context["database_dsn"] == "[REDACTED]"

    # 장애 추적에 필요한 비민감 식별자는 마스킹하지 않아야 한다.
    assert context["file_idx"] == 123


def test_formatter_redacts_sensitive_values_inside_exception_traceback() -> None:
    """예외 메시지와 Traceback에도 URL, 토큰 및 DSN이 남지 않아야 한다."""

    stream = StringIO()
    logger = _create_test_logger(stream)

    try:
        # 실제 라이브러리 예외 메시지에 URL이나 접속 문자열이 포함된 상황을
        # 재현하기 위해 민감 정보를 예외 문자열에 직접 포함한다.
        raise RuntimeError(
            "Unexpected upstream failure: "
            f"{_TEST_PRESIGNED_URL}; "
            f"X-Internal-Token={_TEST_INTERNAL_TOKEN}; "
            f"connection={_TEST_DATABASE_DSN}"
        )
    except RuntimeError:
        logger.exception(
            "The ingest-complete callback raised an unexpected exception.",
        )

    raw_log = stream.getvalue()

    # 예외 문자열뿐 아니라 전체 Traceback에 원본 민감값이 남지 않았는지 확인한다.
    for sensitive_value in (
        _TEST_INTERNAL_TOKEN,
        _TEST_DATABASE_HOST,
        _TEST_DATABASE_PASSWORD,
        _TEST_DATABASE_DSN,
        _TEST_PRESIGNED_URL,
        "temporary-credential",
        "temporary-signature-value",
    ):
        assert sensitive_value not in raw_log

    log_payload = _read_single_json_log(stream)
    exception_text = log_payload["exc_info"]

    # formatException()은 logging.Formatter 계약에 따라
    # 최종 Traceback을 문자열로 반환해야 한다.
    assert isinstance(exception_text, str)

    # 전체 값이 제거되었는지뿐 아니라 각 민감 정보 유형이
    # 의도한 마스킹 값으로 변환되었는지도 함께 검증한다.
    assert "[REDACTED_PRESIGNED_URL]" in exception_text
    assert "[REDACTED_DATABASE_DSN]" in exception_text
    assert "X-Internal-Token=[REDACTED]" in exception_text
