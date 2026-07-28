# PowerShell 기반 Local RAG 실제 E2E 가이드

> **문서 상태:** Active · Windows 실제 E2E Runbook  
> **주 독자:** Local RAG 개발자, QA, 장애 분석 담당자  
> **최종 검토:** 2026-07-28  
> **주의:** 실제 Claude 호출 비용과 CUDA GPU 시간이 발생


## 1. 목적

이 문서는 Windows PowerShell에서 일반 품질 게이트와 실제 CUDA·Local DB·Qdrant·Claude
E2E를 실행하는 표준 절차를 정의합니다.

모든 명령은 저장소의 `RAG` 디렉터리를 기준으로 실행합니다.

## 2. 실행 경로

| 목적 | 스크립트 |
|---|---|
| Ruff·Mypy·일반 Pytest | `scripts/verify-rag-quality.ps1` |
| 실제 PDF 중심 E2E | `scripts/run-real-rag-e2e.ps1` |
| 고정 5개 형식·OCR E2E | `scripts/run-issue-123-e2e.ps1` |
| 전체 실제 E2E | `scripts/run-all-rag-tests.ps1` |
| Local RAG 실행 | `scripts/start-local-rag.ps1` |
| Qdrant·TEI 정지 | `scripts/stop-local-rag.ps1` |

## 3. 실제 E2E 구성요소

- PDF, DOCX, PPTX, XLSX, TXT
- 문서 내부 이미지와 스캔 PDF
- CUDA EasyOCR
- CUDA TEI
- Local RAG MySQL 또는 MariaDB
- Qdrant VectorDB
- Anthropic Claude API
- FastAPI `POST /ingest`
- FastAPI `POST /api/v1/chunks/search`
- FastAPI `POST /api/v1/rag/answers`

AWS Backend manifest, ingest-complete와 Presigned GET URL 경계는 결정적 테스트 대역으로
고정할 수 있습니다. 테스트는 AWS Backend를 실행하거나 수정하지 않습니다.

## 4. 환경 파일

```text
.env.local
.env.development
.env.test
.env.example
```

- `.env.test`: 일반 단위·통합·회귀 테스트
- `.env.local`: 실제 CUDA, DB, Qdrant, Claude E2E
- `.env.example`: 변수 설명과 안전한 예시
- `.env.development`: 개발 프로필이 필요한 경우

실제 E2E 스크립트는 `.env.local`을 현재 PowerShell 프로세스에만 임시 주입하고 종료 시
원래 환경을 복원합니다.

## 5. 필수 `.env.local` 범주

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

비밀값은 PowerShell 출력, Git, 테스트 Assertion과 오류 메시지에 기록하지 않습니다.

## 6. PowerShell 인코딩

한글 주석과 출력 문자열을 포함하는 `.ps1`은 Windows PowerShell 5.1 호환을 위해
UTF-8 with BOM을 유지합니다.

BOM:

```text
EF-BB-BF
```

## 7. 일반 품질 게이트

```powershell
.\scripts\verify-rag-quality.ps1
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

## 8. 고정 다중 형식·OCR E2E

```powershell
.\scripts\run-issue-123-e2e.ps1
```

검증 대상:

- 5개 지원 형식
- 이미지·스캔 페이지 OCR
- 원본 Source Locator
- Local RAG DB·Qdrant
- lookup·synthesis
- 인용 순서
- 재인제스트·보상
- 자원 정리와 민감정보 로그

품질 게이트 중복 생략:

```powershell
.\scripts\run-issue-123-e2e.ps1 -SkipQualityGate
```

장애 분석용 인프라 유지:

```powershell
.\scripts\run-issue-123-e2e.ps1 -KeepInfrastructureRunning
```

## 9. 전체 실제 E2E

```powershell
.\scripts\run-all-rag-tests.ps1
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
.\scripts\run-all-rag-tests.ps1 -SkipQualityGate
.\scripts\run-all-rag-tests.ps1 -KeepInfrastructureRunning
```

## 10. 실행 정책

시스템 전체 실행 정책을 변경하지 않고 현재 프로세스에만 Bypass를 적용할 수 있습니다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-all-rag-tests.ps1'
```

## 11. E2E 안전장치

- `JIPSA_RAG_APP_ENV=test`에서만 테스트 데이터 정리를 허용합니다.
- `JIPSA_RAG_RUN_E2E=1`이 없으면 실제 E2E 모듈을 skip합니다.
- 테스트 전용 사용자와 명시적 `File_IDX`만 삭제합니다.
- 기존에 실행 중이던 컨테이너는 종료하지 않습니다.
- 스크립트가 주입한 환경 변수는 종료 시 복원합니다.
- Assertion은 질문, 청크, OCR, 프롬프트와 응답 원문을 출력하지 않습니다.

## 12. 성공 기준

일반 품질 게이트와 실제 E2E는 별도입니다.

- Ruff·Mypy·일반 Pytest 통과만으로 실제 CUDA E2E 통과를 주장하지 않습니다.
- skip된 E2E는 실제 서비스 검증 완료로 계산하지 않습니다.
- 전체 실제 E2E의 최종 종료 코드가 0인지 확인합니다.
- `-KeepInfrastructureRunning` 사용 시 장애 분석 후 자원을 수동 정리합니다.

## 13. 관련 문서

- [테스트 절차 요약](test-guide.md)
- [Local RAG 실행 절차](../operations/local-runtime.md)
- [재인제스트와 보상 처리](../operations/ingest-recovery-policy.md)
- [환경 변수와 민감정보 관리](../security/environment-and-secrets.md)
