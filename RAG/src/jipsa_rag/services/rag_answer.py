"""청크 검색과 Claude 생성을 연결하여 근거 기반 RAG 답변을 생성한다."""

import logging
from typing import Final, Protocol

from jipsa_rag.infrastructure.embedding.exceptions import EmbeddingError
from jipsa_rag.infrastructure.generation.client import GenerationClient
from jipsa_rag.infrastructure.generation.exceptions import GenerationError
from jipsa_rag.infrastructure.generation.models import (
    GenerationRequest,
    GenerationResult,
)
from jipsa_rag.infrastructure.indexing.exceptions import IndexStorageError
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.rag_answer import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagAnswerStatus,
    RagAnswerUsage,
)
from jipsa_rag.services.prompt_builder import RagPromptBuildResult

_LOGGER = logging.getLogger(__name__)

# 검색 결과가 없을 때 Claude API를 호출하지 않고 반환할 고정 안내 문구다.
#
# 프롬프트 구성기의 시스템 규칙에서도 같은 문구를 사용하고 있으므로,
# 사용자는 검색 단계에서 근거가 없을 때와 Claude가 문서 근거만으로
# 답변할 수 없다고 판단한 경우에 일관된 안내를 받는다.
_INSUFFICIENT_EVIDENCE_ANSWER: Final[str] = (
    "제공된 문서 근거만으로는 답변할 수 없습니다."
)


class ChunkSearcher(Protocol):
    """답변 서비스가 필요로 하는 관련 청크 검색 계약."""

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """현재 질문의 사용자 및 참조문서 범위에서 관련 청크를 검색한다."""

        ...


class PromptBuilder(Protocol):
    """답변 서비스가 필요로 하는 근거 프롬프트 구성 계약."""

    def build(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """검색 청크를 Claude 생성 요청과 외부 출처 목록으로 변환한다."""

        ...


class RagAnswerServiceError(RuntimeError):
    """답변 서비스 내부 계약을 안전하게 외부에 전달하기 위한 예외.

    사용자 질문, 청크 원문, 생성 프롬프트 또는 API Key는 예외 객체에
    저장하지 않는다.

    외부 API 계층은 ``operation`` 값만 이용하여 내부 오류를 분류할 수 있다.
    """

    def __init__(
        self,
        *,
        operation: str,
    ) -> None:
        """질문이나 청크 원문 없이 실패 작업 식별자만 보관한다."""

        self.operation = operation

        super().__init__(
            f"RAG answer service operation failed: {operation}"
        )


class RagAnswerService:
    """청크 검색, 프롬프트 구성 및 Claude 생성을 하나의 유스케이스로 조정한다.

    처리 흐름은 다음과 같다.

    1. 전송 시점의 RAG 답변 요청을 독립적인 스냅샷으로 고정한다.
    2. 답변 요청을 같은 참조문서 범위의 청크 검색 요청으로 변환한다.
    3. 사용자와 선택된 활성 문서 범위에서 관련 청크를 검색한다.
    4. 검색 결과가 없으면 Claude API를 호출하지 않는다.
    5. 검색 청크를 안전한 근거 프롬프트로 구성한다.
    6. 생성 클라이언트를 통해 답변을 생성한다.
    7. 답변, 출처 및 토큰 사용량을 외부 응답으로 변환한다.

    질문, 청크 원문, 프롬프트 및 API Key는 로그 필드나 예외 메시지에
    포함하지 않는다.
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
                검색 청크를 Claude용 시스템 및 사용자 프롬프트로 변환하는
                구성기 계약이다.
            generation_client:
                Claude를 포함한 생성 공급자를 추상화한 비동기 생성 계약이다.
        """

        self._chunk_searcher = chunk_searcher
        self._prompt_builder = prompt_builder
        self._generation_client = generation_client

    async def answer(
        self,
        request: RagAnswerRequest,
    ) -> RagAnswerResponse:
        """전송 시점의 참조문서 범위에서만 근거 기반 답변을 생성한다.

        검색 결과가 비어 있으면 프롬프트 구성기와 생성 클라이언트를 호출하지
        않고 ``insufficient_evidence`` 응답을 반환한다.

        로그에는 사용자 식별자, 참조문서 수, 검색 결과 수 및 출처 수처럼
        운영에 필요한 최소 메타데이터만 기록한다. 질문, 청크, 프롬프트 및
        생성 결과 원문은 기록하지 않는다.

        Args:
            request:
                사용자 식별자, 참조문서 식별자 목록, 질문, 검색 개수 및
                선택적 최소 점수를 포함한 RAG 답변 요청이다.

        Returns:
            선택된 문서 근거 기반 답변 또는 근거 부족 결과다.

        Raises:
            EmbeddingError:
                검색 질의 임베딩 생성이 실패한 경우 발생한다.
            IndexStorageError:
                Qdrant 검색 또는 검색 결과 검증이 실패한 경우 발생한다.
            GenerationError:
                Claude 생성 공급자 호출 또는 응답 변환이 실패한 경우 발생한다.
            RagAnswerServiceError:
                검색 범위, 프롬프트 구성, 응답 매핑 또는 예상하지 못한
                내부 호출이 실패한 경우 발생한다.
        """

        # 각 answer 호출은 전달받은 요청을 독립적인 스냅샷으로 사용한다.
        #
        # 첫 await 전에 깊은 복사본을 생성하므로 질문 처리 중 호출자나 다른
        # 내부 코드가 원본 모델의 참조문서 필드를 재할당하더라도 현재 질문의
        # 검색 범위와 프롬프트에는 영향을 주지 않는다.
        #
        # 서비스 인스턴스에는 참조문서 목록을 저장하지 않으므로 같은 대화의
        # 다음 질문은 다음 호출에서 전달된 새 목록을 독립적으로 사용한다.
        request_snapshot = request.model_copy(
            deep=True,
        )

        reference_file_idxs = tuple(
            request_snapshot.reference_file_idxs
        )
        reference_file_idx_set = frozenset(
            reference_file_idxs
        )

        search_request = ChunkSearchRequest(
            user_idx=request_snapshot.user_idx,
            reference_file_idxs=reference_file_idxs,
            query=request_snapshot.query,
            top_k=request_snapshot.top_k,
            score_threshold=request_snapshot.score_threshold,
        )

        # 질문 원문과 참조문서 식별자 값 자체는 로그에 기록하지 않는다.
        #
        # top_k와 reference_file_count는 민감한 사용자 입력이 아니라
        # 검색 동작을 설명하는 안전한 운영 메타데이터다.
        _LOGGER.info(
            "RAG answer chunk search started.",
            extra={
                "event": "rag_answer_search_started",
                "user_idx": request_snapshot.user_idx,
                "reference_file_count": len(
                    reference_file_idxs
                ),
                "top_k": request_snapshot.top_k,
            },
        )

        search_response = await self._search_chunks(
            request=search_request,
        )

        # ChunkSearchService가 같은 계약을 검증하지만 답변 서비스 경계에서도
        # 사용자 및 참조문서 범위를 다시 확인한다.
        #
        # 향후 다른 검색 구현체가 주입되더라도 선택하지 않은 문서의 청크가
        # 답변 프롬프트와 출처에 포함되는 것을 방지한다.
        self._validate_search_response_scope(
            response=search_response,
            expected_user_idx=request_snapshot.user_idx,
            expected_reference_file_idxs=reference_file_idx_set,
        )

        _LOGGER.info(
            "RAG answer chunk search completed.",
            extra={
                "event": "rag_answer_search_completed",
                "user_idx": request_snapshot.user_idx,
                "reference_file_count": len(
                    reference_file_idxs
                ),
                "result_count": search_response.result_count,
            },
        )

        # 검색 결과가 없을 때 Claude API를 호출하면 외부 지식이나 추측에
        # 의존한 답변이 생성될 수 있다.
        #
        # 근거가 없는 경우에는 프롬프트 구성과 생성 호출을 모두 생략하고
        # 고정된 근거 부족 응답을 반환한다.
        if not search_response.results:
            _LOGGER.info(
                "RAG answer skipped generation because evidence was unavailable.",
                extra={
                    "event": "rag_answer_insufficient_evidence",
                    "user_idx": request_snapshot.user_idx,
                    "reference_file_count": len(
                        reference_file_idxs
                    ),
                    "result_count": 0,
                },
            )

            return RagAnswerResponse(
                answer=_INSUFFICIENT_EVIDENCE_ANSWER,
                status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
            )

        prompt_result = self._build_prompt(
            request=request_snapshot,
            chunks=search_response.results,
        )

        generation_result = await self._generate_answer(
            request=prompt_result.generation_request,
            user_idx=request_snapshot.user_idx,
        )

        response = self._build_answer_response(
            generation_result=generation_result,
            prompt_result=prompt_result,
            user_idx=request_snapshot.user_idx,
        )

        # 생성된 답변, 질문, 청크 발췌문 및 모델 프롬프트는 기록하지 않는다.
        #
        # 정상 완료 여부와 출처 개수만 기록하여 운영 추적에 필요한 정보를
        # 확보하면서 문서 내용 노출을 방지한다.
        _LOGGER.info(
            "RAG answer generation completed.",
            extra={
                "event": "rag_answer_generation_completed",
                "user_idx": request_snapshot.user_idx,
                "reference_file_count": len(
                    reference_file_idxs
                ),
                "source_count": len(response.sources),
            },
        )

        return response

    async def _search_chunks(
        self,
        *,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """검색 예외의 원인 체인에서 질의 또는 요청 본문이 노출되지 않게 한다.

        임베딩 또는 Qdrant 계층의 애플리케이션 예외는 기존 예외 타입을
        유지하여 상위 API 계층이 장애 종류를 구분할 수 있게 한다.

        다만 하위 HTTP 클라이언트 예외의 ``request`` 객체에는 사용자 질문이
        포함될 수 있으므로 ``__cause__``와 ``__context__`` 참조를 제거한다.

        예상하지 못한 예외는 원본 메시지를 전달하지 않고
        ``RagAnswerServiceError``로 변환한다.
        """

        response: ChunkSearchResponse | None = None
        expected_error: EmbeddingError | IndexStorageError | None = None
        unexpected_error = False

        try:
            response = await self._chunk_searcher.search(
                request
            )

        except (
            EmbeddingError,
            IndexStorageError,
        ) as error:
            # 예외 메시지나 traceback을 이 위치에서 로그로 출력하지 않는다.
            #
            # 예외 객체는 except 블록 밖에서 원인 체인을 제거한 뒤 다시
            # 발생시켜 사용자 질문이 하위 요청 객체를 통해 노출되지 않게 한다.
            expected_error = error

        except Exception:
            # 예상하지 못한 예외는 원본 객체를 보관하지 않는다.
            #
            # 질문이나 청크가 원본 예외 메시지 또는 속성에 포함되어 있어도
            # 이후 로그 및 서비스 예외로 전달되지 않는다.
            unexpected_error = True

        if expected_error is not None:
            _remove_exception_chain(
                expected_error
            )

            _LOGGER.warning(
                "RAG answer chunk search failed.",
                extra={
                    "event": "rag_answer_search_failed",
                    "user_idx": request.user_idx,
                },
            )

            raise expected_error

        if unexpected_error or response is None:
            _LOGGER.error(
                "RAG answer chunk search failed unexpectedly.",
                extra={
                    "event": "rag_answer_search_failed",
                    "user_idx": request.user_idx,
                },
            )

            # except 블록 밖에서 새 예외를 발생시켜 민감한 원본 예외가
            # 암묵적인 __context__로 연결되지 않게 한다.
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
        """검색 응답이 현재 질문의 사용자 및 참조문서 범위를 지키는지 검증한다."""

        if response.user_idx != expected_user_idx:
            _LOGGER.error(
                "RAG answer search user scope contract failed.",
                extra={
                    "event": "rag_answer_search_scope_failed",
                    "user_idx": expected_user_idx,
                },
            )

            raise RagAnswerServiceError(
                operation="search_user_scope_contract_violation",
            )

        if any(
            chunk.file_idx
            not in expected_reference_file_idxs
            for chunk in response.results
        ):
            _LOGGER.error(
                "RAG answer search reference file scope contract failed.",
                extra={
                    "event": "rag_answer_reference_file_scope_failed",
                    "user_idx": expected_user_idx,
                    "reference_file_count": len(
                        expected_reference_file_idxs
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation=(
                    "search_reference_file_scope_contract_violation"
                ),
            )

    def _build_prompt(
        self,
        *,
        request: RagAnswerRequest,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagPromptBuildResult:
        """프롬프트 구성 오류에서 질문과 청크 원문을 제거한다.

        Pydantic ValidationError와 ValueError는 입력값을 오류 표현에 포함할
        수 있다. 프롬프트 구성 중 발생한 원본 예외를 그대로 전파하지 않고
        안전한 작업 식별자만 포함한 서비스 예외로 변환한다.
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
            #
            # 질문, 청크 원문 또는 직렬화된 프롬프트가 예외 객체에 포함되어
            # 있더라도 서비스 경계 밖으로 전달되지 않는다.
            build_failed = True

        if build_failed or prompt_result is None:
            _LOGGER.error(
                "RAG answer prompt build failed.",
                extra={
                    "event": "rag_answer_prompt_build_failed",
                    "user_idx": request.user_idx,
                    "source_count": len(chunks),
                },
            )

            # except 블록 밖에서 발생시키므로 민감한 원본 예외가
            # __context__에 자동 연결되지 않는다.
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

        기존 생성 계층의 ``GenerationError`` 타입은 유지한다. 이를 통해
        API 계층이 인증 실패, 요청 제한, 타임아웃 및 서버 장애를 구분할 수 있다.

        다만 Anthropic SDK 예외의 요청 객체나 응답 객체에 프롬프트 또는
        공급자 정보가 남을 수 있으므로 원인 예외 참조는 제거한다.
        """

        result: GenerationResult | None = None
        provider_error: GenerationError | None = None
        unexpected_error = False

        try:
            result = await self._generation_client.generate(
                request=request,
            )

        except GenerationError as error:
            # 질문과 청크가 포함된 GenerationRequest를 로그에 기록하지 않는다.
            #
            # GenerationError 자체는 공급자 독립 예외 계약이므로 타입을
            # 유지하되 원인 SDK 예외 참조만 제거한다.
            provider_error = error

        except Exception:
            # 외부 생성 클라이언트가 계약에 없는 예외를 반환하면 원본
            # 메시지를 전달하지 않고 일반 서비스 오류로 변환한다.
            unexpected_error = True

        if provider_error is not None:
            _remove_exception_chain(
                provider_error
            )

            _LOGGER.warning(
                "RAG answer generation provider request failed.",
                extra={
                    "event": "rag_answer_generation_failed",
                    "user_idx": user_idx,
                },
            )

            raise provider_error

        if unexpected_error or result is None:
            _LOGGER.error(
                "RAG answer generation failed unexpectedly.",
                extra={
                    "event": "rag_answer_generation_failed",
                    "user_idx": user_idx,
                },
            )

            raise RagAnswerServiceError(
                operation="generation_failed",
            )

        return result

    def _build_answer_response(
        self,
        *,
        generation_result: GenerationResult,
        prompt_result: RagPromptBuildResult,
        user_idx: int,
    ) -> RagAnswerResponse:
        """생성 결과를 외부 응답으로 변환하고 검증 오류를 안전하게 처리한다.

        Pydantic 검증 오류는 전체 입력 객체를 표현할 수 있다. 생성 답변이나
        출처 발췌문이 예외에 포함되지 않도록 원본 오류를 서비스 경계에서
        안전한 예외로 변환한다.
        """

        response: RagAnswerResponse | None = None
        mapping_failed = False

        try:
            response = RagAnswerResponse(
                answer=generation_result.text,
                status=RagAnswerStatus.ANSWERED,
                sources=prompt_result.sources,
                model=generation_result.model,
                usage=RagAnswerUsage(
                    input_tokens=(
                        generation_result.usage.input_tokens
                    ),
                    output_tokens=(
                        generation_result.usage.output_tokens
                    ),
                ),
                stop_reason=generation_result.stop_reason,
            )

        except Exception:
            # 생성 답변과 출처가 포함될 수 있는 원본 검증 예외를
            # 서비스 경계 밖으로 전달하지 않는다.
            mapping_failed = True

        if mapping_failed or response is None:
            _LOGGER.error(
                "RAG answer response mapping failed.",
                extra={
                    "event": "rag_answer_response_mapping_failed",
                    "user_idx": user_idx,
                    "source_count": len(
                        prompt_result.sources
                    ),
                },
            )

            raise RagAnswerServiceError(
                operation="response_mapping_failed",
            )

        return response


def _remove_exception_chain(
    error: BaseException,
) -> None:
    """하위 계층 예외가 보관한 민감한 원인 예외 참조를 제거한다.

    ``raise ... from None``은 traceback 표시만 억제하며 예외 객체의
    ``__context__``에는 원본 예외가 남을 수 있다.

    질문이나 생성 프롬프트를 포함할 수 있는 HTTP 요청 객체에 접근하지
    못하도록 원인 및 문맥 참조를 명시적으로 제거한다.

    Args:
        error:
            외부로 다시 전달할 애플리케이션 예외 객체다.
    """

    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True