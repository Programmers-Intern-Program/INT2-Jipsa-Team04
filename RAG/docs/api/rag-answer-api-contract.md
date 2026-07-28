# AWS Backend ↔ Local RAG 답변 API 계약

> **문서 상태:** Stable · AWS Backend 공개 내부 계약  
> **주 독자:** AWS Backend 연동 개발자, Local RAG API 개발자, QA  
> **계약 버전:** `1.3.0`  
> **최종 검토:** 2026-07-28



> **통합 진입점:** 전체 Local RAG API 표면과 공통 transport 계약은
> [종합 API 명세서](comprehensive-api-specification.md)를 먼저 확인합니다. 이 문서는
> `POST /api/v1/rag/answers`의 Backend 연동 payload에 집중합니다.

## 1. 문서 정보

| 항목 | 값 |
|---|---|
| 계약 버전 | `1.3.0` |
| 호출 방향 | AWS Backend → Local RAG |
| HTTP Method | `POST` |
| Path | `/api/v1/rag/answers` |
| Content-Type | `application/json` |
| 인증 헤더 | `X-Internal-Token` |
| 요청 추적 헤더 | `X-Request-ID` |
| 지원 문서 | PDF, DOCX, PPTX, XLSX, TXT와 각 형식의 OCR 청크 |
| 담당 범위 | 선택 문서 검색, lookup·synthesis, 인용 검증, 실제 출처 반환 |
| 제외 범위 | 사용자 인증·파일 권한 판정, S3 직접 접근, 전체 문서 자동 검색 |

### 변경 이력

| 버전 | 변경 내용 |
|---|---|
| `1.0.0` | 선택 참조문서 기반 답변 계약 최초 정의 |
| `1.1.0` | SOURCE-N 검증, 실제 인용 출처 필터링, 근거 부족 응답 |
| `1.2.0` | 혼합 문서·OCR Source Locator와 문서별 부분 실패 |
| `1.2.1` | README·OpenAPI·문서 회귀 기준 동기화 |
| `1.3.0` | 검색 계약의 선택 문서 범위, 출처·재인제스트·실제 E2E 문서 정합성 갱신 |

## 2. 시스템 경계

AWS Backend는 사용자 인증과 파일 접근 권한을 검증한 뒤 질문 시점의 `File.File_IDX`
목록을 `reference_file_idxs`로 확정합니다. Local RAG는 이 목록을 현재 요청의 불변 검색
범위로 사용합니다.

```text
Backend 사용자·파일 권한 검증
  → reference_file_idxs 스냅샷
  → POST /api/v1/rag/answers
  → user + active index + selected files 검색
  → lookup 또는 문서별 synthesis
  → SOURCE-N, cited_source_ids, sources 검증
  → 실제 인용 출처만 반환
```

빈 선택 목록을 사용자 전체 문서 검색으로 변환하지 않습니다.

## 3. 요청 헤더

| 헤더 | 필수 | 설명 |
|---|---:|---|
| `Content-Type` | 예 | `application/json` |
| `X-Internal-Token` | 예 | Backend → Local RAG 내부 인증 |
| `X-Request-ID` | 아니요 | 서비스 간 요청 추적 UUID |

## 4. 요청 본문

```json
{
  "user_idx": 45,
  "reference_file_idxs": [101, 102, 103],
  "query": "선택한 문서를 종합해 정책과 성과를 비교해줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

| 필드 | 타입 | 필수 | 기본값 | 제약 |
|---|---|---:|---:|---|
| `user_idx` | integer | 예 | 없음 | 1 이상 |
| `reference_file_idxs` | integer array | 예 | 없음 | 1~20개, 중복 없음 |
| `query` | string | 예 | 없음 | 정규화 후 1~4096자 |
| `top_k` | integer | 아니요 | 5 | 1~20 |
| `score_threshold` | number/null | 아니요 | null | -1.0~1.0 |

추가 필드는 허용하지 않습니다.

참조문서 미선택:

```json
{
  "success": false,
  "code": "REFERENCE_DOCUMENT_REQUIRED",
  "message": "At least one reference document must be selected.",
  "data": null
}
```

HTTP Status는 `422 Unprocessable Entity`입니다.

## 5. 검색 범위

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

- 일반 텍스트와 OCR 청크를 같은 후보 집합에서 검색합니다.
- 다른 사용자, 선택하지 않은 파일과 비활성 point를 차단합니다.
- Qdrant 응답 payload를 서비스 계층에서 다시 검증합니다.
- 최종 `sources`도 선택 문서 범위를 다시 확인합니다.

## 6. 처리 전략

### lookup

명시적인 비교·대조·종합 의도가 없으면 선택 문서 전체 범위를 한 번 검색하고 단일 Claude
답변을 생성합니다. 파일 형식 제한은 없습니다.

### synthesis

두 개 이상의 문서와 비교·종합 의도가 있으면 파일별 독립 검색과 부분 답변을 사용합니다.

```text
파일별 검색
  → 문서별 청크 상한
  → 전체 컨텍스트 라운드 로빈 제한
  → 문서별 부분 답변과 로컬 인용 검증
  → 전역 SOURCE-N 재매핑
  → 유효 부분 답변만 최종 Claude 입력
  → 최종 인용과 범위 검증
```

### 부분 실패

| 상황 | 처리 |
|---|---|
| 문서가 미파싱·미색인 | 해당 문서 결과 없음으로 처리 |
| 문서별 임베딩·Qdrant 실패 | 해당 문서만 제외하고 계속 |
| 문서별 부분 Claude 실패 | 해당 부분만 제외하고 계속 |
| 문서별 근거 부족 | 해당 부분만 제외하고 계속 |
| 범위 위반 | 전체 요청 실패 |
| 잘못된 인용·선언 순서 | 502 `INVALID_GENERATION_RESPONSE` |
| 유효 검색 그룹 없음 | Claude 호출 없이 근거 부족 |
| 유효 부분 답변 없음 | 최종 Claude 호출 없이 근거 부족 |

## 7. 성공 Envelope

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {}
}
```

`RAG_ANSWER_COMPLETED`는 `answered`와 `insufficient_evidence`를 모두 포함합니다.

## 8. 정상 답변

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "정책과 성과를 함께 확인했습니다. [SOURCE-1][SOURCE-2]",
    "status": "answered",
    "cited_source_ids": ["SOURCE-1", "SOURCE-2"],
    "sources": [
      {
        "source_id": "SOURCE-1",
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "rag_document_idx": 1010,
        "file_idx": 101,
        "folder_idx": 9,
        "file_name": "정책.pdf",
        "file_type": "pdf",
        "chunk_index": 0,
        "score": 0.95,
        "page": 3,
        "slide_no": null,
        "sheet_name": null,
        "section_title": null,
        "source_locator": {
          "file_type": "pdf",
          "kind": "pdf_page",
          "content_origin": "text",
          "unit_type": "paragraph",
          "structure_path": "page:3",
          "page": 3,
          "cell_coordinates": [],
          "merged_cell_ranges": []
        },
        "excerpt": "정책 문서의 근거입니다."
      }
    ],
    "model": "claude-sonnet-5",
    "usage": {
      "input_tokens": 1200,
      "output_tokens": 220
    },
    "stop_reason": "end_turn"
  }
}
```

| `data` 필드 | nullable | 설명 |
|---|---:|---|
| `answer` | 아니요 | 본문과 `[SOURCE-N]` |
| `status` | 아니요 | `answered` 또는 `insufficient_evidence` |
| `cited_source_ids` | 아니요 | 본문 최초 인용 순서 |
| `sources` | 아니요 | 실제 인용 출처만 같은 순서로 포함 |
| `model` | 예 | 최종 Claude 모델 ID |
| `usage` | 예 | 최종 호출 토큰 사용량 |
| `stop_reason` | 예 | 최종 생성 종료 사유 |

## 9. 인용 계약

최종 `sources`에는 실제로 인용한 출처만 포함합니다.

정상 `answered` 응답은 다음을 모두 만족합니다.

1. 본문에 하나 이상의 `[SOURCE-N]`이 있습니다.
2. 반복 인용은 최초 등장만 계산합니다.
3. `cited_source_ids`가 본문 최초 등장 순서와 같습니다.
4. `sources[].source_id`가 같은 순서와 같습니다.
5. 본문에 없는 후보 출처는 `sources`에 포함하지 않습니다.
6. 모든 SOURCE-N이 현재 프롬프트 후보에 존재합니다.
7. `source_id`와 `chunk_id`는 각각 중복되지 않습니다.
8. 모든 `file_idx`가 `reference_file_idxs` 안에 있습니다.
9. 모든 Chunk ID가 현재 활성 Local RAG 문서에 연결됩니다.

불일치를 성공 응답으로 임의 교정하지 않고 계약 오류로 처리합니다.

## 10. Source Locator

공통 필드:

- `file_type`
- `kind`
- `content_origin`
- `unit_type`
- `structure_path`

형식별 대표 필드:

- PDF: `page`
- DOCX: `section_index`, `block_index`, `paragraph_index`, `table_index`
- PPTX: `slide_no`, `shape_index`, `shape_id`, `shape_path`, EMU 좌표
- XLSX: `sheet_number`, `sheet_name`, `start_cell`, `end_cell`, `cell_range`
- TXT: `line_start`, `line_end`, `char_start`, `char_end`
- OCR: `image_ordinal`, `image_index`, `image_id`, `image_kind`,
  `ocr_engine`, `ocr_mean_confidence`

상세 JSON은 [답변·인용·Source Locator 상세 계약](rag-answer-contract.md)을 참조합니다.

## 11. 근거 부족

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "제공된 문서 근거만으로는 답변할 수 없습니다.",
    "status": "insufficient_evidence",
    "cited_source_ids": [],
    "sources": [],
    "model": null,
    "usage": null,
    "stop_reason": null
  }
}
```

검색 근거가 없으면 부분 생성과 최종 생성을 모두 생략합니다.
검색 근거는 있지만 유효한 부분 답변이 없으면 최종 Claude 호출을 생략합니다.

## 12. 오류

| HTTP | 코드 | 의미 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 실패 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED` | 참조문서 미선택 |
| 422 | `REQUEST_VALIDATION_FAILED` | 요청 검증 실패 |
| 429 | `GENERATION_BUDGET_EXCEEDED` | 답변 단위 Claude 예산 초과 |
| 502 | `EMBEDDING_REQUEST_REJECTED` | TEI 요청 거부 |
| 502 | `INVALID_EMBEDDING_RESPONSE` | TEI 응답 계약 위반 |
| 502 | `VECTOR_SEARCH_FAILED` | Qdrant 검색 실패 |
| 502 | `INVALID_VECTOR_SEARCH_RESULT` | Qdrant payload·범위 계약 위반 |
| 502 | `GENERATION_REQUEST_FAILED` | Claude 요청 실패 |
| 502 | `INVALID_GENERATION_RESPONSE` | 구조화 출력·인용 계약 위반 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시적 사용 불가 |
| 503 | `VECTOR_DATABASE_UNAVAILABLE` | Qdrant 일시적 사용 불가 |
| 503 | `GENERATION_SERVICE_UNAVAILABLE` | Claude 일시적 사용 불가 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | TEI 시간 초과 |
| 504 | `GENERATION_SERVICE_TIMEOUT` | Claude 시간 초과 |
| 500 | `INTERNAL_SERVER_ERROR` | 분류되지 않은 내부 오류 |

오류 응답에는 질문, 청크·OCR 원문, 전체 프롬프트, Claude 원문과 내부 자격 증명을
포함하지 않습니다.

## 13. 동기화 기준

다음 항목은 같은 의미를 유지해야 합니다.

1. FastAPI 엔드포인트 `summary`, `description`, `responses`
2. Pydantic 요청·응답 스키마
3. [`../../README.md`](../../README.md)
4. 이 문서
5. [`rag-answer-contract.md`](rag-answer-contract.md)
6. [`../chunk-search-api.md`](../chunk-search-api.md)
7. 문서 회귀 테스트

과거 PDF 전용, 비Office 형식 미지원, OCR 미지원 또는 PDF별 synthesis 전용 문구를
다시 추가하지 않습니다.
