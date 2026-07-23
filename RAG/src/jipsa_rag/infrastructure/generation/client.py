"""텍스트 생성 클라이언트가 구현해야 하는 공통 인터페이스를 정의한다."""

from typing import Protocol

from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
)


class GenerationClient(Protocol):
    """공급자 독립적인 비동기 텍스트 생성 클라이언트 인터페이스."""

    async def generate(
        self,
        *,
        request: GenerationRequest,
    ) -> GenerationResult:
        """내부 생성 요청을 처리하고 정규화된 결과를 반환한다.

        Args:
            request:
                시스템 프롬프트와 사용자 프롬프트를 포함한 내부 요청이다.

        Returns:
            생성된 텍스트, 실제 응답 모델 ID, 토큰 사용량 및 종료 사유를
            포함하는 공급자 독립 결과다.

        Notes:
            구현체는 Anthropic SDK의 요청 및 응답 타입을 이 인터페이스
            바깥으로 노출하지 않아야 한다.

            Claude API 호출 중 발생하는 SDK 예외는 이후 구현 단계에서
            애플리케이션 전용 예외로 변환해야 한다.
        """

        ...
