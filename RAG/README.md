# Jipsa Local RAG Service

`jipsa-rag`는 AWS Backend와 분리된 로컬 문서 검색·답변 서비스입니다.
AWS Backend가 전달한 파일 manifest와 Presigned GET URL을 사용하여 문서를
다운로드하고, 형식별 파싱, 구조 보존 청킹, CUDA 임베딩, Local RAG DB 저장,
Qdrant 색인, 혼합 문서 검색 및 Claude 기반 답변 생성을 수행합니다.

현재 답변 경로는 **PDF, DOCX, PPTX, XLSX, TXT와 각 문서에서 생성된 OCR
청크를 함께 지원**합니다. 일반 텍스트와 OCR 텍스트는 동일한 검색 후보로
취급하며, 최종 응답에는 답변 본문에서 실제로 인용한 출처만 반환합니다.

> Local RAG는 사용자 인증·인가와 파일 접근 권한을 최종 판정하지 않습니다.
> 해당 책임은 AWS Backend에 있으며 Local RAG는 전달받은 `user_idx`와
> `reference_file_idxs`를 현재 요청의 검색 범위로 사용합니다.

---

## 목차

1. [서비스 경계](#1-서비스-경계)
2. [지원 문서와 처리 범위](#2-지원-문서와-처리-범위)
3. [전체 아키텍처](#3-전체-아키텍처)
4. [문서 인제스트 흐름](#4-문서-인제스트-흐름)
5. [RAG 답변 흐름](#5-rag-답변-흐름)
6. [검색 범위와 보안 필터](#6-검색-범위와-보안-필터)
7. [lookup과 synthesis](#7-lookup과-synthesis)
8. [부분 실패와 근거 부족](#8-부분-실패와-근거-부족)
9. [인용과 sources 계약](#9-인용과-sources-계약)
10. [Source Locator](#10-source-locator)
11. [RAG Answer API](#11-rag-answer-api)
12. [로컬 실행 환경](#12-로컬-실행-환경)
13. [설치와 환경 변수](#13-설치와-환경-변수)
14. [실행과 종료](#14-실행과-종료)
15. [품질 게이트](#15-품질-게이트)
16. [실제 E2E 검증](#16-실제-e2e-검증)
17. [보안과 로그](#17-보안과-로그)
18. [주요 파일](#18-주요-파일)
19. [운영 체크리스트](#19-운영-체크리스트)

---

## 1. 서비스 경계

### 1.1 Local RAG가 담당하는 작업

- AWS Backend 내부 인증 토큰 검증
- 파일 manifest 조회
- Presigned GET URL 기반 원본 파일 다운로드
- 파일 크기, 확장자, MIME Type, Magic Byte 및 SHA-256 검증
- PDF, DOCX, PPTX, XLSX, TXT 형식별 파싱
- PDF 페이지, DOCX 블록, PPTX 도형, XLSX 셀 범위, TXT 줄 위치 보존
- 문서 내부 이미지 추출과 OCR 후보 판정
- CUDA 12.9 환경의 EasyOCR 실행
- 일반 텍스트와 OCR 텍스트의 구조 보존 청킹
- CUDA TEI 기반 임베딩 생성
- Local RAG DB 문서·청크·색인 실행 이력 저장
- Qdrant 벡터와 검색 payload 저장
- 활성 색인 전환과 이전 색인 비활성화
- 사용자·선택 문서·활성 색인 범위의 검색
- `lookup`과 `synthesis` 질의 라우팅
- 문서별 부분 답변과 최종 종합 답변 생성
- 답변 인용 검증과 실제 인용 출처 반환
- Request ID 기반 구조화 로그

### 1.2 AWS Backend가 담당하는 작업

- 사용자 로그인과 세션 또는 토큰 검증
- 사용자 인증과 인가
- 파일 업로드와 소유권 관리
- S3 접근과 IAM Role 관리
- Presigned GET URL 발급
- 파일 manifest 제공
- 사용자가 선택한 `File.File_IDX` 목록 확정
- Local RAG 인제스트 및 답변 API 호출
- 사용자에게 최종 처리 상태와 답변 전달

### 1.3 Local RAG가 수행하지 않는 작업

- AWS Access Key 또는 Secret Access Key 보관
- `boto3` 또는 AWS SDK를 사용한 S3 직접 접근
- AWS Backend 데이터베이스 직접 수정
- 사용자 파일 권한의 최종 판정
- 사용자가 선택하지 않은 전체 문서 자동 검색
- Docker Desktop 프로그램 자체 실행
- Local RAG MySQL 또는 MariaDB 서버 자체 실행
- 공유기 포트 포워딩, DDNS 또는 TLS 인증서 설정

---

## 2. 지원 문서와 처리 범위

| 형식 | 일반 텍스트 | 표·구조 위치 | 이미지 추출 | OCR | 최종 출처 위치 |
|---|---:|---:|---:|---:|---|
| PDF | 지원 | 페이지 | 지원 | 지원 | 페이지와 이미지 순번 |
| DOCX | 지원 | 섹션·제목·문단·표 | 지원 | 지원 | 섹션·블록·문단·표 위치 |
| PPTX | 지원 | 슬라이드·도형·표·노트 | 지원 | 지원 | 슬라이드·도형 위치와 좌표 |
| XLSX | 지원 | 시트·행·셀·표·병합 범위 | 지원 | 지원 | 시트와 셀 범위 |
| TXT | 지원 | 줄·문자 범위 | 해당 없음 | 해당 없음 | 줄 번호와 문자 범위 |

### 2.1 공통 원칙

- 지원 형식은 검색과 답변 단계에서 동일한 우선순위를 사용합니다.
- 파일 확장자만 신뢰하지 않고 형식별 내부 구조와 Magic Byte를 검증합니다.
- 일반 텍스트와 OCR 청크는 Qdrant에서 하나의 검색 후보 집합으로 처리합니다.
- OCR 청크도 원본 문서의 페이지, 문단, 슬라이드 또는 시트 위치를 유지합니다.
- 동일 이미지 바이트는 SHA-256 Hash를 사용하여 중복 OCR을 방지합니다.
- 작은 아이콘, 로고와 장식 이미지는 OCR 후보에서 제외할 수 있습니다.

### 2.2 PDF 하위 호환성

기존 PDF 페이지 출처와 `page` 필드는 유지됩니다. 신규 공통 위치 모델인
`source_locator`가 추가되지만 기존 소비자는 top-level `page`를 계속 사용할 수
있습니다.

---

## 3. 전체 아키텍처

```text
┌──────────────────────── AWS ────────────────────────┐
│ 사용자 인증·인가                                    │
│ 파일 업로드 및 S3 저장                              │
│ IAM Role 기반 Presigned GET URL 발급                │
│ reference_file_idxs 확정                            │
└───────────────────────┬─────────────────────────────┘
                        │ 내부 API
                        ▼
┌──────────────────── Local RAG PC ───────────────────┐
│ FastAPI                                              │
│  ├─ 문서 다운로드·검증                              │
│  ├─ PDF/DOCX/PPTX/XLSX/TXT 파서                     │
│  ├─ 이미지 추출·Office 렌더링·EasyOCR               │
│  ├─ 구조 보존 청킹                                  │
│  ├─ TEI CUDA 임베딩                                 │
│  ├─ Local RAG MySQL/MariaDB                         │
│  ├─ Qdrant VectorDB                                 │
│  └─ Claude lookup/synthesis                         │
└─────────────────────────────────────────────────────┘
```

### 3.1 기본 로컬 구성

| 구성 요소 | 기본 주소 | 역할 |
|---|---|---|
| FastAPI | `0.0.0.0:8077` | Local RAG API |
| Qdrant REST | `127.0.0.1:6333` | 벡터 검색 |
| Qdrant gRPC | `127.0.0.1:6334` | 선택적 gRPC 연결 |
| TEI | `127.0.0.1:18081` | CUDA 임베딩 |
| Local RAG DB | `127.0.0.1:3306` | 문서·청크·색인 메타데이터 |

Qdrant와 TEI는 기본적으로 루프백 인터페이스에만 바인딩하여 외부에 직접
노출하지 않습니다.

---

## 4. 문서 인제스트 흐름

```text
사용자 파일 업로드
        ↓
AWS Backend가 S3에 원본 저장
        ↓
IAM Role로 Presigned GET URL 발급
        ↓
POST /ingest
        ↓
내부 인증 및 manifest 조회
        ↓
Streaming 다운로드와 파일 검증
        ↓
형식별 파싱
        ↓
이미지 추출·스캔 위치 탐지·OCR
        ↓
일반 텍스트와 OCR 단위 병합
        ↓
형식별 구조 보존 청킹
        ↓
CUDA TEI 임베딩
        ↓
Local RAG DB 저장
        ↓
Qdrant 비활성 staging point 저장
        ↓
신규 색인 활성화·이전 색인 비활성화
        ↓
AWS Backend에 ingest-complete 콜백
```

### 4.1 형식별 위치 보존

- PDF는 페이지 번호를 유지합니다.
- DOCX는 섹션, 본문 블록, 문단, 제목 계층과 표 순번을 유지합니다.
- PPTX는 슬라이드, 도형 ID, 도형 경로와 EMU 좌표를 유지합니다.
- XLSX는 시트, 행, 시작·종료 셀과 셀 범위를 유지합니다.
- TXT는 줄 번호와 LF 정규화 문자열 기준 문자 범위를 유지합니다.
- OCR은 이미지가 생성된 원본 위치 메타데이터를 상속합니다.

### 4.2 활성 색인 전환

새 색인은 Qdrant에 비활성 상태로 먼저 저장됩니다. Local RAG DB 저장과
Qdrant 저장이 모두 완료된 뒤 신규 point를 활성화하고 이전 정상 point를
비활성화합니다. 실패 시 신규 point 삭제와 이전 상태 복구를 시도합니다.

---

## 5. RAG 답변 흐름

```text
POST /api/v1/rag/answers
        ↓
내부 인증과 요청 스키마 검증
        ↓
reference_file_idxs 요청 스냅샷 고정
        ↓
규칙 기반 lookup / synthesis 분류
        ├─ lookup
        │    ↓
        │  선택 문서 전체 범위 검색
        │    ↓
        │  단일 근거 프롬프트와 Claude 호출
        │    ↓
        │  구조화 출력과 SOURCE-N 검증
        │
        └─ synthesis
             ↓
           선택 파일별 독립 검색
             ↓
           문서별 청크·전체 컨텍스트 제한
             ↓
           문서별 부분 답변과 인용 검증
             ↓
           유효 부분 답변만 최종 입력으로 구성
             ↓
           최종 Claude 종합과 인용 검증
        ↓
실제 인용한 출처만 sources에 반환
```

검색 근거가 없으면 Claude를 호출하지 않습니다. synthesis에서 유효한 부분
답변이 하나도 없으면 최종 Claude 호출도 생략합니다.

---

## 6. 검색 범위와 보안 필터

Qdrant 검색은 다음 조건을 모두 `must` 필터로 사용합니다.

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

### 6.1 요청 범위 고정

`reference_file_idxs`는 질문 전송 시점의 불변 스냅샷입니다. 질문 처리 중 UI에서
선택 문서가 변경되어도 이미 시작된 요청 범위는 바뀌지 않습니다.

### 6.2 다중 방어 계층

1. Qdrant `must` 필터에서 사용자, 활성 상태와 파일 목록을 제한합니다.
2. 검색 결과 payload를 스키마로 변환하며 필수 필드를 검증합니다.
3. 검색 서비스가 결과의 사용자와 선택 파일 범위를 다시 검증합니다.
4. 답변 서비스가 프롬프트 출처와 최종 출처 범위를 다시 검증합니다.

선택하지 않은 문서, 다른 사용자의 문서 또는 비활성 색인의 출처는 정상
답변에 포함될 수 없습니다.

---

## 7. lookup과 synthesis

### 7.1 lookup

다음 조건에서는 기존 단일 검색·단일 생성 흐름을 사용합니다.

- 참조문서가 한 개인 질문
- 여러 문서를 선택했지만 명시적인 비교·종합 의도가 없는 사실 조회
- 특정 값, 규칙, 위치 또는 정의를 찾는 질문

lookup은 PDF뿐 아니라 DOCX, PPTX, XLSX, TXT 및 OCR 근거를 동일하게 사용합니다.

### 7.2 synthesis

두 개 이상의 문서를 선택하고 비교, 대조, 공통점, 차이점, 종합, 통합 또는
문서별 요약 의도가 명시된 경우 synthesis를 사용합니다.

synthesis는 다음 제약을 적용합니다.

- 파일별 독립 검색
- 파일별 최대 청크 수
- 청크별 최대 문자 수
- 전체 컨텍스트 최대 문자 수
- 문서 간 라운드 로빈 컨텍스트 배분
- 문서별 부분 답변의 개별 인용 검증
- 최종 단계의 전역 `SOURCE-N` 재매핑

### 7.3 하위 호환성

기존 PDF 전용 호출자와 회귀 테스트를 위해 다음 호환 별칭을 유지합니다.

- `PdfChunkGroup`
- `group_chunks_by_pdf`
- `max_chunks_per_pdf`
- `pdf_groups`

신규 코드와 문서에서는 `DocumentChunkGroup`, `group_chunks_by_document`,
`max_chunks_per_document`, `document_groups`를 사용합니다.

---

## 8. 부분 실패와 근거 부족

### 8.1 계속 처리하는 문서별 실패

| 실패 | 처리 |
|---|---|
| 파싱·색인 실패로 활성 point 없음 | 해당 문서는 검색 결과 없음으로 제외 |
| 문서별 임베딩 실패 | 해당 문서를 제외하고 나머지 문서 계속 |
| 문서별 Qdrant 검색 실패 | 해당 문서를 제외하고 나머지 문서 계속 |
| 문서별 부분 Claude 실패 | 해당 부분만 제외하고 계속 |
| 문서별 근거 부족 | 해당 부분만 제외하고 계속 |

### 8.2 전체 요청을 실패시키는 계약 위반

- 검색 결과의 사용자 범위 위반
- 선택하지 않은 파일의 검색 결과 유입
- 비활성 색인 결과 유입
- 프롬프트에 없는 `SOURCE-N` 인용
- 본문 인용 순서와 `cited_source_ids` 불일치
- 최종 `sources` 순서 또는 범위 불일치

범위 위반과 인용 계약 위반은 부분 실패로 숨기지 않습니다.

### 8.3 `insufficient_evidence`

다음 경우 고정 근거 부족 응답을 반환합니다.

- 검색 가능한 청크가 없음
- 모든 문서별 검색이 실패하거나 비어 있음
- 모든 문서별 부분 답변이 근거 부족 또는 생성 실패
- Claude가 제공된 근거만으로 답할 수 없다고 구조화 응답으로 판정

```text
제공된 문서 근거만으로는 답변할 수 없습니다.
```

근거 부족 응답에는 출처와 생성 메타데이터가 포함되지 않습니다.

---

## 9. 인용과 sources 계약

Claude 답변은 `[SOURCE-N]` 형식을 사용합니다.

```text
answer의 SOURCE-N 최초 등장 순서
=
cited_source_ids
=
sources[].source_id 순서
```

정상 `answered` 응답은 다음을 모두 만족해야 합니다.

1. 본문에 하나 이상의 유효한 `[SOURCE-N]` 인용이 있습니다.
2. 같은 출처의 반복 인용은 최초 등장만 순서 계산에 사용합니다.
3. 모든 인용 ID가 현재 프롬프트 후보 출처에 존재합니다.
4. `cited_source_ids`는 본문 최초 등장 순서와 정확히 같습니다.
5. `sources`에는 본문이 실제로 인용한 출처만 포함합니다.
6. `sources` 순서도 본문 최초 등장 순서와 같습니다.
7. `source_id`와 `chunk_id`는 응답 안에서 각각 중복되지 않습니다.
8. 모든 출처의 `file_idx`가 요청의 `reference_file_idxs`에 포함됩니다.

인용 계약 위반은 자동 수정하지 않고 `INVALID_GENERATION_RESPONSE` 오류로
처리합니다.

---

## 10. Source Locator

`source_locator`는 문서 형식에 관계없이 원본 위치를 표현하는 공통 모델입니다.
기존 `page`, `slide_no`, `sheet_name`, `section_title` 필드는 하위 호환을 위해
유지합니다.

### 10.1 공통 필드

| 필드 | 설명 |
|---|---|
| `file_type` | `pdf`, `docx`, `pptx`, `xlsx`, `txt` |
| `kind` | 형식별 위치 종류 |
| `content_origin` | `text` 또는 `ocr` |
| `unit_type` | 문단, 표, 도형, 줄, OCR 이미지 등의 세부 단위 |
| `structure_path` | 사람이 읽고 추적할 수 있는 구조 경로 |

### 10.2 PDF

- `kind: "pdf_page"`
- `page`
- OCR이면 `image_ordinal`, `image_index`, `image_id`, `image_kind`

### 10.3 DOCX

- `kind: "docx_block"`
- `section_index`
- `block_index`
- `paragraph_index`
- `table_index`
- `heading_level`
- `section_title`
- `row_count`, `column_count`

### 10.4 PPTX

- `kind: "pptx_slide"` 또는 `"pptx_shape"`
- `slide_no`
- `shape_index`, `shape_id`, `shape_path`
- `shape_name`, `shape_type_name`
- `coordinate_space`
- `shape_left_emu`, `shape_top_emu`
- `shape_width_emu`, `shape_height_emu`

EMU는 Office Open XML의 원본 정수 좌표 단위이며 렌더링 DPI에 의존하지 않습니다.

### 10.5 XLSX

- `kind: "xlsx_cell_range"`
- `sheet_number`, `sheet_name`
- `row_number`
- `start_row`, `end_row`
- `start_column`, `end_column`
- `start_cell`, `end_cell`, `cell_range`
- `cell_coordinates`
- `merged_cell_ranges`

### 10.6 TXT

- `kind: "txt_line"`
- `line_start`, `line_end`
- `char_start`, `char_end`

문자 범위의 end 값은 Python 슬라이스와 동일한 exclusive 위치입니다.

### 10.7 OCR

OCR 출처는 원본 문서 위치를 유지하면서 다음 정보를 추가합니다.

- `content_origin: "ocr"`
- `unit_type: "ocr_image"`
- `image_ordinal`: 신규 표준 1-based 문서 이미지 순번
- `image_index`: 기존 소비자 호환용 이미지 순번
- `image_id`
- `image_kind`
- `ocr_engine`
- `ocr_mean_confidence`

---

## 11. RAG Answer API

### 11.1 엔드포인트

```http
POST /api/v1/rag/answers
Content-Type: application/json
X-Internal-Token: <shared-secret>
```

### 11.2 요청 예시

```json
{
  "user_idx": 45,
  "reference_file_idxs": [101, 102, 103],
  "query": "선택한 문서를 종합해 정책과 성과를 비교해줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

`reference_file_idxs`는 필수이며 빈 배열, `null` 또는 필드 생략을 전체 문서
검색으로 변환하지 않습니다.

### 11.3 정상 응답 예시

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "정책 문서는 기준을 정의하고 발표 자료는 성과를 설명합니다. [SOURCE-1][SOURCE-2]",
    "status": "answered",
    "cited_source_ids": ["SOURCE-1", "SOURCE-2"],
    "sources": [
      {
        "source_id": "SOURCE-1",
        "chunk_id": "11111111-1111-1111-1111-111111111111",
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
        "excerpt": "정책의 적용 기준은 다음과 같습니다."
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

### 11.4 근거 부족 응답 예시

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

### 11.5 주요 오류

| HTTP | 코드 | 의미 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 인증 실패 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED` | 참조문서 미선택 |
| 422 | `REQUEST_VALIDATION_FAILED` | 요청 스키마 검증 실패 |
| 429 | `GENERATION_BUDGET_EXCEEDED` | 답변 단위 생성 예산 초과 |
| 502 | `INVALID_VECTOR_SEARCH_RESULT` | 검색 결과 범위·payload 계약 위반 |
| 502 | `INVALID_GENERATION_RESPONSE` | 구조화 출력 또는 인용 계약 위반 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시적 사용 불가 |
| 503 | `VECTOR_DATABASE_UNAVAILABLE` | Qdrant 일시적 사용 불가 |
| 503 | `GENERATION_SERVICE_UNAVAILABLE` | Claude 일시적 사용 불가 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | TEI 시간 초과 |
| 504 | `GENERATION_SERVICE_TIMEOUT` | Claude 시간 초과 |

세부 계약은 다음 문서를 기준으로 합니다.

- `docs/api/rag-answer-api-contract.md`
- `docs/api/rag-answer-contract.md`

---

## 12. 로컬 실행 환경

### 12.1 필수 소프트웨어

- Windows 10 또는 Windows 11
- Windows PowerShell 5.1 이상 또는 PowerShell 7 이상
- Python 3.12
- `uv`
- Docker Desktop과 Docker Compose v2
- NVIDIA GPU Driver
- Docker NVIDIA GPU 지원
- CUDA 12.9 호환 환경
- MySQL 8.0 이상 또는 MariaDB 10.6 이상
- 이미지 OCR을 위한 EasyOCR 모델
- PPTX/XLSX 시각 요소 렌더링을 위한 Microsoft PowerPoint와 Excel

### 12.2 주요 모델과 저장소

```text
Embedding Model: Qwen/Qwen3-Embedding-0.6B
Embedding Dimension: 1024
OCR: EasyOCR ko/en
PyTorch: CUDA 12.9 전용 wheel
VectorDB: Qdrant
Generation: Anthropic Claude
```

### 12.3 Office 렌더링

PPTX 차트·SmartArt와 XLSX 차트는 Windows의 PowerPoint·Excel COM을 자식
프로세스로 격리하여 렌더링합니다. Office가 설치되지 않은 환경에서는 해당
시각 요소 렌더링 기능을 사용할 수 없으며 일반 텍스트 파싱과 다른 형식의
처리는 독립적으로 동작합니다.

---

## 13. 설치와 환경 변수

### 13.1 의존성 동기화

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'
uv sync --frozen
```

Python은 `3.12.x`를 사용해야 하며 `pyproject.toml`과 `uv.lock`이 일치해야
합니다.

### 13.2 dotenv 파일

```text
.env.local
.env.development
.env.test
```

Git에는 `.env.example`만 저장합니다. 실제 DB 비밀번호, 내부 인증 토큰과
Anthropic API Key는 환경별 파일에만 저장합니다.

### 13.3 주요 환경 변수 범주

- Application: 이름, 버전, host, port, debug
- Internal Authentication: `INTERNAL_TOKEN`, `RAG_INGEST_TOKEN`
- Local RAG DB: host, port, database, user, password
- File Download: 허용 host suffix, timeout, 최대 크기
- Parsing: 청크 크기, overlap, OOXML 압축 제한
- Image Extraction: 개수, 바이트, 픽셀, 중복 제거
- OCR: 언어, GPU, confidence, timeout, 모델 경로
- Embedding: TEI 주소, 모델, 차원, batch size
- Qdrant: URL, collection, timeout
- Generation: Anthropic 모델, timeout, 출력 토큰, 답변 단위 예산

정확한 키와 기본값은 `.env.example`을 기준으로 합니다.

### 13.4 비밀값 금지 사항

다음 값은 소스, README 예제, 테스트 Fixture 또는 로그에 실제 값으로 기록하지
않습니다.

- DB 비밀번호
- 내부 인증 토큰
- Anthropic API Key
- Presigned URL Query String
- AWS 자격 증명

---

## 14. 실행과 종료

### 14.1 통합 실행

```powershell
$env:JIPSA_RAG_APP_ENV = 'development'
.\scripts\start-local-rag.ps1
```

통합 실행 스크립트는 환경 검증, Qdrant 준비, CUDA TEI 준비, 실제 임베딩
검증과 FastAPI 실행을 순서대로 수행합니다.

### 14.2 주요 주소

| 기능 | 로컬 주소 |
|---|---|
| Liveness | `http://127.0.0.1:8077/api/v1/health/live` |
| Readiness | `http://127.0.0.1:8077/api/v1/health/ready` |
| Swagger UI | `http://127.0.0.1:8077/docs` |
| OpenAPI JSON | `http://127.0.0.1:8077/openapi.json` |
| Ingest | `http://127.0.0.1:8077/ingest` |
| RAG Answer | `http://127.0.0.1:8077/api/v1/rag/answers` |

### 14.3 정상 종료

통합 실행 창에서 `Ctrl+C`를 입력하면 FastAPI 연결 자원과 Docker 인프라를
정리합니다. 비정상 종료 후에는 다음 스크립트로 Qdrant와 TEI를 정지합니다.

```powershell
.\scripts\stop-local-rag.ps1
```

일반 종료 과정에서는 Qdrant volume과 Hugging Face 모델 cache를 삭제하지
않습니다.

---

## 15. 품질 게이트

Issue #119 완료 기준은 동일한 commit에서 다음 세 검사가 모두 성공하는 것입니다.

```text
Ruff format + lint
Mypy strict
전체 Pytest
```

저장소의 표준 검증 진입점은 다음 스크립트입니다.

```powershell
.\scripts\verify-rag-quality.ps1
```

스크립트는 다음 순서로 실행됩니다.

1. 프로젝트 구조와 `uv` 확인
2. `uv sync --frozen`
3. `uv run ruff format --check .`
4. `uv run ruff check .`
5. `uv run mypy src tests`
6. `uv run pytest`

### 15.1 전체 Pytest의 외부 서비스 정책

일반 전체 Pytest에서는 실제 Claude, CUDA TEI, Qdrant, Local RAG DB와 Office
COM이 필요한 opt-in E2E 테스트를 명시적으로 skip할 수 있습니다. skip은 성공으로
숨기지 않고 Pytest 결과에 사유와 함께 표시합니다.

### 15.2 품질 게이트 판정

- 하나라도 종료 코드가 0이 아니면 실패입니다.
- `ruff format --check`가 실패하면 자동 포맷을 별도 수행한 뒤 다시 검증합니다.
- Mypy는 `src`와 `tests` 모두 strict 모드로 검사합니다.
- 전체 Pytest는 선택 테스트가 아니라 `tests` 전체를 수집해야 합니다.
- 검증 완료 뒤 `git status --short`에 의도하지 않은 변경이 없어야 합니다.

### 15.3 문서 회귀 테스트

`tests/regression/test_rag_documentation_contract.py`는 README와 API 계약 문서가
다시 PDF 전용 설명으로 퇴행하지 않는지 검증합니다. 또한 지원 형식, OCR,
Source Locator, 인용 순서와 최종 Claude 호출 생략 계약을 확인합니다.

---

## 16. 실제 E2E 검증

### 16.1 통합 E2E

```powershell
.\scripts\run-real-rag-e2e.ps1
```

실제 E2E는 환경에 따라 다음 항목을 검증합니다.

- 원본 문서 다운로드와 형식 검증
- PDF, DOCX, PPTX, XLSX, TXT 실제 파싱
- 문서 내부 이미지 추출과 OCR
- Local RAG DB 저장
- Qdrant payload와 활성 상태
- CUDA TEI 실제 `/embed`
- 혼합 문서 lookup과 synthesis
- 실제 Claude 인용 정합성
- 선택하지 않은 문서 차단
- 일부 문서 실패와 전체 근거 부족
- 테스트 데이터와 인프라 정리

### 16.2 Office COM 통합 테스트

PowerPoint와 Excel이 설치된 Windows 세션에서만 Office 렌더링 통합 테스트를
opt-in으로 실행합니다.

```powershell
$env:JIPSA_RAG_RUN_OFFICE_COM_INTEGRATION = '1'
uv run pytest -ra tests/integration/test_document_image_extractors.py
```

### 16.3 CUDA OCR 확인

OCR 실제 검증에서는 다음 사항을 확인합니다.

- CUDA 사용 가능
- EasyOCR `gpu=True`
- 한국어·영어 모델 준비
- OCR timeout과 문서 전체 timeout
- 이미지 Hash 중복 제거
- OCR 원문과 모델 경로의 로그 비노출

---

## 17. 보안과 로그

### 17.1 로그에 기록하지 않는 값

- 사용자 질문 원문
- 전체 청크 원문
- 생성 프롬프트
- Claude 응답 원문
- 이미지 바이트 또는 Base64
- OCR 인식 원문
- Presigned URL 전체 값과 Query String
- 내부 인증 토큰과 API Key
- DB 비밀번호
- Office worker stdout·stderr 원문

### 17.2 기록 가능한 진단 정보

- Request ID
- 사용자 식별자
- 선택 문서 개수
- 파일 형식
- 처리 단계와 안전한 operation 코드
- 결과·청크·출처 개수
- 외부 서비스 HTTP 상태 코드
- timeout 종류
- 오류 클래스 이름
- 생성 모델 ID와 토큰 사용량

### 17.3 예외 체인 정리

하위 HTTP 또는 SDK 예외가 요청 본문이나 프롬프트를 참조할 수 있으므로 서비스
경계에서 원인 예외 체인을 제거하고 안전한 오류 코드와 operation만 외부 계층에
전달합니다.

---

## 18. 주요 파일

| 경로 | 책임 |
|---|---|
| `src/jipsa_rag/schemas/source_locator.py` | 형식별 공통 원본 위치 모델 |
| `src/jipsa_rag/schemas/chunk_search.py` | 혼합 문서 검색 요청·결과 스키마 |
| `src/jipsa_rag/schemas/rag_answer.py` | 공개 답변·인용·출처 계약 |
| `src/jipsa_rag/infrastructure/indexing/qdrant_search.py` | 사용자·활성·선택 문서 필터 검색 |
| `src/jipsa_rag/services/chunk_search.py` | Qdrant 결과를 검색 응답으로 변환 |
| `src/jipsa_rag/services/prompt_builder.py` | 근거 프롬프트와 공개 출처 구성 |
| `src/jipsa_rag/services/rag_answer.py` | 단일 답변 생성과 인용 검증 |
| `src/jipsa_rag/services/query_routing.py` | lookup/synthesis와 부분 실패 조정 |
| `src/jipsa_rag/infrastructure/document/` | 형식별 파싱과 이미지 추출 |
| `src/jipsa_rag/infrastructure/ocr/` | OCR 추론, 정규화, 문맥 연결 |
| `docs/api/rag-answer-api-contract.md` | AWS Backend ↔ Local RAG API 계약 |
| `docs/api/rag-answer-contract.md` | Local RAG 내부 답변·출처 계약 |
| `scripts/verify-rag-quality.ps1` | Ruff·Mypy·전체 Pytest 품질 게이트 |

---

## 19. 운영 체크리스트

### 인제스트

- [ ] AWS Backend가 사용자와 파일 권한을 검증했는가
- [ ] Presigned GET URL에 필요한 최소 권한과 유효 시간이 적용됐는가
- [ ] 파일 형식과 MIME Type이 일치하는가
- [ ] 신규 Qdrant point가 활성화된 뒤 이전 point가 비활성화됐는가
- [ ] OCR 실패가 일반 텍스트 색인을 불필요하게 중단하지 않는가

### 검색과 답변

- [ ] `reference_file_idxs`가 비어 있지 않은가
- [ ] `users_idx`, `is_active`, `file_idx` 필터가 모두 적용됐는가
- [ ] 일반 텍스트와 OCR 청크가 함께 검색되는가
- [ ] synthesis가 파일별 독립 검색을 수행하는가
- [ ] 유효한 부분 답변이 없을 때 최종 Claude 호출을 생략하는가
- [ ] `cited_source_ids`와 본문 최초 인용 순서가 일치하는가
- [ ] 최종 `sources`에 실제 인용 출처만 포함되는가
- [ ] Source Locator가 원본 위치와 OCR 이미지 순번을 보존하는가

### 품질과 보안

- [ ] Ruff format과 lint가 통과했는가
- [ ] Mypy strict가 통과했는가
- [ ] 전체 Pytest가 통과했는가
- [ ] 실제 E2E의 skip 또는 실행 사유가 명확한가
- [ ] 질문, 프롬프트, OCR 원문과 비밀값이 로그에 노출되지 않는가
- [ ] 문서와 OpenAPI 설명이 현재 구현과 일치하는가
