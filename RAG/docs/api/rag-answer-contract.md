# RAG Answer API 혼합 문서·인용·Source Locator 계약

## 1. 엔드포인트와 책임 경계

```text
POST /api/v1/rag/answers
```

이 API는 Local RAG에서 실행된다. 사용자 인증·인가와 파일 접근 권한의 최종
판정은 AWS Backend가 담당한다. Local RAG는 요청으로 전달된 `user_idx`와
`reference_file_idxs`를 현재 질문의 고정 검색 범위로 사용한다.

지원 원본 형식은 PDF, DOCX, PPTX, TXT, XLSX다. 일반 파서 텍스트와 이미지 OCR
텍스트는 같은 검색·답변·인용 계약을 사용한다.

## 2. 요청 스키마

```json
{
  "user_idx": 45,
  "reference_file_idxs": [101, 102, 103],
  "query": "모든 문서를 종합해 정책과 성과를 비교해줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

| 필드 | 형식 | 제약 | 의미 |
|---|---|---|---|
| `user_idx` | integer | 1 이상 | AWS 서버 DB 사용자 식별자 |
| `reference_file_idxs` | integer array | 1개 이상, 중복 금지 | 질문 전송 시점의 선택 문서 범위 |
| `query` | string | 1~4096자 | 문서 근거로 답할 질문 |
| `top_k` | integer | 1~20, 기본 5 | lookup 또는 문서별 synthesis 검색 상한 |
| `score_threshold` | number/null | -1.0~1.0 | 선택적 Cosine 점수 하한 |

`reference_file_idxs`가 비어 있거나 생략되면 전체 문서 검색으로 확장하지 않고
요청 검증 단계에서 거부한다.

## 3. 검색 범위와 활성 색인 계약

Qdrant 검색은 다음 조건을 `must`로 결합한다.

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

검색 저장소와 서비스 경계가 같은 범위를 각각 재검증한다.

- 선택하지 않은 `file_idx`는 최종 답변 후보에 들어갈 수 없다.
- 다른 사용자의 point는 검색 결과로 인정하지 않는다.
- `is_active != true`인 staging, 이전 버전 또는 실패 색인은 차단한다.
- 일반 텍스트와 OCR 청크를 구분하는 필터는 추가하지 않는다.
- 최종 synthesis 출처도 선택 문서 범위 안인지 다시 검증한다.

## 4. lookup과 synthesis

### 4.1 lookup

명시적인 다문서 비교·통합 의도가 없으면 기존 단일 검색·단일 생성 흐름을
사용한다. 선택 문서가 PDF, DOCX, PPTX, TXT, XLSX 중 어떤 조합이든 같은 흐름을
사용한다.

```text
선택 문서 전체 범위 검색
→ 프롬프트 컨텍스트 제한
→ Claude 생성
→ 본문 SOURCE-N 및 cited_source_ids 검증
→ 실제 인용 sources만 반환
```

### 4.2 synthesis

두 개 이상의 문서가 선택되고 질문에 비교·종합 의도가 있으면 파일별 독립 검색과
부분 답변을 사용한다.

```text
선택 파일별 독립 검색
→ 문서별 청크 수 제한
→ 전체 컨텍스트 예산 라운드 로빈 배분
→ 문서별 부분 답변 생성
→ 부분 답변 SOURCE-N 검증
→ 전역 SOURCE-N으로 재매핑
→ 유효한 부분 답변만 최종 Claude 입력에 포함
→ 최종 인용 검증 및 sources 축소
```

## 5. 문서별 부분 실패 처리

일부 문서의 파싱·색인 실패는 답변 시점에 활성 Qdrant point가 없는 상태로
나타나며 해당 파일의 검색 결과는 비게 된다. 일부 문서의 임베딩 또는 Qdrant
검색 자체가 실패할 수도 있다.

synthesis는 다음과 같이 처리한다.

| 상황 | 처리 |
|---|---|
| 한 문서가 미파싱·미색인되어 검색 결과 없음 | 해당 문서만 제외하고 계속 |
| 한 문서의 임베딩·Qdrant 검색 실패 | 안전한 로그만 남기고 다른 문서로 계속 |
| 한 문서의 부분 Claude 호출 실패 | 해당 부분 답변만 제외하고 계속 |
| 한 문서가 `insufficient_evidence` 반환 | 해당 부분 답변만 제외하고 계속 |
| 사용자 또는 선택 문서 범위 위반 | 부분 실패로 숨기지 않고 전체 요청 실패 |
| 잘못된 SOURCE-N 또는 인용 순서 | 근거 무결성 위반으로 전체 요청 실패 |
| 유효한 문서 그룹 없음 | Claude 호출 없이 근거 부족 반환 |
| 유효한 부분 답변 없음 | 최종 Claude 호출 없이 근거 부족 반환 |

## 6. 공통 Source Locator

모든 검색 결과와 최종 출처는 `source_locator` 객체를 사용한다. `file_type`은
원본 문서 형식이고 `content_origin`은 일반 텍스트 또는 OCR 텍스트 여부다.

### 6.1 공통 필드

| 필드 | 의미 |
|---|---|
| `file_type` | `pdf`, `docx`, `pptx`, `txt`, `xlsx` |
| `kind` | 대표 위치 종류 |
| `content_origin` | `text` 또는 `ocr` |
| `unit_type` | `paragraph`, `table`, `shape_text`, `ocr_image` 등 |
| `structure_path` | 사람이 확인 가능한 결정적 구조 경로 |

### 6.2 PDF

```json
{
  "file_type": "pdf",
  "kind": "pdf_page",
  "content_origin": "text",
  "structure_path": "page:7",
  "page": 7
}
```

기존 `RagAnswerSource.page`도 계속 반환하며 `source_locator.page`와 같은 값이어야
한다.

### 6.3 DOCX

```json
{
  "file_type": "docx",
  "kind": "docx_block",
  "content_origin": "text",
  "unit_type": "table",
  "structure_path": "section:2/block:9",
  "section_index": 2,
  "block_index": 9,
  "paragraph_index": 7,
  "table_index": 1,
  "heading_level": 2,
  "section_title": "운영 절차",
  "row_count": 4,
  "column_count": 3
}
```

제목 청크 이후 같은 섹션의 문단과 표에는 구조화 청커가 최근 제목과 제목 레벨을
연결한다.

### 6.4 PPTX

```json
{
  "file_type": "pptx",
  "kind": "pptx_shape",
  "content_origin": "text",
  "structure_path": "slide:4/shape:3.2",
  "slide_no": 4,
  "shape_index": 3,
  "shape_id": 27,
  "shape_path": "3.2",
  "shape_name": "성과 차트",
  "shape_type_name": "CHART",
  "coordinate_space": "group",
  "shape_left_emu": 100,
  "shape_top_emu": 200,
  "shape_width_emu": 300,
  "shape_height_emu": 400
}
```

기존 `RagAnswerSource.slide_no`도 유지하며 locator 값과 일치해야 한다.

### 6.5 XLSX

```json
{
  "file_type": "xlsx",
  "kind": "xlsx_cell_range",
  "content_origin": "text",
  "structure_path": "sheet:성과/range:B12:E12",
  "sheet_number": 2,
  "sheet_name": "성과",
  "row_number": 12,
  "start_row": 12,
  "end_row": 12,
  "start_column": 2,
  "end_column": 5,
  "start_cell": "B12",
  "end_cell": "E12",
  "cell_range": "B12:E12",
  "cell_coordinates": ["B12", "D12", "E12"],
  "merged_cell_ranges": ["B12:C12"]
}
```

기존 `RagAnswerSource.sheet_name`도 유지하며 locator 값과 일치해야 한다.

### 6.6 TXT

```json
{
  "file_type": "txt",
  "kind": "txt_line",
  "content_origin": "text",
  "structure_path": "line:14-16",
  "line_start": 14,
  "line_end": 16,
  "char_start": 120,
  "char_end": 184
}
```

`char_end`는 Python 문자열 slice와 같은 exclusive 위치다.

### 6.7 OCR

OCR 출처는 원본 형식 위치와 이미지 정보를 같은 locator에 함께 넣는다.

```json
{
  "file_type": "xlsx",
  "kind": "xlsx_cell_range",
  "content_origin": "ocr",
  "unit_type": "ocr_image",
  "structure_path": "sheet:성과/range:B2:E8",
  "sheet_name": "성과",
  "cell_range": "B2:E8",
  "image_ordinal": 2,
  "image_index": 2,
  "image_id": "xlsx-chart-2",
  "image_kind": "xlsx_chart_render",
  "ocr_engine": "easyocr",
  "ocr_mean_confidence": 0.93
}
```

- `image_ordinal`은 신규 표준 1-based 표시 순번이다.
- `image_index`는 기존 응답 소비자를 위한 동일 값의 호환 필드다.
- PDF OCR에는 `page`, DOCX OCR에는 섹션·블록, PPTX OCR에는 슬라이드·도형,
  XLSX OCR에는 시트·셀 범위가 함께 존재한다.
- OCR 청크는 일반 텍스트와 같은 SOURCE-N 검증을 통과해야 한다.

## 7. 정상 응답 스키마

```json
{
  "answer": "정책과 성과 차트를 함께 확인했습니다. [SOURCE-1][SOURCE-5]",
  "status": "answered",
  "cited_source_ids": ["SOURCE-1", "SOURCE-5"],
  "sources": [
    {
      "source_id": "SOURCE-1",
      "chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
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
```

## 8. 인용 무결성 계약

정상 응답은 다음 조건을 모두 만족해야 한다.

1. `answer`에 하나 이상의 `[SOURCE-N]`이 존재한다.
2. 본문 인용은 왼쪽에서 오른쪽으로 읽고 중복을 제거해 최초 등장 순서를 구한다.
3. `cited_source_ids`는 이 최초 등장 순서와 정확히 일치한다.
4. `sources[].source_id` 순서도 같은 최초 등장 순서와 정확히 일치한다.
5. 본문에 없는 후보 출처는 최종 `sources`에 포함할 수 없다.
6. 모든 SOURCE-N은 현재 프롬프트의 후보 출처에 존재해야 한다.
7. 같은 `source_id` 또는 `chunk_id`를 중복 반환할 수 없다.
8. synthesis 최종 출처는 요청의 `reference_file_idxs` 안에 있어야 한다.

기존 서비스 호출자가 `cited_source_ids`를 생성자에 생략해도 응답 스키마가 검증된
본문 인용 순서로 채운다. 외부 입력이 값을 명시한 경우에는 불일치를 자동으로
고치지 않고 응답 계약 위반으로 거부한다.

## 9. 근거 부족 응답

검색 가능한 문서가 없거나 유효한 부분 답변이 하나도 없으면 최종 Claude 호출을
생략하고 다음 응답을 반환한다.

```json
{
  "answer": "제공된 문서 근거만으로는 답변할 수 없습니다.",
  "status": "insufficient_evidence",
  "cited_source_ids": [],
  "sources": [],
  "model": null,
  "usage": null,
  "stop_reason": null
}
```

근거 부족 응답에는 본문 인용, sources, 모델 ID, 토큰 사용량 또는 종료 사유가
존재할 수 없다.

## 10. 오류 계약

공통 오류 응답 envelope는 프로젝트의 `ApiResponse` 및 전역 예외 처리 계약을
따른다. 질문 원문, 청크 원문, 프롬프트, Claude 원문, API Key, Qdrant payload
전체는 외부 오류 메시지나 로그에 포함하지 않는다.

| HTTP | 대표 코드 | 발생 조건 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 토큰 누락 또는 불일치 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED` | 참조문서 미선택 |
| 422 | `REQUEST_VALIDATION_FAILED` | user/query/top_k/threshold 형식 오류 |
| 429 | `GENERATION_BUDGET_EXCEEDED` | 답변별 Claude 호출·토큰 예산 초과 |
| 502 | `EMBEDDING_REQUEST_REJECTED` | TEI가 내부 요청을 거부 |
| 502 | `INVALID_EMBEDDING_RESPONSE` | TEI 응답 벡터 계약 위반 |
| 502 | `VECTOR_SEARCH_FAILED` | Qdrant 검색 요청 거부 또는 설정 불일치 |
| 502 | `INVALID_VECTOR_SEARCH_RESULT` | 검색 payload·범위·정렬 계약 위반 |
| 502 | `GENERATION_REQUEST_FAILED` | Claude 요청 실패 |
| 502 | `INVALID_GENERATION_RESPONSE` | 구조화 출력·SOURCE-N·인용 순서 계약 위반 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시적 사용 불가 |
| 503 | `VECTOR_DATABASE_UNAVAILABLE` | Qdrant 일시적 사용 불가 |
| 503 | `GENERATION_SERVICE_UNAVAILABLE` | Claude 인증·요청 제한·서버 장애 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | TEI 요청 시간 초과 |
| 504 | `GENERATION_SERVICE_TIMEOUT` | Claude 요청 시간 초과 |
| 500 | `INTERNAL_SERVER_ERROR` | 분류되지 않은 RAG 오케스트레이션 실패 |

문서별 synthesis 검색 실패는 다른 유효 문서가 있는 경우 외부 오류로 즉시
변환하지 않고 부분 실패로 처리한다. 단, 범위 위반과 인용 무결성 실패는 부분
성공으로 숨기지 않는다.
