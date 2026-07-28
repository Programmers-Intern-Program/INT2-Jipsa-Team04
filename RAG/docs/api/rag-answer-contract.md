# RAG Answer API 혼합 문서·인용·Source Locator 계약

> **문서 상태:** Stable · 답변·인용·Source Locator 상세 계약  
> **주 독자:** Local RAG 서비스 개발자, Backend·Frontend 출처 소비자, QA  
> **계약 버전:** `1.3.0`  
> **최종 검토:** 2026-07-28


적용 범위는 Local RAG 검색, lookup, synthesis, 부분 실패, 인용과 출처 응답입니다.


> **통합 진입점:** 전체 endpoint와 공통 오류·인증 계약은
> [종합 API 명세서](comprehensive-api-specification.md)를 확인합니다. 이 문서는 답변 상태,
> 인용 불변식과 형식별 `SourceLocator`를 가장 세밀하게 정의합니다.

## 1. 엔드포인트와 책임 경계

```text
POST /api/v1/rag/answers
```

사용자 인증·인가와 파일 접근 권한의 최종 판정은 AWS Backend가 담당합니다.
Local RAG는 `user_idx`와 `reference_file_idxs`를 현재 질문의 불변 검색 범위로 사용합니다.

지원 원본 형식은 PDF, DOCX, PPTX, XLSX, TXT입니다. 일반 파서 텍스트와 이미지 OCR
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
| `user_idx` | integer | 1 이상 | AWS Backend 사용자 식별자 |
| `reference_file_idxs` | integer array | 1~20개, 중복 금지 | 질문 시점의 선택 문서 |
| `query` | string | 1~4096자 | 선택 문서 근거로 답할 질문 |
| `top_k` | integer | 1~20, 기본 5 | lookup 또는 문서별 검색 상한 |
| `score_threshold` | number/null | -1.0~1.0 | 선택적 Cosine 점수 하한 |

## 3. 검색 보안 경계

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

다음 불일치는 즉시 거부합니다.

- `users_idx`가 요청 사용자와 다름
- `file_idx`가 선택 문서 범위 밖
- `is_active`가 `true`가 아님
- payload `chunk_id`와 Qdrant Point ID가 다름
- 임베딩 모델 또는 벡터 차원 계약이 다름
- 최종 `sources`가 선택 문서 범위를 벗어남

## 4. lookup

```text
선택 문서 전체 범위 검색
  → 컨텍스트 제한
  → Claude 구조화 생성
  → SOURCE-N 검증
  → 실제 인용 sources만 반환
```

검색 결과가 없으면 Claude를 호출하지 않고 `insufficient_evidence`를 반환합니다.

## 5. synthesis

```text
선택 파일별 독립 검색
  → 문서별 청크 수 제한
  → 전체 컨텍스트 예산 라운드 로빈 배분
  → 문서별 부분 답변
  → 문서 로컬 SOURCE-N 검증
  → 전역 SOURCE-N 재매핑
  → 유효 부분 답변만 최종 Claude 입력
  → 최종 SOURCE-N과 범위 검증
```

## 6. 부분 실패

| 상황 | 처리 |
|---|---|
| 한 문서가 미파싱·미색인 | 해당 문서만 제외 |
| 한 문서 임베딩·검색 실패 | 안전한 진단만 기록하고 계속 |
| 한 문서 부분 Claude 실패 | 해당 부분만 제외 |
| 한 문서 근거 부족 | 해당 부분만 제외 |
| 모든 문서 검색 결과 없음 | Claude 호출 없이 근거 부족 |
| 유효 부분 답변 없음 | 최종 Claude 호출 없이 근거 부족 |
| 사용자·파일 범위 위반 | 전체 실패 |
| 잘못된 SOURCE-N·순서 | 전체 실패 |

부분 성공 응답은 성공한 문서 근거만 사용합니다.

## 7. 정상 응답

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

최종 `sources`에는 실제로 인용한 출처만 포함합니다.

정상 `answered` 응답은 다음 조건을 모두 만족합니다.

1. `answer`에 하나 이상의 `[SOURCE-N]`이 존재합니다.
2. 반복 인용은 최초 등장만 계산합니다.
3. `cited_source_ids`가 본문 최초 등장 순서와 같습니다.
4. `sources[].source_id` 순서도 같습니다.
5. 본문에 없는 후보 출처를 `sources`에 포함하지 않습니다.
6. 모든 SOURCE-N이 현재 프롬프트 후보에 존재합니다.
7. `source_id`와 `chunk_id`는 각각 중복되지 않습니다.
8. 모든 `sources[].file_idx`가 선택 문서 범위 안에 있습니다.
9. 모든 Chunk ID가 최신 활성 Local RAG 청크와 연결됩니다.

## 9. 근거 부족

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

근거 부족 응답에는 SOURCE-N, 모델 ID, 토큰 사용량과 종료 사유가 존재하지 않습니다.

## 10. Source Locator 공통 계약

| 필드 | 의미 |
|---|---|
| `file_type` | `pdf`, `docx`, `pptx`, `xlsx`, `txt` |
| `kind` | `pdf_page`, `docx_block`, `pptx_shape`, `xlsx_cell_range`, `txt_line` |
| `content_origin` | `text` 또는 `ocr` |
| `unit_type` | `paragraph`, `table`, `shape_text`, `row`, `ocr_image` 등 |
| `structure_path` | 사람이 확인 가능한 결정적 구조 경로 |

## 11. PDF

```json
{
  "file_type": "pdf",
  "kind": "pdf_page",
  "content_origin": "text",
  "unit_type": "paragraph",
  "structure_path": "page:7",
  "page": 7
}
```

top-level `page`가 존재하면 `source_locator.page`와 같아야 합니다.

## 12. DOCX

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

## 13. PPTX

```json
{
  "file_type": "pptx",
  "kind": "pptx_shape",
  "content_origin": "text",
  "unit_type": "shape_text",
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

top-level `slide_no`가 존재하면 locator 값과 같아야 합니다.

## 14. XLSX

```json
{
  "file_type": "xlsx",
  "kind": "xlsx_cell_range",
  "content_origin": "text",
  "unit_type": "row",
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

`sheet_number`는 1-based입니다. top-level `sheet_name`은 locator 값과 같아야 합니다.

## 15. TXT

```json
{
  "file_type": "txt",
  "kind": "txt_line",
  "content_origin": "text",
  "unit_type": "line_range",
  "structure_path": "line:21-27",
  "line_start": 21,
  "line_end": 27,
  "char_start": 410,
  "char_end": 588
}
```

`char_end`는 exclusive입니다.

## 16. OCR

OCR은 원본 형식의 위치를 유지하면서 다음 필드를 추가합니다.

```json
{
  "content_origin": "ocr",
  "unit_type": "ocr_image",
  "image_ordinal": 2,
  "image_index": 2,
  "image_id": "sha256:...",
  "image_kind": "embedded",
  "ocr_engine": "easyocr",
  "ocr_mean_confidence": 0.91
}
```

- `image_ordinal`: 사용자에게 표시하는 1-based 이미지 순번
- `image_index`: 기존 소비자 호환용 동일 값
- `image_id`: 결정적 이미지 식별자
- `image_kind`: embedded, scan_page, chart, smartart 등
- `ocr_engine`: OCR 구현체
- `ocr_mean_confidence`: 유효 OCR 줄 평균 신뢰도

## 17. legacy 위치 호환

다음 top-level 필드는 기존 소비자를 위해 유지합니다.

- `page`
- `slide_no`
- `sheet_name`
- `section_title`

신규 소비자는 `source_locator`를 기준으로 표시합니다.
legacy와 locator 값이 모두 존재하면 서로 일치해야 합니다.

## 18. 출처 데이터 최소화

검색 API는 내부 서비스가 사용할 청크 `content`를 반환할 수 있지만,
답변 API의 `sources`는 실제 인용한 출처와 제한된 `excerpt`만 반환합니다.

다음 값은 답변 출처에 포함하지 않습니다.

- 전체 문서 원문
- 임베딩 벡터
- Presigned URL
- S3 서명 파라미터
- 내부 인증 토큰
- DB 접속 정보

## 19. 관련 문서

- [AWS Backend용 답변 API 계약](rag-answer-api-contract.md)
- [관련 청크 검색 API](../chunk-search-api.md)
- [지원 문서 형식과 이미지 OCR](../features/document-support-and-ocr.md)
- [환경 변수와 민감정보 관리](../security/environment-and-secrets.md)
