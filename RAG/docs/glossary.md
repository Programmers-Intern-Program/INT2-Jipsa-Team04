# Local RAG 용어집

> **문서 상태:** Stable · 전 문서 공통 용어 기준  
> **주 독자:** 모든 개발자, QA, 운영자, 문서 리뷰어  
> **최종 검토:** 2026-07-28  
> **원칙:** 같은 개념은 같은 이름·대소문자·필드명을 사용

## 1. 시스템과 저장소

| 표준 용어 | 정의 | 사용하지 않는 표현 |
|---|---|---|
| AWS Backend | 사용자 인증·인가, 파일 권한·상태, S3와 Local RAG 호출을 담당하는 Spring Boot 서비스 | AI 서버, 메인 서버 |
| Local RAG | AWS Backend와 분리된 로컬 GPU 기반 FastAPI 문서 검색·답변 서비스 | RAG Server, Python 서버 |
| Local RAG DB | `Jipsa_Local_RAG` 스키마의 문서·청크·색인 실행 이력 저장소 | RAG MySQL DB처럼 구현체만 강조한 이름 |
| Qdrant | 청크 임베딩과 검색 payload를 저장하는 VectorDB | 벡터 DB 서버라는 모호한 이름 |
| TEI | Hugging Face Text Embeddings Inference. 문서·질의 임베딩 생성 서비스 | 임베딩 AI |
| EasyOCR | 한국어·영어 문서 이미지 OCR 엔진 | 이미지 파서 |
| Frontend | React·TypeScript·Vite 기반 사용자 UI | Client만 단독 사용 |

## 2. 식별자와 요청 범위

| 용어 | 정의 |
|---|---|
| `File_IDX` | AWS Backend DB `File.File_IDX` 식별자 |
| `file_idx` | Python·JSON에서 사용하는 `File_IDX` 표현 |
| `user_idx` | AWS Backend 사용자 식별자 |
| `reference_file_idxs` | Backend가 권한 검증 후 현재 요청에 고정한 선택 문서 목록 |
| `rag_document_idx` | Local RAG DB 문서 버전 식별자 |
| `rag_index_run_idx` | 단일 색인 실행 이력 식별자 |
| `chunk_id` | 결정적 청크 식별자이자 Qdrant Point ID |
| `chunk_index` | 문서 안 청크 순서 |
| `source_id` | 한 답변 안에서 사용하는 `SOURCE-N` 공개 식별자 |

`reference_file_idxs`는 사용자 전체 문서의 별칭이 아닙니다. 비어 있으면 검색 범위를
확대하지 않고 요청을 거부합니다.

## 3. 문서 처리

| 용어 | 정의 |
|---|---|
| manifest | Backend가 제공하는 최신 파일 메타데이터와 Presigned GET URL |
| 인제스트 | 다운로드, 검증, 파싱, OCR, 청킹, 임베딩, 저장과 완료 콜백 전체 흐름 |
| 재인제스트 | 같은 `File_IDX`를 다시 처리하는 요청 |
| 재색인 | Parser Version, Embedding Model 또는 Index Version 변경으로 신규 색인을 만드는 처리 |
| 구조 보존 파싱 | 페이지·섹션·문단·도형·시트·셀·줄의 원본 위치를 유지하는 파싱 |
| 청크 | 검색과 LLM 근거에 사용하는 제한된 텍스트 단위 |
| OCR 청크 | `content_origin="ocr"`인 이미지 인식 텍스트 청크 |
| staging point | 검색 노출 전 `is_active=false`로 저장한 신규 Qdrant point |
| 활성 point | `is_active=true`이며 정상 검색 후보인 Qdrant point |
| soft delete | 물리 삭제 대신 이전 문서를 비활성·삭제 상태로 표시하는 처리 |
| 보상 처리 | 부분 실패 후 이전 정상 상태를 복구하고 신규 임시 상태를 제거하는 작업 |

## 4. 검색과 답변

| 용어 | 정의 |
|---|---|
| lookup | 명시적 다문서 종합 의도가 없을 때 선택 문서 전체를 한 번 검색하는 전략 |
| synthesis | 파일별 독립 검색·부분 답변 후 유효 결과를 최종 종합하는 전략 |
| 부분 실패 | 한 문서 실패를 다른 유효 문서 처리와 분리하는 정책 |
| 근거 부족 | 검색·부분 답변 근거가 없어 Claude 호출을 생략하는 `insufficient_evidence` 상태 |
| `top_k` | lookup 또는 문서별 검색의 최대 반환 청크 수 |
| `score_threshold` | Qdrant Cosine 관련도 최소 점수 |
| `answered` | 유효 근거와 검증된 인용을 포함한 답변 상태 |
| `insufficient_evidence` | 문서 근거가 부족해 출처·모델·사용량이 비어 있는 성공 상태 |

## 5. 인용과 위치

| 용어 | 정의 |
|---|---|
| `[SOURCE-N]` | 답변 본문의 공개 인용 표기 |
| `cited_source_ids` | 본문 최초 등장 순서의 중복 제거된 SOURCE ID 목록 |
| `sources` | 실제로 인용한 출처만 같은 순서로 반환하는 배열 |
| Source Locator | 형식별 원본 위치와 OCR 이미지 위치를 표현하는 공통 객체 |
| `source_locator` | API JSON 필드명 |
| `content_origin` | 일반 파서 텍스트는 `text`, OCR 텍스트는 `ocr` |
| `unit_type` | 문단, 표, 도형, 셀 범위, 줄, OCR 이미지 등 세부 단위 |
| `structure_path` | 사람이 확인 가능한 결정적 원본 구조 경로 |
| legacy 위치 필드 | `page`, `slide_no`, `sheet_name`, `section_title` 하위 호환 필드 |

인용 순서 불변식:

```text
답변 본문의 SOURCE-N 최초 등장 순서
=
cited_source_ids
=
sources[].source_id 순서
```

## 6. 인증과 보안

| 용어 | 정의 |
|---|---|
| `RAG_INGEST_TOKEN` | AWS Backend → Local RAG 내부 요청 인증 비밀값 |
| `INTERNAL_TOKEN` | Local RAG → AWS Backend `/internal/**` 요청 인증 비밀값 |
| Presigned GET URL | Backend가 제한된 시간 동안 발급하는 원본 파일 다운로드 URL |
| Request ID | 서비스 간 요청 추적용 식별자 |
| 민감 원문 | 질문, 청크, OCR, 전체 프롬프트와 Claude 전체 응답 |
| 안전한 진단 | 식별자, 오류 코드·클래스, 처리 단계와 개수만 포함한 로그 |

## 7. 형식과 위치 기준

| 형식 | 표준 `kind` | 대표 위치 |
|---|---|---|
| PDF | `pdf_page` | `page` |
| DOCX | `docx_block` | `section_index`, `block_index`, `paragraph_index`, `table_index` |
| PPTX | `pptx_shape` | `slide_no`, `shape_path`, EMU 좌표 |
| XLSX | `xlsx_cell_range` | `sheet_number`, `sheet_name`, `cell_range` |
| TXT | `txt_line` | `line_start`, `line_end`, `char_start`, `char_end` |

`page`, `slide_no`, `sheet_number`, `image_ordinal`은 사용자 표시 기준 1-based입니다.
TXT `char_end`는 exclusive입니다.

## 8. 표기 규칙

- 문서 형식명은 `PDF`, `DOCX`, `PPTX`, `XLSX`, `TXT`로 씁니다.
- JSON 필드는 코드와 동일한 snake_case를 사용합니다.
- DB 식별자는 원본 스키마를 설명할 때 `File_IDX`, JSON에서는 `file_idx`를 사용합니다.
- 제품명은 `Qdrant`, `EasyOCR`, `FastAPI`, `PowerShell`로 씁니다.
- `CUDA 12.9`, `Python 3.12`, `Windows PowerShell 5.1`처럼 버전을 명시합니다.
- 출처 객체 일반 명칭은 Source Locator, 필드명은 `source_locator`로 구분합니다.
