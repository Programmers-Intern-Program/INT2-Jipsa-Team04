# RAG Answer API Contract

## 1. 목적

`POST /api/v1/rag/answers`는 질문 전송 시점에 선택된 PDF만 검색 범위로
고정하고, 해당 PDF에서 확인한 근거만 사용하여 답변을 생성한다.

RAG 서비스는 AWS 자격 증명을 사용하거나 S3에 직접 접근하지 않는다.
문서 인제스트는 애플리케이션 서버가 발급한 Presigned GET URL을 통해
별도로 완료되어 있어야 하며, 답변 API는 Local RAG DB, Qdrant, TEI와
Claude를 사용한다.

현재 답변 대상 문서 형식은 PDF만 지원한다. OCR, TXT, DOCX, XLSX,
PPTX는 이 계약의 범위에 포함하지 않는다.

## 2. 요청

```json
{
  "user_idx": 45,
  "reference_file_idxs": [123, 456],
  "query": "두 PDF를 비교하여 공통점과 차이점을 알려줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

| 필드                  | 타입          | 필수   | 제약                          |
| --------------------- | ------------- | ------ | ----------------------------- |
| `user_idx`            | integer       | 예     | 0보다 큰 정수                 |
| `reference_file_idxs` | integer array | 예     | 1개 이상 20개 이하, 중복 금지 |
| `query`               | string        | 예     | 1자 이상 4096자 이하          |
| `top_k`               | integer       | 아니요 | 기본값 5, 1 이상 20 이하      |
| `score_threshold`     | number/null   | 아니요 | -1.0 이상 1.0 이하            |

`reference_file_idxs`가 없거나 비어 있으면 사용자의 전체 문서를 검색하지
않고 요청 검증 오류로 종료한다.

## 3. 질의 유형

### lookup

명시적인 다문서 비교·대조·종합 의도가 없는 질문이다.

선택 문서 범위에서 한 번 검색하고 기존 단일 프롬프트, 단일 Claude 생성,
출처 검증 흐름을 유지한다.

### synthesis

두 개 이상의 PDF가 선택되고 비교, 대조, 차이점, 공통점, 종합, 문서별
정리와 같은 다문서 의도가 명시된 질문이다.

1. PDF별로 독립 검색한다.
2. PDF별 청크 수와 전체 컨텍스트를 제한한다.
3. PDF별 부분 답변을 생성한다.
4. 실제 인용 출처만 전역 `SOURCE-N`으로 변환한다.
5. 검증된 부분 답변만 최종 종합한다.
6. 최종 답변에서 실제 사용한 출처만 반환한다.

## 4. 근거 부족

### 검색 결과 전체 부족

검색 결과가 전혀 없으면 Claude를 호출하지 않는다.

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "제공된 문서 근거만으로는 답변할 수 없습니다.",
    "status": "insufficient_evidence",
    "sources": [],
    "model": null,
    "usage": null,
    "stop_reason": null
  }
}
```

### 일부 PDF 근거 부족

일부 PDF의 검색 결과가 없거나 PDF별 부분 답변이
`insufficient_evidence`이면 해당 PDF를 최종 종합 후보에서 제외한다.

하나 이상의 PDF가 검증된 부분 답변을 제공하면 최종 종합을 계속한다.
근거가 없는 PDF의 내용을 외부 지식이나 추측으로 보완하지 않는다.

### 모든 부분 답변 근거 부족

모든 PDF 부분 답변이 `insufficient_evidence`이면 최종 종합 Claude
호출을 실행하지 않는다.

고정 근거 부족 문구와 빈 `sources`를 반환한다.

## 5. 정상 답변

정상 답변은 다음 조건을 모두 만족해야 한다.

- `status`는 `answered`
- 답변 본문에 한 개 이상의 `[SOURCE-N]` 존재
- `cited_source_ids`와 본문 최초 인용 순서 일치
- 모든 출처가 현재 프롬프트 후보에 존재
- 선택하지 않은 PDF 출처 미포함
- 응답 `sources`에는 실제 사용 출처만 포함

`usage`는 기존 API 하위 호환성을 위해 최종 Claude 호출의 사용량을
반환한다.

부분 답변과 최종 답변 전체의 누적 사용량은 내부 생성 예산 제한기가
별도로 관리한다.

## 6. Claude 제한

| 환경 변수                                          | 기본값 | 의미                |
| -------------------------------------------------- | -----: | ------------------- |
| `JIPSA_RAG_ANTHROPIC_MAX_CALLS_PER_ANSWER`         |     21 | 부분·최종 호출 합계 |
| `JIPSA_RAG_ANTHROPIC_MAX_INPUT_TOKENS_PER_ANSWER`  | 400000 | 누적 입력 토큰      |
| `JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS_PER_ANSWER` |  64000 | 누적 출력 토큰      |
| `JIPSA_RAG_ANTHROPIC_MAX_CONCURRENT_REQUESTS`      |      2 | 프로세스 동시 호출  |
| `JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS`            |   4096 | 단일 호출 출력 상한 |

호출 횟수 또는 토큰 예산을 초과하면 부분 답변을 정상 응답으로 반환하지
않는다.

```json
{
  "success": false,
  "code": "GENERATION_BUDGET_EXCEEDED",
  "message": "The generation budget for this answer was exceeded.",
  "data": null
}
```

HTTP 상태는 `429 Too Many Requests`다.

## 7. 로그 보안

다음 원문은 정상, 근거 부족, 예산 초과, 공급자 실패 경로 모두에서 로그와
예외 메시지에 기록하지 않는다.

- 사용자 질문
- 검색 청크
- 출처 발췌문
- 시스템·사용자 프롬프트
- PDF별 부분 답변
- 최종 Claude 답변
- 구조화 출력 JSON
- Anthropic API Key
- 내부 인증 토큰
- Presigned URL과 Query String

로그에는 이벤트 이름, 사용자 식별자, 문서 수, 결과 수, PDF 그룹 수,
안전한 오류 종류와 상태 코드만 기록한다.

## 8. 오류 계약

| HTTP | 코드                             |
| ---: | -------------------------------- |
|  401 | `UNAUTHORIZED`                   |
|  422 | `REFERENCE_DOCUMENT_REQUIRED`    |
|  422 | `REQUEST_VALIDATION_FAILED`      |
|  429 | `GENERATION_BUDGET_EXCEEDED`     |
|  502 | `VECTOR_SEARCH_FAILED`           |
|  502 | `INVALID_VECTOR_SEARCH_RESULT`   |
|  502 | `GENERATION_REQUEST_FAILED`      |
|  502 | `INVALID_GENERATION_RESPONSE`    |
|  503 | `EMBEDDING_SERVICE_UNAVAILABLE`  |
|  503 | `VECTOR_DATABASE_UNAVAILABLE`    |
|  503 | `GENERATION_SERVICE_UNAVAILABLE` |
|  504 | `EMBEDDING_SERVICE_TIMEOUT`      |
|  504 | `GENERATION_SERVICE_TIMEOUT`     |
|  500 | `INTERNAL_SERVER_ERROR`          |
