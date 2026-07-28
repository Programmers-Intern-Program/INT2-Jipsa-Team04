# RAG Answer API 혼합 문서·인용·Source Locator 계약

> 계약 버전: `1.3.0`  
> 적용 범위: Local RAG 검색, lookup, synthesis, 부분 실패, 인용과 출처 응답

## 1. 엔드포인트와 책임 경계

```text
POST /api/v1/rag/answers
```

이 API는 AWS Backend와 분리된 Local RAG에서 실행됩니다. 사용자 인증·인가와 파일
접근 권한의 최종 판정은 AWS Backend가 담당합니다. Local RAG는 요청의 `user_idx`와
`reference_file_idxs`를 현재 질문의 불변 검색 범위로 사용합니다.

지원 원본 형식은 PDF, DOCX, PPTX, TXT, XLSX입니다. 일반 파서 텍스트와 이미지 OCR
텍스트는 같은 검색·답변·인용 계약을 사용합니다.

## 2. 요청 계약

```json
{
  "user_idx": 45,
  "reference_file_idxs": [101, 102, 103],
  "query": "선택한 문서를 종합해 정책과 성과를 비교해줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

| 필드 | 형식 | 제약 | 의미 |
|---|---|---|---|
| `user_idx` | integer | 1 이상 | AWS 서버 DB 사용자 식별자 |
| `reference_file_idxs` | integer array | 1개 이상, 중복 금지 | 질문 시점의 선택 문서 범위 |
| `query` | string | 1~4096자 | 선택 문서 근거로 답할 질문 |
| `top_k` | integer | 1~20, 기본 5 | lookup 또는 문서별 검색 상한 |
| `score_threshold` | number/null | -1.0~1.0 | 선택적 Cosine 점수 하한 |

`reference_file_idxs`가 비어 있거나 생략되면 전체 문서 검색으로 확장하지 않고 요청
검증 단계에서 거부합니다.

## 3. 검색 보안 경계

Qdrant 검색은 다음 조건을 하나의 AND 필터로 구성합니다.

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

검색 저장소는 Qdrant가 반환한 payload를 다시 검증합니다.

- `users_idx`가 요청 사용자와 다르면 거부합니다.
- `file_idx`가 선택 문서 범위 밖이면 거부합니다.
- `is_active`가 `true`가 아니면 거부합니다.
- payload `chunk_id`와 Qdrant Point ID가 다르면 거부합니다.
- 임베딩 모델 또는 벡터 차원 계약이 다르면 거부합니다.
- 최종 synthesis `sources`도 선택 문서 범위를 다시 검증합니다.

선택하지 않은 문서와 다른 사용자의 출처는 검색, 프롬프트 또는 최종 응답에 포함될 수
없습니다.

## 4. lookup

명시적인 다문서 비교·종합 의도가 없으면 단일 검색·단일 생성 흐름을 사용합니다.
선택 문서가 어떤 지원 형식 조합이든 같은 흐름을 사용합니다.

```text
선택 문서 전체 범위 검색
→ 컨텍스트 제한
→ Claude 구조화 생성
→ SOURCE-N 검증
→ 실제 인용 sources만 반환
```

검색 결과가 없으면 Claude를 호출하지 않고 `insufficient_evidence`를 반환합니다.

## 5. synthesis

두 개 이상의 문서를 선택하고 질문에 비교, 대조, 종합, 통합 또는 문서별 요약 의도가
있으면 파일별 독립 검색과 부분 답변을 사용합니다.

```text
선택 파일별 독립 검색
→ 문서별 청크 수 제한
→ 전체 컨텍스트 예산 라운드 로빈 배분
→ 문서별 부분 답변 생성
→ 문서 로컬 SOURCE-N 검증
→ 요청 전체 전역 SOURCE-N 재매핑
→ 유효 부분 답변만 최종 Claude 입력에 포함
→ 최종 SOURCE-N과 선택 문서 범위 검증
```

## 6. 부분 실패 계약

| 상황 | 처리 |
|---|---|
| 한 문서가 미파싱·미색인 | 해당 문서만 제외하고 계속 |
| 한 문서의 임베딩·Qdrant 검색 실패 | 안전한 진단만 기록하고 다른 문서로 계속 |
| 한 문서의 부분 Claude 호출 실패 | 해당 부분 답변만 제외하고 계속 |
| 한 문서가 근거 부족 반환 | 해당 부분 답변만 제외하고 계속 |
| 모든 문서 검색 결과 없음 | Claude 호출 없이 근거 부족 반환 |
| 유효 부분 답변 없음 | 최종 Claude 호출 없이 근거 부족 반환 |
| 사용자·선택 문서 범위 위반 | 전체 요청 실패 |
| 잘못된 SOURCE-N 또는 인용 순서 | 전체 요청 실패 |

부분 성공 응답은 성공한 문서의 근거만 사용합니다. 실패한 문서의 빈 출처, 오류 원문
또는 선택 범위 밖 출처를 `sources`에 합성하지 않습니다.

## 7. 정상 응답 계약

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "정책과 성과를 확인했습니다. [SOURCE-1][SOURCE-2]",
    "status": "answered",
    "cited_source_ids": ["SOURCE-1", "SOURCE-2"],
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

## 8. 인용 무결성

정상 `answered` 응답은 다음 조건을 모두 만족해야 합니다.

1. `answer`에 하나 이상의 `[SOURCE-N]`이 존재합니다.
2. 본문 인용은 최초 등장 순서로 중복을 제거합니다.
3. `cited_source_ids`가 본문 최초 등장 순서와 정확히 같습니다.
4. `sources[].source_id` 순서도 같은 순서와 정확히 같습니다.
5. 본문에 없는 후보 출처를 최종 `sources`에 포함하지 않습니다.
6. 모든 SOURCE-N은 현재 프롬프트 후보에 존재합니다.
7. 같은 `source_id` 또는 `chunk_id`를 중복 반환하지 않습니다.
8. 모든 `sources[].file_idx`가 요청 선택 문서 범위 안에 있습니다.
9. 모든 출처의 Chunk ID는 선택 문서의 실제 활성 Local RAG 청크와 연결됩니다.

외부 입력이 `cited_source_ids`를 명시한 경우 불일치를 자동으로 고치지 않고 응답 계약
위반으로 거부합니다.

## 9. 근거 부족 응답

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

근거 부족 응답에는 본문 SOURCE-N, sources, 모델 ID, 토큰 사용량 또는 종료 사유가
존재할 수 없습니다. 검색 결과가 전혀 없는 경우 생성 클라이언트 호출 횟수는 0입니다.

## 10. Source Locator 공통 계약

| 필드 | 의미 |
|---|---|
| `file_type` | `pdf`, `docx`, `pptx`, `txt`, `xlsx` |
| `kind` | 대표 위치 종류 |
| `content_origin` | `text` 또는 `ocr` |
| `unit_type` | `paragraph`, `table`, `shape_text`, `ocr_image` 등 |
| `structure_path` | 사람이 확인 가능한 결정적 구조 경로 |

### PDF

```json
{
  "file_type": "pdf",
  "kind": "pdf_page",
  "content_origin": "text",
  "structure_path": "page:7",
  "page": 7
}
```

기존 top-level `page`가 존재하면 `source_locator.page`와 같아야 합니다.

### DOCX

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

### PPTX

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

기존 top-level `slide_no`가 존재하면 locator 값과 같아야 합니다.

### XLSX

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

`sheet_number`는 사용자 표시를 위한 1-based 값입니다. 기존 top-level `sheet_name`이
존재하면 locator 값과 같아야 합니다.

### TXT

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

`char_end`는 Python 문자열 slice와 같은 exclusive 위치입니다.

### OCR

OCR 출처는 원본 형식의 위치와 이미지 정보를 한 locator에 함께 넣습니다.

```json
{
  "file_type": "xlsx",
  "kind": "xlsx_cell_range",
  "content_origin": "ocr",
  "unit_type": "ocr_image",
  "structure_path": "sheet:성과/range:B2:E8",
  "sheet_number": 1,
  "sheet_name": "성과",
  "cell_range": "B2:E8",
  "image_ordinal": 2,
  "image_index": 2,
  "image_id": "xlsx-chart-2",
  "image_kind": "xlsx_chart_render",
  "ocr_engine": "EASYOCR_CUDA",
  "ocr_mean_confidence": 0.93
}
```

- `image_ordinal`은 신규 표준 1-based 문서 이미지 순번입니다.
- `image_index`는 기존 응답 소비자를 위한 같은 값의 호환 필드입니다.
- PDF OCR에는 페이지, DOCX OCR에는 블록·문단·표, PPTX OCR에는 슬라이드·도형,
  XLSX OCR에는 시트·셀 범위가 함께 존재합니다.
- OCR 청크는 일반 텍스트와 동일한 SOURCE-N 검증을 통과해야 합니다.

## 11. 재색인과 활성 출처

답변 검색에는 `is_active=true`인 Qdrant point만 사용합니다.

- 새 색인은 비활성 staging point로 저장합니다.
- 새 point 전체 저장 후 신규 문서를 활성화합니다.
- 신규 활성화 후 이전 문서를 비활성화합니다.
- Local RAG 성공 확정 후 이전 DB 문서를 soft delete합니다.
- 실패하면 이전 정상 point를 복구하고 실패 실행의 신규 point를 삭제합니다.
- 같은 정상 색인 재사용 실패는 기존 문서와 point를 삭제하지 않습니다.

동일 `File_IDX` 동시 요청은 파일 advisory lock을 사용하여 직렬화하며, 최종적으로
하나의 활성 문서와 결정적 Chunk ID 집합으로 수렴해야 합니다.

## 12. 로그와 오류 정보 제한

질문 원문, 청크와 OCR 원문, 전체 프롬프트, Claude 응답 원문과 인증정보를 오류 메시지
또는 구조화 로그에 포함하지 않습니다.

금지 또는 마스킹 대상:

- `query`, 청크 `content`, OCR 원문
- system/user prompt 전체
- `X-Internal-Token`, `RAG_INGEST_TOKEN`, Bearer 토큰
- Anthropic 및 Qdrant API Key
- Presigned URL과 AWS 서명 파라미터
- DB URL, DSN, 사용자명과 비밀번호

허용되는 진단값은 요청 ID, 사용자·파일 식별자, 안전한 오류 종류, 외부 HTTP 상태 코드,
작업명과 개수 정보입니다.

## 13. 오류 계약

| HTTP | 대표 코드 | 발생 조건 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 토큰 누락 또는 불일치 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED` | 참조문서 미선택 |
| 422 | `REQUEST_VALIDATION_FAILED` | 요청 형식 오류 |
| 429 | `GENERATION_BUDGET_EXCEEDED` | 답변별 Claude 호출·토큰 예산 초과 |
| 502 | `EMBEDDING_REQUEST_REJECTED` | TEI 요청 거부 |
| 502 | `INVALID_EMBEDDING_RESPONSE` | TEI 벡터 계약 위반 |
| 502 | `VECTOR_SEARCH_FAILED` | Qdrant 검색 거부 또는 설정 불일치 |
| 502 | `INVALID_VECTOR_SEARCH_RESULT` | 검색 payload·범위 계약 위반 |
| 502 | `GENERATION_REQUEST_FAILED` | Claude 요청 실패 |
| 502 | `INVALID_GENERATION_RESPONSE` | 구조화 출력·SOURCE-N 계약 위반 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시적 사용 불가 |
| 503 | `VECTOR_DATABASE_UNAVAILABLE` | Qdrant 일시적 사용 불가 |
| 503 | `GENERATION_SERVICE_UNAVAILABLE` | Claude 인증·제한·서버 장애 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | TEI 요청 시간 초과 |
| 504 | `GENERATION_SERVICE_TIMEOUT` | Claude 요청 시간 초과 |
| 500 | `INTERNAL_SERVER_ERROR` | 분류되지 않은 오케스트레이션 실패 |

문서별 synthesis 검색·생성 실패는 유효한 다른 문서가 있으면 부분 실패로 처리합니다.
범위 위반과 인용 무결성 실패는 부분 성공으로 숨기지 않습니다.

## 14. E2E 검증 계약

`tests/e2e/test_fixed_document_full_pipeline_e2e.py`는 다음을 실제 Local 인프라에서
검증합니다.

- PDF, DOCX, PPTX, XLSX, TXT 구조와 출처 위치
- 실제 CUDA EasyOCR와 OCR 이미지 순번
- CUDA TEI 임베딩과 벡터 차원
- Local RAG DB 문서·청크·색인 실행 이력
- Qdrant point, payload와 활성 상태
- 형식별 lookup, 다중 형식 synthesis와 텍스트·OCR 혼합 답변
- 본문 SOURCE-N, `cited_source_ids`, `sources` 순서
- 선택하지 않은 문서와 다른 사용자 출처 차단
- 전체 근거 부족 시 Claude 미호출
- 일부 문서 실패 시 정상 문서 답변 유지
- 재인제스트, 재색인, soft delete와 보상 처리
- 중복·동시 인제스트 수렴
- 임시 파일과 추출 이미지 정리
- 질문, 청크, OCR 원문, 프롬프트와 인증정보 로그 비노출

실행기는 `scripts/run-issue-123-e2e.ps1`입니다. 일반 품질 게이트는
`scripts/verify-rag-quality.ps1`을 사용하며 다음을 모두 통과해야 합니다.

1. Ruff 포맷 검사
2. Ruff 린트 검사
3. Mypy strict 검사
4. 일반 전체 Pytest
5. 실제 Issue #123 E2E

실제 외부 서비스 E2E는 opt-in이며, 실행하지 않은 결과를 통과로 기록하지 않습니다.
