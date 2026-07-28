# Jipsa Local RAG Service

`jipsa-rag`는 AWS Backend와 분리된 로컬 문서 검색·답변 서비스입니다.
AWS Backend가 전달한 manifest와 Presigned GET URL을 사용해 문서를 다운로드하고,
구조 보존 파싱, 이미지 OCR, CUDA 임베딩, Local RAG DB 저장, Qdrant 검색과
Claude 기반 답변 생성을 수행합니다.

현재 지원 범위는 **PDF, DOCX, PPTX, XLSX, TXT 및 문서 내부 이미지 OCR**입니다.
일반 텍스트와 OCR 텍스트는 동일한 검색 후보로 취급하며, 답변에는 실제로 인용된
출처만 반환합니다.

> 사용자 인증·인가와 파일 접근 권한의 최종 판정은 AWS Backend가 담당합니다.
> Local RAG는 전달받은 `user_idx`와 `reference_file_idxs`를 현재 요청의 고정
> 검색 범위로 사용합니다.

---

## 1. 서비스 경계

### Local RAG 책임

- 내부 인증 토큰 검증
- Backend 파일 manifest 조회
- Presigned GET URL 기반 스트리밍 다운로드
- 확장자, MIME Type, Magic Byte, OOXML 구조 및 SHA-256 검증
- PDF, DOCX, PPTX, XLSX, TXT 형식별 파싱
- 문서 구조와 원본 위치를 유지하는 청킹
- PDF·Office 문서 이미지 추출과 스캔 위치 탐지
- CUDA 12.9 환경의 EasyOCR 실행
- CUDA TEI 문서·질의 임베딩
- Local RAG DB 문서·청크·색인 실행 이력 저장
- Qdrant staging, 활성 전환, 검색 및 보상 처리
- `lookup`과 `synthesis` 답변
- `[SOURCE-N]`, `cited_source_ids`, `sources` 무결성 검증
- Request ID 기반 구조화 로그와 민감정보 마스킹

### AWS Backend 책임

- 사용자 인증·인가
- 파일 업로드, 소유권과 상태 관리
- S3 접근과 IAM Role 관리
- Presigned GET URL 발급
- manifest 및 ingest-complete 내부 API 제공
- 사용자 선택 `File_IDX` 목록 확정
- Local RAG API 호출과 사용자 응답 전달

### Local RAG가 하지 않는 작업

- AWS Access Key 또는 Secret Access Key 보관
- `boto3`를 사용한 S3 직접 접근
- AWS Backend DB 직접 수정
- 사용자가 선택하지 않은 전체 문서 자동 검색
- Docker Desktop 또는 Local DB 서버 자체 실행

---

## 2. 지원 문서와 위치 계약

| 형식 | 일반 텍스트 | 표·구조 | 이미지 OCR | 대표 출처 위치 |
|---|---:|---:|---:|---|
| PDF | 지원 | 페이지·표 | 지원 | 페이지와 이미지 순번 |
| DOCX | 지원 | 섹션·문단·표 | 지원 | 섹션·블록·문단·표 |
| PPTX | 지원 | 슬라이드·도형·표·노트 | 지원 | 슬라이드·도형 경로 |
| XLSX | 지원 | 시트·행·셀·표·병합 범위 | 지원 | 시트와 셀 범위 |
| TXT | 지원 | 줄·문자 범위 | 해당 없음 | 줄과 문자 범위 |

공통 원칙은 다음과 같습니다.

- 확장자만 신뢰하지 않고 형식별 내부 구조를 검증합니다.
- OCR 청크는 원본 페이지, 문단, 슬라이드 또는 시트 위치를 상속합니다.
- 동일 이미지 바이트는 SHA-256 Hash로 중복 OCR을 방지합니다.
- 작은 아이콘, 로고와 장식 이미지는 OCR 후보에서 제외할 수 있습니다.
- 기존 PDF `page` 필드는 유지하고 신규 `source_locator`와 같은 값을 사용합니다.

---

## 3. 전체 처리 흐름

```text
AWS Backend
  ├─ 사용자 인증·인가
  ├─ S3 파일 저장
  ├─ Presigned GET URL 발급
  └─ reference_file_idxs 확정
            │
            ▼
Local RAG FastAPI
  ├─ manifest 조회와 파일 검증
  ├─ 형식별 파싱
  ├─ 이미지 추출·Office 렌더링·EasyOCR
  ├─ 구조 보존 청킹
  ├─ CUDA TEI 임베딩
  ├─ Local RAG DB
  ├─ Qdrant VectorDB
  └─ Claude lookup/synthesis
```

기본 로컬 구성은 다음과 같습니다.

| 구성요소 | 기본 주소 | 역할 |
|---|---|---|
| FastAPI | `0.0.0.0:8077` | Local RAG API |
| Qdrant REST | `127.0.0.1:6333` | 벡터 검색 |
| Qdrant gRPC | `127.0.0.1:6334` | 선택적 gRPC |
| CUDA TEI | `127.0.0.1:18081` | 임베딩 |
| Local RAG DB | `127.0.0.1:3306` | 문서·청크·실행 이력 |

Qdrant와 TEI는 기본적으로 루프백 인터페이스에만 바인딩합니다.

---

## 4. 인제스트

```text
POST /ingest
  → 내부 인증
  → 최신 manifest 조회
  → 원본 스트리밍 다운로드와 형식 검증
  → 형식별 파싱
  → 이미지 추출과 OCR
  → 구조 보존 청킹
  → CUDA TEI 임베딩
  → Local RAG DB 준비
  → Qdrant 비활성 staging point 저장
  → 신규 point 활성화
  → 이전 정상 point 비활성화
  → Local RAG 성공 확정
  → ingest-complete callback
```

### 재인제스트와 재색인

동일 파일 Hash, Parser Version, Embedding Model과 Index Version이면 기존 정상 문서와
결정적 Chunk ID를 재사용합니다. 파서 버전이나 정체성이 달라지면 새 문서를 만들고,
새 색인이 성공한 뒤에만 이전 문서를 soft delete하고 이전 Qdrant point를
비활성화합니다.

### 실패 보상

- 신규 Qdrant 업서트 실패: 신규 staging point만 삭제합니다.
- 활성 전환 실패: 이전 정상 point를 재활성화하고 신규 point를 제거합니다.
- Local 성공 확정 실패: Qdrant 전환을 되돌리고 실행을 실패 처리합니다.
- 기존 정상 색인 재사용 중 실패: 기존 문서와 point는 유지하고 신규 실행만 실패합니다.
- 동일 `File_IDX` 동시 요청: MySQL advisory lock으로 색인 임계 구역을 직렬화합니다.

---

## 5. 검색 범위와 보안 필터

Qdrant 검색은 다음 세 조건을 하나의 `must` 필터로 결합합니다.

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

검색 저장소는 Qdrant 응답 payload를 다시 검증하며, 서비스 계층과 최종 답변 계층도
사용자·선택 문서 범위를 재검증합니다. 선택하지 않은 문서, 다른 사용자의 point,
비활성 staging point와 이전 색인은 정상 답변 출처가 될 수 없습니다.

`reference_file_idxs`가 비어 있거나 생략되면 전체 문서 검색으로 확장하지 않고 요청을
거부합니다.

---

## 6. lookup과 synthesis

### lookup

단일 문서 또는 명시적인 비교·종합 의도가 없는 사실 조회는 선택 문서 전체 범위를
한 번 검색하고 단일 Claude 생성 흐름을 사용합니다. PDF, DOCX, PPTX, XLSX, TXT와
OCR 청크가 같은 흐름을 사용합니다.

### synthesis

두 개 이상의 문서를 선택하고 비교, 대조, 종합, 통합 또는 문서별 요약 의도가 있으면
파일별 독립 검색과 부분 답변을 사용합니다.

```text
선택 파일별 독립 검색
  → 문서별 컨텍스트 제한
  → 문서별 부분 Claude 답변
  → 문서 로컬 SOURCE-N 검증
  → 전역 SOURCE-N 재매핑
  → 유효한 부분 답변만 최종 종합
  → 최종 인용과 범위 검증
```

한 문서의 임베딩, Qdrant 검색 또는 부분 Claude 호출이 실패해도 유효한 다른 문서가
있으면 계속 처리합니다. 사용자·선택 문서 범위 위반과 인용 무결성 위반은 부분
실패로 숨기지 않고 전체 요청을 실패시킵니다.

---

## 7. 근거 부족

다음 경우 Claude를 호출하지 않거나 최종 Claude 호출을 생략합니다.

- 검색 가능한 청크가 없음
- 모든 문서별 검색이 실패하거나 비어 있음
- 유효한 문서별 부분 답변이 없음

응답은 다음 계약을 사용합니다.

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

---

## 8. 인용과 Source Locator

정상 답변은 아래 순서를 정확히 일치시킵니다.

```text
answer의 SOURCE-N 최초 등장 순서
=
cited_source_ids
=
sources[].source_id 순서
```

최종 `sources`에는 본문에서 실제로 인용한 후보만 남습니다. 같은 `source_id` 또는
`chunk_id`를 중복 반환할 수 없습니다.

`source_locator` 공통 필드는 다음과 같습니다.

| 필드 | 의미 |
|---|---|
| `file_type` | `pdf`, `docx`, `pptx`, `xlsx`, `txt` |
| `kind` | 형식별 대표 위치 종류 |
| `content_origin` | `text` 또는 `ocr` |
| `unit_type` | 문단, 표, 도형, 줄, OCR 이미지 등의 세부 단위 |
| `structure_path` | 사람이 확인 가능한 결정적 구조 경로 |

형식별 주요 위치:

- PDF: `page`
- DOCX: `section_index`, `block_index`, `paragraph_index`, `table_index`
- PPTX: `slide_no`, `shape_index`, `shape_path`, EMU 좌표
- XLSX: `sheet_number`, `sheet_name`, `start_cell`, `end_cell`, `cell_range`
- TXT: `line_start`, `line_end`, `char_start`, `char_end`
- OCR: `image_ordinal`, `image_index`, `image_id`, `image_kind`, `ocr_engine`

상세 JSON 계약은 `docs/api/rag-answer-contract.md`를 참조합니다.

---

## 9. 설치와 환경

필수 환경:

- Windows PowerShell 5.1 이상
- Python 3.12
- `uv`
- Docker Desktop과 Docker Compose
- NVIDIA Driver와 Docker GPU 지원
- CUDA 12.9용 PyTorch
- Local MySQL 또는 MariaDB
- Anthropic API Key

의존성은 `uv.lock`을 기준으로 동기화합니다.

```powershell
uv sync --frozen
```

실제 로컬 구성은 `.env.local`, 일반 테스트 구성은 `.env.test`를 사용합니다.
비밀값은 저장소에 커밋하지 않습니다.

---

## 10. 실행과 종료

일반 Local RAG 실행:

```powershell
.\scripts\start-local-rag.ps1
```

인프라 종료:

```powershell
.\scripts\stop-local-rag.ps1
```

스크립트는 Docker Desktop 프로그램과 Local DB 서버 자체를 시작하지 않습니다.

---

## 11. 품질 게이트

```powershell
.\scripts\verify-rag-quality.ps1
```

검사 순서:

1. `uv sync --frozen`
2. `ruff format --check .`
3. `ruff check .`
4. `mypy src tests`
5. 전체 `pytest`

일반 전체 Pytest에서는 `JIPSA_RAG_RUN_E2E`를 제거하여 실제 GPU, Local DB,
Qdrant와 Claude 호출이 필요한 E2E를 명시적으로 skip합니다.

---

## 12. Issue #123 전체 E2E

```powershell
.\scripts\run-issue-123-e2e.ps1
```

이 스크립트는 품질 게이트를 먼저 실행한 뒤 Qdrant와 CUDA TEI를 준비하고 다음 테스트를
실행합니다.

```text
tests/e2e/test_fixed_document_full_pipeline_e2e.py
```

검증 범위:

- 5개 형식 고정 Fixture 다운로드와 형식 검증
- 문단, 표, 슬라이드, 시트, 셀과 줄 위치
- 이미지 포함 문서, 스캔 PDF와 이미지 전용 페이지
- 실제 CUDA EasyOCR와 원본 이미지 위치
- OCR 일부 실패의 부분 성공
- CUDA TEI 임베딩과 벡터 차원
- Local RAG DB와 Qdrant 저장 상태
- 형식별 lookup, 다중 형식 synthesis와 텍스트·OCR 혼합 답변
- `[SOURCE-N]`, `cited_source_ids`, `sources` 일치
- 선택하지 않은 문서와 다른 사용자 출처 차단
- 전체 근거 부족 시 Claude 미호출
- 일부 문서 실패 시 나머지 답변 유지
- 재인제스트, 재색인, soft delete와 보상 처리
- 중복·동시 인제스트 수렴
- 임시 파일과 추출 이미지 정리
- 질문, 청크, OCR 원문, 프롬프트와 인증정보 로그 비노출

실행 안전장치:

- `JIPSA_RAG_APP_ENV=test`에서만 E2E 정리를 허용합니다.
- `JIPSA_RAG_RUN_E2E=1`이 없으면 테스트 모듈을 skip합니다.
- 테스트 전용 사용자와 `File_IDX`만 정리합니다.
- 스크립트 실행 전 이미 동작하던 컨테이너는 종료하지 않습니다.
- 실제 Claude API 호출 비용이 발생합니다.

같은 Commit에서 품질 게이트가 이미 통과했다면 다음 옵션으로 중복 실행을 생략할 수
있습니다.

```powershell
.\scripts\run-issue-123-e2e.ps1 -SkipQualityGate
```

실패 분석을 위해 스크립트가 시작한 인프라를 유지하려면 다음 옵션을 사용합니다.

```powershell
.\scripts\run-issue-123-e2e.ps1 -KeepInfrastructureRunning
```

---

## 13. 로그와 비밀정보

구조화 로그에는 안전한 식별값과 오류 종류만 기록합니다. 다음 값은 로그 필드 또는
문자열에서 제거하거나 기록 자체를 금지합니다.

- 질문 원문
- 청크와 OCR 원문
- Claude 전체 프롬프트와 응답 원문
- 내부 인증 토큰과 Bearer 토큰
- Anthropic 또는 Qdrant API Key
- Presigned URL과 AWS 서명 파라미터
- Local DB DSN과 비밀번호

E2E Assertion 메시지도 응답 본문, 질문과 프롬프트를 출력하지 않고 상태 코드와 공개
오류 코드만 사용합니다.

---

## 14. 주요 파일

| 경로 | 역할 |
|---|---|
| `src/jipsa_rag/api/ingest.py` | 인제스트와 Backend callback |
| `src/jipsa_rag/api/v1/endpoints/rag_answer.py` | RAG Answer API |
| `src/jipsa_rag/infrastructure/document/` | 형식별 파서와 이미지 추출 |
| `src/jipsa_rag/infrastructure/ocr/` | CUDA EasyOCR |
| `src/jipsa_rag/infrastructure/embedding/` | CUDA TEI 클라이언트 |
| `src/jipsa_rag/infrastructure/indexing/` | Local DB와 Qdrant |
| `src/jipsa_rag/services/file_indexing.py` | 색인 전환과 보상 |
| `src/jipsa_rag/services/rag_answer.py` | lookup과 synthesis |
| `tests/fixtures/e2e_documents/` | 고정 다중 형식 Fixture |
| `tests/e2e/test_fixed_document_full_pipeline_e2e.py` | Issue #123 전체 E2E |
| `scripts/verify-rag-quality.ps1` | Ruff·Mypy·전체 Pytest |
| `scripts/run-issue-123-e2e.ps1` | 실제 GPU·인프라 E2E |
| `docs/api/rag-answer-contract.md` | 답변·출처 API 계약 |

---

## 15. 병합 전 체크리스트

- [ ] `.env.local` 비밀값이 Git 추적 대상이 아님
- [ ] Docker Desktop과 Local DB가 실행 중임
- [ ] NVIDIA GPU가 Docker와 PyTorch에서 확인됨
- [ ] `verify-rag-quality.ps1` 통과
- [ ] `run-issue-123-e2e.ps1` 통과
- [ ] 테스트 종료 후 전용 DB·Qdrant 데이터가 정리됨
- [ ] API 계약 문서와 구현 응답이 일치함
- [ ] 실제로 실행하지 않은 검증을 통과로 기록하지 않음
