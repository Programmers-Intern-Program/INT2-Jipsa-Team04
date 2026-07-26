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
- `cited_source_ids`와 실제 인용 일치
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
- Docker GPU 실행 환경
- CUDA 12.9 런타임을 사용하는 TEI 컨테이너
- Local RAG MySQL 또는 MariaDB
- Local RAG DB 스키마
- 실제 Anthropic API Key
- `.env.local`
- `.env.test`

Docker Desktop과 Local RAG DB는 스크립트가 직접 실행하지 않는다.

Docker Desktop의 Docker Engine과 Local RAG DB 서버를 먼저 실행해야 한다.

---

## 5. 기존 환경 파일 사용 원칙

실제 E2E를 위해 새로운 환경 파일을 추가하지 않는다.

기존 환경 파일은 다음 역할을 유지한다.

```text
.env.local
.env.development
.env.test
.env.example
```

### 5.1 `.env.test`

`.env.test`는 일반 단위·통합 테스트의 격리 환경이다.

이 파일에 다음 Mock 전용 주소가 있어도 실제 E2E를 위해 수정하지 않는다.

```dotenv
JIPSA_RAG_EMBEDDING_BASE_URL=http://embedding.test
JIPSA_RAG_QDRANT_URL=http://qdrant.test:6333
```

일반 `uv run pytest`와 `verify-rag-quality.ps1`은 이 Mock 계약을 사용한다.

### 5.2 `.env.local`

실제 E2E의 다음 설정은 기존 `.env.local`에서 읽는다.

- Local RAG DB
- CUDA TEI
- Qdrant
- Anthropic Claude
- 내부 인증 토큰
- 로컬 애플리케이션 설정

`run-real-rag-e2e.ps1`은 품질 게이트가 끝난 뒤 `.env.local`을 현재
PowerShell 프로세스 환경으로 임시 로드한다.

그다음 `JIPSA_RAG_APP_ENV=test`를 다시 고정한다.

이에 따라 Pydantic은 test 프로필을 유지하면서도, 더 높은 우선순위인
프로세스 환경 변수에서 실제 Local RAG 인프라 값을 읽는다.

스크립트 종료 시 임시로 주입한 값은 모두 원래 상태로 복원된다.

### 5.3 Local RAG DB

`.env.local`에 다음 범주의 실제 로컬 값이 있어야 한다.

```dotenv
JIPSA_RAG_DATABASE_HOST=127.0.0.1
JIPSA_RAG_DATABASE_PORT=3306
JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG
JIPSA_RAG_DATABASE_USER=로컬_DB_계정
JIPSA_RAG_DATABASE_PASSWORD=실제_로컬_DB_비밀번호
JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true
JIPSA_RAG_DATABASE_ECHO=false
```

E2E 테스트는 전용 사용자와 파일 범위의 데이터를 생성하고 삭제한다.

운영 DB 또는 팀 공용 DB를 사용하지 않는다.

### 5.4 CUDA TEI

`.env.local`은 다음 실제 루프백 주소를 사용해야 한다.

```dotenv
JIPSA_RAG_EMBEDDING_PROVIDER=tei
JIPSA_RAG_EMBEDDING_BASE_URL=http://127.0.0.1:18081
JIPSA_RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
JIPSA_RAG_EMBEDDING_DIM=1024
JIPSA_RAG_EMBEDDING_BATCH_SIZE=32
JIPSA_RAG_EMBEDDING_DISTANCE=cosine
JIPSA_RAG_EMBEDDING_TIMEOUT_SECONDS=60
```

### 5.5 Qdrant

`.env.local`은 다음 실제 루프백 주소를 사용해야 한다.

```dotenv
JIPSA_RAG_VECTOR_DB_PROVIDER=qdrant
JIPSA_RAG_QDRANT_URL=http://127.0.0.1:6333
JIPSA_RAG_QDRANT_COLLECTION=rag_chunk_vector_qwen3_embedding_0_6b_1024
JIPSA_RAG_QDRANT_GRPC_PORT=6334
JIPSA_RAG_QDRANT_PREFER_GRPC=false
JIPSA_RAG_QDRANT_TIMEOUT_SECONDS=10
```

### 5.6 Claude

`.env.local`에는 실제 호출 가능한 Claude 설정이 있어야 한다.

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

## 6. PowerShell 스크립트 인코딩

다음 스크립트는 한글 주석과 출력 문자열을 포함한다.

```text
scripts/run-real-rag-e2e.ps1
scripts/verify-rag-quality.ps1
```

Windows PowerShell 5.1에서 안전하게 파싱하려면 두 파일을
`UTF-8 with BOM`으로 저장한다.

BOM 값은 다음과 같아야 한다.

```text
EF-BB-BF
```

PowerShell 7만 사용하는 경우에도 저장소 내 Windows PowerShell 5.1
호환성을 위해 같은 인코딩을 유지한다.

---

## 7. 표준 실행 절차

PowerShell에서 RAG 디렉터리로 이동한다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location `
    -LiteralPath 'D:\Programming\INT2-Jipsa-Team04\RAG'
```

실행 정책을 시스템 전체에서 변경하지 않고 현재 실행에만 우회하려면
다음 명령을 사용한다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-real-rag-e2e.ps1'
```

이 명령은 다음 작업을 자동으로 수행한다.

1. `JIPSA_RAG_APP_ENV=test` 설정
2. UTF-8 Python 실행 환경 설정
3. 필수 파일과 명령 확인
4. Ruff, Mypy 및 전체 Pytest 품질 게이트 실행
5. `.env.local` 실제 인프라 설정을 현재 프로세스에 임시 로드
6. `JIPSA_RAG_APP_ENV=test` 재고정
7. 실제 Qdrant, CUDA TEI, DB 및 Claude 설정 검증
8. Docker Compose 구성 검증
9. Qdrant 실행
10. CUDA TEI 실행
11. Qdrant `/readyz` 확인
12. 실제 TEI `/embed` 확인
13. Local RAG DB 연결 확인
14. `JIPSA_RAG_RUN_E2E=1` 설정
15. 실제 PDF RAG E2E 실행
16. E2E 전용 DB 및 Qdrant 데이터 정리
17. 스크립트가 새로 실행한 컨테이너 정지
18. 기존 PowerShell 환경 변수 복원

---

## 8. 품질 검사만 실행

실제 Claude 호출 없이 Ruff, Mypy 및 전체 Pytest만 실행하려면 다음 명령을
사용한다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\verify-rag-quality.ps1'
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

## 9. 이미 품질 검사를 완료한 경우

동일한 Commit 상태에서 품질 검사를 이미 통과했고 실제 E2E만 다시
실행해야 한다면 다음 옵션을 사용할 수 있다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-real-rag-e2e.ps1' `
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

## 10. E2E 종료 후 인프라 유지

실패 원인 분석을 위해 Qdrant와 TEI를 계속 실행하려면 다음 옵션을
사용한다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-real-rag-e2e.ps1' `
    -KeepInfrastructureRunning
```

품질 검사까지 이미 완료했다면 두 옵션을 함께 사용할 수 있다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-real-rag-e2e.ps1' `
    -SkipQualityGate `
    -KeepInfrastructureRunning
```

분석이 끝난 후 다음 기존 종료 스크립트를 실행한다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\stop-local-rag.ps1'
```

이 종료 스크립트는 Docker Volume을 삭제하지 않는다.

따라서 다음 데이터는 유지된다.

- Qdrant Collection 데이터
- Qdrant Snapshot
- Hugging Face 모델 Cache
- Docker 이미지

---

## 11. 정상 완료 기준

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
실제 Local RAG 환경 변수 로드 완료
실제 Qdrant, CUDA TEI, Local RAG DB 및 Claude 설정 검증 통과
Qdrant /readyz 확인 성공
TEI 실제 /embed 요청 성공
Local RAG DB 연결 검증 통과
```

품질 게이트를 포함한 최종 성공 메시지는 다음과 같다.

```text
Ruff, Mypy, 전체 Pytest 및 실제 PDF RAG E2E가 모두 통과했습니다.
```

`-SkipQualityGate`를 사용한 경우에는 품질 게이트를 생략했다는 문구가
별도로 출력된다.

---

## 12. 일반 전체 Pytest와 실제 E2E의 차이

### 일반 전체 Pytest

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\verify-rag-quality.ps1'
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
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-real-rag-e2e.ps1'
```

추가 실행 범위:

- Docker Qdrant
- Docker CUDA TEI
- 실제 Local RAG DB 쓰기와 정리
- 실제 Claude API
- 실제 검색 및 답변 생성

실제 Claude API 사용량이 발생한다.

---

## 13. 오류별 확인 사항

### 13.1 PowerShell ParserError

먼저 UTF-8 BOM과 문법을 검사한다.

```powershell
$ScriptPaths = @(
    '.\scripts\run-real-rag-e2e.ps1',
    '.\scripts\verify-rag-quality.ps1'
)

foreach ($RelativePath in $ScriptPaths) {
    $ResolvedPath = (
        Resolve-Path -LiteralPath $RelativePath
    ).Path

    $Bytes = [System.IO.File]::ReadAllBytes(
        $ResolvedPath
    )

    $Bom = [System.BitConverter]::ToString(
        $Bytes[0..2]
    )

    $Tokens = $null
    $ParseErrors = $null

    [System.Management.Automation.Language.Parser]::ParseFile(
        $ResolvedPath,
        [ref] $Tokens,
        [ref] $ParseErrors
    ) | Out-Null

    Write-Host "파일: $RelativePath"
    Write-Host "BOM: $Bom"

    if ($Bom -ne 'EF-BB-BF') {
        throw "UTF-8 BOM이 없습니다: $RelativePath"
    }

    if ($ParseErrors.Count -gt 0) {
        $ParseErrors |
            Format-List `
                Message,
                Extent

        throw "PowerShell 문법 검사 실패: $RelativePath"
    }

    Write-Host 'PowerShell 문법 검사 통과'
}
```

### 13.2 `uv`를 찾을 수 없음

확인:

```powershell
Get-Command uv
uv --version
```

uv 설치 경로가 현재 PowerShell PATH에 포함되어 있어야 한다.

### 13.3 `uv sync --frozen` 실패

다음 파일이 일치하지 않을 가능성이 있다.

- `pyproject.toml`
- `uv.lock`

의존성을 변경한 작업이 아니라면 임의로 `uv.lock`을 다시 생성하지 않고
변경 내역을 먼저 확인한다.

### 13.4 Ruff 포맷 실패

검사 명령:

```powershell
uv run ruff format --check .
```

포맷이 필요한 파일 확인 후 다음 명령을 실행한다.

```powershell
uv run ruff format .
```

품질 검사 스크립트 자체는 파일을 수정하지 않는다.

### 13.5 Ruff 린트 실패

검사 명령:

```powershell
uv run ruff check .
```

자동 수정 전에 오류 코드와 대상 파일을 확인한다.

### 13.6 Mypy 실패

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

### 13.7 Local RAG DB 연결 실패

실제 E2E 스크립트의 DB 사전 검사를 실행한다.

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-real-rag-e2e.ps1' `
    -SkipQualityGate `
    -KeepInfrastructureRunning
```

확인 대상:

- MySQL 또는 MariaDB 실행 여부
- `.env.local`의 DB Host와 Port
- DB 이름
- 사용자 계정
- 비밀번호
- 테이블 생성 여부
- 방화벽
- 계정 Host 권한

### 13.8 Qdrant 준비 실패

상태 확인:

```powershell
docker compose `
    --env-file .env.local `
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

### 13.9 CUDA TEI 준비 실패

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

### 13.10 Claude 설정 실패

확인 대상:

- `ANTHROPIC_API_KEY`
- `JIPSA_RAG_GENERATION_PROVIDER`
- `JIPSA_RAG_ANTHROPIC_MODEL`
- API Key 권한
- 모델 사용 가능 여부
- 네트워크 연결
- API 사용 한도

API Key 원문을 콘솔이나 오류 보고에 포함하지 않는다.

### 13.11 실제 Claude 답변 실패

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

## 14. 데이터 정리 정책

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

## 15. 보안 주의 사항

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
