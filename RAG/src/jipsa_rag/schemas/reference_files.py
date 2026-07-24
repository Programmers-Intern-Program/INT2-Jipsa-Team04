"""참조문서 기반 검색 요청에 공통으로 사용하는 식별자 계약을 정의한다."""

from typing import Annotated, Final

from pydantic import AfterValidator, Field

# 한 질문에 포함할 수 있는 참조문서의 최대 개수다.
#
# 참조문서 목록은 Qdrant의 file_idx 필터 조건으로 변환된다. 제한 없이
# 허용하면 단일 요청이 과도하게 큰 검색 필터를 생성할 수 있으므로 최대
# 20개로 제한한다. 정책 변경이 필요하면 두 요청 스키마가 동일한 값을
# 사용하도록 이 상수만 변경한다.
MAX_REFERENCE_FILE_COUNT: Final[int] = 20


def ensure_unique_reference_file_idxs(
    value: tuple[int, ...],
) -> tuple[int, ...]:
    """참조문서 식별자가 중복되지 않았는지 검증한다.

    중복 식별자를 자동으로 제거하면 호출자가 전달한 잘못된 선택 상태를
    숨기게 된다. 따라서 순서를 보존한 채 중복 여부만 확인하고, 중복이
    존재하면 요청 검증 오류를 발생시킨다.

    Args:
        value:
            개별 식별자 타입과 목록 길이 검증을 통과한 참조문서 식별자
            tuple이다.

    Returns:
        중복이 없는 원본 참조문서 식별자 tuple이다.

    Raises:
        ValueError:
            동일한 파일 식별자가 두 번 이상 포함된 경우 발생한다.
    """

    if len(value) != len(set(value)):
        raise ValueError("reference_file_idxs must contain unique values.")

    return value


# AWS 서버 DB File.File_IDX는 양의 정수 식별자만 허용한다.
#
# strict=True를 적용하여 문자열 "123", 실수 123.0 또는 bool처럼 Python에서
# 정수로 암묵 변환될 수 있는 값도 요청 계약에서 거부한다.
ReferenceFileIdx = Annotated[
    int,
    Field(
        strict=True,
        gt=0,
    ),
]

# 외부 JSON 배열은 Pydantic 검증 후 불변 tuple로 저장한다.
#
# 질문 전송 이후 호출자가 원본 list를 변경하더라도 이미 검증된 요청의
# 참조문서 범위가 변하지 않도록 한다. 최소 1개, 최대 선택 개수 및 중복
# 금지 규칙을 하나의 공통 타입에 결합하여 답변 요청과 청크 검색 요청이
# 항상 동일한 검증 계약을 사용하게 한다.
ReferenceFileIdxs = Annotated[
    tuple[ReferenceFileIdx, ...],
    Field(
        min_length=1,
        max_length=MAX_REFERENCE_FILE_COUNT,
    ),
    AfterValidator(ensure_unique_reference_file_idxs),
]