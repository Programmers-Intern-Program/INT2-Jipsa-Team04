# RAG Answer API 혼합 문서 및 Source Locator 계약

## 1. 적용 범위

`POST /api/v1/rag/answers`는 사용자가 선택한 파일 범위에서만 근거를 검색한다.
지원 원본 형식은 다음과 같다.

- PDF
- DOCX
- PPTX
- TXT
- XLSX

일반 파서 텍스트와 이미지 OCR 텍스트는 같은 Qdrant collection과 같은 검색
점수 기준을 사용한다. OCR 청크를 별도로 제외하는 검색 필터는 사용하지 않는다.

사용자 인증·인가와 파일 접근 권한의 최종 판정은 AWS Backend의 책임이다.
Local RAG는 요청으로 전달된 `user_idx`와 `reference_file_idxs`를 검색 범위로
고정하고, Qdrant 결과가 해당 범위를 벗어나지 않는지 재검증한다.

## 2. 검색 범위 계약

Qdrant 검색은 다음 세 조건을 `must`로 결합한다.

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

Repository와 Service가 같은 조건을 각각 검증한다.

- 선택하지 않은 `file_idx`가 반환되면 요청을 실패시킨다.
- 다른 사용자의 `users_idx`가 반환되면 요청을 실패시킨다.
- 비활성 point는 검색 필터와 결과 재검증에서 모두 차단한다.
- 검색 결과의 중복 Chunk ID, 점수 순서 및 `top_k` 초과를 차단한다.

## 3. 질의 유형

### lookup

명시적인 다문서 비교·종합 의도가 없으면 기존 lookup 흐름을 사용한다.
PDF, DOCX, PPTX, TXT, XLSX 중 어떤 형식이 선택되어도 동일하게 동작한다.

```text
선택 문서 전체 범위 검색
→ 전체 컨텍스트 제한 적용
→ 단일 Claude 생성
→ 답변 인용과 cited_source_ids 검증
→ 실제 인용 출처만 반환
```

### synthesis

두 개 이상의 문서가 선택되고 질문에 비교·종합 의도가 명시되면 synthesis를
사용한다.

```text
선택 파일별 독립 검색
→ 문서별 청크 수 제한
→ 전체 컨텍스트 예산을 문서 간 라운드 로빈 배분
→ 문서별 부분 답변 생성
→ 각 부분 답변의 SOURCE-N 검증
→ 요청 전체의 전역 SOURCE-N으로 재매핑
→ 검증된 부분 답변만 최종 Claude 입력으로 사용
→ 최종 인용 검증
```

한 문서의 검색이 실패하더라도 다른 문서의 유효한 검색 결과는 유지한다.
다만 사용자·선택 문서 범위 위반이나 잘못된 인용은 부분 실패로 숨기지 않고
전체 요청을 실패시킨다.

유효한 부분 답변이 하나도 없으면 최종 Claude 호출을 생략하고
`insufficient_evidence`를 반환한다.

## 4. Source Locator

모든 검색 결과와 최종 출처는 `source_locator`를 사용한다.

```json
{
  "file_type": "xlsx",
  "kind": "xlsx_cell_range",
  "content_origin": "ocr",
  "unit_type": "ocr_image",
  "structure_path": "sheet:성과/range:B2:E10",
  "sheet_name": "성과",
  "cell_range": "B2:E10",
  "image_index": 2,
  "image_id": "chart-2",
  "image_kind": "xlsx_chart_render",
  "ocr_engine": "easyocr",
  "ocr_mean_confidence": 0.91
}
```

### PDF

- `kind`: `pdf_page`
- `page`: 1부터 시작하는 페이지 번호
- OCR인 경우 `image_index`, `image_id`, `image_kind` 추가

### DOCX

- `kind`: `docx_block`
- `section_index`, `block_index`, `paragraph_index`, `table_index`
- `section_title`, `structure_path`
- OCR인 경우 원본 블록 위치와 이미지 정보를 함께 반환

### PPTX

- `kind`: `pptx_slide` 또는 `pptx_shape`
- `slide_no`, `shape_index`, `shape_id`, `shape_path`
- 필요한 경우 EMU 좌표와 크기 반환
- OCR인 경우 그림·차트·SmartArt 렌더 이미지 정보를 함께 반환

### XLSX

- `kind`: `xlsx_cell_range`
- `sheet_number`, `sheet_name`, `cell_range`
- `cell_coordinates`, `merged_cell_ranges`
- OCR인 경우 삽입 이미지 또는 차트 렌더 이미지 정보를 함께 반환

### TXT

- `kind`: `txt_line`
- `line_number`, `char_start`, `char_end`

## 5. OCR 출처 계약

OCR 청크는 다음 조건을 만족한다.

- `source_locator.content_origin == "ocr"`
- `source_locator.image_id` 또는 `image_index`가 존재한다.
- 원본 문서 위치가 동일한 locator에 함께 존재한다.
- 일반 텍스트 청크와 동일한 SOURCE-N 인용 검증을 통과한다.
- OCR 원문 전체는 외부 응답에 반환하지 않고 제한된 `excerpt`만 반환한다.

## 6. 응답 예시

```json
{
  "answer": "정책 목표와 성과 증가가 함께 확인됩니다. [SOURCE-1][SOURCE-2]",
  "status": "answered",
  "sources": [
    {
      "source_id": "SOURCE-1",
      "chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      "rag_document_idx": 1001,
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
      "excerpt": "정책 문서는 목표를 설명합니다."
    },
    {
      "source_id": "SOURCE-2",
      "chunk_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
      "rag_document_idx": 2002,
      "file_idx": 202,
      "folder_idx": 9,
      "file_name": "성과.xlsx",
      "file_type": "xlsx",
      "chunk_index": 0,
      "score": 0.93,
      "page": null,
      "slide_no": null,
      "sheet_name": "성과",
      "section_title": null,
      "source_locator": {
        "file_type": "xlsx",
        "kind": "xlsx_cell_range",
        "content_origin": "ocr",
        "unit_type": "ocr_image",
        "structure_path": "sheet:성과/range:B2:E10",
        "sheet_name": "성과",
        "cell_range": "B2:E10",
        "cell_coordinates": [],
        "merged_cell_ranges": [],
        "image_index": 2,
        "image_id": "chart-2",
        "image_kind": "xlsx_chart_render"
      },
      "excerpt": "[이미지 OCR] 성과 차트는 증가 추세입니다."
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

## 7. 인용 무결성

정상 답변은 다음 조건을 모두 만족해야 한다.

1. answer에 하나 이상의 `[SOURCE-N]`이 존재한다.
2. `cited_source_ids`는 answer의 최초 등장 순서와 정확히 일치한다.
3. 모든 SOURCE-N은 현재 프롬프트의 후보 출처에 존재한다.
4. 응답 `sources`에는 실제 인용된 출처만 최초 등장 순서로 포함한다.
5. 같은 `source_id` 또는 `chunk_id`가 중복되지 않는다.
6. synthesis의 최종 출처는 요청 시 선택한 `reference_file_idxs` 안에 있어야 한다.

## 8. 근거 부족 응답

검색 결과 또는 유효한 부분 답변이 없으면 Claude 호출을 생략할 수 있다.

```json
{
  "answer": "제공된 문서 근거만으로는 답변할 수 없습니다.",
  "status": "insufficient_evidence",
  "sources": [],
  "model": null,
  "usage": null,
  "stop_reason": null
}
```
