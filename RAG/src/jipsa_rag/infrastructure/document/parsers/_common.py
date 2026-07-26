"""형식별 문서 파서가 공유하는 검증과 텍스트 정규화 유틸리티.

이 모듈은 DOCX, PPTX, XLSX 파서가 공통으로 사용하는 OOXML 방어 경계를
제공한다. OOXML은 ZIP 컨테이너이므로 정상 문서처럼 보이는 입력 안에도 과도한
압축률, 비정상적으로 많은 엔트리, 암호화 엔트리 또는 경로 순회 이름이 포함될
수 있다. 각 형식별 라이브러리에 파일을 넘기기 전에 중앙 디렉터리를 검증하여
불필요한 메모리·디스크 사용과 라이브러리별 예외 누출을 줄인다.

이 검증은 다운로드 계층의 MIME/Magic Byte 검증을 대체하지 않는다. 다운로드
계층은 외부 입력의 전송 계약을 확인하고, 이 모듈은 파서가 직접 호출되는 경우까지
포함하여 실제 OOXML 패키지 구조를 다시 확인하는 방어적 2차 검증을 담당한다.
"""

import re
from pathlib import Path, PurePosixPath
from typing import Final
from zipfile import BadZipFile, LargeZipFile, ZipFile, ZipInfo

from jipsa_rag.core.document_processing import (
    DocumentProcessingSettings,
    get_document_processing_settings,
)
from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentReadError,
    EncryptedDocumentError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.models import DocumentType

# 세 줄 이상의 연속 줄바꿈은 후속 청킹에서 의미 없는 공백 청크를 만들 수 있다.
# 문단 구분에 필요한 두 줄은 유지하고 그 이상만 두 줄로 축소한다.
_EXCESSIVE_BLANK_LINES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")

# Office의 Agile/Standard 암호화 문서는 일반 OOXML ZIP이 아니라 OLE Compound
# File Binary 컨테이너로 저장된다. 이 시그니처를 먼저 확인하면 암호화 문서를
# 단순 손상 ZIP으로 오인하지 않고 명시적인 EncryptedDocumentError로 변환할 수 있다.
_OLE_COMPOUND_FILE_SIGNATURE: Final[bytes] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE_ENCRYPTION_MARKERS: Final[tuple[bytes, ...]] = (
    "EncryptedPackage".encode("utf-16-le"),
    "EncryptionInfo".encode("utf-16-le"),
)

# OLE 디렉터리 엔트리는 파일 뒤쪽에 배치될 수 있다. 암호화 스트림 이름을 찾기
# 위해 전체 파일을 메모리에 올리지 않고 앞·뒤 각 1 MiB만 검사한다.
_OLE_MARKER_SCAN_BYTES: Final[int] = 1024 * 1024


def validate_regular_file(file_path: Path) -> None:
    """파싱 대상 경로가 존재하는 일반 파일인지 검증한다.

    ``Path.exists()``만 확인하면 디렉터리도 통과할 수 있다. 문서 라이브러리에
    디렉터리 경로를 전달하면 구현별 예외가 발생하므로, 공통 문서 예외로 변환하기
    전에 일반 파일 여부까지 명시적으로 확인한다.
    """

    if not file_path.exists() or not file_path.is_file():
        raise DocumentFileNotFoundError(file_path)


def normalize_text(text: str | None) -> str:
    """문서 형식과 무관한 최소 수준의 다중 행 텍스트 정규화를 수행한다.

    적용 규칙은 다음과 같다.

    1. CRLF와 CR을 LF로 통일한다.
    2. DB와 JSON 직렬화에 문제를 줄 수 있는 NULL 문자를 제거한다.
    3. 각 줄의 오른쪽 공백만 제거한다.
    4. 세 줄 이상의 연속 줄바꿈을 두 줄로 제한한다.
    5. 전체 문자열의 앞뒤 공백을 제거한다.

    왼쪽 공백은 목록 들여쓰기나 코드 블록의 구조일 수 있으므로 유지한다.
    """

    if text is None:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\x00", "")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = _EXCESSIVE_BLANK_LINES_PATTERN.sub("\n\n", normalized)
    return normalized.strip()


def normalize_inline_text(text: str | None) -> str:
    """표 셀처럼 한 줄로 직렬화할 텍스트를 단일 공백 기준으로 정규화한다."""

    normalized = normalize_text(text)
    return " ".join(part.strip() for part in normalized.splitlines() if part.strip())


def validate_ooxml_package(
    file_path: Path,
    *,
    file_type: DocumentType,
    required_members: frozenset[str],
) -> None:
    """OOXML 패키지의 형식 루트, 암호화 여부와 압축 안전성을 검증한다.

    DOCX, PPTX, XLSX는 모두 ``PK``로 시작하므로 확장자와 ZIP 시그니처만으로는
    서로를 구분할 수 없다. 이 함수는 형식별 필수 루트 파일을 확인하는 동시에
    다음 입력을 파서 라이브러리 호출 전에 거부한다.

    - 빈 파일 또는 손상된 ZIP 중앙 디렉터리
    - Office 암호화 컨테이너 또는 ZIP 암호화 엔트리
    - 엔트리 개수·해제 크기·압축률 한계를 초과한 패키지
    - 중복 엔트리 또는 절대 경로·상위 경로를 포함한 엔트리
    - 기대한 DOCX/PPTX/XLSX 루트 파일이 없는 다른 OOXML 형식

    ``ZipFile``의 중앙 디렉터리 정보만으로 안전 한계를 검사하며, XML 본문을
    해석하지 않는다. 실제 관계와 텍스트 구조 검증은 각 형식별 라이브러리가
    담당하고 그 예외는 형식별 파서에서 ``InvalidDocumentError``로 변환한다.
    """

    validate_regular_file(file_path)
    _reject_ole_encrypted_document(file_path, file_type=file_type)

    try:
        with ZipFile(file_path, allowZip64=True) as package:
            infos = tuple(package.infolist())
            _validate_zip_directory(
                infos,
                file_type=file_type,
                settings=get_document_processing_settings(),
            )
            members = frozenset(info.filename for info in infos)
    except EncryptedDocumentError:
        raise
    except InvalidDocumentError:
        raise
    except (BadZipFile, LargeZipFile) as error:
        raise InvalidDocumentError(file_type) from error
    except OSError as error:
        raise DocumentReadError(file_path) from error

    if not required_members.issubset(members):
        raise InvalidDocumentError(file_type)


def _reject_ole_encrypted_document(
    file_path: Path,
    *,
    file_type: DocumentType,
) -> None:
    """OLE 기반 Office 암호화 문서를 손상 ZIP과 구분하여 거부한다.

    오래된 바이너리 Office 문서도 동일 OLE 시그니처를 사용할 수 있다. 따라서
    암호화 스트림 이름이 확인되면 ``EncryptedDocumentError``를 사용하고, 단순
    OLE 문서라면 현재 지원 형식이 아닌 입력이므로 ``InvalidDocumentError``로
    처리한다. 전체 파일을 메모리에 적재하지 않고 앞부분만 읽는다.
    """

    try:
        with file_path.open("rb") as stream:
            header = stream.read(8)
            if header != _OLE_COMPOUND_FILE_SIGNATURE:
                return

            head_sample = header + stream.read(_OLE_MARKER_SCAN_BYTES)

            # OLE의 디렉터리 스트림은 파일 끝부분에 위치하는 경우가 많다. 파일이
            # 충분히 크면 마지막 1 MiB도 읽어 EncryptedPackage/EncryptionInfo
            # 이름을 놓치지 않는다. 작은 파일은 앞부분 검사만으로 전체를 포함한다.
            stream.seek(0, 2)
            file_size = stream.tell()
            tail_start = max(0, file_size - _OLE_MARKER_SCAN_BYTES)
            stream.seek(tail_start)
            tail_sample = stream.read(_OLE_MARKER_SCAN_BYTES)
            sample = head_sample + tail_sample
    except OSError as error:
        raise DocumentReadError(file_path) from error

    if any(marker in sample for marker in _OLE_ENCRYPTION_MARKERS):
        raise EncryptedDocumentError(file_type)

    raise InvalidDocumentError(file_type)


def _validate_zip_directory(
    infos: tuple[ZipInfo, ...],
    *,
    file_type: DocumentType,
    settings: DocumentProcessingSettings,
) -> None:
    """ZIP 중앙 디렉터리의 수량, 경로와 압축 비율을 검증한다."""

    if not infos or len(infos) > settings.ooxml_max_member_count:
        raise InvalidDocumentError(file_type)

    seen_names: set[str] = set()
    total_uncompressed_bytes = 0

    for info in infos:
        _validate_zip_member_name(info.filename, file_type=file_type)

        if info.filename in seen_names:
            # 같은 이름의 중복 엔트리는 라이브러리마다 마지막/첫 번째 값을 다르게
            # 선택할 수 있어 파싱 결과가 비결정적이므로 허용하지 않는다.
            raise InvalidDocumentError(file_type)
        seen_names.add(info.filename)

        if info.flag_bits & 0x1:
            # ZIP 일반 암호화와 AES 확장 암호화 모두 기본 encrypted bit를 사용한다.
            raise EncryptedDocumentError(file_type)

        if info.file_size < 0 or info.compress_size < 0:
            raise InvalidDocumentError(file_type)

        if info.file_size > settings.ooxml_max_member_uncompressed_bytes:
            raise InvalidDocumentError(file_type)

        total_uncompressed_bytes += info.file_size
        if total_uncompressed_bytes > settings.ooxml_max_total_uncompressed_bytes:
            raise InvalidDocumentError(file_type)

        if info.is_dir() or info.file_size == 0:
            continue

        if info.compress_size == 0:
            # 내용이 있는데 압축 크기가 0인 엔트리는 정상적인 ZIP 메타데이터가 아니다.
            raise InvalidDocumentError(file_type)

        compression_ratio = info.file_size / info.compress_size
        if compression_ratio > settings.ooxml_max_compression_ratio:
            raise InvalidDocumentError(file_type)


def _validate_zip_member_name(
    member_name: str,
    *,
    file_type: DocumentType,
) -> None:
    """OOXML ZIP 엔트리가 패키지 내부의 안전한 POSIX 상대 경로인지 확인한다."""

    if not member_name or "\\" in member_name or "\x00" in member_name:
        raise InvalidDocumentError(file_type)

    member_path = PurePosixPath(member_name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise InvalidDocumentError(file_type)
