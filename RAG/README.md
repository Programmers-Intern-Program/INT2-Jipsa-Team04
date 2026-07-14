# Jipsa RAG Service

`jipsa-rag`는 사용자 업로드 파일을 S3에서 조회하고 문서 파싱, 청킹,
임베딩 생성, VectorDB 저장 및 근거 기반 질의응답으로 확장하기 위한
FastAPI 기반 RAG 서비스입니다.

현재 단계에서는 FastAPI 애플리케이션 기반 구조, 환경별 설정,
Local RAG MySQL 비동기 연결, Health Check, 테스트 및 코드 품질 도구를 제공합니다.

## 현재 구현 범위

- `uv` 및 Python 3.12 기반 `src` 패키지 구조
- FastAPI 애플리케이션 팩토리와 lifespan 관리
- API v1 Router 구성
- `pydantic-settings` 기반 환경별 설정 관리
- `.env.local`, `.env.development`, `.env.test` 환경 분리
- SQLAlchemy AsyncIO와 AsyncMy 기반 MySQL 비동기 연결
- 애플리케이션 시작 시 선택적 DB 연결 검사
- 애플리케이션 종료 시 DB 연결 풀 정리
- Liveness Health Check
- Pytest, Ruff, Mypy 기반 품질 검사
- HTTPX2 기반 테스트 및 향후 애플리케이션 서버 통신 준비

## 후속 구현 예정

- Local RAG ORM 모델과 Repository
- Alembic 마이그레이션 환경
- S3 객체 조회 및 다운로드
- 다운로드 파일 유효성 검사
- 임시 파일 정리
- AWS 애플리케이션 서버 API 연동
- PDF 텍스트 추출
- 문서 청킹
- 임베딩 생성
- VectorDB Upsert
- 문서 메타데이터 생성 및 저장
- 검색 및 근거 기반 RAG 질의응답

현재 단계에서는 다음 라이브러리를 추가하지 않습니다.

- LangChain
- LlamaIndex
- Celery
- Redis
- PyTorch
- Sentence Transformers
- VectorDB Client
- PDF Parser

각 기능 구현 단계에서 실제로 필요한 라이브러리만 추가합니다.

## 요구 환경

- Python 3.12
- uv
- MySQL 8.0 이상 또는 MariaDB 10.6 이상

## 설치

프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
uv sync
```

`uv`는 프로젝트 기본 의존성과 개발 dependency group을 동기화합니다.

## 환경 변수

환경별 dotenv 파일을 사용합니다.

```text
.env.local
.env.development
.env.test
```

기본 실행 환경은 `local`입니다.

PowerShell에서 실행 환경을 명시하려면 다음과 같이 설정합니다.

```powershell
$env:JIPSA_RAG_APP_ENV = 'local'
```

개발 환경:

```powershell
$env:JIPSA_RAG_APP_ENV = 'development'
```

테스트 환경:

```powershell
$env:JIPSA_RAG_APP_ENV = 'test'
```

실제 비밀번호, AWS 자격 증명, 내부 API 키는 Git에 커밋하지 않습니다.

## 서버 실행

로컬 개발 서버는 다음 명령으로 실행합니다.

```powershell
uv run uvicorn jipsa_rag.main:app `
    --reload `
    --host 0.0.0.0 `
    --port 8000
```

이번 의존성 구성에서는 기존 `httpx` 설치를 피하기 위해
`fastapi[standard]`를 사용하지 않습니다.

따라서 서버 실행은 `fastapi dev` 명령이 아니라
명시적으로 설치한 Uvicorn을 사용합니다.

## 테스트

전체 테스트:

```powershell
uv run pytest -v
```

간단한 테스트:

```powershell
uv run pytest
```

테스트 커버리지:

```powershell
uv run pytest `
    --cov=src/jipsa_rag `
    --cov-report=term-missing
```

## 코드 검사

포맷 검사:

```powershell
uv run ruff format --check .
```

린트 검사:

```powershell
uv run ruff check .
```

정적 타입 검사:

```powershell
uv run mypy src
```

자동 포맷:

```powershell
uv run ruff format .
```

자동 수정 가능한 린트 오류 수정:

```powershell
uv run ruff check --fix .
```

## 의존성 확인

전체 의존성 트리:

```powershell
uv tree
```

HTTP 클라이언트 설치 상태:

```powershell
uv run python -c "import httpx2; print('httpx2:', httpx2.__version__)"
```

기존 HTTPX와 HTTPX2 설치 여부 확인:

```powershell
uv run python -c "import importlib.util; print('httpx2:', importlib.util.find_spec('httpx2') is not None); print('httpx:', importlib.util.find_spec('httpx') is not None)"
```

예상 결과:

```text
httpx2: True
httpx: False
```

## API 주소

기본 로컬 포트가 `8000`인 경우 다음 주소를 사용합니다.

- Liveness: `http://localhost:8000/api/v1/health/live`
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## 프로젝트 구조

```text
RAG/
├── .env.development
├── .env.example
├── .env.local
├── .env.test
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
│       │       ├── database.py
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
│       │   └── config.py
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
│           └── health.py
│
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── integration/
    └── unit/
```

## 계층별 책임

### `api`

- FastAPI Router
- HTTP 요청 및 응답 처리
- 요청값 검증
- HTTP 상태 코드 관리
- Application 계층 호출

비즈니스 로직이나 SQLAlchemy 쿼리를 직접 작성하지 않습니다.

### `application`

- 파일 처리 유스케이스
- S3 파일 조회 흐름
- 문서 처리 단계 조합
- 처리 결과 및 상태 변경 흐름

### `domain`

- 파일, 문서, 청크 및 처리 작업의 핵심 모델
- 파싱 및 색인 상태
- 외부 라이브러리에 의존하지 않는 비즈니스 규칙

### `infrastructure`

- SQLAlchemy 및 MySQL
- AWS S3
- AWS 애플리케이션 서버
- VectorDB
- 외부 시스템 연동 구현

### `core`

- 환경 설정
- 애플리케이션 공통 설정
- 공통 예외
- 로깅
- 전역 정책

### `schemas`

- FastAPI 요청 모델
- FastAPI 응답 모델
- 외부 서버 통신 DTO
