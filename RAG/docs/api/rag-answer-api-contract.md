# AWS Backend ↔ Local RAG 답변 API 계약

## 1. 문서 정보

| 항목 | 값 |
|---|---|
| 계약 버전 | `1.2.1` |
| 호출 방향 | AWS Backend → Local RAG |
| HTTP Method | `POST` |
| Path | `/api/v1/rag/answers` |
| Content-Type | `application/json` |
| 인증 헤더 | `X-Internal-Token` |
| 요청 추적 헤더 | `X-Request-ID` |
| 지원 문서 | PDF, DOCX, PPTX, TXT, XLSX 및 각 형식의 OCR 청크 |
| 담당 범위 | 선택 문서 검색, 부분 답변, Claude 종합, 인용 검증, 실제 출처 반환 |
| 제외 범위 | AWS 사용자 인증, 파일 권한 판정, S3 직접 접근, 전체 문서 검색 |

### 1.1 변경 이력

| 버전 | 변경 내용 |
|---|---|
| `1.0.0` | 선택 참조문서 기반 검색과 답변 계약 최초 정의 |
| `1.1.0` | SOURCE-N 검증, 실제 인용 출처 필터링, 근거 부족 응답 정의 |
| `1.2.0` | 혼합 문서·OCR Source Locator, 공개 cited_source_ids, 부분 실패와 최종 호출 생략 계약 추가 |
| `1.2.1` | README·API 설명 동기화, 품질 게이트와 문서 회귀 검증 기준 명시 |

## 2. 시스템 경계

AWS Backend는 사용자 인증과 파일 접근 권한을 확인한 뒤 질문 전송 시점의
`File.File_IDX` 목록을 `reference_file_idxs`로 확정한다. Local RAG는 이 목록을
현재 요청의 불변 검색 범위로 사용한다.

```text
AWS Backend가 사용자·파일 권한 검증
        ↓
reference_file_idxs 스냅샷 생성
        ↓
POST /api/v1/rag/answers
        ↓
Local RAG가 user + active index + selected files로 Qdrant 검색
        ↓
lookup 또는 문서별 synthesis
        ↓
본문 SOURCE-N, cited_source_ids, sources 순서 검증
        ↓
실제 인용 출처만 반환
```

Local RAG는 `reference_file_idxs`가 없거나 비어 있는 요청을 사용자의 전체 문서
검색으로 변환하지 않는다.

## 3. 요청 헤더

| 헤더 | 필수 | 설명 |
|---|---|---|
| `Content-Type` | 예 | `application/json` |
| `X-Internal-Token` | 예 | AWS Backend → Local RAG 내부 인증 토큰 |
| `X-Request-ID` | 아니요 | 서비스 간 요청 추적용 UUID |

내부 인증 토큰, 사용자 질문, 청크 원문, 생성 프롬프트, Claude 응답 원문 및 API
Key는 외부 오류 응답이나 구조화 로그에 기록하지 않는다.

## 4. 요청 본문

### 4.1 필드 계약

| 필드 | 타입 | 필수 | 기본값 | 제약 |
|---|---|---|---|---|
| `user_idx` | integer | 예 | 없음 | 1 이상 |
| `reference_file_idxs` | integer array | 예 | 없음 | 1~20개, 중복 없음, 각 값 1 이상 |
| `query` | string | 예 | 없음 | 공백 정규화 후 1~4096자 |
| `top_k` | integer | 아니요 | 5 | 1~20 |
| `score_threshold` | number/null | 아니요 | null | -1.0~1.0 |

정의되지 않은 추가 필드는 허용하지 않는다.

### 4.2 정상 요청

```json
{
  "user_idx": 45,
  "reference_file_idxs": [101, 102, 103, 104, 105],
  "query": "모든 문서를 종합해 정책과 성과를 비교해줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

### 4.3 참조문서 미선택

필드 생략, `null`, 빈 배열은 전체 검색으로 확장하지 않고 다음 오류로 처리한다.

```json
{
  "success": false,
  "code": "REFERENCE_DOCUMENT_REQUIRED",
  "message": "At least one reference document must be selected.",
  "data": null
}
```

HTTP Status는 `422 Unprocessable Entity`다.

## 5. Qdrant 검색 범위

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

- 일반 텍스트와 OCR 청크는 같은 후보 집합에서 검색한다.
- 선택하지 않은 파일, 다른 사용자 및 비활성 point는 차단한다.
- Qdrant 응답을 받은 뒤 서비스가 같은 범위를 재검증한다.
- synthesis 최종 `sources`도 선택 문서 범위 안인지 다시 확인한다.

## 6. 처리 전략

### 6.1 lookup

명시적인 다문서 비교·종합 의도가 없으면 선택 문서 전체를 한 번 검색하고 한 번의
Claude 답변을 생성한다. 파일 형식 제한은 없다.

### 6.2 synthesis

비교·종합 의도가 있으면 선택 파일마다 독립 검색하고 문서별 부분 답변을 생성한다.

```text
파일별 검색
→ 문서별 청크 상한
→ 전체 컨텍스트 라운드 로빈 제한
→ 문서별 부분 답변 및 인용 검증
→ 전역 SOURCE-N 재매핑
→ 유효 부분 답변만 최종 Claude 입력
→ 최종 인용 검증
```

### 6.3 부분 실패

| 상황 | 처리 |
|---|---|
| 파싱·색인 실패로 활성 point 없음 | 해당 문서 결과 없음으로 처리하고 계속 |
| 문서별 임베딩·Qdrant 검색 실패 | 해당 문서만 제외하고 계속 |
| 문서별 부분 Claude 실패 | 해당 부분만 제외하고 계속 |
| 문서별 근거 부족 | 해당 부분만 제외하고 계속 |
| 범위 위반 | 전체 요청 실패 |
| 잘못된 인용 또는 선언 순서 | 502 `INVALID_GENERATION_RESPONSE` |
| 유효 그룹 없음 | Claude 호출 없이 근거 부족 |
| 유효 부분 답변 없음 | 최종 Claude 호출 없이 근거 부족 |

## 7. 공통 성공 Envelope

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {}
}
```

`RAG_ANSWER_COMPLETED`는 정상 answered뿐 아니라 Claude 호출 없이 처리한
`insufficient_evidence`도 포함한다.

## 8. 정상 답변 응답

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "정책과 성과 차트를 함께 확인했습니다. [SOURCE-1][SOURCE-5]",
    "status": "answered",
    "cited_source_ids": ["SOURCE-1", "SOURCE-5"],
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

### 8.1 `data` 필드

| 필드 | 타입 | nullable | 설명 |
|---|---|---|---|
| `answer` | string | 아니요 | 본문과 `[SOURCE-N]` 인용 |
| `status` | enum | 아니요 | `answered` 또는 `insufficient_evidence` |
| `cited_source_ids` | string array | 아니요 | 본문 최초 인용 순서 |
| `sources` | object array | 아니요 | 실제 인용 출처만 같은 순서로 포함 |
| `model` | string | 예 | 최종 Claude 모델 ID |
| `usage` | object | 예 | 최종 생성 입력·출력 토큰 |
| `stop_reason` | string | 예 | 최종 생성 종료 사유 |

## 9. 인용 계약

정상 응답은 다음을 모두 만족한다.

1. 본문에 하나 이상의 `[SOURCE-N]`이 있다.
2. 같은 ID의 반복 인용은 최초 등장만 계산한다.
3. `cited_source_ids`가 본문 최초 등장 순서와 같다.
4. `sources[].source_id`도 같은 순서와 같다.
5. 본문에 없는 후보 출처는 `sources`에 포함하지 않는다.
6. 모든 ID가 현재 프롬프트 후보에 존재한다.
7. `source_id`와 `chunk_id`는 각각 중복되지 않는다.
8. 모든 `file_idx`가 요청의 선택 문서 목록 안에 있다.

불일치는 성공 응답으로 자동 교정하지 않으며 502 오류로 처리한다. 단, 기존 내부
서비스가 `cited_source_ids` 생성자 인자를 생략한 경우 응답 스키마가 검증된 본문
인용 순서로 채운다.

## 10. Source Locator

### 10.1 PDF

- `page`
- 기존 top-level `page`도 동일 값으로 유지

### 10.2 DOCX

- `section_index`, `block_index`, `paragraph_index`, `table_index`
- `heading_level`, `section_title`
- `row_count`, `column_count`

### 10.3 PPTX

- `slide_no`, `shape_index`, `shape_id`, `shape_path`
- `shape_name`, `shape_type_name`, `coordinate_space`
- `shape_left_emu`, `shape_top_emu`, `shape_width_emu`, `shape_height_emu`

### 10.4 XLSX

- `sheet_number`, `sheet_name`, `row_number`
- `start_row`, `end_row`, `start_column`, `end_column`
- `start_cell`, `end_cell`, `cell_range`
- `cell_coordinates`, `merged_cell_ranges`

### 10.5 TXT

- `line_start`, `line_end`
- `char_start`, `char_end`이며 end는 exclusive

### 10.6 OCR

OCR은 원본 형식 위치를 유지하면서 다음 필드를 추가한다.

- `content_origin: "ocr"`
- `unit_type: "ocr_image"`
- `image_ordinal`: 신규 표준 1-based 이미지 순번
- `image_index`: 기존 소비자 호환용 동일 값
- `image_id`, `image_kind`
- `ocr_engine`, `ocr_mean_confidence`

## 11. 근거 부족 응답

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

유효한 검색 근거가 없으면 부분 생성과 최종 생성을 모두 생략한다. 검색 근거는
있지만 유효한 부분 답변이 없으면 최종 생성만 생략한다.

## 12. 오류 계약

| HTTP | 코드 | 의미 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 실패 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED` | 참조문서 미선택 |
| 422 | `REQUEST_VALIDATION_FAILED` | 요청 본문 검증 실패 |
| 429 | `GENERATION_BUDGET_EXCEEDED` | Claude 요청 범위 예산 초과 |
| 502 | `EMBEDDING_REQUEST_REJECTED` | TEI 요청 거부 |
| 502 | `INVALID_EMBEDDING_RESPONSE` | TEI 응답 계약 위반 |
| 502 | `VECTOR_SEARCH_FAILED` | Qdrant 검색 요청 실패 |
| 502 | `INVALID_VECTOR_SEARCH_RESULT` | Qdrant 결과 범위·payload 계약 위반 |
| 502 | `GENERATION_REQUEST_FAILED` | Claude 요청 실패 |
| 502 | `INVALID_GENERATION_RESPONSE` | 구조화 출력·인용·순서 계약 위반 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시적 사용 불가 |
| 503 | `VECTOR_DATABASE_UNAVAILABLE` | Qdrant 일시적 사용 불가 |
| 503 | `GENERATION_SERVICE_UNAVAILABLE` | Claude 일시적 사용 불가 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | TEI 시간 초과 |
| 504 | `GENERATION_SERVICE_TIMEOUT` | Claude 시간 초과 |
| 500 | `INTERNAL_SERVER_ERROR` | 분류되지 않은 RAG 처리 또는 범위 계약 실패 |

오류 응답은 공통 envelope를 사용하며 질문, 청크, 프롬프트, Claude 원문 및 내부
자격 증명을 포함하지 않는다.

## 13. 문서와 OpenAPI 동기화 계약

다음 세 설명은 동일한 요청·응답 의미를 사용해야 한다.

1. FastAPI 엔드포인트의 `summary`, `description`, `responses`
2. `README.md`의 RAG Answer 설명과 예시
3. 이 문서와 `rag-answer-contract.md`

문서에는 다음 과거 제한 문구를 다시 추가하지 않는다.

- 텍스트 레이어 PDF만 답변 지원
- DOCX, PPTX, XLSX, TXT 답변 미지원
- OCR 답변 미지원
- synthesis가 PDF별로만 동작

지원 형식, 인용 순서 또는 오류 코드가 변경되면 스키마, OpenAPI 설명, 두 계약
문서와 문서 회귀 테스트를 같은 변경 단위에서 갱신한다.

## 14. 품질 게이트

이 계약의 변경 완료 여부는 문서 편집만으로 판단하지 않는다. 동일한 commit에서
다음 검사가 모두 성공해야 한다.

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

표준 진입점은 `scripts/verify-rag-quality.ps1`이다. 일반 전체 Pytest에서 실제
Claude, CUDA TEI, Qdrant, Local RAG DB 또는 Office COM이 필요한 opt-in E2E가
skip되면 Pytest 결과에 사유가 표시되어야 한다. 외부 환경이 없어 실행하지 못한
E2E를 일반 단위 테스트 성공으로 대체해서는 안 된다.

`tests/regression/test_rag_documentation_contract.py`는 README와 두 API 계약 문서가
혼합 문서, OCR, Source Locator, 부분 실패, 최종 Claude 호출 생략 및 공개 인용
순서 계약을 계속 설명하는지 검증한다.
