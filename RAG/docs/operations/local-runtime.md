# Windows Local RAG 실행 절차

> **문서 상태:** Active · 로컬 실행 Runbook  
> **주 독자:** Local RAG 개발자, QA, 장애 대응 담당자  
> **최종 검토:** 2026-07-29  
> **범위:** FastAPI, CUDA TEI, Qdrant, Local RAG DB, Office COM, EasyOCR

## 1. 실행 전 요구사항

- Windows PowerShell 5.1 이상
- Python 3.12
- `uv`
- Docker Desktop과 Docker Compose
- NVIDIA Driver와 Docker GPU 지원
- CUDA 12.9용 PyTorch
- Local MySQL 또는 MariaDB
- PowerPoint·Excel Office COM이 필요한 경우 Windows 대화형 사용자 세션
- `.env.local`

스크립트는 Docker Desktop 프로그램과 Local DB 서버 자체를 시작하지 않습니다.

## 2. PowerShell 실행 정책

다음 오류가 발생하면 스크립트 파일이나 Python 코드 문제가 아니라 PowerShell 실행
정책이 `.ps1`을 차단한 상태입니다.

```text
running scripts is disabled on this system
PSSecurityException
UnauthorizedAccess
```

현재 PowerShell 프로세스에만 실행 허용:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'
```

이 설정은 현재 창을 닫으면 자동으로 사라집니다. 시스템 전체 정책을 변경하지 않습니다.

정책 확인:

```powershell
Get-ExecutionPolicy -List
```

조직 Group Policy가 적용된 PC에서는 `MachinePolicy` 또는 `UserPolicy`가 우선합니다.
이 경우 현재 프로세스에만 다음 방식으로 실행합니다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\start-local-rag.ps1'
```

## 3. 의존성 동기화

```powershell
uv sync --frozen
```

`pyproject.toml`과 `uv.lock`이 일치하지 않으면 임의로 의존성을 변경하지 말고 원인을
먼저 확인합니다.

## 4. Local RAG 시작

```powershell
& .\scripts\start-local-rag.ps1
```

기본 주소:

| 구성요소 | 주소 |
|---|---|
| FastAPI | `http://127.0.0.1:8077` |
| Qdrant REST | `http://127.0.0.1:6333` |
| Qdrant gRPC | `127.0.0.1:6334` |
| CUDA TEI | `http://127.0.0.1:18081` |
| Local RAG DB | `127.0.0.1:3306` |

기본 Local RAG DB 이름은 `Jipsa_Local_RAG`입니다. 실제 값은 `.env.local`의
`JIPSA_RAG_DATABASE_NAME`을 기준으로 하며, E2E는 운영 DB가 아니라 전용 테스트 범위를
사용해야 합니다.

권장 `.env.local` 핵심값:

```dotenv
JIPSA_RAG_APP_ENV=local
JIPSA_RAG_DATABASE_HOST=127.0.0.1
JIPSA_RAG_DATABASE_PORT=3306
JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG
JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true
JIPSA_RAG_DATABASE_ECHO=false

JIPSA_RAG_VECTOR_DB_PROVIDER=qdrant
JIPSA_RAG_QDRANT_URL=http://127.0.0.1:6333
JIPSA_RAG_QDRANT_GRPC_PORT=6334
JIPSA_RAG_QDRANT_PREFER_GRPC=false

JIPSA_RAG_EMBEDDING_PROVIDER=tei
JIPSA_RAG_EMBEDDING_BASE_URL=http://127.0.0.1:18081
JIPSA_RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
JIPSA_RAG_EMBEDDING_DIM=1024

JIPSA_RAG_OCR_ENABLED=true
JIPSA_RAG_OCR_GPU=true
JIPSA_RAG_OCR_GPU_REQUIRED=true
JIPSA_RAG_OCR_DEVICE=cuda:0
```

시작 스크립트는 다음을 확인해야 합니다.

1. `.env.local` 로드
2. Docker Engine과 Compose
3. Qdrant 시작과 readiness
4. CUDA TEI 시작과 실제 임베딩
5. Python CUDA 장치
6. FastAPI 시작
7. `/api/v1/health/live`
8. `/api/v1/health/ready`

정상 시작 시 확인할 출력:

- 선택된 `.env.local` 경로
- FastAPI bind 주소와 port `8077`
- Qdrant REST `127.0.0.1:6333`와 gRPC `6334`
- CUDA TEI `127.0.0.1:18081`
- Embedding Model과 차원 `1024`
- EasyOCR GPU 필수 여부와 worker concurrency
- Local DB `Jipsa_Local_RAG` startup check 결과
- Uvicorn startup 완료와 health 응답

스크립트가 `reload` 또는 debug 모드로 실행되면 애플리케이션 시작 로그가 두 번 보일 수
있습니다. 부모 reloader와 실제 worker의 PID를 구분하고, health check가 최종적으로
성공했는지 확인합니다.

## 5. Health 확인

```powershell
$BaseUrl = 'http://127.0.0.1:8077'

Invoke-RestMethod `
    -Method Get `
    -Uri "$BaseUrl/api/v1/health/live"

Invoke-RestMethod `
    -Method Get `
    -Uri "$BaseUrl/api/v1/health/ready"
```

판정:

- `live`: FastAPI 프로세스 생존
- `ready`: 요청 처리에 필요한 Local RAG DB 연결 상태
- Qdrant와 TEI의 실제 E2E는 시작 스크립트 또는 전체 테스트에서 별도로 확인

## 6. 로컬 로그 설정

권장 기본값:

```dotenv
JIPSA_RAG_LOG_LEVEL=INFO
JIPSA_RAG_LOG_FORMAT=console
JIPSA_RAG_LOG_CONSOLE_TIMEZONE=local
JIPSA_RAG_LOG_COLOR=auto
JIPSA_RAG_LOG_REQUEST_ID_LENGTH=8
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=WARNING
JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS=5000
```

상세 디버깅:

```dotenv
JIPSA_RAG_LOG_LEVEL=DEBUG
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=INFO
```

운영·수집기 연동:

```dotenv
JIPSA_RAG_LOG_FORMAT=json
JIPSA_RAG_LOG_CONSOLE_TIMEZONE=utc
JIPSA_RAG_LOG_COLOR=never
```

JSON 로그는 `LOG_CONSOLE_TIMEZONE`과 관계없이 항상 UTC RFC 3339 형식입니다.

## 7. 정상 요청 로그 흐름

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

Request ID가 같은 로그를 연결하면 inbound 요청부터 Backend callback과 최종 HTTP 응답까지
추적할 수 있습니다.

## 8. 수동 사전 점검

스크립트 실행 전 인프라 상태를 빠르게 확인할 수 있습니다.

```powershell
docker version
docker compose version
nvidia-smi

uv run python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"

Invoke-RestMethod `
    -Method Get `
    -Uri 'http://127.0.0.1:6333/readyz'
```

판정 기준:

- Docker Engine과 Compose가 모두 응답함
- NVIDIA GPU가 Windows와 Docker 양쪽에서 보임
- `torch.cuda.is_available()`가 `True`
- Qdrant readiness가 성공함
- TEI는 단순 port open이 아니라 실제 `/embed` 결과로 검증함
- Local DB는 `SELECT 1` 또는 통합 테스트로 확인함

## 9. Local RAG 정지

```powershell
& .\scripts\stop-local-rag.ps1
```

정지 후 확인:

- FastAPI 프로세스 종료
- OCR worker pool 종료
- 스크립트가 시작한 Qdrant·TEI 컨테이너 정지
- 임시 환경 변수 복원
- `-KeepInfrastructureRunning` 사용 시 의도한 컨테이너만 유지

## 10. 대표 장애

### 스크립트를 실행할 수 없음

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force
```

같은 PowerShell 창에서 다시 `& .\scripts\...ps1`을 실행합니다.

### Docker 연결 실패

- Docker Desktop 실행 상태
- `docker version`
- `docker compose version`
- Desktop Linux Context
- NVIDIA Container Runtime

### CUDA TEI 준비 실패

- `nvidia-smi`
- GPU reservation
- 모델 Cache
- 포트 `18081`
- 실제 `/embed` 결과와 벡터 차원 `1024`

### Local DB 준비 실패

- Local DB 서버 실행
- `.env.local` Host·Port·Database·User
- 전용 테스트 계정 권한
- 운영 DB가 아닌지 확인

### Office COM 실패

- PowerPoint·Excel 최초 실행과 초기 설정 완료
- Windows 대화형 사용자 세션
- 동일 Office 프로세스 잔류 여부
- COM 테스트 환경 변수
- timeout 후 worker 정리 여부

### 로그에 WARNING이 표시됨

`is_slow_stage=true`이면 처리 시간이 설정 임계값을 초과한 성능 경고입니다.
`success=true`이고 HTTP 상태가 2xx이면 실패가 아닙니다.

OCR 일부 실패나 보상 시나리오 테스트는 의도적으로 WARNING·ERROR를 발생시킬 수
있습니다. 최종 테스트 결과와 종료 코드를 함께 확인합니다.

## 11. 전체 실제 E2E 진입점

Local RAG 실행 환경을 포함한 최종 검증은 다음 스크립트를 사용합니다.

```powershell
& .\scripts\run-all-rag-tests.ps1
```

이 스크립트는 일반 품질 게이트, CUDA TEI, PyTorch CUDA, Local RAG DB, Qdrant,
Office COM, 다중 형식 OCR과 Claude E2E를 순서대로 확인합니다.

실행 순서:

1. `uv sync --frozen`
2. `ruff format --check`, `ruff check`, `mypy src tests`, 일반 `uv run pytest`
3. Docker·Compose와 Qdrant 준비
4. CUDA TEI 컨테이너와 실제 임베딩 검증
5. PyTorch CUDA 장치 검증
6. `Jipsa_Local_RAG` 연결 검증
7. PowerPoint·Excel Office COM 이미지·차트 렌더링
8. PDF, DOCX, PPTX, XLSX, TXT와 OCR 전체 파이프라인
9. 실제 Claude lookup·synthesis와 인용 검증
10. 환경 변수와 스크립트가 시작한 인프라 복원

일반 Pytest의 opt-in E2E `skip`은 정상입니다. 실제 서비스 준비 완료는 이 스크립트의
최종 종료 코드가 0이고 각 CUDA·DB·Qdrant·Office COM·Claude 단계가 실제로 수행된
경우에만 기록합니다.

## 12. 관련 문서

- [PowerShell 실제 E2E](../testing/powershell-e2e.md)
- [테스트 가이드](../testing/test-guide.md)
- [관측성과 문제 해결](observability-and-troubleshooting.md)
- [환경 변수와 비밀정보](../security/environment-and-secrets.md)
