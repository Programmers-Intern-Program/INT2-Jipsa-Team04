from pydantic import BaseModel, ConfigDict, Field


class ApiResponse[DataT](BaseModel):
    """Jipsa RAG API에서 공통으로 사용하는 응답 스키마."""

    # API 응답 모델에 정의되지 않은 필드가 포함되는 것을 차단한다.
    #
    # 응답 객체를 구성하는 과정에서 비밀값이나 내부 데이터가
    # 의도하지 않게 추가되는 실수를 조기에 탐지하기 위한 설정이다.
    model_config = ConfigDict(extra="forbid")

    success: bool = Field(
        description="요청 처리 성공 여부",
        examples=[True],
    )

    code: str = Field(
        min_length=1,
        description="클라이언트가 처리 결과를 식별할 수 있는 응답 코드",
        examples=["SUCCESS"],
    )

    message: str = Field(
        min_length=1,
        description="요청 처리 결과에 대한 외부 공개용 메시지",
        examples=["Request completed successfully."],
    )

    data: DataT | None = Field(
        default=None,
        description="요청 처리 결과 데이터",
    )


class ValidationErrorItem(BaseModel):
    """단일 요청값 검증 오류를 표현하는 스키마."""

    # 검증 오류 응답에도 정의되지 않은 필드가 포함되지 않도록 제한한다.
    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        min_length=1,
        description="검증에 실패한 요청값의 위치",
        examples=["body.file_id"],
    )

    message: str = Field(
        min_length=1,
        description="검증 실패 원인에 대한 외부 공개용 메시지",
        examples=["Field required"],
    )

    error_type: str = Field(
        min_length=1,
        description="Pydantic에서 제공하는 검증 오류 유형",
        examples=["missing"],
    )


class ValidationErrorData(BaseModel):
    """요청값 검증 오류 목록을 포함하는 스키마."""

    # 오류 목록 모델에 정의되지 않은 필드가 추가되는 것을 차단한다.
    model_config = ConfigDict(extra="forbid")

    errors: list[ValidationErrorItem] = Field(
        min_length=1,
        description="검증에 실패한 요청값 목록",
    )
