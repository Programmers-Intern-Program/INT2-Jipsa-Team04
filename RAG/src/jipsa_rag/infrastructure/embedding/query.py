"""Hugging Face TEI를 이용하여 검색 질의 임베딩을 생성한다."""

import math
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final, cast

import httpx2

from jipsa_rag.core.config import Settings
from jipsa_rag.infrastructure.embedding.exceptions import (
    EmbeddingServiceRejectedError,
    EmbeddingServiceTimeoutError,
    EmbeddingServiceUnavailableError,
    InvalidEmbeddingResponseError,
)
from jipsa_rag.infrastructure.embedding.models import EmbeddingVector

_TEI_EMBED_PATH: Final[str] = "/embed"

# Qwen3-Embedding은 검색 문서에는 instruction을 붙이지 않고,
# 검색 질의에만 수행할 작업을 한 문장으로 설명하는 instruction을 권장한다.
#
# Jipsa의 검색 대상은 일반 웹 문서가 아니라 사용자가 업로드한 문서이므로
# 사용자 질문에 답할 수 있는 관련 문단을 찾는 작업으로 목적을 구체화한다.
_QUERY_TASK_DESCRIPTION: Final[str] = (
    "Given a user question, retrieve relevant passages from the user's documents "
    "that answer the question"
)


@dataclass(frozen=True, slots=True)
class QueryEmbedding:
    """검색 질의 하나에 대해 생성된 임베딩 결과."""

    embedding_model: str
    embedding_dim: int
    vector: EmbeddingVector

    def __post_init__(self) -> None:
        """모델 식별자, 벡터 차원 및 벡터 값의 유효성을 검증한다."""

        normalized_model = self.embedding_model.strip()
        normalized_vector: list[float] = []

        if not normalized_model:
            raise ValueError("embedding_model must not be empty.")

        if self.embedding_dim <= 0:
            raise ValueError("embedding_dim must be greater than zero.")

        for value in self.vector:
            # bool은 int의 하위 타입이지만 임베딩 값으로는 허용하지 않는다.
            if isinstance(value, bool) or not isinstance(
                value,
                (
                    int,
                    float,
                ),
            ):
                raise ValueError("query embedding values must be numeric.")

            normalized_value = float(value)

            # NaN과 무한대는 Qdrant 벡터 검색에 사용할 수 없다.
            if not math.isfinite(normalized_value):
                raise ValueError("query embedding values must be finite.")

            normalized_vector.append(normalized_value)

        if len(normalized_vector) != self.embedding_dim:
            raise ValueError("query embedding dimension does not match embedding_dim.")

        object.__setattr__(
            self,
            "embedding_model",
            normalized_model,
        )

        object.__setattr__(
            self,
            "vector",
            tuple(normalized_vector),
        )


class TeiQueryEmbedder:
    """Qwen3 검색 질의를 Hugging Face TEI 임베딩으로 변환한다."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """임베딩 설정과 선택적인 테스트용 HTTP transport를 주입받는다."""

        self._settings = settings
        self._transport = transport

    @property
    def embedding_model(self) -> str:
        """검색 질의 임베딩에 사용하는 모델 식별자를 반환한다."""

        return self._settings.embedding_model

    async def embed(
        self,
        *,
        query: str,
    ) -> QueryEmbedding:
        """사용자 질의를 Qwen3 instruction 형식으로 변환하여 임베딩한다."""

        normalized_query = query.strip()

        if not normalized_query:
            raise ValueError("query must not be empty.")

        # 질의 원문은 예외 메시지나 로그 컨텍스트에 포함하지 않는다.
        #
        # 검색 문서 청크는 instruction 없이 색인되어 있으므로
        # 질의 쪽에만 아래 형식을 적용한다.
        query_input = f"Instruct: {_QUERY_TASK_DESCRIPTION}\nQuery: {normalized_query}"

        timeout_seconds = self._settings.embedding_timeout_seconds

        timeout = httpx2.Timeout(
            connect=timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )

        async with httpx2.AsyncClient(
            base_url=self._settings.embedding_base_url,
            timeout=timeout,
            follow_redirects=False,
            # 로컬 TEI 요청이 운영체제 프록시 환경 변수의 영향을 받지 않게 한다.
            trust_env=False,
            transport=self._transport,
        ) as client:
            vector = await self._request_query_embedding(
                client=client,
                query_input=query_input,
            )

        return QueryEmbedding(
            embedding_model=self._settings.embedding_model,
            embedding_dim=self._settings.embedding_dim,
            vector=vector,
        )

    async def _request_query_embedding(
        self,
        *,
        client: httpx2.AsyncClient,
        query_input: str,
    ) -> EmbeddingVector:
        """TEI /embed에 단일 검색 질의를 요청하고 벡터 하나를 반환한다."""

        try:
            response = await client.post(
                _TEI_EMBED_PATH,
                json={
                    # 단일 질의도 TEI의 배치 입력 계약에 맞춰 목록으로 전달한다.
                    "inputs": [
                        query_input,
                    ],
                },
            )

        except httpx2.TimeoutException as error:
            raise EmbeddingServiceTimeoutError from error

        except httpx2.RequestError as error:
            raise EmbeddingServiceUnavailableError from error

        status_code = response.status_code

        if (
            status_code == HTTPStatus.TOO_MANY_REQUESTS
            or status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        ):
            raise EmbeddingServiceUnavailableError(
                status_code=status_code,
            )

        if not HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES:
            # TEI 응답 본문에는 내부 모델 정보가 포함될 수 있으므로
            # 응답 원문을 예외나 로그에 복사하지 않는다.
            raise EmbeddingServiceRejectedError(
                status_code=status_code,
            )

        try:
            payload: object = response.json()

        except ValueError as error:
            raise InvalidEmbeddingResponseError(
                reason="response body is not valid JSON",
                batch_start_index=0,
            ) from error

        return self._parse_query_embedding_response(
            payload=payload,
        )

    def _parse_query_embedding_response(
        self,
        *,
        payload: object,
    ) -> EmbeddingVector:
        """TEI 응답의 벡터 개수, 값 타입, 유한성 및 차원을 검증한다."""

        if not isinstance(payload, list):
            raise InvalidEmbeddingResponseError(
                reason="response root must be a list",
                batch_start_index=0,
            )

        raw_vectors = cast(
            list[object],
            payload,
        )

        if len(raw_vectors) != 1:
            raise InvalidEmbeddingResponseError(
                reason=(f"expected 1 vector but received {len(raw_vectors)}"),
                batch_start_index=0,
            )

        raw_vector = raw_vectors[0]

        if not isinstance(raw_vector, list):
            raise InvalidEmbeddingResponseError(
                reason="vector 0 must be a list",
                batch_start_index=0,
            )

        raw_values = cast(
            list[object],
            raw_vector,
        )

        if len(raw_values) != self._settings.embedding_dim:
            raise InvalidEmbeddingResponseError(
                reason=(
                    f"vector 0 has dimension {len(raw_values)} instead of "
                    f"{self._settings.embedding_dim}"
                ),
                batch_start_index=0,
            )

        normalized_vector: list[float] = []

        for value_offset, raw_value in enumerate(raw_values):
            if isinstance(raw_value, bool) or not isinstance(
                raw_value,
                (
                    int,
                    float,
                ),
            ):
                raise InvalidEmbeddingResponseError(
                    reason=(f"vector 0 value {value_offset} is not numeric"),
                    batch_start_index=0,
                )

            normalized_value = float(raw_value)

            if not math.isfinite(normalized_value):
                raise InvalidEmbeddingResponseError(
                    reason=(f"vector 0 value {value_offset} is not finite"),
                    batch_start_index=0,
                )

            normalized_vector.append(normalized_value)

        return tuple(normalized_vector)
