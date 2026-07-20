"""RAG 인제스트 완료 콜백에서 사용하는 외부 API 스키마를 정의한다."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IngestCompleteRequest(BaseModel):
    """RAG 서버가 애플리케이션 서버에 전달하는 인제스트 완료 결과."""

    # 백엔드와 RAG 사이의 계약에 정의되지 않은 필드가
    # 실수로 전송되지 않도록 추가 필드를 허용하지 않는다.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    success: bool = Field(
        description="파일 인제스트와 색인 처리의 최종 성공 여부",
        examples=[True],
    )

    error_message: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "인제스트 실패 원인에 대한 외부 공개용 메시지다. 성공한 경우에는 전송하지 않는다."
        ),
        examples=["INVALID_DOCUMENT: The document structure is invalid."],
    )

    @model_validator(mode="after")
    def validate_result_contract(self) -> Self:
        """성공 여부와 오류 메시지 조합을 검증한다."""

        if self.success:
            if self.error_message is not None:
                raise ValueError("Successful ingestion must not include an error message.")

            return self

        if not self.error_message:
            raise ValueError("Failed ingestion must include an error message.")

        return self
