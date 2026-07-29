# Jipsa Local RAG Service

> **문서 상태:** Stable · Local RAG 운영 기준 문서  
> **주 독자:** Local RAG 개발자, AWS Backend 연동 개발자, 운영·리뷰 담당자  
> **최종 검토:** 2026-07-29 · 구조화 로그와 Windows 실행 정책 반영  
> **Source of truth:** Pydantic 스키마 → FastAPI 엔드포인트 → 서비스 코드 → 실행 스크립트  
> **변경 시 함께 검토:** API 계약, 로그 계약, `.env.example`, Docker Compose, 회귀 테스트


`jipsa-rag`는 AWS Backend와 분리된 로컬 문서 검색·답변 서비스입니다.
Backend가 전달한 manifest와 Presigned GET URL을 사용해 문서를 다운로드하고,
구조 보존 파싱, 이미지 OCR, CUDA 임베딩, Local RAG DB 저장, Qdrant 검색과
Claude 기반 답변 생성을 수행합니다.

현재 지원 범위는 **PDF, DOCX, PPTX, XLSX, TXT 및 문서 내부 이미지 OCR**입니다.
일반 텍스트와 OCR 텍스트는 같은 검색 후보로 취급하며, 답변에는 실제로 인용한 출처만
반환합니다.

> 사용자 인증·인가와 파일 접근 권한의 최종 판정은 AWS Backend가 담당합니다.
> Local RAG는 전달받은 `user_idx`와 `reference_file_idxs`를 현재 요청의 불변 검색 범위로
> 사용합니다.

이 README는 특정 기능 하나의 사용 설명서가 아니라 **Local RAG 전체를 조망하는 시작점**입니다.
서비스 책임, 지원 형식, 처리 흐름, API, 로그, 검색 범위, 인용, 실행, 검증과 보안의 핵심
계약을 한곳에서 확인하고, 더 깊은 내용은 `docs/`의 전문 문서로 이동하도록 구성합니다.

## 1. 문서 바로가기

| 주제 | 문서 |
|---|---|
| 전체 문서 인덱스 | [`docs/README.md`](docs/README.md) |
| 용어집 | [`docs/glossary.md`](docs/glossary.md) |
| AWS Backend와 Local RAG 경계 | [`docs/architecture/responsibility-boundary.md`](docs/architecture/responsibility-boundary.md) |
| 지원 형식과 OCR | [`docs/features/document-support-and-ocr.md`](docs/features/document-support-and-ocr.md) |
| **종합 API 명세서** | [`docs/api/comprehensive-api-specification.md`](docs/api/comprehensive-api-specification.md) |
| API 거버넌스·호환성 | [`docs/api/api-governance-and-compatibility.md`](docs/api/api-governance-and-compatibility.md) |
| 청크 검색 API | [`docs/chunk-search-api.md`](docs/chunk-search-api.md) |
| 답변 API | [`docs/api/rag-answer-api-contract.md`](docs/api/rag-answer-api-contract.md) |
| Source Locator 상세 | [`docs/api/rag-answer-contract.md`](docs/api/rag-answer-contract.md) |
| 재인제스트와 보상 | [`docs/operations/ingest-recovery-policy.md`](docs/operations/ingest-recovery-policy.md) |
| 로컬 인프라 실행 | [`docs/operations/local-runtime.md`](docs/operations/local-runtime.md) |
| 관측성과 문제 해결 | [`docs/operations/observability-and-troubleshooting.md`](docs/operations/observability-and-troubleshooting.md) |
| 테스트 | [`docs/testing/test-guide.md`](docs/testing/test-guide.md) |
| PowerShell 실제 E2E | [`docs/testing/powershell-e2e.md`](docs/testing/powershell-e2e.md) |
| 환경 변수와 비밀정보 | [`docs/security/environment-and-secrets.md`](docs/security/environment-and-secrets.md) |
| 문서 품질 표준 | [`docs/governance/documentation-quality-standard.md`](docs/governance/documentation-quality-standard.md) |
| 품질 검토 보고서 | [`docs/governance/documentation-review-report.md`](docs/governance/documentation-review-report.md) |

### 역할별 빠른 진입

| 역할 | 먼저 볼 내용 | 다음 문서 |
|---|---|---|
| Local RAG 개발자 | 처리 흐름, 로그, 인제스트, 테스트 | `local-runtime.md`, `test-guide.md` |
| AWS Backend 연동 개발자 | 책임 경계, API 표면, callback | `comprehensive-api-specification.md` |
| 검색·답변 개발자 | 검색 범위, lookup·synthesis, 인용 | `rag-answer-api-contract.md` |
| QA·리뷰어 | 검증 기록, 품질 게이트, 병합 체크리스트 | `powershell-e2e.md` |
| 장애 대응 담당자 | 구조화 로그, 대표 이벤트, 복구 원칙 | `observability-and-troubleshooting.md` |


## 2. 서비스 책임

### Local RAG 책임

- `X-Internal-Token` 기반 내부 인증
- Backend 파일 manifest 조회
- Presigned GET URL 기반 스트리밍 다운로드
- 확장자, MIME Type, Magic Byte, OOXML 구조와 SHA-256 검증
- PDF, DOCX, PPTX, XLSX, TXT 형식별 파싱
- PDF·Office 문서 이미지 추출과 스캔 페이지 탐지
- CUDA 12.9 환경의 EasyOCR 실행
- 구조와 원본 위치를 보존하는 청킹
- CUDA TEI 문서·질의 임베딩
- Local RAG DB 문서·청크·색인 실행 이력 저장
- Qdrant staging, 활성 전환, 검색과 보상 처리
- `lookup`과 `synthesis` 답변
- `[SOURCE-N]`, `cited_source_ids`, `sources` 무결성 검증
- Request ID 기반 구조화 로그와 민감정보 마스킹

### AWS Backend 책임

- 사용자 인증·인가
- 파일 업로드, 소유권과 상태 관리
- S3 접근과 IAM Role 관리
- Presigned GET URL 발급
- manifest와 ingest-complete 내부 API 제공
- 사용자 선택 `File_IDX` 목록 확정
- Local RAG 호출과 최종 사용자 응답 전달

### Local RAG 제외 범위

- AWS Access Key 또는 Secret Access Key 보관
- `boto3` 기반 S3 직접 접근
- AWS Backend DB 직접 수정
- 선택하지 않은 전체 문서 자동 검색
- Docker Desktop 또는 Local DB 서버 자체 실행

## 3. 지원 문서와 OCR

| 형식 | 일반 텍스트 | 구조 | 이미지 OCR | 대표 위치 |
|---|---:|---:|---:|---|
| PDF | 지원 | 페이지·표 | 지원 | 페이지, 이미지 순번 |
| DOCX | 지원 | 섹션·문단·표 | 지원 | 섹션, 블록, 문단, 표 |
| PPTX | 지원 | 슬라이드·도형·표·노트 | 지원 | 슬라이드, 도형 경로 |
| XLSX | 지원 | 시트·행·셀·표·병합 범위 | 지원 | 시트, 셀 범위 |
| TXT | 지원 | 줄·문자 범위 | 해당 없음 | 줄, 문자 범위 |

공통 원칙:

- 확장자만 신뢰하지 않고 내부 형식과 Magic Byte를 검증합니다.
- OCR 청크는 원본 페이지, 문단, 슬라이드 또는 시트 위치를 상속합니다.
- 같은 이미지 바이트는 SHA-256 Hash로 중복 OCR을 방지합니다.
- 작은 아이콘, 로고, 배지와 장식 이미지는 OCR 후보에서 제외합니다.
- 기존 `page`, `slide_no`, `sheet_name`, `section_title`은 하위 호환용으로 유지하고
  신규 소비자는 `source_locator`를 기준으로 위치를 해석합니다.
- PPTX 차트·SmartArt와 XLSX 차트 렌더링은 Windows 대화형 세션의
  Microsoft Office COM을 사용합니다.

## 4. 처리 흐름

```text
AWS Backend
  ├─ 사용자·파일 권한 검증
  ├─ S3 파일 관리
  ├─ Presigned GET URL 발급
  └─ reference_file_idxs 확정
            │
            ▼
Local RAG FastAPI
  ├─ 최신 manifest 조회와 원본 검증
  ├─ 형식별 파싱
  ├─ 이미지 추출·Office 렌더링·EasyOCR
  ├─ 구조 보존 청킹
  ├─ CUDA TEI 임베딩
  ├─ Local RAG DB
  ├─ Qdrant VectorDB
  └─ Claude lookup/synthesis
```

기본 로컬 구성:

| 구성요소 | 기본 주소 | 역할 |
|---|---|---|
| FastAPI | `0.0.0.0:8077` | Local RAG API |
| Qdrant REST | `127.0.0.1:6333` | 벡터 검색 |
| Qdrant gRPC | `127.0.0.1:6334` | 선택적 gRPC |
| CUDA TEI | `127.0.0.1:18081` | 임베딩 |
| Local RAG DB | `127.0.0.1:3306` | 문서·청크·실행 이력 |

Qdrant와 TEI는 기본적으로 루프백 인터페이스에만 바인딩합니다.

## 5. API 표면

| Method·Path | 인증 | 목적 |
|---|---|---|
| `POST /ingest` | 내부 토큰 | 최신 manifest 재조회·색인·Backend callback |
| `POST /api/v1/files/process` | 내부 토큰 | 전달 manifest 직접 처리·색인 |
| `GET /api/v1/health/live` | 공개 | 프로세스 생존 확인 |
| `GET /api/v1/health/ready` | 공개 | Local RAG DB 연결 확인 |
| `GET /api/v1/diagnostics/network` | 내부 토큰 | bind·외부 주소·egress 진단 |
| `POST /api/v1/chunks/search` | 내부 토큰 | 선택 문서 활성 청크 검색 |
| `POST /api/v1/rag/answers` | 내부 토큰 | 선택 문서 기반 lookup·synthesis 답변 |

요청·응답 필드, 공통 envelope, 인증 방향, 오류 코드, 재시도와 Local RAG → Backend
manifest·callback 계약은
[`docs/api/comprehensive-api-specification.md`](docs/api/comprehensive-api-specification.md)를
기준으로 확인합니다.

> 모든 응답은 `X-Request-ID`를 포함합니다. 현재 outbound Backend client는 inbound
> `X-Request-ID`를 manifest·callback 요청에 자동 전파하지 않으므로 cross-service 전파를
> 구현된 계약으로 가정하지 않습니다.

## 6. 구조화 로그와 요청 추적

Local RAG는 사람이 읽기 쉬운 Console 로그와 수집기용 JSON 로그를 분리합니다.

| 형식 | 목적 | 시간·Request ID |
|---|---|---|
| `console` | Windows 로컬 개발·장애 분석 | 로컬/UTC 선택, 기본 8자리 Request ID |
| `json` | 로그 수집기·자동 분석 | UTC RFC 3339, 전체 Request ID, `log_schema_version=1` |

기본 로컬 설정:

```dotenv
JIPSA_RAG_LOG_LEVEL=INFO
JIPSA_RAG_LOG_FORMAT=console
JIPSA_RAG_LOG_CONSOLE_TIMEZONE=local
JIPSA_RAG_LOG_COLOR=auto
JIPSA_RAG_LOG_REQUEST_ID_LENGTH=8
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=WARNING
JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS=5000
```

대표 Console 로그:

```text
2026-07-29 15:46:41.030+09:00 INFO     [jipsa-rag/local] [file-processing] file_download_completed req=5a506ea3 | File download completed. | file=952301 type=pdf size=2.09KiB duration=1.70ms
```

### 요청 전체 흐름

같은 Request ID를 기준으로 inbound HTTP 요청부터 manifest 조회, 파일 처리, Backend
callback과 최종 HTTP 결과까지 연결합니다.

```text
ingest_manifest_fetch_completed
file_download_completed
document_parsing_ocr_completed
document_chunking_completed
document_embedding_completed
file_indexing_completed
file_processing_completed
ingest_success_callback_completed
http_request_completed
```

실패 시에는 실패 지점에 따라 다음 이벤트가 함께 기록될 수 있습니다.

```text
ingest_failure_callback_completed
rag_answer_search_failed
application_exception
http_request_completed
```

### 레벨 판정

- `DEBUG`: 명시적으로 활성화한 상세 진단
- `INFO`: 정상 단계 완료와 정상 HTTP 결과
- `WARNING`: OCR 부분 실패 또는 지연 임계값을 넘은 정상 단계
- `ERROR`: 요청 실패, 외부 연동 실패, HTTP 5xx와 예외
- `CRITICAL`: 프로세스 지속이 어려운 필수 자원 초기화 실패

`is_slow_stage=true`와 `success=true`가 같이 있으면 장애가 아니라 성능 경고입니다.

### 기록하지 않는 정보

- 사용자 질문, 청크·OCR 텍스트
- Claude 전체 프롬프트·응답
- 임베딩 벡터
- HTTP 요청·응답 본문
- 내부 토큰, API Key, Presigned URL과 DB DSN
- 원본·임시 파일 절대 경로

자세한 이벤트, 장애 해석과 복구 순서는
[`docs/operations/observability-and-troubleshooting.md`](docs/operations/observability-and-troubleshooting.md)를
참조합니다.

## 7. 인제스트와 재인제스트

```text
POST /ingest
  → 내부 인증
  → 최신 manifest 조회
  → 원본 다운로드와 형식 검증
  → 파싱·이미지 추출·OCR
  → 구조 보존 청킹
  → CUDA TEI 임베딩
  → Local RAG DB 준비
  → Qdrant 비활성 staging point 업서트
  → 신규 point 활성화
  → 이전 정상 point 비활성화
  → Local RAG 성공 확정
  → 최신 활성 청크 ingest-complete callback
```

동일 파일 Hash, Parser Version, Embedding Model, Index Version이면 기존 정상 문서와
결정적 Chunk ID를 멱등 재사용합니다. 정체성이 달라지면 신규 문서를 만든 뒤 새 색인이
성공한 경우에만 이전 문서를 soft delete하고 이전 Qdrant point를 비활성화합니다.

실패 시 원칙:

- 신규 업서트 실패: 신규 staging point만 정리합니다.
- 활성 전환 실패: 이전 정상 point를 복구하고 신규 point를 제거합니다.
- Local 성공 확정 실패: Qdrant 전환을 되돌립니다.
- 기존 정상 색인 재사용 실패: 기존 문서와 point는 유지하고 신규 실행만 실패 처리합니다.
- 같은 `File_IDX` 동시 요청: MySQL advisory lock으로 직렬화합니다.
- 오래된 실행이 소유권을 잃으면 최신 실행의 point에 보상 작업을 수행하지 않습니다.

상세 정책은
[`docs/operations/ingest-recovery-policy.md`](docs/operations/ingest-recovery-policy.md)를
참조합니다.

## 8. 검색 범위

Qdrant 검색은 다음 조건을 하나의 `must` 필터로 결합합니다.

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

검색 저장소, 서비스 계층과 최종 답변 계층이 같은 범위를 반복 검증합니다.
선택하지 않은 문서, 다른 사용자, 비활성 staging point와 이전 색인은 정상 출처가 될 수
없습니다.

`reference_file_idxs`가 비어 있거나 생략되면 전체 문서 검색으로 확대하지 않고
`REFERENCE_DOCUMENT_REQUIRED` 또는 요청 검증 오류로 거부합니다.

## 9. lookup과 synthesis

### lookup

명시적인 다문서 비교·종합 의도가 없으면 선택 문서 전체를 한 번 검색하고 단일 Claude
생성 흐름을 사용합니다.

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
  → 최종 인용과 선택 범위 검증
```

한 문서의 임베딩, Qdrant 검색 또는 부분 Claude 호출이 실패해도 유효한 다른 문서가
있으면 계속 처리하는 문서별 부분 실패 정책을 적용합니다. 사용자·선택 문서 범위 위반과
인용 무결성 위반은 전체 요청 실패입니다.

## 10. 근거 부족과 인용

검색 가능한 청크가 없거나 유효한 문서별 부분 답변이 없으면 최종 Claude 호출을 생략하고
다음 의미의 응답을 반환합니다.

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

정상 답변의 순서 계약:

```text
본문 SOURCE-N 최초 등장 순서
=
cited_source_ids
=
sources[].source_id 순서
```

최종 `sources`에는 실제 인용한 출처만 포함하며 같은 `source_id` 또는 `chunk_id`를
중복 반환하지 않습니다.

`source_locator`의 공통 필드는 `file_type`, `kind`, `content_origin`, `unit_type`,
`structure_path`이며 형식별 위치와 OCR 이미지 위치를 추가합니다.

## 11. 설치와 실행

필수 환경:

- Windows PowerShell 5.1 이상
- Python 3.12
- `uv`
- Docker Desktop과 Docker Compose
- NVIDIA Driver와 Docker GPU 지원
- CUDA 12.9용 PyTorch
- Local MySQL 또는 MariaDB
- 실제 답변·E2E용 Anthropic API Key
- Office 렌더링용 PowerPoint·Excel과 Windows 대화형 세션

### PowerShell 실행 정책

다음 오류는 Python, Docker 또는 RAG 기능 문제가 아니라 PowerShell이 `.ps1` 실행을
차단한 상태입니다.

```text
running scripts is disabled on this system
PSSecurityException
UnauthorizedAccess
```

프로젝트 권장 방식은 현재 PowerShell 프로세스에만 `Bypass`를 적용하는 것입니다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'
```

이 설정은 관리자 권한이 필요하지 않고 현재 PowerShell 창을 닫으면 자동으로 사라집니다.
시스템 전체 `LocalMachine` 정책이나 `Unrestricted` 정책은 변경하지 않습니다.

현재 사용자 계정에 지속 적용해야 하는 개인 개발 PC에서는 다음 선택지를 사용할 수
있습니다.

```powershell
Set-ExecutionPolicy `
    -Scope CurrentUser `
    -ExecutionPolicy RemoteSigned `
    -Force

Get-ExecutionPolicy -List
```

조직의 `MachinePolicy` 또는 `UserPolicy`가 우선하는 환경에서는 현재 프로세스에만 다음
방식으로 실행합니다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-all-rag-tests.ps1'
```

의존성 동기화:

```powershell
uv sync --frozen
```

일반 Local RAG 실행:

```powershell
& .\scripts\start-local-rag.ps1
```

인프라 정지:

```powershell
& .\scripts\stop-local-rag.ps1
```

스크립트는 Docker Desktop 프로그램과 Local DB 서버를 시작하지 않습니다.
상세 절차는
[`docs/operations/local-runtime.md`](docs/operations/local-runtime.md)를 참조합니다.

## 12. 품질 게이트와 실제 E2E

일반 품질 게이트:

```powershell
& .\scripts\verify-rag-quality.ps1
```

검사 순서:

1. `uv sync --frozen`
2. `uv run ruff format --check .`
3. `uv run ruff check .`
4. `uv run mypy src tests`
5. `uv run pytest`

일반 Pytest는 `JIPSA_RAG_RUN_E2E`를 제거하여 실제 GPU, Local DB, Qdrant, Office COM과
Claude 호출이 필요한 opt-in E2E를 명시적으로 skip합니다.

고정 다중 형식·OCR 전체 파이프라인 E2E:

```powershell
& .\scripts\run-issue-123-e2e.ps1
```

전체 실제 E2E:

```powershell
& .\scripts\run-all-rag-tests.ps1
```

지원 옵션:

```powershell
& .\scripts\run-all-rag-tests.ps1 -SkipQualityGate
& .\scripts\run-all-rag-tests.ps1 -KeepInfrastructureRunning
```

상세 절차와 검증 범위는
[`docs/testing/test-guide.md`](docs/testing/test-guide.md)를 참조합니다.

## 13. 실제 검증 기록

> **기능·인프라 마지막 검증:** 2026-07-28  
> **문서 재구성:** 2026-07-29  
> README 변경 후에는 아래 명령으로 문서 계약과 전체 품질 게이트를 다시 검증해야 합니다.

| 계층 | 마지막 확인 결과 | 의미 |
|---|---:|---|
| 로그 집중 회귀 | `32 passed` | Console·JSON·마스킹·요청 흐름 |
| 일반 전체 Pytest | `863 passed, 129 skipped` | opt-in 실제 E2E는 별도 실행 |
| Local RAG DB | `1 passed` | 전용 로컬 DB 연결 |
| Office COM | `4 passed` | PowerPoint·Excel 이미지·차트 |
| 실제 비PDF E2E | `21 passed` | DOCX·PPTX·XLSX·TXT 혼합 처리 |
| 전체 실제 E2E | 종료 코드 `0` | CUDA·Qdrant·DB·Office·Claude 경로 |

일반 Pytest의 `skip`은 실패가 아닙니다. 다만 실제 서비스 준비를 주장할 때는
`run-all-rag-tests.ps1`의 종료 코드와 각 실제 인프라 단계 결과가 필요합니다.

OCR 일부 실패, Qdrant 503, Claude 503과 HTTP 5xx 로그는 장애 변환·부분 실패·보상
테스트에서 의도적으로 발생할 수 있습니다. 해당 테스트가 `PASSED`이고 최종 종료 코드가
`0`이면 예상된 관측성 검증입니다.


## 14. 환경 변수와 비밀정보

환경 파일 역할:

| 파일 | 목적 | Git 커밋 |
|---|---|---:|
| `.env.example` | 변수 설명과 안전한 예시 | 허용 |
| `.env.local` | 실제 로컬 실행과 전체 E2E | 금지 |
| `.env.development` | 개발 프로필 | 금지 |
| `.env.test` | 일반 테스트 프로필 | 금지 |

- 실제 로컬 실행은 `.env.local`, 일반 테스트는 `.env.test`를 사용합니다.
- `.env.local`, `.env.development`, `.env.test`는 Git에 커밋하지 않습니다.
- 예시와 변수 설명은 [`.env.example`](.env.example)을 사용합니다.
- Local RAG는 AWS Access Key, Secret Access Key와 Session Token을 사용하지 않습니다.
- `INTERNAL_TOKEN`과 `RAG_INGEST_TOKEN`은 호출 방향이 다르므로 혼용하지 않습니다.
- 질문, 청크·OCR 원문, 전체 프롬프트, 내부 토큰, API Key, Presigned URL과 DB DSN은
  로그에 기록하지 않습니다.

상세 정책은
[`docs/security/environment-and-secrets.md`](docs/security/environment-and-secrets.md)를
참조합니다.

## 15. 병합 전 체크리스트

- [ ] `.env.local`과 비밀값이 Git 추적 대상이 아님
- [ ] 문서 상대 링크와 경로가 실제 저장소 구조와 일치함
- [ ] PDF, DOCX, PPTX, XLSX, TXT와 OCR 지원 범위가 문서 전체에서 일치함
- [ ] AWS Backend와 Local RAG 책임 경계가 뒤바뀌지 않음
- [ ] 검색·답변·출처 API 계약이 OpenAPI와 스키마에 일치함
- [ ] 재인제스트, 부분 실패와 보상 처리 설명이 현재 서비스 코드와 일치함
- [ ] Console·JSON 로그 변수, 이벤트와 민감정보 정책이 현재 구현과 일치함
- [ ] PowerShell 실행 정책 안내가 Markdown·HTML·테스트 문서에서 일치함
- [ ] HTML의 Markdown 보기, 인쇄, 검색, 테마와 내부 링크가 정상 동작함
- [ ] `verify-rag-quality.ps1` 통과
- [ ] 필요한 실제 E2E를 별도로 실행하고 결과를 기록함
- [ ] 실제로 실행하지 않은 검증을 통과로 기록하지 않음
