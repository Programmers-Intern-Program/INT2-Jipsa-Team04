# PowerShell 기반 실제 PDF RAG E2E 실행 가이드

## 1. 문서 목적

이 문서는 Windows PowerShell 환경에서 다음 검증을 순서대로 실행하는
표준 절차를 정의한다.

1. Ruff 전체 포맷 검사
2. Ruff 전체 린트 검사
3. Mypy 전체 정적 타입 검사
4. 전체 Pytest 회귀 테스트
5. 실제 PDF 인제스트
6. Local RAG DB 문서·청크·색인 상태 검증
7. Qdrant 활성 Point와 payload 검증
8. 단일·복수 참조문서 기반 실제 Claude 답변 검증

모든 명령은 저장소의 `RAG` 디렉터리를 기준으로 실행한다.

---

## 2. 실제 E2E 검증 범위

`tests/e2e/test_real_pdf_rag_e2e.py`는 다음 구성요소를 실제로 사용한다.

- 텍스트 레이어를 포함한 실제 PDF 바이트
- PDF 다운로드 및 형식 검증
- pypdf 기반 PDF 텍스트 추출
- 문서 청킹
- NVIDIA CUDA 기반 Hugging Face TEI
- Local RAG MySQL 또는 MariaDB
- Qdrant VectorDB
- Anthropic Claude API
- FastAPI `POST /ingest`
- FastAPI `POST /api/v1/rag/answers`

다음 외부 HTTP 경계만 결정적인 MockTransport로 고정한다.

- AWS Backend의 파일 manifest 조회
- AWS Backend의 ingest-complete 콜백
- Presigned GET URL을 제공하는 S3 다운로드 경계

따라서 이 테스트는 단위 테스트가 아니라 실제 Local RAG 파이프라인의
종단간 검증이다.

---

## 3. 검증 항목

실제 E2E는 다음 계약을 확인한다.

### 3.1 고정 PDF와 질문

- E2E 전용 PDF 바이트와 SHA-256
- PDF 텍스트 레이어
- 질문 원문
- 단일 참조문서 범위
- 복수 참조문서 범위
- 예상 File_IDX
- 답변에 포함되어야 하는 고유 토큰

### 3.2 인제스트와 완료 콜백

- `POST /ingest` 성공
- Backend manifest 조회
- PDF 다운로드 바이트 수
- 페이지 수
- 추출 텍스트 단위 수
- 생성 청크 수
- 임베딩 모델과 차원
- `INDEXED` 처리 상태
- ingest-complete 콜백 payload
- 콜백 청크와 실제 파싱 결과 일치

### 3.3 Local RAG DB

- `RAG_Document`
- `RAG_Chunk`
- `RAG_Index_Run`
- 파일 식별자
- 사용자 식별자
- 파서 타입과 버전
- 문서 SHA-256
- 청크 content hash
- 색인 버전
- 활성 색인 상태
- 성공한 색인 실행 상태

### 3.4 Qdrant

- 사용자 범위
- 파일 범위
- `is_active=true`
- Point ID와 Chunk_ID 일치
- 1024차원 임베딩
- Local RAG DB 청크 원문과 payload 일치
- 파일명과 파일 형식
- 문서 및 청크 해시
- 페이지 번호
- 파서 버전
- 임베딩 모델
- 색인 버전

### 3.5 실제 Claude 답변

- 단일 참조문서 답변
- 복수 참조문서 종합 답변
- 실제 Claude 모델 ID
- 입력 및 출력 토큰 사용량
- PDF 고유 토큰 포함
- 선택하지 않은 문서 내용 비유입
- `[SOURCE-N]` 인용 존재
- 답변의 `[SOURCE-N]` 순서와 `sources` 순서 일치
- `sources.file_idx`가 `reference_file_idxs` 범위를 벗어나지 않음
- 응답 Chunk_ID가 실제 Local RAG DB 청크에 존재

---

## 4. 실행 전 요구 환경

다음 항목이 준비되어 있어야 한다.

- Windows PowerShell 5.1 이상
- Python 3.12
- uv
- Docker Desktop
- Docker Compose Plugin
- NVIDIA GPU
- NVIDIA Driver
- NVIDIA Container Toolkit
- CUDA 12.9 호환 환경
- Local RAG MySQL 또는 MariaDB
- Local RAG DB 스키마
- 실제 Anthropic API Key
- `.env.test`

Docker Desktop과 Local RAG DB는 스크립트가 직접 실행하지 않는다.

Docker Desktop의 Docker Engine과 Local RAG DB 서버를 먼저 실행해야 한다.

---

## 5. `.env.test` 필수 설정

저장소의 `RAG/.env.test`에 테스트 환경 설정을 작성한다.

실제 비밀번호, 토큰 및 API Key는 문서나 Git에 기록하지 않는다.

필수 설정 범주는 다음과 같다.

### 5.1 내부 인증

```dotenv
INTERNAL_TOKEN=실제_테스트용_내부_토큰
RAG_INGEST_TOKEN=실제_테스트용_인제스트_토큰
```

두 토큰은 충분히 긴 테스트 전용 임의 문자열을 사용한다.

### 5.2 Local RAG DB

```dotenv
JIPSA_RAG_DATABASE_HOST=127.0.0.1
JIPSA_RAG_DATABASE_PORT=3306
JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG
JIPSA_RAG_DATABASE_USER=테스트_DB_계정
JIPSA_RAG_DATABASE_PASSWORD=실제_테스트_DB_비밀번호
JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true
JIPSA_RAG_DATABASE_ECHO=false
```

E2E 테스트는 전용 사용자와 파일 범위의 데이터를 생성하고 삭제한다.

운영 DB 또는 공용 개발 DB를 사용하지 않는다.

### 5.3 CUDA TEI

```dotenv
JIPSA_RAG_EMBEDDING_PROVIDER=tei
JIPSA_RAG_EMBEDDING_BASE_URL=http://127.0.0.1:18081
JIPSA_RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
JIPSA_RAG_EMBEDDING_DIM=1024
```

### 5.4 Qdrant

```dotenv
JIPSA_RAG_VECTOR_DB_PROVIDER=qdrant
JIPSA_RAG_QDRANT_URL=http://127.0.0.1:6333
JIPSA_RAG_QDRANT_COLLECTION=rag_chunk_vector_qwen3_embedding_0_6b_1024
JIPSA_RAG_QDRANT_PREFER_GRPC=false
```

### 5.5 Claude

```dotenv
JIPSA_RAG_GENERATION_PROVIDER=anthropic
ANTHROPIC_API_KEY=실제_Anthropic_API_Key
JIPSA_RAG_ANTHROPIC_MODEL=사용_가능한_Claude_모델_ID
JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS=4096
JIPSA_RAG_ANTHROPIC_TIMEOUT_SECONDS=60
```

API Key를 PowerShell 출력, Git 커밋, 테스트 코드 또는 오류 메시지에
직접 기록하지 않는다.

---

## 6. 표준 실행 절차

저장소 루트가 다음 경로라고 가정한다.

```text
D:\Programming\Python\INT2-Jipsa-Team04
```

PowerShell에서 RAG 디렉터리로 이동한다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location `
    -LiteralPath 'D:\Programming\Python\INT2-Jipsa-Team04\RAG'
```

통합 E2E 스크립트를 실행한다.

```powershell
.\scripts\run-real-rag-e2e.ps1
```

이 명령은 다음 작업을 자동으로 수행한다.

1. `JIPSA_RAG_APP_ENV=test` 설정
2. UTF-8 Python 실행 환경 설정
3. 필수 파일과 명령 확인
4. `uv sync --frozen`
5. Ruff 포맷 검사
6. Ruff 린트 검사
7. Mypy 전체 검사
8. 전체 Pytest 실행
9. Docker Compose 구성 검증
10. Qdrant 실행
11. CUDA TEI 실행
12. Qdrant `/readyz` 확인
13. 실제 TEI `/embed` 확인
14. Local RAG DB 연결 확인
15. `JIPSA_RAG_RUN_E2E=1` 설정
16. 실제 PDF RAG E2E 실행
17. E2E 전용 DB 및 Qdrant 데이터 정리
18. 스크립트가 새로 실행한 컨테이너 정지
19. 기존 PowerShell 환경 변수 복원

---

## 7. 품질 검사만 실행

실제 Claude 호출 없이 Ruff, Mypy 및 전체 Pytest만 실행하려면 다음 명령을
사용한다.

```powershell
.\scripts\verify-rag-quality.ps1
```

전체 Pytest 실행 중에는 `JIPSA_RAG_RUN_E2E`가 제거된다.

따라서 실제 Claude API, CUDA TEI 및 Qdrant를 사용하는 E2E 테스트는
자동으로 skip된다.

다음 검사 중 하나라도 실패하면 스크립트가 즉시 종료된다.

```text
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

---

## 8. 이미 품질 검사를 완료한 경우

동일한 Commit 상태에서 품질 검사를 이미 통과했고 실제 E2E만 다시
실행해야 한다면 다음 옵션을 사용할 수 있다.

```powershell
.\scripts\run-real-rag-e2e.ps1 `
    -SkipQualityGate
```

이 옵션은 다음 검사를 생략한다.

- Ruff 포맷 검사
- Ruff 린트 검사
- Mypy
- 전체 Pytest

단, `uv sync --frozen`은 계속 수행한다.

코드가 변경된 이후에는 이 옵션을 사용하지 않는다.

---

## 9. E2E 종료 후 인프라 유지

실패 원인 분석을 위해 Qdrant와 TEI를 계속 실행하려면 다음 옵션을
사용한다.

```powershell
.\scripts\run-real-rag-e2e.ps1 `
    -KeepInfrastructureRunning
```

품질 검사까지 이미 완료했다면 두 옵션을 함께 사용할 수 있다.

```powershell
.\scripts\run-real-rag-e2e.ps1 `
    -SkipQualityGate `
    -KeepInfrastructureRunning
```

분석이 끝난 후 다음 기존 종료 스크립트를 실행한다.

```powershell
.\scripts\stop-local-rag.ps1
```

이 종료 스크립트는 Docker Volume을 삭제하지 않는다.

따라서 다음 데이터는 유지된다.

- Qdrant Collection 데이터
- Qdrant Snapshot
- Hugging Face 모델 Cache
- Docker 이미지

---

## 10. 정상 완료 기준

품질 검사에서는 다음 메시지가 출력되어야 한다.

```text
Ruff 포맷 검사 통과
Ruff 린트 검사 통과
Mypy 정적 타입 검사 통과
전체 Pytest 회귀 테스트 통과
Ruff, Mypy 및 전체 Pytest 검증이 모두 통과했습니다.
```

실제 E2E에서는 추가로 다음 상태를 확인한다.

```text
Qdrant /readyz 확인 성공
TEI 실제 /embed 요청 성공
Local RAG DB 연결 검증 통과
실제 PDF RAG E2E 테스트 통과
```

최종 성공 메시지는 다음과 같다.

```text
Ruff, Mypy, 전체 Pytest 및 실제 PDF RAG E2E가 모두 통과했습니다.
```

---

## 11. 일반 전체 Pytest와 실제 E2E의 차이

### 일반 전체 Pytest

```powershell
.\scripts\verify-rag-quality.ps1
```

실행 범위:

- 단위 테스트
- 통합 테스트
- API 계약 테스트
- 예외 처리 테스트
- 보안 로그 비노출 테스트
- 실제 E2E 모듈 수집
- 실제 E2E 실행은 skip

실제 Claude 비용이 발생하지 않는다.

### 실제 E2E

```powershell
.\scripts\run-real-rag-e2e.ps1
```

추가 실행 범위:

- Docker Qdrant
- Docker CUDA TEI
- 실제 Local RAG DB 쓰기와 정리
- 실제 Claude API
- 실제 검색 및 답변 생성

실제 Claude API 사용량이 발생한다.

---

## 12. 오류별 확인 사항

### 12.1 `uv`를 찾을 수 없음

확인:

```powershell
Get-Command uv
uv --version
```

uv 설치 경로가 현재 PowerShell PATH에 포함되어 있어야 한다.

### 12.2 `uv sync --frozen` 실패

다음 파일이 일치하지 않을 가능성이 있다.

- `pyproject.toml`
- `uv.lock`

의존성을 변경한 작업이 아니라면 임의로 `uv.lock`을 다시 생성하지 않고
변경 내역을 먼저 확인한다.

### 12.3 Ruff 포맷 실패

검사 명령:

```powershell
uv run ruff format --check .
```

포맷이 필요한 파일 확인 후 별도 작업에서 다음 명령을 실행할 수 있다.

```powershell
uv run ruff format .
```

품질 검사 스크립트 자체는 파일을 수정하지 않는다.

### 12.4 Ruff 린트 실패

검사 명령:

```powershell
uv run ruff check .
```

자동 수정 전에 오류 코드와 대상 파일을 확인한다.

### 12.5 Mypy 실패

검사 명령:

```powershell
uv run mypy src tests
```

다음 항목을 우선 확인한다.

- 누락된 반환 타입
- Optional 처리
- Mapping과 dict 타입 불일치
- 비동기 함수 반환 타입
- Fixture 타입
- Pydantic 모델 생성자 타입
- Qdrant SDK 반환 타입

### 12.6 Local RAG DB 연결 실패

직접 연결 테스트:

```powershell
$env:JIPSA_RAG_APP_ENV = 'test'

uv run pytest `
    tests/integration/test_database_connection.py `
    -vv
```

확인 대상:

- MySQL 또는 MariaDB 실행 여부
- DB Host와 Port
- DB 이름
- 사용자 계정
- 비밀번호
- 테이블 생성 여부
- 방화벽
- 계정 Host 권한

### 12.7 Qdrant 준비 실패

상태 확인:

```powershell
docker compose `
    --env-file .env.test `
    --file infra/qdrant/compose.yaml `
    ps
```

로그 확인:

```powershell
docker logs `
    --tail 200 `
    jipsa-qdrant
```

준비 상태 직접 확인:

```powershell
Invoke-WebRequest `
    -Method Get `
    -Uri 'http://127.0.0.1:6333/readyz' `
    -UseBasicParsing
```

### 12.8 CUDA TEI 준비 실패

컨테이너 상태 확인:

```powershell
docker inspect `
    --format '{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}' `
    jipsa-embedding
```

GPU 할당 확인:

```powershell
docker inspect `
    --format '{{json .HostConfig.DeviceRequests}}' `
    jipsa-embedding
```

최근 로그 확인:

```powershell
docker logs `
    --tail 300 `
    jipsa-embedding
```

로그에서 다음 상태가 없어야 한다.

```text
CUDA_ERROR_NO_DEVICE
Using CPU instead
Starting Qwen3 model on Cpu
```

실제 임베딩 요청 확인:

```powershell
$RequestBody = @{
    inputs = @(
        'Jipsa RAG TEI GPU readiness test.'
    )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Method Post `
    -Uri 'http://127.0.0.1:18081/embed' `
    -ContentType 'application/json' `
    -Body $RequestBody
```

### 12.9 Claude 설정 실패

확인 대상:

- `ANTHROPIC_API_KEY`
- `JIPSA_RAG_GENERATION_PROVIDER`
- `JIPSA_RAG_ANTHROPIC_MODEL`
- API Key 권한
- 모델 사용 가능 여부
- 네트워크 연결
- API 사용 한도

API Key 원문을 콘솔이나 오류 보고에 포함하지 않는다.

### 12.10 실제 Claude 답변 실패

다음 원인을 구분한다.

- 관련 청크 검색 실패
- 참조문서 범위 불일치
- Claude API 인증 실패
- Claude API 요청 제한
- Claude 응답 JSON 계약 위반
- `[SOURCE-N]` 누락
- `cited_source_ids` 불일치
- 응답 `sources` 불일치
- 단일 문서 질문에 선택하지 않은 문서 내용 유입

질문, 검색 청크, 전체 생성 프롬프트 및 Claude 응답 원문을 로그에
추가해서는 안 된다.

---

## 13. 데이터 정리 정책

실제 E2E는 일반 사용자 데이터와 충돌하지 않는 고정 식별자 범위를
사용한다.

테스트 시작 전에 이전 실패로 남은 E2E 전용 데이터를 삭제한다.

테스트 종료 시 성공 여부와 관계없이 다음 데이터를 정리한다.

- E2E 전용 Qdrant 활성 Point
- E2E 전용 Qdrant 비활성 Point
- E2E 전용 RAG Chunk
- E2E 전용 RAG Index Run
- E2E 전용 RAG Document

Qdrant Collection 자체와 Docker Volume은 삭제하지 않는다.

테스트가 PowerShell 프로세스 강제 종료나 시스템 종료로 중단되면 정리
코드가 실행되지 않을 수 있다.

이 경우 동일 E2E를 다시 실행하면 테스트 시작 전 정리 절차가 같은 전용
범위를 다시 삭제한다.

---

## 14. 보안 주의 사항

다음 값을 Git, 문서, 스크린샷 및 공유 로그에 포함하지 않는다.

- `ANTHROPIC_API_KEY`
- Local RAG DB 비밀번호
- `INTERNAL_TOKEN`
- `RAG_INGEST_TOKEN`
- Qdrant API Key
- Presigned GET URL Query String
- 실제 사용자 문서 내용
- 개인정보
- 전체 Claude 프롬프트
- Claude 응답 원문

E2E 오류를 보고할 때는 다음 정보만 공유한다.

- 실패한 테스트 이름
- 오류 코드
- 예외 타입
- HTTP 상태 코드
- 컨테이너 상태
- 민감정보를 제거한 로그
