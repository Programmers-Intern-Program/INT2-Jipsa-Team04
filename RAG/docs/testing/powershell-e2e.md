# PowerShell 기반 Local RAG 실제 E2E 가이드

> **문서 상태:** Active · Windows 실제 E2E Runbook  
> **주 독자:** Local RAG 개발자, QA, 장애 분석 담당자  
> **최종 검토:** 2026-07-29  
> **주의:** 실제 Claude 호출 비용과 CUDA GPU 시간이 발생

## 1. 목적

이 문서는 Windows PowerShell에서 일반 품질 게이트와 실제 CUDA·Local DB·Qdrant·Office
COM·Claude E2E를 실행하는 표준 절차를 정의합니다.

모든 명령은 저장소의 `RAG` 디렉터리를 기준으로 실행합니다.

## 2. 실행 정책 오류를 먼저 해결

다음 오류는 Python, Docker 또는 테스트 실패가 아니라 PowerShell이 `.ps1` 실행을
차단한 상태입니다.

```text
running scripts is disabled on this system
PSSecurityException
UnauthorizedAccess
```

### 프로젝트 권장 설정

현재 PowerShell 프로세스에만 `Bypass`를 적용합니다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'
```

특징:

- 관리자 권한이 필요하지 않습니다.
- 현재 PowerShell 창에서만 유효합니다.
- PowerShell 창을 닫으면 자동으로 원복됩니다.
- 시스템 전체 또는 다른 사용자 정책을 변경하지 않습니다.

스크립트는 호출 연산자 `&`로 실행합니다.

```powershell
& .\scripts\verify-rag-quality.ps1
& .\scripts\run-all-rag-tests.ps1
```

### 한 번의 명령으로 실행

현재 세션 정책을 바꾸지 않고 새 PowerShell 프로세스에서만 실행할 수도 있습니다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-all-rag-tests.ps1'
```

### 현재 사용자 계정에 지속 적용

개인 개발 PC에서 매번 설정하기 어렵다면 `CurrentUser` 범위에 `RemoteSigned`를
사용합니다.

```powershell
Set-ExecutionPolicy `
    -Scope CurrentUser `
    -ExecutionPolicy RemoteSigned `
    -Force
```

정책 확인:

```powershell
Get-ExecutionPolicy -List
```

`MachinePolicy` 또는 `UserPolicy`는 조직의 Group Policy이므로 사용자 설정보다
우선합니다. 조직 정책을 임의로 해제하거나 다음과 같은 광범위한 설정을 사용하지
않습니다.

```text
LocalMachine + Unrestricted
LocalMachine + Bypass
```

## 3. 실행 경로

| 목적 | 스크립트 |
|---|---|
| Ruff·Mypy·일반 Pytest | `scripts/verify-rag-quality.ps1` |
| 실제 PDF 중심 E2E | `scripts/run-real-rag-e2e.ps1` |
| 고정 5개 형식·OCR E2E | `scripts/run-issue-123-e2e.ps1` |
| 전체 실제 E2E | `scripts/run-all-rag-tests.ps1` |
| Local RAG 실행 | `scripts/start-local-rag.ps1` |
| Qdrant·TEI 정지 | `scripts/stop-local-rag.ps1` |

## 4. 실제 E2E 구성요소

- PDF, DOCX, PPTX, XLSX, TXT
- 문서 내부 이미지와 스캔 PDF
- CUDA EasyOCR
- CUDA TEI
- Local RAG MySQL 또는 MariaDB
- Qdrant VectorDB
- Microsoft PowerPoint·Excel Office COM
- Anthropic Claude API
- FastAPI `POST /ingest`
- FastAPI `POST /api/v1/chunks/search`
- FastAPI `POST /api/v1/rag/answers`

AWS Backend manifest, ingest-complete와 Presigned GET URL 경계는 결정적 테스트 대역으로
고정할 수 있습니다. 테스트는 AWS Backend를 실행하거나 수정하지 않습니다.

## 5. 환경 파일

```text
.env.local
.env.development
.env.test
.env.example
```

- `.env.test`: 일반 단위·통합·회귀 테스트
- `.env.local`: 실제 CUDA, DB, Qdrant, Office COM, Claude E2E
- `.env.example`: 변수 설명과 안전한 예시
- `.env.development`: 개발 프로필이 필요한 경우

실제 E2E 스크립트는 `.env.local`을 현재 PowerShell 프로세스에만 임시 주입하고 종료 시
원래 환경을 복원합니다.

## 6. 필수 `.env.local` 범주

### Local RAG DB

```dotenv
JIPSA_RAG_DATABASE_HOST=127.0.0.1
JIPSA_RAG_DATABASE_PORT=3306
JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG
JIPSA_RAG_DATABASE_USER=로컬_DB_계정
JIPSA_RAG_DATABASE_PASSWORD=실제_로컬_DB_비밀번호
JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true
JIPSA_RAG_DATABASE_ECHO=false
```

### CUDA EasyOCR

```dotenv
JIPSA_RAG_OCR_ENABLED=true
JIPSA_RAG_OCR_LANGUAGES_CSV=ko,en
JIPSA_RAG_OCR_GPU=true
JIPSA_RAG_OCR_GPU_REQUIRED=true
JIPSA_RAG_OCR_DEVICE=cuda:0
```

### CUDA TEI

```dotenv
JIPSA_RAG_EMBEDDING_PROVIDER=tei
JIPSA_RAG_EMBEDDING_BASE_URL=http://127.0.0.1:18081
JIPSA_RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
JIPSA_RAG_EMBEDDING_DIM=1024
JIPSA_RAG_EMBEDDING_BATCH_SIZE=32
JIPSA_RAG_EMBEDDING_DISTANCE=cosine
JIPSA_RAG_EMBEDDING_TIMEOUT_SECONDS=60
```

### Qdrant

```dotenv
JIPSA_RAG_VECTOR_DB_PROVIDER=qdrant
JIPSA_RAG_QDRANT_URL=http://127.0.0.1:6333
JIPSA_RAG_QDRANT_COLLECTION=rag_chunk_vector_qwen3_embedding_0_6b_1024
JIPSA_RAG_QDRANT_GRPC_PORT=6334
JIPSA_RAG_QDRANT_PREFER_GRPC=false
JIPSA_RAG_QDRANT_TIMEOUT_SECONDS=10
```

### Claude

```dotenv
JIPSA_RAG_GENERATION_PROVIDER=anthropic
ANTHROPIC_API_KEY=실제_Anthropic_API_Key
JIPSA_RAG_ANTHROPIC_MODEL=사용_가능한_Claude_모델_ID
JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS=4096
JIPSA_RAG_ANTHROPIC_TIMEOUT_SECONDS=60
```

### 로그

```dotenv
JIPSA_RAG_LOG_LEVEL=INFO
JIPSA_RAG_LOG_FORMAT=console
JIPSA_RAG_LOG_CONSOLE_TIMEZONE=local
JIPSA_RAG_LOG_COLOR=auto
JIPSA_RAG_LOG_REQUEST_ID_LENGTH=8
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=WARNING
JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS=5000
```

비밀값은 PowerShell 출력, Git, 테스트 Assertion과 오류 메시지에 기록하지 않습니다.

## 7. PowerShell 인코딩

한글 주석과 출력 문자열을 포함하는 `.ps1`은 Windows PowerShell 5.1 호환을 위해
UTF-8 with BOM을 유지합니다.

```text
EF-BB-BF
```

## 8. 일반 품질 게이트

```powershell
& .\scripts\verify-rag-quality.ps1
```

실행 내용:

```text
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

일반 Pytest는 실제 E2E 플래그를 제거하므로 외부 서비스 비용이 발생하지 않습니다.

## 9. 로그 집중 검증

```powershell
uv run pytest `
    tests/unit/core/test_logging_observability.py `
    tests/unit/core/test_sensitive_logging.py `
    tests/unit/core/test_request_logging_middleware.py `
    tests/unit/api/test_ingest_stage_logging.py `
    tests/unit/api/v1/endpoints/test_file_processing_stage_logging.py `
    tests/unit/diagnostics/test_logging_performance.py `
    -v
```

검증 범위:

- Console·JSON 출력 계약
- Request ID
- inbound 요청 완료 로그
- manifest·callback 등 outbound 단계 로그
- 지연 단계 WARNING 승격
- 예외와 HTTP 5xx 로그
- 민감정보 마스킹
- 질문·청크·OCR·벡터·HTTP 본문 비출력
- ANSI와 제어 문자 정제
- 로그 출력량과 성능 회귀

## 10. 고정 다중 형식·OCR E2E

```powershell
& .\scripts\run-issue-123-e2e.ps1
```

품질 게이트 중복 생략:

```powershell
& .\scripts\run-issue-123-e2e.ps1 -SkipQualityGate
```

장애 분석용 인프라 유지:

```powershell
& .\scripts\run-issue-123-e2e.ps1 -KeepInfrastructureRunning
```

## 11. 전체 실제 E2E

```powershell
& .\scripts\run-all-rag-tests.ps1
```

전체 실행에는 다음이 포함됩니다.

1. 일반 품질 게이트
2. Docker·Compose 확인
3. Qdrant·CUDA TEI 준비
4. PyTorch CUDA 확인
5. Local RAG DB 연결
6. Office COM 이미지·차트 렌더링
7. 고정 다중 형식·OCR E2E
8. 실제 PDF·Claude·생성 제한 E2E
9. 실제 DOCX·PPTX·XLSX·TXT E2E
10. 환경과 인프라 복원

옵션:

```powershell
& .\scripts\run-all-rag-tests.ps1 -SkipQualityGate
& .\scripts\run-all-rag-tests.ps1 -KeepInfrastructureRunning
```

## 12. 실행 전 사전 점검

전체 E2E를 실행하기 전에 다음 항목을 개별 확인합니다.

```powershell
Get-ExecutionPolicy -List
Get-Command uv
Get-Command docker

docker version
docker compose version
nvidia-smi

uv run python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

사전 점검 기준:

| 항목 | 성공 기준 | 실패 시 우선 조치 |
|---|---|---|
| PowerShell | Process 범위 `Bypass` 또는 실행 가능한 정책 | 실행 정책 섹션 적용 |
| `uv` | 실행 경로 출력 | Python·uv 설치 확인 |
| Docker | Client·Server 모두 응답 | Docker Desktop 시작 |
| Compose | 버전 출력 | Docker Desktop 구성 확인 |
| NVIDIA | GPU와 Driver 표시 | Driver·WSL·Container Runtime 확인 |
| PyTorch | `True`와 GPU 이름 | CUDA 12.9 wheel과 환경 확인 |
| Local DB | 전용 DB 연결 가능 | Host·Port·계정·스키마 확인 |
| Office COM | PowerPoint·Excel 자동화 가능 | 최초 실행·대화형 세션 확인 |
| Claude | 모델 접근 가능 | Key, 모델 ID, 예산 확인 |

`.env.local` 필수 변수의 존재 여부만 확인하고 전체 파일이나 비밀값을 콘솔에 출력하지
않습니다.

## 13. 단계별 예상 결과

### 품질 게이트

```text
Ruff format: no files would be reformatted
Ruff lint: All checks passed
Mypy: Success: no issues found
Pytest: failures=0
```

일반 Pytest의 E2E skip은 정상입니다. skip된 항목을 실제 서비스 검증 완료로 기록하지
않습니다.

### Qdrant와 CUDA TEI

- Qdrant readiness 성공
- 예상 Collection 존재 또는 안전한 생성
- 벡터 차원 `1024`
- 거리 함수 Cosine
- TEI `/embed` 실제 응답
- 입력 수와 출력 벡터 수 일치
- NaN·Infinity 없음
- GPU 할당과 CPU fallback 없음

### Office COM

- PowerPoint 이미지·차트 렌더링 성공
- Excel 이미지·차트 렌더링 성공
- COM timeout과 잔류 프로세스 없음
- 임시 렌더링 파일 정리

### 다중 형식·OCR

- PDF, DOCX, PPTX, XLSX, TXT 처리
- 문서 내부 이미지와 스캔 PDF OCR
- 주변 구조와 `source_locator` 유지
- OCR 일부 실패 시 전체 문서 정책에 맞는 부분 실패 처리
- 질문·청크·OCR 원문과 벡터 로그 비노출

### 검색·답변

- 사용자·활성·선택 문서 필터 유지
- lookup과 synthesis 정상 수행
- 근거 부족 시 Claude 최종 호출 생략
- `SOURCE-N`, `cited_source_ids`, `sources` 순서 일치
- 실제로 인용한 출처만 응답에 포함

## 14. 실패 단계별 진단표

| 중단 지점 | 의미 | 확인 파일·환경 |
|---|---|---|
| 실행 정책 | `.ps1` 실행 전 차단 | `Get-ExecutionPolicy -List` |
| `uv sync` | lock과 의존성 불일치 | `pyproject.toml`, `uv.lock` |
| Ruff format | 소스 포맷 차이 | 출력된 파일 경로 |
| Ruff lint | 정적 규칙 위반 | 오류 코드·줄 번호 |
| Mypy | 타입 계약 위반 | 최초 오류와 연쇄 오류 |
| 일반 Pytest | 단위·회귀 실패 | 최초 실패 Fixture |
| Docker | Engine 미실행 | Docker Desktop |
| Qdrant | readiness·Collection 오류 | compose, Collection 설정 |
| TEI | CUDA·모델·차원 오류 | container log, GPU, model ID |
| Python CUDA | PyTorch CPU build | CUDA 12.9 wheel |
| Local DB | 연결·권한·스키마 오류 | `Jipsa_Local_RAG`, 전용 계정 |
| Office COM | 대화형 세션·초기화 오류 | PowerPoint·Excel |
| OCR | EasyOCR GPU·모델 오류 | device, cache, timeout |
| Claude | Key·모델·예산 오류 | 안전한 공개 오류 코드 |
| Callback | Backend 경계 오류 | URL, token 방향, 응답 status |

항상 최초 실패를 먼저 확인합니다. 정리 또는 보상 단계의 후속 오류가 최초 원인을
덮어쓰지 않았는지 Request ID와 단계별 event로 확인합니다.

## 15. 종료와 자원 정리

기본 실행은 스크립트가 시작한 인프라와 임시 환경 변수를 종료 시 복원합니다.

정리 확인:

- OCR worker pool 종료
- FastAPI child process 종료
- 스크립트가 시작한 Qdrant·TEI 컨테이너 정지
- 기존에 실행 중이던 컨테이너는 유지
- 임시 파일과 Office 렌더링 출력 정리
- PowerShell 프로세스 환경 변수 원복

`-KeepInfrastructureRunning`을 사용한 경우 장애 분석 후 다음 스크립트로 프로젝트
인프라를 정리합니다.

```powershell
& .\scripts\stop-local-rag.ps1
```

## 16. E2E 안전장치

- `JIPSA_RAG_APP_ENV=test`에서만 테스트 데이터 정리를 허용합니다.
- `JIPSA_RAG_RUN_E2E=1`이 없으면 실제 E2E 모듈을 skip합니다.
- 테스트 전용 사용자와 명시적 `File_IDX`만 삭제합니다.
- 기존에 실행 중이던 컨테이너는 종료하지 않습니다.
- 스크립트가 주입한 환경 변수는 종료 시 복원합니다.
- Assertion은 질문, 청크, OCR, 프롬프트와 응답 원문을 출력하지 않습니다.

## 17. 성공 기준

- Ruff·Mypy·일반 Pytest 통과만으로 실제 CUDA E2E 통과를 주장하지 않습니다.
- 일반 Pytest에서 opt-in E2E가 skip되는 것은 정상입니다.
- 실제 서비스 검증은 `run-all-rag-tests.ps1`의 최종 종료 코드 0과 각 인프라 검증
  결과를 함께 확인합니다.
- `-KeepInfrastructureRunning` 사용 시 장애 분석 후 자원을 수동 정리합니다.

최종 기록 예시:

```text
[통과]
- verify-rag-quality.ps1
- run-all-rag-tests.ps1

[실제 인프라]
- CUDA EasyOCR: 실행
- CUDA TEI: 실행
- Local RAG DB: 실행
- Qdrant: 실행
- Office COM: 실행
- Claude: 실행

[미실행]
- 없음

[환경]
- Windows PowerShell 버전:
- Python 3.12.x
- CUDA 12.9
- GPU 모델:
- Local DB 종류:
```

실제로 실행하지 않은 항목은 반드시 `미실행`으로 기록합니다.

## 18. 관련 문서

- [테스트 절차 요약](test-guide.md)
- [Local RAG 실행 절차](../operations/local-runtime.md)
- [관측성과 문제 해결](../operations/observability-and-troubleshooting.md)
- [환경 변수와 민감정보 관리](../security/environment-and-secrets.md)
