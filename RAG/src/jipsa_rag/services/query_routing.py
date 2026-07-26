"""RAG 질의를 조회형과 종합형으로 분류하고 검색 결과 전략을 선택한다."""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from jipsa_rag.infrastructure.generation.client import GenerationClient
from jipsa_rag.schemas.chunk_search import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    ChunkSearchResult,
)
from jipsa_rag.schemas.file_processing import SupportedFileType
from jipsa_rag.services.rag_answer import (
    ChunkSearcher,
    PromptBuilder,
    RagAnswerService,
)

_LOGGER = logging.getLogger(__name__)

# 다문서 비교·대조·종합 의도를 직접 나타내는 한국어 표현이다.
#
# 단순히 여러 참조문서가 선택되었다는 이유만으로 synthesis로 분류하지
# 않는다. 사용자가 단일 사실을 조회하는 질문을 여러 문서 범위에서
# 실행할 수 있으므로, 명시적인 종합 의도가 있을 때만 분기한다.
_KOREAN_SYNTHESIS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:비교|대조|차이점?|공통점|유사점|상충|모순)"),
    re.compile(r"(?:종합|통합|취합|한데\s*모아)"),
    re.compile(r"(?:문서|pdf|파일)\s*별"),
    re.compile(r"각(?:각의)?\s*(?:문서|pdf|파일)"),
    re.compile(
        r"(?:모든|전체|여러|복수의?|두|세)\s*(?:참조\s*)?"
        r"(?:문서|pdf|파일).*(?:요약|정리|분석)"
    ),
    re.compile(
        r"(?:요약|정리|분석).*(?:모든|전체|여러|복수의?|각(?:각의)?|두|세)\s*"
        r"(?:참조\s*)?(?:문서|pdf|파일)"
    ),
)

# 영어 질문에서도 같은 라우팅 계약을 유지하기 위한 표현이다.
_ENGLISH_SYNTHESIS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\b(?:compare|contrast|differences?|similarities?|synthesize|synthesis|"
        r"aggregate|consolidate)\b"
    ),
    re.compile(
        r"\b(?:all|multiple|each|two|three)\b.*\b(?:documents?|pdfs?|files?)\b.*"
        r"\b(?:summari[sz]e|summary|analy[sz]e|analysis)\b"
    ),
    re.compile(
        r"\b(?:summari[sz]e|summary|analy[sz]e|analysis)\b.*"
        r"\b(?:all|multiple|each|two|three)\b.*\b(?:documents?|pdfs?|files?)\b"
    ),
)


class RagQueryType(StrEnum):
    """RAG 답변 처리 전략을 선택하는 질의 유형."""

    LOOKUP = "lookup"
    SYNTHESIS = "synthesis"


class RagQueryClassifier(Protocol):
    """검색 요청을 조회형 또는 종합형으로 분류하는 계약."""

    def classify(
        self,
        request: ChunkSearchRequest,
    ) -> RagQueryType:
        """질문 원문을 외부에 노출하지 않고 질의 유형을 반환한다."""
        ...


class RuleBasedRagQueryClassifier:
    """명시적인 다문서 종합 표현을 사용하는 결정적 질의 분류기.

    분류기는 외부 LLM을 호출하지 않으며 같은 요청에 항상 같은 결과를
    반환한다. 참조문서가 한 개뿐이면 비교·종합 표현이 포함되어 있어도
    기존 조회 흐름을 사용한다.

    두 개 이상의 참조문서가 선택된 경우에도 종합 의도가 명시되지 않은
    질문은 기본값인 ``lookup``으로 유지한다. 이 기본값이 기존 RAG 답변
    흐름의 하위 호환성을 보장한다.
    """

    def classify(
        self,
        request: ChunkSearchRequest,
    ) -> RagQueryType:
        """참조문서 수와 질문의 규칙 표현을 기준으로 질의를 분류한다."""

        if len(request.reference_file_idxs) < 2:
            return RagQueryType.LOOKUP

        normalized_query = _normalize_query(
            request.query,
        )

        if any(
            pattern.search(normalized_query) is not None for pattern in _KOREAN_SYNTHESIS_PATTERNS
        ):
            return RagQueryType.SYNTHESIS

        if any(
            pattern.search(normalized_query) is not None for pattern in _ENGLISH_SYNTHESIS_PATTERNS
        ):
            return RagQueryType.SYNTHESIS

        return RagQueryType.LOOKUP


@dataclass(frozen=True, slots=True)
class PdfChunkGroup:
    """같은 PDF 원본에 속한 검색 청크 묶음."""

    file_idx: int
    rag_document_idx: int
    file_name: str
    chunks: tuple[ChunkSearchResult, ...]

    def __post_init__(self) -> None:
        """그룹 안의 모든 청크가 동일한 활성 PDF를 가리키는지 검증한다."""

        if not self.chunks:
            raise ValueError("PDF chunk group must contain at least one chunk.")

        if any(chunk.file_type != SupportedFileType.PDF for chunk in self.chunks):
            raise ValueError("PDF chunk group must contain only PDF chunks.")

        if any(chunk.file_idx != self.file_idx for chunk in self.chunks):
            raise ValueError("PDF chunk group must contain one file_idx.")

        if any(chunk.rag_document_idx != self.rag_document_idx for chunk in self.chunks):
            raise ValueError("PDF chunk group must contain one rag_document_idx.")

        if any(chunk.file_name != self.file_name for chunk in self.chunks):
            raise ValueError("PDF chunk group must contain one file_name snapshot.")


@dataclass(frozen=True, slots=True)
class RagQueryRoutingPlan:
    """질의 유형별 프롬프트 입력 순서와 PDF 그룹 정보."""

    query_type: RagQueryType
    prompt_chunks: tuple[ChunkSearchResult, ...]
    pdf_groups: tuple[PdfChunkGroup, ...] = ()

    def __post_init__(self) -> None:
        """전략 결과가 청크를 누락하거나 임의로 추가하지 않도록 검증한다."""

        if not self.prompt_chunks:
            raise ValueError("RAG query routing plan must contain prompt chunks.")

        if self.query_type is RagQueryType.LOOKUP:
            if self.pdf_groups:
                raise ValueError("Lookup routing plan must not contain PDF groups.")

            return

        if not self.pdf_groups:
            raise ValueError("Synthesis routing plan must contain PDF groups.")

        flattened_chunks = tuple(chunk for group in self.pdf_groups for chunk in group.chunks)

        if flattened_chunks != self.prompt_chunks:
            raise ValueError("Synthesis prompt chunks must match the flattened PDF groups.")


class RagQueryStrategy(Protocol):
    """검색 청크를 질의 유형에 맞는 프롬프트 순서로 준비하는 계약."""

    def prepare(
        self,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagQueryRoutingPlan:
        """검색 결과를 손실 없이 전략별 처리 계획으로 변환한다."""
        ...


class _LookupRagQueryStrategy:
    """기존 관련도 순서를 그대로 유지하는 조회 전략."""

    def prepare(
        self,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagQueryRoutingPlan:
        """기존 검색 결과 tuple을 변경하지 않고 그대로 전달한다."""

        return RagQueryRoutingPlan(
            query_type=RagQueryType.LOOKUP,
            prompt_chunks=chunks,
        )


class _SynthesisRagQueryStrategy:
    """검색 청크를 PDF별로 묶어 프롬프트 입력 순서를 구성하는 종합 전략."""

    def prepare(
        self,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagQueryRoutingPlan:
        """PDF 최초 등장 순서와 문서 내부 관련도 순서를 모두 보존한다."""

        pdf_groups = group_chunks_by_pdf(
            chunks,
        )
        prompt_chunks = tuple(chunk for group in pdf_groups for chunk in group.chunks)

        return RagQueryRoutingPlan(
            query_type=RagQueryType.SYNTHESIS,
            prompt_chunks=prompt_chunks,
            pdf_groups=pdf_groups,
        )


class RagQueryRouter(Protocol):
    """분류된 질의를 해당 검색 결과 준비 전략으로 연결하는 계약."""

    def route(
        self,
        *,
        query_type: RagQueryType,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagQueryRoutingPlan:
        """질의 유형에 맞는 처리 계획을 반환한다."""
        ...


class RagQueryStrategyRouter:
    """lookup과 synthesis 전략을 명시적으로 분기하는 기본 라우터."""

    def __init__(self) -> None:
        """상태를 공유하지 않는 기본 전략 객체를 초기화한다."""

        self._lookup_strategy: RagQueryStrategy = _LookupRagQueryStrategy()
        self._synthesis_strategy: RagQueryStrategy = _SynthesisRagQueryStrategy()

    def route(
        self,
        *,
        query_type: RagQueryType,
        chunks: tuple[ChunkSearchResult, ...],
    ) -> RagQueryRoutingPlan:
        """분류 결과에 대응하는 전략만 실행한다."""

        if query_type is RagQueryType.LOOKUP:
            return self._lookup_strategy.prepare(
                chunks,
            )

        if query_type is RagQueryType.SYNTHESIS:
            return self._synthesis_strategy.prepare(
                chunks,
            )

        # StrEnum 외의 값이 런타임에 주입되는 잘못된 구현을 방어한다.
        raise ValueError("Unsupported RAG query type.")


class RoutedChunkSearcher:
    """기존 청크 검색 결과에 질의 유형별 순서 전략을 적용하는 어댑터.

    실제 임베딩과 Qdrant 검색은 주입받은 기존 ``ChunkSearcher``에 그대로
    위임한다. lookup은 원본 ``ChunkSearchResponse``를 그대로 반환하므로
    기존 검색·프롬프트·생성 흐름이 변경되지 않는다.

    synthesis는 검색 결과의 청크 집합을 변경하지 않고 PDF별 연속 구간으로
    재배열한다. 현재 ``RagPromptBuilder``가 청크 순서대로 SOURCE-N을
    부여하므로 Claude에 전달되는 document_sources_json도 PDF별로 묶인다.
    """

    def __init__(
        self,
        *,
        delegate: ChunkSearcher,
        query_classifier: RagQueryClassifier | None = None,
        query_router: RagQueryRouter | None = None,
    ) -> None:
        """기존 검색기와 선택적 분류기·라우터를 주입받는다."""

        self._delegate = delegate
        self._query_classifier = (
            query_classifier if query_classifier is not None else RuleBasedRagQueryClassifier()
        )
        self._query_router = query_router if query_router is not None else RagQueryStrategyRouter()

    async def search(
        self,
        request: ChunkSearchRequest,
    ) -> ChunkSearchResponse:
        """검색 결과를 질의 유형에 맞게 손실 없이 정렬한다."""

        query_type = self._query_classifier.classify(
            request,
        )
        response = await self._delegate.search(
            request,
        )

        # 검색 결과가 없으면 기존 RagAnswerService가 Claude 호출 없이
        # 근거 부족을 반환해야 한다. 빈 결과에 그룹 전략을 적용하지 않는다.
        if not response.results:
            _LOGGER.info(
                "RAG query routing skipped because evidence was unavailable.",
                extra={
                    "event": "rag_query_routing_skipped",
                    "user_idx": request.user_idx,
                    "query_type": query_type.value,
                    "result_count": 0,
                    "pdf_group_count": 0,
                },
            )

            return response

        routing_plan = self._query_router.route(
            query_type=query_type,
            chunks=response.results,
        )

        _validate_routing_plan(
            query_type=query_type,
            original_chunks=response.results,
            routing_plan=routing_plan,
        )

        _LOGGER.info(
            "RAG query routing completed.",
            extra={
                "event": "rag_query_routing_completed",
                "user_idx": request.user_idx,
                "query_type": query_type.value,
                "result_count": response.result_count,
                "pdf_group_count": len(
                    routing_plan.pdf_groups,
                ),
            },
        )

        # lookup 전략은 동일한 tuple을 그대로 반환한다. 응답 모델까지 새로
        # 만들지 않아 기존 흐름의 객체와 필드 계약을 완전히 유지한다.
        if routing_plan.prompt_chunks is response.results:
            return response

        return ChunkSearchResponse(
            user_idx=response.user_idx,
            result_count=response.result_count,
            results=routing_plan.prompt_chunks,
        )


class RoutedRagAnswerService(RagAnswerService):
    """기존 RAG 답변 서비스에 질의 분류와 전략 라우팅을 결합한다.

    상속받은 ``RagAnswerService.answer`` 구현은 수정하지 않는다. 검색 의존성만
    ``RoutedChunkSearcher``로 감싸므로 lookup 질문은 기존 동작을 그대로
    사용하고 synthesis 질문에만 PDF 그룹 순서가 적용된다.
    """

    def __init__(
        self,
        *,
        chunk_searcher: ChunkSearcher,
        prompt_builder: PromptBuilder,
        generation_client: GenerationClient,
        query_classifier: RagQueryClassifier | None = None,
        query_router: RagQueryRouter | None = None,
    ) -> None:
        """기존 답변 의존성과 선택적 라우팅 구성 요소를 초기화한다."""

        super().__init__(
            chunk_searcher=RoutedChunkSearcher(
                delegate=chunk_searcher,
                query_classifier=query_classifier,
                query_router=query_router,
            ),
            prompt_builder=prompt_builder,
            generation_client=generation_client,
        )


def group_chunks_by_pdf(
    chunks: tuple[ChunkSearchResult, ...],
) -> tuple[PdfChunkGroup, ...]:
    """검색 결과를 PDF별로 묶고 최초 등장 순서를 보존한다.

    Qdrant가 반환한 전역 관련도 순서에서 어떤 PDF가 처음 등장했는지를
    문서 그룹 순서로 사용한다. 각 PDF 안에서는 해당 청크들의 기존 관련도
    순서를 그대로 유지한다.

    파일 이름이 같은 서로 다른 PDF는 ``file_idx``가 다르므로 별도 그룹으로
    처리한다. 반대로 같은 ``file_idx``에 서로 다른 문서 또는 파일명
    스냅샷이 섞이면 활성 문서 검색 계약 위반으로 거부한다.
    """

    if not chunks:
        raise ValueError("At least one chunk is required for PDF grouping.")

    grouped_chunks: dict[int, list[ChunkSearchResult]] = {}
    group_metadata: dict[int, tuple[int, str]] = {}

    for chunk in chunks:
        if chunk.file_type != SupportedFileType.PDF:
            raise ValueError("Synthesis routing supports only PDF chunks.")

        metadata = (
            chunk.rag_document_idx,
            chunk.file_name,
        )
        existing_metadata = group_metadata.get(
            chunk.file_idx,
        )

        if existing_metadata is not None and existing_metadata != metadata:
            raise ValueError("Chunks for one file_idx must share document metadata.")

        if existing_metadata is None:
            group_metadata[chunk.file_idx] = metadata
            grouped_chunks[chunk.file_idx] = []

        grouped_chunks[chunk.file_idx].append(
            chunk,
        )

    return tuple(
        PdfChunkGroup(
            file_idx=file_idx,
            rag_document_idx=group_metadata[file_idx][0],
            file_name=group_metadata[file_idx][1],
            chunks=tuple(file_chunks),
        )
        for file_idx, file_chunks in grouped_chunks.items()
    )


def _normalize_query(
    query: str,
) -> str:
    """규칙 매칭을 위해 대소문자와 연속 공백만 정규화한다."""

    return re.sub(
        r"\s+",
        " ",
        query.casefold(),
    ).strip()


def _validate_routing_plan(
    *,
    query_type: RagQueryType,
    original_chunks: tuple[ChunkSearchResult, ...],
    routing_plan: RagQueryRoutingPlan,
) -> None:
    """라우터가 검색 청크를 추가·누락하거나 lookup 순서를 바꾸지 못하게 한다."""

    if routing_plan.query_type is not query_type:
        raise ValueError("Routing plan query type does not match classification.")

    original_chunk_ids = tuple(chunk.chunk_id for chunk in original_chunks)
    routed_chunk_ids = tuple(chunk.chunk_id for chunk in routing_plan.prompt_chunks)

    if len(original_chunk_ids) != len(routed_chunk_ids):
        raise ValueError("Routing plan must preserve the search result count.")

    if sorted(original_chunk_ids) != sorted(routed_chunk_ids):
        raise ValueError("Routing plan must preserve the search result chunk IDs.")

    if query_type is RagQueryType.LOOKUP and routing_plan.prompt_chunks != original_chunks:
        raise ValueError("Lookup routing must preserve the original chunk order.")
