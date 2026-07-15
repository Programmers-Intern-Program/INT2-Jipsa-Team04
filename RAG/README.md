# Jipsa RAG Service

`jipsa-rag`는 애플리케이션 서버로부터 사용자 업로드 파일의 식별 정보와
Presigned GET URL을 전달받아 파일을 처리하고, 문서 파싱, 청킹,
임베딩 생성, VectorDB 저장 및 근거 기반 질의응답으로 확장하기 위한
FastAPI 기반 RAG 서비스입니다.

RAG 서버는 AWS Access Key, Secret Access Key 또는 Session Token을
사용하여 S3에 직접 접근하지 않습니다.

S3 접근과 Presigned GET URL 발급은 EC2에서 실행되는 애플리케이션 서버가
IAM Role을 이용하여 담당합니다.

RAG 서버는 애플리케이션 서버가 제공한 Presigned GET URL을 일반적인
HTTPS 파일 접근 URL로 사용하여 파일을 다운로드합니다.

전체 파일 연동 방향은 다음과 같습니다.

```text
사용자 파일 업로드
        ↓
애플리케이션 서버에서 S3에 파일 저장
        ↓
애플리케이션 서버가 IAM Role로 Presigned GET URL 발급
        ↓
애플리케이션 서버가 RAG 서버에 파일 처리 요청 전달
        ↓
RAG 서버가 Presigned GET URL로 파일 다운로드
        ↓
파일 유효성 검증 및 메타데이터 생성
        ↓
후속 문서 파싱, 청킹 및 임베딩 처리
```

현재 단계에서는 FastAPI 애플리케이션 기반 구조, 환경별 설정,
Local RAG MySQL 비동기 연결, 공통 응답 및 예외 처리,
Request ID 기반 로깅, Health Check, 테스트 및 코드 품질 도구를 제공합니다.

## 파일 연동 원칙

RAG 서버의 파일 연동은 다음 원칙을 따릅니다.

- RAG 서버는 AWS 자격 증명을 보관하지 않습니다.
- RAG 서버는 `boto3`를 사용하여 S3에 직접 접근하지 않습니다.
- 애플리케이션 서버가 S3 접근 권한을 관리합니다.
- 애플리케이션 서버가 제한된 유효 시간을 가진 Presigned GET URL을 발급합니다.
- RAG 서버는 HTTP 클라이언트로 Presigned GET URL에 접근합니다.
- S3 Object Key는 파일 다운로드 수단이 아니라 식별 및 검증 정보로 사용합니다.
- Presigned GET URL과 URL Query String은 로그에 기록하지 않습니다.
- 만료된 URL과 권한 오류는 자동 재시도 대상에서 제외합니다.

## 현재 구현 범위

- `uv` 및 Python 3.12 기반 `src` 패키지 구조
- FastAPI 애플리케이션 팩토리 구성
- FastAPI lifespan 기반 시작 및 종료 처리
- API v1 Router 구성
- `pydantic-settings` 기반 환경별 설정 관리
- `.env.local`, `.env.development`, `.env.test` 환경 분리
- 필수 환경 변수 타입 및 형식 검증
- DB 비밀번호의 로그 및 문자열 출력 방지
- SQLAlchemy AsyncIO와 AsyncMy 기반 MySQL 비동기 연결
- 요청 단위 `AsyncSession` 생성
- 요청 실패 시 데이터베이스 트랜잭션 Rollback
- 애플리케이션 시작 시 선택적 DB 연결 검사
- 애플리케이션 종료 시 DB 연결 풀 정리
- 공통 API 성공 응답 구조
- 공통 API 오류 응답 구조
- 애플리케이션 공통 예외 정의
- FastAPI 전역 예외 처리기
- 요청 검증 오류의 공통 응답 변환
- 존재하지 않는 API의 공통 404 응답
- 내부 예외 정보의 외부 응답 노출 방지
- Request ID 생성 및 전달
- `X-Request-ID` 응답 헤더 생성
- 요청 시작, 완료 및 실패 로그
- 구조화된 JSON 로깅
- Liveness Health Check
- Readiness Health Check
- Pytest, Ruff, Mypy 기반 품질 검사
- HTTPX2 기반 테스트 및 향후 애플리케이션 서버 통신 준비
- `uv run jipsa-rag` 실행 명령

## 후속 구현 예정

- Local RAG ORM 모델
- Local RAG Repository
- Alembic 마이그레이션 환경
- 애플리케이션 서버와 RAG 서버 사이의 파일 처리 요청 API
- 애플리케이션 서버 인증 및 인가
- Presigned GET URL 요청 및 수신
- Presigned GET URL 보안 검증
- Presigned GET URL 기반 파일 다운로드
- HTTP Streaming 기반 대용량 파일 처리
- 다운로드 연결 및 응답 Timeout 처리
- 일시적인 네트워크 오류 재시도
- 다운로드 중 최대 파일 크기 제한
- 임시 파일 생성 및 정리
- 파일 확장자 검증
- MIME Type 검증
- Magic Byte 기반 실제 파일 형식 검증
- 빈 파일 및 손상된 파일 탐지
- SHA-256 Checksum 생성
- 파일 기본 메타데이터 생성
- 문서별 선택 메타데이터 생성
- 파일 처리 실행 이력 저장
- 동일 파일과 동일 버전에 대한 멱등성 보장
- PDF 텍스트 추출
- 문서 청킹
- 청크 메타데이터 생성
- 임베딩 생성
- VectorDB Upsert
- 유사도 검색
- 검색 결과 기반 RAG 질의응답

현재 단계에서는 다음 라이브러리를 추가하지 않습니다.

- LangChain
- LlamaIndex
- Celery
- Redis
- PyTorch
- Sentence Transformers
- VectorDB Client
- PDF Parser
- boto3
- boto3-stubs

각 기능 구현 단계에서 실제로 필요한 라이브러리만 추가합니다.

## 요구 환경

- Python 3.12
- uv
- MySQL 8.0 이상 또는 MariaDB 10.6 이상

## 설치

`RAG` 디렉터리에서 다음 명령을 실행합니다.

```powershell
uv sync
```

`uv`는 다음 항목을 동기화합니다.

- 프로젝트 런타임 의존성
- 개발 및 테스트 의존성
- `uv.lock`에 고정된 정확한 패키지 버전
- 프로젝트 실행 스크립트

## 환경 변수

환경별 dotenv 파일을 사용합니다.

```text
.env.local
.env.development
.env.test
```

환경별 파일은 실제 비밀번호와 내부 주소를 포함할 수 있으므로
Git에 커밋하지 않습니다.

Git에는 환경 변수 작성 기준을 제공하는 `.env.example`만 포함합니다.

### 기본 실행 환경

`JIPSA_RAG_APP_ENV`가 지정되지 않으면 `local` 환경을 사용합니다.

```powershell
$env:JIPSA_RAG_APP_ENV = 'local'
```

다음 파일을 불러옵니다.

```text
.env.local
```

### 개발 환경

```powershell
$env:JIPSA_RAG_APP_ENV = 'development'
```

다음 파일을 불러옵니다.

```text
.env.development
```

### 테스트 환경

```powershell
$env:JIPSA_RAG_APP_ENV = 'test'
```

다음 파일을 불러옵니다.

```text
.env.test
```

### 보안 주의 사항

다음 값은 Git에 커밋하지 않습니다.

- 실제 Local RAG MySQL 비밀번호
- 내부 애플리케이션 서버 인증 정보
- 내부 API Key
- Presigned GET URL
- Presigned GET URL의 Query String
- 사용자 업로드 파일 내용
- 개인정보
- 토큰 및 세션 정보

RAG 서버는 다음 AWS 자격 증명을 사용하지 않습니다.

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
```

따라서 위 값은 RAG 환경 변수 파일에 추가하지 않습니다.

## 서버 실행

### 프로젝트 실행 명령 사용

다음 명령을 권장합니다.

```powershell
uv run jipsa-rag
```

이 명령은 `pyproject.toml`에 등록된 다음 Entry Point를 실행합니다.

```toml
[project.scripts]
jipsa-rag = "jipsa_rag.main:main"
```

`main()` 함수는 현재 환경 설정에서 다음 값을 읽어 Uvicorn을 실행합니다.

- `JIPSA_RAG_HOST`
- `JIPSA_RAG_PORT`
- `JIPSA_RAG_DEBUG`

`JIPSA_RAG_DEBUG=true`이면 Uvicorn의 소스 변경 감지 기능을 활성화합니다.

### Uvicorn 직접 실행

Uvicorn 명령을 직접 사용할 수도 있습니다.

```powershell
uv run uvicorn jipsa_rag.main:app `
    --reload `
    --host 127.0.0.1 `
    --port 8000
```

RAG 서버는 로컬에서만 실행하므로 기본 Host는 다음과 같습니다.

```text
127.0.0.1
```

외부 네트워크에 공개할 필요가 없는 로컬 RAG 서버에서
`0.0.0.0`을 기본값으로 사용하지 않습니다.

이번 의존성 구성에서는 기존 `httpx` 설치를 피하기 위해
`fastapi[standard]`를 사용하지 않습니다.

따라서 서버 실행은 `fastapi dev` 명령이 아니라
명시적으로 설치한 Uvicorn 또는 `jipsa-rag` 스크립트를 사용합니다.

## 테스트

### 전체 테스트

테스트 환경을 선택합니다.

```powershell
$env:JIPSA_RAG_APP_ENV = 'test'
```

전체 테스트를 실행합니다.

```powershell
uv run pytest -v
```

간단한 출력으로 실행하려면 다음 명령을 사용합니다.

```powershell
uv run pytest
```

### 데이터베이스 통합 테스트

다음 테스트는 실제 Local RAG MySQL 연결을 확인합니다.

```text
tests/integration/test_database_connection.py
```

테스트는 데이터를 생성, 수정 또는 삭제하지 않으며 다음 쿼리만 실행합니다.

```sql
SELECT 1;
```

통합 테스트를 실행하기 전 `.env.test`의 다음 값이 올바른지 확인합니다.

```text
JIPSA_RAG_DATABASE_HOST
JIPSA_RAG_DATABASE_PORT
JIPSA_RAG_DATABASE_NAME
JIPSA_RAG_DATABASE_USER
JIPSA_RAG_DATABASE_PASSWORD
```

데이터베이스 통합 테스트만 실행하려면 다음 명령을 사용합니다.

```powershell
uv run pytest tests/integration -v
```

단위 테스트만 실행하려면 다음 명령을 사용합니다.

```powershell
uv run pytest tests/unit -v
```

### 테스트 커버리지

```powershell
uv run pytest `
    --cov=src/jipsa_rag `
    --cov-report=term-missing
```

## 코드 검사

### 자동 포맷

```powershell
uv run ruff format .
```

### 포맷 검사

```powershell
uv run ruff format --check .
```

### 자동 수정 가능한 린트 오류 수정

```powershell
uv run ruff check --fix .
```

### 린트 검사

```powershell
uv run ruff check .
```

### 정적 타입 검사

소스 코드와 테스트 코드를 모두 검사합니다.

```powershell
uv run mypy src tests
```

## 의존성 확인

### 전체 의존성 트리

```powershell
uv tree
```

### HTTP 클라이언트 설치 상태

```powershell
uv run python -c "import httpx2; print('httpx2:', httpx2.__version__)"
```

### 기존 HTTPX와 HTTPX2 설치 여부

```powershell
uv run python -c "import importlib.util; print('httpx2:', importlib.util.find_spec('httpx2') is not None); print('httpx:', importlib.util.find_spec('httpx') is not None)"
```

예상 결과:

```text
httpx2: True
httpx: False
```

### boto3 제거 여부

RAG 서버는 S3에 직접 접근하지 않으므로 다음 패키지가
의존성 트리에 존재하지 않아야 합니다.

- boto3
- boto3-stubs
- botocore
- s3transfer

PowerShell에서 다음 명령으로 확인합니다.

```powershell
uv tree | Select-String -Pattern 'boto3|botocore|s3transfer'
```

아무 내용도 출력되지 않으면 정상입니다.

## API 주소

기본 로컬 포트가 `8000`인 경우 다음 주소를 사용합니다.

- Liveness: `http://127.0.0.1:8000/api/v1/health/live`
- Readiness: `http://127.0.0.1:8000/api/v1/health/ready`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

### Liveness

```http
GET /api/v1/health/live
```

FastAPI 프로세스가 정상적으로 실행 중인지 확인합니다.

데이터베이스와 같은 외부 의존성은 검사하지 않습니다.

### Readiness

```http
GET /api/v1/health/ready
```

RAG 서버가 요청을 처리하기 위해 필요한 Local RAG MySQL 연결 상태를 확인합니다.

데이터베이스 연결이 정상인 경우 `200 OK`를 반환합니다.

데이터베이스에 연결할 수 없는 경우 내부 DB 정보는 노출하지 않고
공통 오류 응답과 함께 `503 Service Unavailable`을 반환합니다.

## 프로젝트 구조

다음 구조는 Git에 포함된 파일과 로컬에서만 사용하는 환경 파일을 함께 나타냅니다.

```text
RAG/
├── .env.development             # 로컬 전용, Git 제외
├── .env.example                 # 환경 변수 작성 예시
├── .env.local                   # 로컬 전용, Git 제외
├── .env.test                    # 로컬 전용, Git 제외
├── .gitignore
├── .python-version
├── pyproject.toml
├── README.md
├── uv.lock
│
├── src/
│   └── jipsa_rag/
│       ├── __init__.py
│       ├── main.py
│       │
│       ├── api/
│       │   ├── __init__.py
│       │   └── v1/
│       │       ├── __init__.py
│       │       ├── router.py
│       │       └── endpoints/
│       │           ├── __init__.py
│       │           └── health.py
│       │
│       ├── application/
│       │   └── __init__.py
│       │
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py
│       │   ├── error_codes.py
│       │   ├── exception_handlers.py
│       │   ├── exceptions.py
│       │   ├── logging.py
│       │   ├── middleware.py
│       │   └── request_context.py
│       │
│       ├── domain/
│       │   └── __init__.py
│       │
│       ├── infrastructure/
│       │   ├── __init__.py
│       │   └── database/
│       │       ├── __init__.py
│       │       └── session.py
│       │
│       └── schemas/
│           ├── __init__.py
│           ├── common.py
│           └── health.py
│
└── tests/
    ├── conftest.py
    │
    ├── integration/
    │   └── test_database_connection.py
    │
    └── unit/
        ├── api/
        │   └── v1/
        │       └── endpoints/
        │           └── test_health.py
        │
        ├── core/
        │   ├── test_config.py
        │   └── test_exception_handlers.py
        │
        └── infrastructure/
            └── database/
                └── test_session.py
```

## 계층별 책임

### `api`

- FastAPI Router 구성
- HTTP 요청 및 응답 처리
- 요청값 검증
- HTTP 상태 코드 관리
- Application 계층 호출

비즈니스 로직이나 SQLAlchemy 쿼리를 직접 작성하지 않습니다.

### `application`

- 파일 처리 유스케이스
- 애플리케이션 서버 파일 정보 조회 흐름
- Presigned GET URL 수신 및 파일 다운로드 흐름
- 문서 처리 단계 조합
- 처리 결과 및 상태 변경 흐름

AWS SDK를 사용한 직접 S3 접근 로직을 작성하지 않습니다.

### `domain`

- 파일, 문서, 청크 및 처리 작업의 핵심 모델
- 파일 및 문서 처리 상태
- 파싱 및 색인 상태
- 중복 처리 방지 규칙
- 외부 라이브러리에 의존하지 않는 비즈니스 규칙

### `infrastructure`

- SQLAlchemy 및 Local RAG MySQL
- 애플리케이션 서버 HTTP Client
- Presigned GET URL 파일 다운로드 Client
- 임시 파일 시스템
- 문서 Parser
- Embedding Model
- VectorDB
- 외부 시스템 연동 구현

AWS Access Key나 boto3를 이용한 직접 S3 Client를 구성하지 않습니다.

### `core`

- 환경 설정
- 애플리케이션 공통 설정
- 공통 오류 코드
- 공통 예외
- 전역 예외 처리
- Request ID 관리
- 요청 추적 Middleware
- 구조화된 로깅
- 전역 정책

### `schemas`

- FastAPI 요청 모델
- FastAPI 응답 모델
- 공통 성공 및 오류 응답
- 외부 서버 통신 DTO
- Health Check 응답 모델
