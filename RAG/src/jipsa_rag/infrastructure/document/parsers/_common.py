"""형식별 문서 파서가 공유하는 파일 검증과 텍스트 정규화 유틸리티.

이 모듈에는 특정 문서 라이브러리에 종속되지 않는 최소 공통 로직만 둔다.
파서마다 같은 파일 존재 검사, 줄바꿈 정규화, OOXML ZIP 구조 검증을 반복하면
형식별 동작이 조금씩 달라지고 예외 매핑이 불일치할 수 있다. 공통 함수를 통해
입력 검증과 기본 정규화 기준을 한곳에서 유지한다.

주의할 점은 이 모듈이 문서 의미를 재구성하지 않는다는 것이다. 예를 들어 문단을
합치거나 표 구조를 추론하지 않으며, 원본 위치 경계는 각 형식별 파서가 결정한다.
"""

import re
from pathlib import Path
from typing import Final
from zipfile import BadZipFile, ZipFile

from jipsa_rag.infrastructure.document.exceptions import (
    DocumentFileNotFoundError,
    DocumentReadError,
    InvalidDocumentError,
)
from jipsa_rag.infrastructure.document.models import DocumentType

# 세 줄 이상의 연속 줄바꿈은 후속 청킹에서 의미 없는 공백 청크를 만들 수 있다.
# 문단 구분에 필요한 두 줄은 유지하고 그 이상만 두 줄로 축소한다.
_EXCESSIVE_BLANK_LINES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")


def validate_regular_file(file_path: Path) -> None:
    """파싱 대상 경로가 존재하는 일반 파일인지 검증한다.

    ``Path.exists()``만 확인하면 디렉터리도 통과할 수 있다. 문서 라이브러리에
    디렉터리 경로를 전달하면 라이브러리별 ``IsADirectoryError`` 또는 모호한 XML
    오류가 발생할 수 있으므로, 공통 문서 예외로 변환하기 전에 일반 파일 여부까지
    명시적으로 검사한다.

    Args:
        file_path:
            다운로드가 끝난 임시 파일 또는 단위 테스트가 만든 로컬 파일 경로다.

    Raises:
        DocumentFileNotFoundError:
            경로가 존재하지 않거나 디렉터리, 소켓 등 일반 파일이 아닌 경우 발생한다.
    """

    if not file_path.exists() or not file_path.is_file():
        raise DocumentFileNotFoundError(file_path)


def normalize_text(text: str | None) -> str:
    """문서 형식과 무관한 최소 수준의 다중 행 텍스트 정규화를 수행한다.

    적용 규칙은 다음과 같다.

    1. Windows CRLF와 과거 Mac CR을 LF로 통일한다.
    2. 후속 저장·검색에서 문제를 일으킬 수 있는 NULL 문자를 제거한다.
    3. 각 줄의 오른쪽 공백만 제거한다.
    4. 세 줄 이상의 연속 줄바꿈을 두 줄로 제한한다.
    5. 전체 문자열의 앞뒤 공백을 제거한다.

    줄 앞쪽 공백은 목록 들여쓰기, 코드 블록 또는 표 셀 내부 정렬을 나타낼 수 있으므로
    의도적으로 유지한다. 단어 사이의 연속 공백도 원본 의미를 훼손하지 않도록 임의로
    하나로 축소하지 않는다.

    Args:
        text:
            형식별 라이브러리가 반환한 문자열이다. 텍스트가 없는 위치에서는 ``None``일
            수 있다.

    Returns:
        최소 정규화가 적용된 문자열이다. 입력이 ``None``이면 빈 문자열을 반환한다.
    """

    if text is None:
        return ""

    # 운영체제별 줄바꿈 차이를 제거하여 Local RAG DB의 Content_Hash와 결정적
    # Chunk ID가 실행 환경에 따라 달라지지 않게 한다.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    # NULL 문자는 일반 문서 텍스트에 의미가 없고 일부 DB 드라이버나 직렬화 과정에서
    # 예외를 유발할 수 있으므로 제거한다. TXT의 바이너리 여부는 이 단계보다 앞에서
    # 원본 바이트를 기준으로 별도 검증한다.
    normalized = normalized.replace("\x00", "")

    # 왼쪽 공백은 구조 정보일 수 있으므로 보존하고 오른쪽 공백만 제거한다.
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))

    # 문단 경계 두 줄은 보존하되 과도한 공백만 줄인다.
    normalized = _EXCESSIVE_BLANK_LINES_PATTERN.sub("\n\n", normalized)

    return normalized.strip()


def normalize_inline_text(text: str | None) -> str:
    """표 셀처럼 한 줄로 직렬화할 텍스트를 단일 공백 기준으로 정규화한다.

    DOCX와 PPTX 표 셀 안에는 여러 문단이나 줄바꿈이 들어갈 수 있다. 행 전체를
    탭으로 연결할 때 셀 내부 줄바꿈을 그대로 두면 하나의 표 행이 여러 줄처럼 보이고
    원본 행 경계가 깨질 수 있다. 따라서 셀 내부의 비어 있지 않은 줄을 공백 하나로
    연결한다.

    Args:
        text:
            셀 또는 인라인 컨테이너에서 추출한 문자열이다.

    Returns:
        줄바꿈이 제거되고 비어 있지 않은 조각이 공백 하나로 연결된 문자열이다.
    """

    normalized = normalize_text(text)
    return " ".join(part.strip() for part in normalized.splitlines() if part.strip())


def validate_ooxml_package(
    file_path: Path,
    *,
    file_type: DocumentType,
    required_members: frozenset[str],
) -> None:
    """ZIP 기반 OOXML 문서에 형식별 필수 구성 파일이 존재하는지 확인한다.

    DOCX, PPTX, XLSX는 모두 ZIP 컨테이너이고 동일한 ``PK`` Magic Byte로 시작한다.
    따라서 확장자와 ZIP 시그니처만 확인하면 ``.xlsx`` 파일의 이름을 ``.docx``로
    바꾼 위장 파일을 구분할 수 없다. 각 파서는 다음 대표 루트 구성 파일을 확인한다.

    - DOCX: ``word/document.xml``
    - PPTX: ``ppt/presentation.xml``
    - XLSX: ``xl/workbook.xml``

    다운로드 계층에서도 OOXML 계열과 MIME Type을 교차 검증하지만, 파서는 독립적으로
    직접 호출될 수 있으므로 자신의 경계에서 구조 검증을 다시 수행한다. 이는 방어적
    중복 검증이며, 파서 단위 테스트와 향후 다른 호출 경로에서도 동일한 안전성을
    유지하기 위한 것이다.

    ZIP 내부 XML 본문은 이 함수에서 해석하거나 실행하지 않는다. 중앙 디렉터리의 구성
    파일 이름만 확인하고, 실제 문서 구조와 텍스트 추출은 각 라이브러리가 담당한다.

    Args:
        file_path:
            검증할 OOXML 임시 파일 경로다.
        file_type:
            검증 실패 시 예외에 기록할 기대 문서 형식이다.
        required_members:
            해당 형식의 유효한 패키지에 반드시 존재해야 하는 ZIP 멤버 집합이다.

    Raises:
        DocumentFileNotFoundError:
            파일이 존재하지 않거나 일반 파일이 아닌 경우 발생한다.
        DocumentReadError:
            파일 시스템 오류로 ZIP을 읽을 수 없는 경우 발생한다.
        InvalidDocumentError:
            유효한 ZIP이 아니거나 필수 OOXML 멤버가 없는 경우 발생한다.
    """

    validate_regular_file(file_path)

    try:
        with ZipFile(file_path) as package:
            # namelist() 결과를 frozenset으로 고정하면 여러 필수 멤버를 검사할 때
            # 반복 탐색 비용을 줄이고 이후 코드에서 실수로 수정할 수 없게 한다.
            members = frozenset(package.namelist())
    except BadZipFile as error:
        # ZIP 라이브러리의 구현 예외를 외부에 노출하지 않고 문서 형식 오류로 통일한다.
        raise InvalidDocumentError(file_type) from error
    except OSError as error:
        # 권한, 파일 잠금, 장치 오류 등 파일 시스템 문제는 손상 문서와 구분한다.
        raise DocumentReadError(file_path) from error

    if not required_members.issubset(members):
        # ZIP 자체는 유효해도 기대한 OOXML 형식의 루트가 없으면 다른 형식으로 위장된
        # 파일이거나 불완전한 패키지이므로 유효하지 않은 문서로 처리한다.
        raise InvalidDocumentError(file_type)
