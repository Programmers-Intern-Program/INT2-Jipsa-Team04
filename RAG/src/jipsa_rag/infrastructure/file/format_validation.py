"""다운로드한 문서의 MIME Type, Magic Byte와 OOXML 내부 구조를 검증한다.

파일명과 요청의 ``file_type``만 신뢰하면 확장자만 바꾼 위장 파일이 파서까지
도달할 수 있다. 반대로 MIME Type만 엄격하게 신뢰하면 S3 객체 메타데이터가
``application/octet-stream``으로 저장된 정상 문서를 거부할 수 있다.

따라서 다음 신호를 단계적으로 교차 검증한다.

1. HTTP ``Content-Type``이 지원 목록에 있는지 확인한다.
2. 파일 시작 바이트로 PDF, OOXML, 일반 텍스트 또는 바이너리 계열을 분류한다.
3. 구체 MIME Type이 있는 경우 Magic 계열과 일치하는지 확인한다.
4. OOXML은 ZIP 중앙 디렉터리에서 DOCX/PPTX/XLSX 루트 멤버를 정확히 하나 찾는다.
5. OOXML MIME Type이 구체적이면 패키지 루트와 다시 교차 검증한다.

TXT는 표준 고정 Magic Byte가 없으므로 BOM, NULL 바이트, 인코딩 후보와 제어
문자 비율을 이용한 제한적 휴리스틱을 적용한다. 최종 전체 파일 디코딩과
바이너리 거부는 ``TxtDocumentParser``가 다시 수행한다.
"""

import codecs
from pathlib import Path
from typing import Final, Literal
from zipfile import BadZipFile, ZipFile

from charset_normalizer import from_bytes

from jipsa_rag.core.error_codes import ErrorCode
from jipsa_rag.core.exceptions import AppException

# PDF 표준 파일 헤더다. 버전 문자열보다 앞의 "%PDF-" 다섯 바이트만 검사한다.
PDF_MAGIC_BYTES: Final[bytes] = b"%PDF-"

# ZIP 파일은 일반 로컬 파일 헤더, 빈 ZIP 중앙 디렉터리, 분할 ZIP 데이터 descriptor
# 등 여러 유효한 PK 시그니처로 시작할 수 있다. OOXML은 ZIP 기반이므로 모두 허용한다.
ZIP_MAGIC_BYTES: Final[tuple[bytes, ...]] = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
)

# 다운로드 중 형식 판별에 보관할 선두 바이트 수다. PDF/ZIP 시그니처에는 몇 바이트면
# 충분하지만 TXT의 제어 문자 비율과 인코딩 후보를 조금 더 안정적으로 판단하기 위해
# 8 KiB를 사용한다. 파일 전체는 임시 파일에 스트리밍하고 메모리에 올리지 않는다.
FORMAT_SNIFF_BYTES: Final[int] = 8192

# S3 객체가 구체 MIME Type 없이 저장되었을 때 흔히 반환되는 일반 바이너리 타입이다.
# 이 경우 MIME만으로 형식을 결정하지 않고 Magic Byte와 파서 구조 검증을 사용한다.
_GENERIC_BINARY_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/octet-stream",
        "binary/octet-stream",
    }
)

_PDF_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
    }
)

_DOCX_CONTENT_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_PPTX_CONTENT_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
_XLSX_CONTENT_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

_OOXML_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        _DOCX_CONTENT_TYPE,
        _PPTX_CONTENT_TYPE,
        _XLSX_CONTENT_TYPE,
    }
)

# 각 구체 OOXML MIME Type이 요구하는 대표 패키지 루트다. ZIP 시그니처는 같지만
# 이 멤버 경로는 DOCX, PPTX, XLSX를 결정적으로 구분한다.
_OOXML_ROOT_MEMBER_BY_CONTENT_TYPE: Final[dict[str, str]] = {
    _DOCX_CONTENT_TYPE: "word/document.xml",
    _PPTX_CONTENT_TYPE: "ppt/presentation.xml",
    _XLSX_CONTENT_TYPE: "xl/workbook.xml",
}

_SUPPORTED_OOXML_ROOT_MEMBERS: Final[frozenset[str]] = frozenset(
    _OOXML_ROOT_MEMBER_BY_CONTENT_TYPE.values()
)

_TXT_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "text/plain",
        "application/text",
    }
)

_ALLOWED_CONTENT_TYPES: Final[frozenset[str]] = (
    _GENERIC_BINARY_CONTENT_TYPES
    | _PDF_CONTENT_TYPES
    | _OOXML_CONTENT_TYPES
    | _TXT_CONTENT_TYPES
)

# 다운로드 선두 바이트를 넓은 형식 계열로만 분류한다. OOXML의 구체 형식은 ZIP
# 중앙 디렉터리를 읽은 뒤 별도로 결정한다.
type DetectedMagicFamily = Literal["PDF", "OOXML", "TEXT", "BINARY"]


def validate_content_type_and_magic(
    *,
    content_type: str | None,
    leading_bytes: bytes,
    users_idx: int,
    file_idx: int,
) -> DetectedMagicFamily:
    """MIME Type을 정규화하고 선두 바이트 계열과 일치하는지 검증한다.

    MIME Type이 없거나 일반 바이너리 타입이면 Magic Byte 결과를 기준으로 다음
    검증 단계로 진행한다. 구체 MIME Type이 제공된 경우에는 해당 계열과 정확히
    일치해야 한다.

    Args:
        content_type:
            HTTP 응답의 원본 ``Content-Type`` 헤더다. 파라미터가 포함될 수 있다.
        leading_bytes:
            다운로드 과정에서 보관한 파일 시작 부분이다.
        users_idx:
            안전한 구조화 로그 컨텍스트에 사용할 사용자 식별자다.
        file_idx:
            안전한 구조화 로그 컨텍스트에 사용할 파일 식별자다.

    Returns:
        탐지된 넓은 Magic 계열이다.

    Raises:
        AppException:
            지원하지 않는 MIME Type, 명백한 바이너리 또는 MIME/Magic 불일치 시 발생한다.
    """

    normalized_content_type = validate_supported_content_type(
        content_type=content_type,
        users_idx=users_idx,
        file_idx=file_idx,
    )

    detected_family = detect_magic_family(leading_bytes)

    if detected_family == "BINARY":
        # 지원 형식 어느 계열에도 해당하지 않는 바이너리다. 선두 바이트 원문은
        # 로그에 남기지 않고 검증 종류만 기록한다.
        raise AppException(
            ErrorCode.INVALID_FILE,
            public_message=(
                "The downloaded file does not match a supported document format."
            ),
            log_context={
                "users_idx": users_idx,
                "file_idx": file_idx,
                "validation": "document_magic_bytes",
            },
        )

    # 구체 MIME Type이 제공된 경우에만 계열 일치를 강제한다. 일반 binary MIME은
    # 실제 파일 형식 정보를 주지 않으므로 아래 분기에 포함하지 않는다.
    if (
        normalized_content_type in _PDF_CONTENT_TYPES
        and detected_family != "PDF"
    ):
        _raise_invalid_magic(
            users_idx,
            file_idx,
            normalized_content_type,
        )

    if (
        normalized_content_type in _OOXML_CONTENT_TYPES
        and detected_family != "OOXML"
    ):
        _raise_invalid_magic(
            users_idx,
            file_idx,
            normalized_content_type,
        )

    if (
        normalized_content_type in _TXT_CONTENT_TYPES
        and detected_family != "TEXT"
    ):
        # text/plain으로 선언된 파일이 PDF/ZIP처럼 다른 지원 형식인 경우에도
        # 선언과 실제 내용이 일치하지 않으므로 미디어 타입 오류로 거부한다.
        _raise_unsupported_media_type(
            users_idx,
            file_idx,
            normalized_content_type,
        )

    return detected_family


def validate_supported_content_type(
    *,
    content_type: str | None,
    users_idx: int,
    file_idx: int,
) -> str | None:
    """Content-Type을 미디어 타입 부분만으로 정규화하고 허용 여부를 확인한다.

    ``text/plain; charset=utf-8``이나 ``application/pdf; charset=binary``처럼
    파라미터가 붙은 헤더는 세미콜론 앞의 미디어 타입만 사용한다. 이 검사는
    응답 본문을 수신하기 전에 호출할 수 있어, 명백히 지원하지 않는 대용량 파일을
    조기에 거부한다.

    Content-Type이 없거나 빈 값이면 즉시 실패시키지 않는다. Presigned URL의 객체
    메타데이터가 누락될 수 있으므로 파일 본문의 Magic Byte와 파서 구조 검증으로
    최종 판단한다.
    """

    if content_type is None:
        return None

    normalized_content_type = content_type.partition(";")[0].strip().lower()

    if not normalized_content_type:
        return None

    if normalized_content_type not in _ALLOWED_CONTENT_TYPES:
        raise AppException(
            ErrorCode.UNSUPPORTED_FILE_MEDIA_TYPE,
            log_context={
                "users_idx": users_idx,
                "file_idx": file_idx,
                "content_type": normalized_content_type,
            },
        )

    return normalized_content_type


def validate_ooxml_package_and_mime(
    *,
    file_path: Path,
    content_type: str | None,
    users_idx: int,
    file_idx: int,
) -> str:
    """OOXML ZIP 패키지의 실제 형식과 구체 MIME Type을 교차 검증한다.

    ZIP 중앙 디렉터리에서 지원 루트 멤버를 찾고 정확히 하나만 존재하는지 확인한다.
    루트가 없으면 지원하지 않는 ZIP이고, 둘 이상이면 서로 다른 OOXML 구조가 섞인
    모호한 패키지이므로 모두 거부한다.

    XML 본문이나 매크로를 읽거나 실행하지 않는다. 이 함수는 중앙 디렉터리의 경로명만
    확인하고, 실제 문서 구조와 텍스트 추출은 형식별 파서가 다시 검증한다.

    Returns:
        확인된 구체 형식의 소문자 문자열(``docx``, ``pptx``, ``xlsx``)이다.
    """

    normalized_content_type = validate_supported_content_type(
        content_type=content_type,
        users_idx=users_idx,
        file_idx=file_idx,
    )

    try:
        with ZipFile(file_path) as package:
            members = frozenset(package.namelist())
    except (BadZipFile, OSError) as error:
        # ZIP 파싱 오류나 파일 시스템 상세를 외부에 노출하지 않는다.
        raise AppException(
            ErrorCode.INVALID_FILE,
            public_message="The downloaded OOXML package is invalid.",
            log_context={
                "users_idx": users_idx,
                "file_idx": file_idx,
                "validation": "ooxml_zip_structure",
            },
        ) from error

    detected_roots = _SUPPORTED_OOXML_ROOT_MEMBERS.intersection(members)

    if len(detected_roots) != 1:
        raise AppException(
            ErrorCode.INVALID_FILE,
            public_message="The downloaded file is not a supported OOXML document.",
            log_context={
                "users_idx": users_idx,
                "file_idx": file_idx,
                "validation": "ooxml_root_member",
                "detected_root_count": len(detected_roots),
            },
        )

    detected_root = next(iter(detected_roots))

    if normalized_content_type in _OOXML_CONTENT_TYPES:
        expected_root = _OOXML_ROOT_MEMBER_BY_CONTENT_TYPE[
            normalized_content_type
        ]

        if detected_root != expected_root:
            # 예: MIME은 DOCX지만 실제 ZIP 루트가 xl/workbook.xml인 경우다.
            raise AppException(
                ErrorCode.INVALID_FILE,
                public_message=(
                    "The OOXML file content does not match its MIME type."
                ),
                log_context={
                    "users_idx": users_idx,
                    "file_idx": file_idx,
                    "content_type": normalized_content_type,
                    "validation": "ooxml_mime_package_mismatch",
                },
            )

    format_by_root = {
        "word/document.xml": "docx",
        "ppt/presentation.xml": "pptx",
        "xl/workbook.xml": "xlsx",
    }

    return format_by_root[detected_root]


def detect_magic_family(leading_bytes: bytes) -> DetectedMagicFamily:
    """파일 선두 바이트를 PDF, OOXML, TEXT 또는 BINARY 계열로 분류한다."""

    if leading_bytes.startswith(PDF_MAGIC_BYTES):
        return "PDF"

    if any(
        leading_bytes.startswith(signature)
        for signature in ZIP_MAGIC_BYTES
    ):
        return "OOXML"

    if _looks_like_text(leading_bytes):
        return "TEXT"

    return "BINARY"


def _looks_like_text(payload: bytes) -> bool:
    """선두 바이트가 일반 텍스트 후보인지 보수적으로 판단한다.

    이 함수는 최종 인코딩을 결정하지 않는다. 다운로드 단계에서 명백한 바이너리를
    조기에 차단하기 위한 1차 휴리스틱이며, 전체 바이트에 대한 strict 디코딩과
    세부 바이너리 판별은 TXT 파서가 수행한다.
    """

    if not payload:
        return False

    # UTF BOM은 강한 텍스트 신호다. 긴 UTF-32 BOM도 tuple에 포함한다.
    if payload.startswith(
        (
            codecs.BOM_UTF8,
            codecs.BOM_UTF16_LE,
            codecs.BOM_UTF16_BE,
            codecs.BOM_UTF32_LE,
            codecs.BOM_UTF32_BE,
        )
    ):
        return True

    if b"\x00" in payload:
        # 일반 단일 바이트 텍스트에서 NULL은 바이너리 신호지만 BOM 없는 UTF-16/32
        # 텍스트에서는 정상적으로 나타난다. 통계 후보가 해당 UTF 계열이고 strict
        # 디코딩 결과의 제어 문자 비율도 정상일 때만 허용한다.
        best_match = from_bytes(payload).best()

        if best_match is None or best_match.encoding is None:
            return False

        normalized_encoding = best_match.encoding.replace("-", "_").lower()

        if not normalized_encoding.startswith(("utf_16", "utf_32")):
            return False

        try:
            decoded = payload.decode(
                best_match.encoding,
                errors="strict",
            )
        except (LookupError, UnicodeDecodeError):
            return False

        return _has_text_control_ratio(decoded)

    # 단일 바이트 후보에서는 원본 바이트의 C0 제어 문자 비율을 직접 검사한다.
    # 백스페이스, 탭, LF, 폼 피드와 CR은 일반 텍스트에서 허용한다.
    disallowed_controls = sum(
        1
        for value in payload
        if value < 32 and value not in {8, 9, 10, 12, 13}
    )

    return disallowed_controls / len(payload) <= 0.05


def _has_text_control_ratio(text: str) -> bool:
    """디코딩 문자열의 제어 문자 비율이 일반 텍스트 허용 범위인지 반환한다."""

    if not text:
        return False

    disallowed_controls = sum(
        1
        for character in text
        if (
            ord(character) < 32
            and character not in {"\n", "\r", "\t", "\f", "\b"}
        )
    )

    return disallowed_controls / len(text) <= 0.05


def _raise_invalid_magic(
    users_idx: int,
    file_idx: int,
    content_type: str,
) -> None:
    """구체 MIME Type과 Magic 계열이 다를 때 공통 파일 오류를 발생시킨다."""

    raise AppException(
        ErrorCode.INVALID_FILE,
        public_message="The file content does not match its MIME type.",
        log_context={
            "users_idx": users_idx,
            "file_idx": file_idx,
            "content_type": content_type,
            "validation": "mime_magic_mismatch",
        },
    )


def _raise_unsupported_media_type(
    users_idx: int,
    file_idx: int,
    content_type: str,
) -> None:
    """텍스트 MIME Type과 실제 계열 불일치를 미디어 타입 오류로 변환한다."""

    raise AppException(
        ErrorCode.UNSUPPORTED_FILE_MEDIA_TYPE,
        log_context={
            "users_idx": users_idx,
            "file_idx": file_idx,
            "content_type": content_type,
            "validation": "mime_magic_mismatch",
        },
    )
