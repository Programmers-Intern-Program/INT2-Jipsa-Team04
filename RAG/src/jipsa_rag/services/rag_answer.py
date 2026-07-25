"""청크 검색과 Claude 생성을 연결하여 근거 기반 RAG 답변을 생성한다."""

import logging
import re
from typing import Final, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingError,
)
from jipsa_rag.infrastructure.generation.client import (
    GenerationClient,
)
from jipsa_rag.infrastructure.generation.exceptions import (
    GenerationError,
)
from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
)
from jipsa_rag.infrastructure.indexing.exceptions import (
    IndexStorageError,
)
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagAnswerSource,
    RagAnswerStatus,
    RagAnswerUsage,
)
from jipsa_rag.services.prompt_builder import (
    RagPromptBuildResult,
)

_LOGGER = logging.getLogger(__name__)

# 검색 결과가 없거나 Claude가 제공된 문서만으로 답변할 수 없다고
# 판단했을 때 반환하는 고정 안내 문구다.
#
# 프롬프트, 구조화 생성 결과 검증 및 외부 API 응답이 모두 같은 값을
# 사용해야 상태 판정이 문자열 표현 차이로 흔들리지 않는다.
_INSUFFICIENT_EVIDENCE_ANSWER: Final[str] = "제공된 문서 근거만으로는 답변할 수 없습니다."

# 답변 본문에서 대괄호로 감싼 SOURCE 숫자 식별자를 추출한다.
#
# 숫자 부분을 넓게 허용하여 SOURCE-0, SOURCE-01 같은 비정상
# 식별자도 먼저 추출한 뒤 후보 출처 집합과 비교한다.
#
# 형식상 인용처럼 보이는 잘못된 값이 정규식에 잡히지 않았다는 이유로
# 정상 답변에 남는 것을 방지한다.
_SOURCE_CITATION_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[(?P<source_id>SOURCE-[0-9]+)\]")

# 구조화 응답의 cited_source_ids 항목에는 공개 출처 스키마와 동일하게
# SOURCE-1부터 시작하는 양의 정수 식별자만 허용한다.
_VALID_SOURCE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^SOURCE-[1-9][0-9]*$")

# 생성 결과의 구조, 상태, 답변 인용 또는 선언된 출처 목록이 계약을
# 위반했을 때 API 계층이 INVALID_GENERATION_RESPONSE로 변환하는
# 기존 작업 식별자를 재사용한다.
#
# 기존 엔드포인트와 테스트의 오류 계약을 유지하면서 자유 텍스트
# 인용 검증과 구조화 응답 검증을 하나의 외부 오류 범주로 처리한다.
_GENERATION_RESPONSE_VALIDATION_OPERATION: Final[str] = "answer_citation_validation_failed"


class _StructuredRagAnswer(BaseModel):
    """Claude 구조화 출력의 RAG 도메인 계약을 검증하는 내부 모델.

    Anthropic JSON Schema 구조화 출력은 JSON 문법과 기본 필드 형식을
    보장하지만 다음과 같은 애플리케이션 의미 규칙은 Local RAG가 다시
    검증해야 한다.

    - answered 상태에는 한 개 이상의 출처가 필요하다.
    - insufficient_evidence 상태에는 출처가 없어야 한다.
    - 근거 부족 상태의 답변은 고정 문구와 정확히 일치해야 한다.
    - 출처 식별자는 SOURCE-1 형식이어야 하며 중복될 수 없다.

    Claude 답변 원문이나 구조화 JSON 전체는 검증 오류 메시지와 로그에
    포함하지 않는다.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    status: RagAnswerStatus
    answer: str
    cited_source_ids: tuple[str, ...]

    @field_validator(
        "status",
        mode="before",
    )
    @classmethod
    def normalize_status(
        cls,
        value: object,
    ) -> object:
        """구조화 출력 enum의 대소문자 차이를 안전하게 정규화한다.

        구조화 출력 공급자가 enum 문자열의 대소문자를 다르게 반환할 수
        있는 상황을 방어하기 위해 문자열 입력만 앞뒤 공백 제거와 소문자
        변환을 적용한다.

        문자열이 아닌 값은 Pydantic 기본 타입 검증에 맡긴다.
        """

        if isinstance(
            value,
            str,
        ):
            return value.strip().lower()

        return value

    @field_validator("answer")
    @classmethod
    def validate_answer(
        cls,
        value: str,
    ) -> str:
        """답변 서식은 보존하면서 공백으로만 구성된 값을 거부한다."""

        if not value.strip():
            raise ValueError("answer must not be empty.")

        return value

    @field_validator("cited_source_ids")
    @classmethod
    def validate_cited_source_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        """선언된 출처 ID 형식과 중복 여부를 검증한다."""

        if any(
            _VALID_SOURCE_ID_PATTERN.fullmatch(
                source_id,
            )
            is None
            for source_id in value
        ):
            raise ValueError("cited_source_ids must contain only valid SOURCE-N values.")

        if len(value) != len(set(value)):
            raise ValueError("cited_source_ids must not contain duplicates.")

        return value

    @model_validator(mode="after")
    def validate_status_contract(
        self,
    ) -> Self:
        """상태, 답변 및 선언 출처 목록의 조합을 검증한다."""

        normalized_answer = self.answer.strip()

        if self.status is RagAnswerStatus.ANSWERED:
            if not self.cited_source_ids:
                raise ValueError("answered structured outputs must contain cited_source_ids.")

            if normalized_answer == _INSUFFICIENT_EVIDENCE_ANSWER:
                raise ValueError(
                    "answered structured outputs must not use the insufficient evidence answer."
                )

            return self

        if self.cited_source_ids:
            raise ValueError(
                "insufficient_evidence structured outputs must not contain cited_source_ids."
            )

        if normalized_answer != _INSUFFICIENT_EVIDENCE_ANSWER:
            raise ValueError("insufficient_evidence structured outputs must use the fixed answer.")

        return self


class ChunkSearcher(Protocol):
    """답변 서비스가 필요로 하는 관련 청크 검색 계약."""

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """현재 질문의 사용자 및 참조문서 범위에서 청크를 검색한다."""

        ...


class PromptBuilder(Protocol):
    """답변 서비스가 필요로 하는 근거 프롬프트 구성 계약."""

    def build(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """검색 청크를 생성 요청과 외부 출처 후보 목록으로 변환한다."""

        ...


class RagAnswerServiceError(RuntimeError):
    """답변 서비스 내부 계약을 안전하게 외부에 전달하기 위한 예외.

    사용자 질문, 청크 원문, 생성 프롬프트, Claude 응답 또는 API Key는
    예외 객체에 저장하지 않는다.

    외부 API 계층은 ``operation`` 값만 이용하여 내부 오류를 분류한다.
    """

    def __init__(
        self,
        *,
        operation: str,
    ) -> None:
        """민감한 원문 없이 실패 작업 식별자만 보관한다."""

        self.operation = operation

        super().__init__(f"RAG answer service operation failed: {operation}")


class RagAnswerService:
    """청크 검색, 프롬프트 구성 및 Claude 생성을 조정한다.

    처리 흐름은 다음과 같다.

    1. 전송 시점 RAG 답변 요청을 독립적인 스냅샷으로 고정한다.
    2. 같은 참조문서 범위의 청크 검색 요청으로 변환한다.
    3. 사용자와 선택된 활성 문서 범위에서 관련 청크를 검색한다.
    4. 검색 결과가 없으면 Claude API를 호출하지 않는다.
    5. 청크를 안전한 프롬프트와 구조화 출력 요청으로 구성한다.
    6. 생성 클라이언트를 통해 답변을 생성한다.
    7. 구조화 결과의 status, answer, cited_source_ids를 검증한다.
    8. 답변 [SOURCE-N]과 cited_source_ids가 일치하는지 확인한다.
    9. 모든 출처 ID가 현재 프롬프트 후보 출처에 존재하는지 검증한다.
    10. 실제 인용된 출처만 최초 인용 순서로 외부 응답에 포함한다.

    기존 테스트 대역이나 다른 GenerationClient 구현체가 아직 구조화
    결과를 제공하지 않는 경우에는 기존 자유 텍스트 인용 검증 경로를
    유지한다.

    운영 Claude 클라이언트는 output_schema 요청을 JSON 객체로
    파싱하므로 실제 Claude 경로에서는 구조화 검증이 우선 적용된다.

    질문, 청크 원문, 프롬프트, Claude 응답 원문 및 API Key는 로그나
    예외 메시지에 포함하지 않는다.
    """

    def __init__(
        self,
        *,
        chunk_searcher: ChunkSearcher,
        prompt_builder: PromptBuilder,
        generation_client: GenerationClient,
    ) -> None:
        """외부 의존성을 공급자 독립 계약으로 주입받는다.

        Args:
            chunk_searcher:
                사용자 질의를 임베딩하고 Qdrant에서 관련 청크를 검색하는
                서비스 계약이다.
            prompt_builder:
                검색 청크를 Claude 프롬프트와 구조화 출력 스키마로
                변환하는 구성기 계약이다.
            generation_client:
                Claude를 포함한 생성 공급자를 추상화한 비동기 생성
                계약이다.
        """

        self._chunk_searcher = chunk_searcher
        self._prompt_builder = prompt_builder
        self._generation_client = generation_client

    async def answer(
        self,
        request: RagAnswerRequest,
    ) -> RagAnswerResponse:
        """전송 시점 참조문서 범위에서만 근거 기반 답변을 생성한다.

        검색 결과가 비어 있으면 프롬프트 구성기와 생성 클라이언트를
        호출하지 않고 ``insufficient_evidence``를 반환한다.

        구조화 정상 답변에는 한 개 이상의 유효한
        ``cited_source_ids``와 답변 본문의 동일한 ``[SOURCE-N]``
        인용이 필요하다.

        모든 출처는 현재 프롬프트 후보 목록에 존재해야 하며 외부
        응답에는 실제 인용 출처만 포함한다.

        Args:
            request:
                사용자 식별자, 참조문서 목록, 질문, 검색 개수 및 선택적
                최소 점수를 포함한 RAG 답변 요청이다.

        Returns:
            선택 문서 근거 기반 답변 또는 근거 부족 결과다.

        Raises:
            EmbeddingError:
                검색 질의 임베딩 생성이 실패한 경우 발생한다.
            IndexStorageError:
                Qdrant 검색 또는 결과 검증이 실패한 경우 발생한다.
            GenerationError:
                Claude 공급자 호출 또는 응답 변환이 실패한 경우
                발생한다.
            RagAnswerServiceError:
                검색 범위, 프롬프트 구성, 구조화 결과, 인용 또는 응답
                매핑 계약이 실패한 경우 발생한다.
        """

        # 각 answer 호출은 전달받은 요청을 독립적인 스냅샷으로
        # 사용한다.
        #
        # 첫 await 전에 깊은 복사본을 생성하므로 질문 처리 중 원본
        # 참조문서 필드가 바뀌어도 현재 질문 범위에는 영향을 주지 않는다.
        request_snapshot = request.model_copy(
            deep=True,
        )

        reference_file_idxs = tuple(
            request_snapshot.reference_file_idxs,
        )
        reference_file_idx_set = frozenset(
            reference_file_idxs,
        )

        search_request = ChunkSearchRequest(
            user_idx=request_snapshot.user_idx,
            reference_file_idxs=(reference_file_idxs),
            query=request_snapshot.query,
            top_k=request_snapshot.top_k,
            score_threshold=(request_snapshot.score_threshold),
        )

        # 질문 원문과 참조문서 식별자 값 자체는 로그에 기록하지 않는다.
        _LOGGER.info(
            "RAG answer chunk search started.",
            extra={
                "event": ("rag_answer_search_started"),
                "user_idx": (request_snapshot.user_idx),
                "reference_file_count": len(
                    reference_file_idxs,
                ),
                "top_k": request_snapshot.top_k,
            },
        )

        search_response = await self._search_chunks(
            request=search_request,
        )

        # ChunkSearchService가 같은 계약을 검증하지만 서비스 경계에서도
        # 사용자 및 참조문서 범위를 다시 확인한다.
        self._validate_search_response_scope(
            response=search_response,
            expected_user_idx=(request_snapshot.user_idx),
            expected_reference_file_idxs=(reference_file_idx_set),
        )

        _LOGGER.info(
            "RAG answer chunk search completed.",
            extra={
                "event": ("rag_answer_search_completed"),
                "user_idx": (request_snapshot.user_idx),
                "reference_file_count": len(
                    reference_file_idxs,
                ),
                "result_count": (search_response.result_count),
            },
        )

        # 검색 결과가 없는데 Claude를 호출하면 외부 지식이나 추측에
        # 의존한 답변이 생성될 수 있으므로 생성 단계를 생략한다.
        if not search_response.results:
            _LOGGER.info(
                "RAG answer skipped generation because evidence was unavailable.",
                extra={
                    "event": ("rag_answer_insufficient_evidence"),
                    "user_idx": (request_snapshot.user_idx),
                    "reference_file_count": len(
                        reference_file_idxs,
                    ),
                    "result_count": 0,
                },
            )

            return RagAnswerResponse(
                answer=(_INSUFFICIENT_EVIDENCE_ANSWER),
                status=(RagAnswerStatus.INSUFFICIENT_EVIDENCE),
            )

        prompt_result = self._build_prompt(
            request=request_snapshot,
            chunks=search_response.results,
        )

        generation_result = await self._generate_answer(
            request=(prompt_result.generation_request),
            user_idx=(request_snapshot.user_idx),
        )

        response = self._build_answer_response(
            generation_result=generation_result,
            prompt_result=prompt_result,
            user_idx=request_snapshot.user_idx,
        )

        # 생성 답변, 구조화 JSON, 질문, 청크 발췌문 및 프롬프트는
        # 기록하지 않는다.
        _LOGGER.info(
            "RAG answer generation completed.",
            extra={
                "event": ("rag_answer_generation_completed"),
                "user_idx": (request_snapshot.user_idx),
                "reference_file_count": len(
                    reference_file_idxs,
                ),
                "answer_status": (response.status.value),
                "source_count": len(
                    response.sources,
                ),
            },
        )

        return response

    async def _search_chunks(
        self,
        *,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """검색 예외에서 질의 또는 요청 본문이 노출되지 않게 한다.

        임베딩 또는 Qdrant 계층의 애플리케이션 예외는 기존 예외
        타입을 유지한다.

        하위 HTTP 예외의 request 객체에 사용자 질문이 포함될 수 있으므로
        원인 및 문맥 예외 참조를 제거한다.

        예상하지 못한 예외는 원본 메시지를 전달하지 않고 안전한 서비스
        오류로 변환한다.
        """

        response: ChunkSearchResponse | None = None
        expected_error: EmbeddingError | IndexStorageError | None = None
        unexpected_error = False

        try:
            response = await self._chunk_searcher.search(
                request,
            )

        except (
            EmbeddingError,
            IndexStorageError,
        ) as error:
            expected_error = error

        except Exception:
            # 원본 예외를 보관하지 않아 민감한 메시지나 속성이
            # 서비스 경계 밖으로 전달될 가능성을 차단한다.
            unexpected_error = True

        if expected_error is not None:
            _remove_exception_chain(
                expected_error,
            )

            _LOGGER.warning(
                "RAG answer chunk search failed.",
                extra={
                    "event": ("rag_answer_search_failed"),
                    "user_idx": request.user_idx,
                },
            )

            raise expected_error

        if unexpected_error or response is None:
            _LOGGER.error(
                "RAG answer chunk search failed unexpectedly.",
                extra={
                    "event": ("rag_answer_search_failed"),
                    "user_idx": request.user_idx,
                },
            )

            raise RagAnswerServiceError(
                operation="chunk_search_failed",
            )

        return response

    def _validate_search_response_scope(
        self,
        *,
        response: ChunkSearchResponse,
        expected_user_idx: int,
        expected_reference_file_idxs: frozenset[int],
    ) -> None:
        """검색 응답이 현재 질문의 사용자 및 문서 범위를 지키는지 검증한다."""

        if response.user_idx != expected_user_idx:
            _LOGGER.error(
                "RAG answer search user scope contract failed.",
                extra={
                    "event": ("rag_answer_search_scope_failed"),
                    "user_idx": expected_user_idx,
                },
            )

            raise RagAnswerServiceError(
                operation=("search_user_scope_contract_violation"),
            )

        if any(chunk.file_idx not in expected_reference_file_idxs for chunk in response.results):
            _LOGGER.error(
                "RAG answer search reference file scope contract failed.",
                extra={
                    "event": ("rag_answer_reference_file_scope_failed"),
                    "user_idx": expected_user_idx,
                    "reference_file_count": len(
                        expected_reference_file_idxs,
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation=("search_reference_file_scope_contract_violation"),
            )

    def _build_prompt(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """프롬프트 구성 오류에서 질문과 청크 원문을 제거한다.

        Pydantic ValidationError와 ValueError는 입력값을 오류 표현에
        포함할 수 있다.

        원본 예외를 그대로 전파하지 않고 안전한 작업 식별자만 포함한
        서비스 예외로 변환한다.
        """

        prompt_result: RagPromptBuildResult | None = None
        build_failed = False

        try:
            prompt_result = self._prompt_builder.build(
                request=request,
                chunks=chunks,
            )

        except Exception:
            # 원본 예외를 변수에 저장하지 않고 즉시 폐기한다.
            build_failed = True

        if build_failed or prompt_result is None:
            _LOGGER.error(
                "RAG answer prompt build failed.",
                extra={
                    "event": ("rag_answer_prompt_build_failed"),
                    "user_idx": request.user_idx,
                    "source_count": len(chunks),
                },
            )

            raise RagAnswerServiceError(
                operation="prompt_build_failed",
            )

        return prompt_result

    async def _generate_answer(
        self,
        *,
        request: GenerationRequest,
        user_idx: int,
    ) -> GenerationResult:
        """생성 공급자 오류를 안전한 예외 체인으로 정리한다.

        기존 ``GenerationError`` 타입은 유지하여 API 계층이 인증 실패,
        요청 제한, 타임아웃, 서버 장애 및 잘못된 구조화 응답을 구분할
        수 있게 한다.

        Anthropic SDK 예외의 요청 또는 응답 객체에 프롬프트가 남을 수
        있으므로 원인 예외 참조는 제거한다.
        """

        result: GenerationResult | None = None
        provider_error: GenerationError | None = None
        unexpected_error = False

        try:
            result = await self._generation_client.generate(
                request=request,
            )

        except GenerationError as error:
            provider_error = error

        except Exception:
            # 계약에 없는 예외는 원본 메시지를 전달하지 않고 일반
            # 서비스 오류로 변환한다.
            unexpected_error = True

        if provider_error is not None:
            _remove_exception_chain(
                provider_error,
            )

            _LOGGER.warning(
                "RAG answer generation provider request failed.",
                extra={
                    "event": ("rag_answer_generation_failed"),
                    "user_idx": user_idx,
                },
            )

            raise provider_error

        if unexpected_error or result is None:
            _LOGGER.error(
                "RAG answer generation failed unexpectedly.",
                extra={
                    "event": ("rag_answer_generation_failed"),
                    "user_idx": user_idx,
                },
            )

            raise RagAnswerServiceError(
                operation="generation_failed",
            )

        return result

    def _parse_structured_answer(
        self,
        *,
        generation_result: GenerationResult,
        user_idx: int,
    ) -> _StructuredRagAnswer | None:
        """구조화 출력 매핑을 RAG 도메인 모델로 검증한다.

        ``structured_output``이 없는 결과는 기존 GenerationClient와
        테스트 대역과의 호환성을 위해 자유 텍스트 경로로 처리한다.

        검증 실패 시 Pydantic 오류 객체를 보관하거나 출력하지 않는다.

        오류에는 Claude 답변과 JSON 입력값이 포함될 수 있으므로 안전한
        실패 사유만 로그에 기록한다.
        """

        if generation_result.structured_output is None:
            return None

        structured_answer: _StructuredRagAnswer | None = None
        validation_failed = False

        try:
            structured_answer = _StructuredRagAnswer.model_validate(
                dict(generation_result.structured_output),
            )
        except Exception:
            validation_failed = True

        if validation_failed or structured_answer is None:
            _LOGGER.error(
                "RAG structured generation response validation failed.",
                extra={
                    "event": ("rag_answer_citation_validation_failed"),
                    "user_idx": user_idx,
                    "validation_reason": ("structured_response_invalid"),
                },
            )

            raise RagAnswerServiceError(
                operation=(_GENERATION_RESPONSE_VALIDATION_OPERATION),
            )

        return structured_answer

    def _resolve_cited_sources(
        self,
        *,
        answer: str,
        prompt_sources: tuple[
            RagAnswerSource,
            ...,
        ],
        user_idx: int,
        declared_source_ids: (tuple[str, ...] | None) = None,
    ) -> tuple[RagAnswerSource, ...]:
        """답변 인용과 구조화 출처 선언을 검증한다.

        답변의 ``[SOURCE-N]``은 최초 등장 순서로 수집하며 같은
        식별자가 반복되어도 응답 출처에는 한 번만 포함한다.

        구조화 출력의 ``declared_source_ids``가 제공되면 답변 본문의
        최초 인용 순서와 정확히 일치해야 한다.

        모든 식별자는 현재 생성 프롬프트 후보 출처와 일치해야 한다.

        답변 원문과 실제 출처 ID는 로그나 예외에 포함하지 않고 실패
        종류와 개수만 남긴다.
        """

        prompt_source_by_id = {source.source_id: source for source in prompt_sources}

        # 다른 PromptBuilder가 주입되더라도 dict 변환에서 중복 출처가
        # 조용히 덮어써지지 않도록 서비스 경계에서 재검증한다.
        if len(prompt_source_by_id) != len(prompt_sources):
            _LOGGER.error(
                "RAG answer prompt source contract failed.",
                extra={
                    "event": ("rag_answer_prompt_source_contract_failed"),
                    "user_idx": user_idx,
                    "prompt_source_count": len(
                        prompt_sources,
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation=("prompt_source_contract_violation"),
            )

        cited_source_ids = _extract_unique_source_ids(
            answer,
        )

        if not cited_source_ids:
            _LOGGER.error(
                "RAG answer citation validation failed because no citation was present.",
                extra={
                    "event": ("rag_answer_citation_validation_failed"),
                    "user_idx": user_idx,
                    "validation_reason": ("citation_required"),
                    "citation_count": 0,
                    "prompt_source_count": len(
                        prompt_sources,
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation=(_GENERATION_RESPONSE_VALIDATION_OPERATION),
            )

        if declared_source_ids is not None and cited_source_ids != declared_source_ids:
            _LOGGER.error(
                "RAG answer citation validation failed "
                "because structured source declarations "
                "did not match the answer.",
                extra={
                    "event": ("rag_answer_citation_validation_failed"),
                    "user_idx": user_idx,
                    "validation_reason": ("declared_citation_mismatch"),
                    "citation_count": len(
                        cited_source_ids,
                    ),
                    "declared_source_count": len(
                        declared_source_ids,
                    ),
                    "prompt_source_count": len(
                        prompt_sources,
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation=(_GENERATION_RESPONSE_VALIDATION_OPERATION),
            )

        unknown_source_count = sum(
            source_id not in prompt_source_by_id for source_id in cited_source_ids
        )

        if unknown_source_count > 0:
            _LOGGER.error(
                "RAG answer citation validation failed because an unknown source was cited.",
                extra={
                    "event": ("rag_answer_citation_validation_failed"),
                    "user_idx": user_idx,
                    "validation_reason": ("unknown_source"),
                    "citation_count": len(
                        cited_source_ids,
                    ),
                    "unknown_source_count": (unknown_source_count),
                    "prompt_source_count": len(
                        prompt_sources,
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation=(_GENERATION_RESPONSE_VALIDATION_OPERATION),
            )

        return tuple(prompt_source_by_id[source_id] for source_id in cited_source_ids)

    def _build_answer_response(
        self,
        *,
        generation_result: GenerationResult,
        prompt_result: RagPromptBuildResult,
        user_idx: int,
    ) -> RagAnswerResponse:
        """생성 결과를 검증한 뒤 외부 RAG 응답으로 변환한다.

        구조화 출력이 존재하면 status, answer 및 cited_source_ids를
        우선 사용한다.

        structured_output이 없는 결과에는 기존 자유 텍스트 인용 검증을
        적용하여 테스트 대역과 공급자 독립 인터페이스 호환성을 유지한다.

        근거 부족 응답에는 출처와 Claude 생성 메타데이터를 포함하지
        않는다.

        정상 답변에는 실제 인용 출처, 모델 ID 및 사용량을 포함한다.
        """

        structured_answer = self._parse_structured_answer(
            generation_result=(generation_result),
            user_idx=user_idx,
        )

        if structured_answer is not None:
            if structured_answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
                return RagAnswerResponse(
                    answer=(_INSUFFICIENT_EVIDENCE_ANSWER),
                    status=(RagAnswerStatus.INSUFFICIENT_EVIDENCE),
                )

            answer = structured_answer.answer
            declared_source_ids: tuple[str, ...] | None = structured_answer.cited_source_ids
        else:
            # 구조화 출력이 없는 기존 결과는 앞뒤 공백과 개행만
            # 허용하여 고정 근거 부족 문구를 판정한다.
            if generation_result.text.strip() == _INSUFFICIENT_EVIDENCE_ANSWER:
                return RagAnswerResponse(
                    answer=(_INSUFFICIENT_EVIDENCE_ANSWER),
                    status=(RagAnswerStatus.INSUFFICIENT_EVIDENCE),
                )

            answer = generation_result.text
            declared_source_ids = None

        cited_sources = self._resolve_cited_sources(
            answer=answer,
            prompt_sources=(prompt_result.sources),
            user_idx=user_idx,
            declared_source_ids=(declared_source_ids),
        )

        response: RagAnswerResponse | None = None
        mapping_failed = False

        try:
            response = RagAnswerResponse(
                answer=answer,
                status=RagAnswerStatus.ANSWERED,
                sources=cited_sources,
                model=generation_result.model,
                usage=RagAnswerUsage(
                    input_tokens=(generation_result.usage.input_tokens),
                    output_tokens=(generation_result.usage.output_tokens),
                ),
                stop_reason=(generation_result.stop_reason),
            )

        except Exception:
            # 생성 답변과 출처가 포함될 수 있는 원본 Pydantic 예외를
            # 서비스 경계 밖으로 전달하지 않는다.
            mapping_failed = True

        if mapping_failed or response is None:
            _LOGGER.error(
                "RAG answer response mapping failed.",
                extra={
                    "event": ("rag_answer_response_mapping_failed"),
                    "user_idx": user_idx,
                    "source_count": len(
                        cited_sources,
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation="response_mapping_failed",
            )

        return response


def _extract_unique_source_ids(
    answer: str,
) -> tuple[str, ...]:
    """답변에서 인용 ID를 중복 없이 최초 등장 순서로 추출한다.

    정규식 검색은 답변 왼쪽에서 오른쪽으로 진행한다.

    이미 확인한 ID는 ``seen_source_ids``로 제외하므로 같은 출처가
    여러 문장에 반복 인용되어도 첫 번째 위치를 기준으로 한 번만
    반환한다.
    """

    ordered_source_ids: list[str] = []
    seen_source_ids: set[str] = set()

    for match in _SOURCE_CITATION_PATTERN.finditer(
        answer,
    ):
        source_id = match.group(
            "source_id",
        )

        if source_id in seen_source_ids:
            continue

        seen_source_ids.add(
            source_id,
        )
        ordered_source_ids.append(
            source_id,
        )

    return tuple(
        ordered_source_ids,
    )


def _remove_exception_chain(
    error: BaseException,
) -> None:
    """하위 계층 예외가 보관한 민감한 원인 예외 참조를 제거한다.

    ``raise ... from None``은 traceback 표시만 억제하며 예외 객체의
    ``__context__``에는 원본 예외가 남을 수 있다.

    질문이나 생성 프롬프트를 포함할 수 있는 HTTP 요청 객체에 접근하지
    못하도록 원인 및 문맥 참조를 명시적으로 제거한다.
    """

    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
