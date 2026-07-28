# 관련 청크 검색 API

> **문서 상태:** Stable · 내부 API 계약  
> **주 독자:** AWS Backend 연동 개발자, Local RAG API 개발자, QA  
> **계약 기준:** `ChunkSearchRequest`, `ChunkSearchResult`, FastAPI OpenAPI  
> **최종 검토:** 2026-07-28



> **통합 진입점:** 전체 endpoint, 인증, 공통 envelope, 오류·재시도와 Backend callback은
> [종합 API 명세서](api/comprehensive-api-specification.md)를 먼저 확인합니다. 이 문서는
> `POST /api/v1/chunks/search`의 검색 범위와 결과 schema를 심화 설명합니다.

## 1. 문서 정보

| 항목 | 값 |
|---|---|
| 호출 방향 | AWS Backend → Local RAG |
| HTTP Method | `POST` |
| Path | `/api/v1/chunks/search` |
| Content-Type | `application/json` |
| 인증 헤더 | `X-Internal-Token` |
| 요청 추적 헤더 | `X-Request-ID` |
| 검색 범위 | 요청 사용자 + 활성 색인 + 선택 문서 |
| 지원 형식 | PDF, DOCX, PPTX, XLSX, TXT와 OCR 청크 |

이 API는 브라우저나 모바일 클라이언트가 직접 호출하지 않습니다.

## 2. 인증

```text
X-Internal-Token: <RAG_INGEST_TOKEN과 동일한 값>
```

Backend → Local RAG 호출에는 `RAG_INGEST_TOKEN`을 사용합니다.
Local RAG → Backend `/internal/**` 호출에 사용하는 `INTERNAL_TOKEN`과 혼용하지 않습니다.

## 3. 요청

```json
{
  "user_idx": 45,
  "reference_file_idxs": [123, 456],
  "query": "선택한 문서의 배포 절차를 비교해줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

| 필드 | 타입 | 필수 | 기본값 | 제약 |
|---|---|---:|---:|---|
| `user_idx` | integer | 예 | 없음 | 1 이상 |
| `reference_file_idxs` | integer array | 예 | 없음 | 1~20개, 중복 없음, 각 값 1 이상 |
| `query` | string | 예 | 없음 | 공백 정규화 후 1~4096자 |
| `top_k` | integer | 아니요 | 5 | 1~20 |
| `score_threshold` | number/null | 아니요 | null | -1.0~1.0 |

정의되지 않은 추가 필드는 허용하지 않습니다.

`reference_file_idxs`가 없거나 빈 배열이면 사용자 전체 문서 검색으로 확대하지 않고
요청을 거부합니다.

## 4. 처리 흐름

```text
내부 토큰 검증
  → 요청 스키마 검증
  → TEI CUDA 질의 임베딩
  → Qdrant must 필터 검색
  → payload 사용자·파일·활성 상태 재검증
  → score_threshold 방어 검증
  → score 내림차순 정렬
  → Source Locator 포함 응답
```

Qdrant 필터:

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

`top_k`는 Qdrant `limit`과 서비스 결과 상한에 모두 적용합니다.
`score_threshold`를 사용하면 실제 결과 수는 `top_k`보다 작을 수 있습니다.

## 5. 성공 응답

```json
{
  "success": true,
  "code": "CHUNK_SEARCH_COMPLETED",
  "message": "Relevant document chunks were retrieved.",
  "data": {
    "user_idx": 45,
    "result_count": 1,
    "results": [
      {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "score": 0.92,
        "rag_document_idx": 100,
        "file_idx": 123,
        "folder_idx": 9,
        "file_name": "프로젝트 가이드.pdf",
        "file_type": "pdf",
        "chunk_index": 3,
        "content": "로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다.",
        "token_count": 128,
        "page": 2,
        "slide_no": null,
        "sheet_name": null,
        "section_title": "로컬 실행 방법",
        "source_locator": {
          "file_type": "pdf",
          "kind": "pdf_page",
          "content_origin": "text",
          "unit_type": "paragraph",
          "structure_path": "page:2",
          "page": 2,
          "cell_coordinates": [],
          "merged_cell_ranges": []
        },
        "parser_version": "1.0.0",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "index_version": 2
      }
    ]
  }
}
```

결과가 없는 경우도 요청 자체는 성공입니다.

```json
{
  "success": true,
  "code": "CHUNK_SEARCH_COMPLETED",
  "message": "Relevant document chunks were retrieved.",
  "data": {
    "user_idx": 45,
    "result_count": 0,
    "results": []
  }
}
```

## 6. 결과 필드

| 필드 | 의미 |
|---|---|
| `chunk_id` | Local RAG DB `RAG_Chunk.Chunk_ID`와 같은 Qdrant Point ID |
| `score` | 질의와 청크의 Cosine 관련도 |
| `rag_document_idx` | Local RAG 문서 식별자 |
| `file_idx` | AWS Backend `File.File_IDX` |
| `content` | 일반 텍스트 또는 OCR 텍스트 근거 원문 |
| `source_locator` | 형식별 원본 위치와 OCR 이미지 위치 |
| `page` 등 legacy 필드 | 기존 소비자 하위 호환 |
| `parser_version` | 파서 계약 버전 |
| `embedding_model` | 색인에 사용된 임베딩 모델 |
| `index_version` | 청킹·색인 계약 버전 |

신규 UI와 서비스는 `source_locator`를 우선 사용합니다.
legacy 위치와 `source_locator`가 동시에 존재하면 값이 일치해야 합니다.

## 7. Source Locator 예시

### DOCX OCR

```json
{
  "file_type": "docx",
  "kind": "docx_block",
  "content_origin": "ocr",
  "unit_type": "ocr_image",
  "structure_path": "section:1/block:7/image:2",
  "section_index": 1,
  "block_index": 7,
  "paragraph_index": 5,
  "image_ordinal": 2,
  "image_index": 2,
  "image_id": "sha256:...",
  "image_kind": "inline",
  "ocr_engine": "easyocr"
}
```

### XLSX

```json
{
  "file_type": "xlsx",
  "kind": "xlsx_cell_range",
  "content_origin": "text",
  "unit_type": "row",
  "structure_path": "sheet:성과/range:B12:E12",
  "sheet_number": 2,
  "sheet_name": "성과",
  "start_cell": "B12",
  "end_cell": "E12",
  "cell_range": "B12:E12",
  "cell_coordinates": ["B12", "D12", "E12"],
  "merged_cell_ranges": ["B12:C12"]
}
```

## 8. 오류

```json
{
  "success": false,
  "code": "ERROR_CODE",
  "message": "Public error message.",
  "data": null
}
```

| HTTP | 코드 | 의미 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 실패 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED` | 참조문서 미선택 |
| 422 | `REQUEST_VALIDATION_FAILED` | 요청 필드 검증 실패 |
| 502 | `EMBEDDING_REQUEST_REJECTED` | TEI가 요청을 거부 |
| 502 | `INVALID_EMBEDDING_RESPONSE` | TEI 응답·차원 계약 위반 |
| 502 | `VECTOR_SEARCH_FAILED` | Qdrant 검색 실패 |
| 502 | `INVALID_VECTOR_SEARCH_RESULT` | Qdrant payload·범위 계약 위반 |
| 503 | `SERVICE_UNAVAILABLE` | 내부 토큰 미설정 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시적 사용 불가 |
| 503 | `VECTOR_DATABASE_UNAVAILABLE` | Qdrant 일시적 사용 불가 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | TEI 시간 초과 |
| 500 | `INTERNAL_SERVER_ERROR` | 분류되지 않은 내부 오류 |

오류 응답과 로그에는 질문 원문, 청크·OCR 원문, 임베딩 벡터, 내부 토큰,
Presigned URL과 DB 접속 정보를 포함하지 않습니다.

## 9. 관련 문서

- [AWS Backend와 Local RAG 책임 경계](architecture/responsibility-boundary.md)
- [답변 API 계약](api/rag-answer-api-contract.md)
- [Source Locator 상세 계약](api/rag-answer-contract.md)
- [환경 변수와 민감정보 관리](security/environment-and-secrets.md)
