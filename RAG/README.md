# Jipsa RAG Service

`jipsa-rag`는 Jipsa 애플리케이션 서버로부터 파일 식별 정보와
Presigned GET URL을 전달받아 문서를 다운로드하고, 파싱, 청킹,
임베딩 생성, Local RAG DB 저장, Qdrant VectorDB 저장 및 후속 검색을
처리하는 FastAPI 기반 로컬 RAG 서비스입니다.

RAG 서비스는 AWS 자격 증명을 보관하거나 `boto3`를 사용하여 S3에
직접 접근하지 않습니다. S3 접근과 Presigned GET URL 발급은
AWS에서 실행되는 애플리케이션 서버가 IAM Role을 사용하여 담당합니다.

현재 로컬 실행 구성은 다음 요소를 함께 사용합니다.

- FastAPI 기반 RAG 애플리케이션
- Local RAG MySQL 또는 MariaDB
- Qdrant VectorDB
- Hugging Face Text Embeddings Inference(TEI)
- NVIDIA CUDA GPU
- PowerShell 기반 로컬 실행 및 종료 자동화

---

## 목차

1. [서비스 역할과 시스템 경계](#서비스-역할과-시스템-경계)
2. [파일 처리 흐름](#파일-처리-흐름)
3. [현재 구현 범위](#현재-구현-범위)
4. [현재 제한 사항](#현재-제한-사항)
5. [로컬 실행 구성](#로컬-실행-구성)
6. [요구 환경](#요구-환경)
7. [설치](#설치)
8. [환경 변수](#환경-변수)
9. [로컬 RAG 통합 실행](#로컬-rag-통합-실행)
10. [서버 접근 주소](#서버-접근-주소)
11. [로컬 RAG 종료](#로컬-rag-종료)
12. [실행 실패 시 자동 진단](#실행-실패-시-자동-진단)
13. [문제 해결](#문제-해결)
14. [FastAPI 단독 실행](#fastapi-단독-실행)
15. [Docker 수동 확인 명령](#docker-수동-확인-명령)
16. [테스트](#테스트)
17. [코드 품질 검사](#코드-품질-검사)
18. [의존성 확인](#의존성-확인)
19. [보안 주의 사항](#보안-주의-사항)
20. [주요 파일과 책임](#주요-파일과-책임)
21. [계층별 책임](#계층별-책임)
22. [운영 체크리스트](#운영-체크리스트)

---

## 서비스 역할과 시스템 경계

### RAG 서비스가 담당하는 작업

RAG 서비스는 애플리케이션 서버가 전달한 파일 정보를 기준으로 다음 작업을
수행합니다.

- 내부 인증 토큰 검증
- 애플리케이션 서버의 파일 manifest 조회
- Presigned GET URL을 이용한 원본 파일 다운로드
- S3 Object Key와 파일 메타데이터 검증
- 파일 크기, 확장자, MIME Type 및 Magic Byte 검증
- SHA-256 파일 해시 검증
- PDF 페이지 단위 텍스트 추출
- 문서 청킹과 청크 메타데이터 생성
- TEI를 이용한 청크 임베딩 생성
- Local RAG DB에 문서, 청크 및 색인 실행 이력 저장
- Qdrant에 임베딩 벡터와 검색용 payload 저장
- 신규 색인의 활성 전환과 이전 색인의 비활성 전환
- 실패 시 신규 색인 보상 처리
- 애플리케이션 서버에 인제스트 완료 결과 콜백
- Request ID 기반 구조화 로그 출력

### 애플리케이션 서버가 담당하는 작업

애플리케이션 서버는 다음 작업을 담당합니다.

- 사용자 인증과 인가
- 사용자 파일 업로드
- IAM Role 기반 S3 접근
- Presigned GET URL 발급
- 파일 manifest 제공
- RAG 인제스트 요청 전송
- RAG 처리 완료 결과 수신
- 사용자에게 최종 파일 처리 상태 제공

### RAG 서비스가 수행하지 않는 작업

RAG 서비스는 다음 작업을 수행하지 않습니다.

- AWS Access Key를 이용한 S3 직접 접근
- AWS Secret Access Key 보관
- AWS Session Token 보관
- `boto3` 또는 AWS SDK 기반 파일 다운로드
- Docker Desktop 프로그램 자체 실행
- Local RAG MySQL 또는 MariaDB 서버 자체 실행
- 공유기 포트 포워딩 설정
- DDNS 등록 또는 갱신
- TLS 인증서 발급과 HTTPS 종료

---

## 파일 처리 흐름

전체 파일 처리 흐름은 다음과 같습니다.

```text
사용자 파일 업로드
        ↓
애플리케이션 서버가 S3에 원본 파일 저장
        ↓
애플리케이션 서버가 IAM Role로 Presigned GET URL 발급
        ↓
애플리케이션 서버가 RAG 서버의 POST /ingest 호출
        ↓
RAG 서버가 X-Internal-Token 검증
        ↓
RAG 서버가 애플리케이션 서버에서 파일 manifest 조회
        ↓
RAG 서버가 Presigned GET URL로 원본 파일 다운로드
        ↓
파일 크기·형식·해시·메타데이터 검증
        ↓
PDF 페이지 단위 텍스트 추출
        ↓
문서 청킹 및 청크 메타데이터 생성
        ↓
TEI CUDA 서버에서 임베딩 생성
        ↓
Local RAG DB에 문서·청크·실행 이력 저장
        ↓
Qdrant에 비활성 staging Point 저장
        ↓
신규 Point 활성화 및 이전 정상 Point 비활성화
        ↓
Local RAG DB 색인 상태 확정
        ↓
애플리케이션 서버에 ingest-complete 콜백 전송
```

### S3 연동 원칙

- RAG 서버는 AWS 자격 증명을 보관하지 않습니다.
- RAG 서버는 `boto3`를 사용하지 않습니다.
- 애플리케이션 서버가 S3 접근 권한을 관리합니다.
- 애플리케이션 서버가 제한된 유효 시간을 가진 Presigned GET URL을 발급합니다.
- RAG 서버는 HTTP 클라이언트로 Presigned GET URL을 사용합니다.
- S3 Object Key는 파일 식별과 경로 검증 정보로 사용합니다.
- Presigned GET URL 전체 값과 Query String은 로그에 기록하지 않습니다.
- 만료된 URL, 권한 오류 및 잘못된 요청은 자동 재시도 대상에서 제외합니다.

---

## 현재 구현 범위

### FastAPI 애플리케이션

- FastAPI 애플리케이션 팩토리
- FastAPI lifespan 기반 시작 및 종료 처리
- API v1 Router
- 루트 경로의 `POST /ingest`
- 기존 파일 처리 API
- Liveness Health Check
- Readiness Health Check
- Swagger UI, ReDoc 및 OpenAPI JSON
- 전역 예외 처리
- 공통 성공 응답과 오류 응답
- 요청 검증 오류 변환
- 존재하지 않는 API의 공통 404 응답
- 내부 예외 정보의 외부 응답 노출 방지

### 설정과 보안

- Python 3.12와 `uv` 기반 패키지 관리
- `pydantic-settings` 기반 환경별 설정
- `.env.local`, `.env.development`, `.env.test` 환경 분리
- 필수 환경 변수 타입과 형식 검증
- DB 비밀번호와 내부 토큰의 문자열 노출 방지
- `INTERNAL_TOKEN` 기반 애플리케이션 서버 내부 API 인증
- `RAG_INGEST_TOKEN` 기반 RAG 인제스트 API 인증
- Presigned URL과 민감한 Query String 로그 마스킹
- Request ID 생성과 전달
- `X-Request-ID` 응답 헤더
- 구조화된 JSON 로깅

### 파일 다운로드와 문서 처리

- 애플리케이션 서버 manifest 조회
- Presigned GET URL 기반 파일 다운로드
- HTTP Streaming 기반 다운로드
- 연결 및 읽기 Timeout
- 일시적인 네트워크 오류 재시도
- 최대 파일 크기 제한
- 임시 파일 생성과 정리
- 파일 확장자 검증
- MIME Type 검증
- Magic Byte 기반 실제 파일 형식 검증
- 빈 파일과 손상된 파일 탐지
- SHA-256 Checksum 생성과 검증
- 공통 문서 파서 인터페이스
- 문서 형식별 파서 Factory
- PDF 페이지 단위 텍스트 추출
- 청킹과 청크 메타데이터 생성
- 결정적인 청크 식별자 생성

### 임베딩과 VectorDB

- Hugging Face TEI 기반 임베딩 생성
- `Qwen/Qwen3-Embedding-0.6B` 모델
- 1024차원 임베딩
- 임베딩 배치 처리
- Qdrant Collection 준비
- Qdrant Vector Upsert
- 검색 필터용 payload 저장
- 신규 색인의 비활성 staging
- 신규 색인 활성화
- 이전 정상 색인 비활성화
- 실패한 신규 Point 보상 삭제
- 이전 정상 Point 복구

### Local RAG DB와 색인 안정성

- SQLAlchemy AsyncIO와 AsyncMy 기반 비동기 MySQL 연결
- 요청 단위 `AsyncSession`
- 요청 실패 시 트랜잭션 Rollback
- 애플리케이션 종료 시 DB 연결 풀 정리
- 문서와 청크 메타데이터 저장
- 색인 실행 이력 저장
- 동일 파일과 동일 버전에 대한 멱등 처리
- 파일 단위 동시 실행 직렬화
- 최신 색인 실행 소유권 검증
- Local RAG DB와 Qdrant 사이의 보상 처리
- 파서 버전과 임베딩 모델 변경을 고려한 재색인

### 로컬 실행 자동화

- `start-local-rag.ps1` 통합 실행 스크립트
- `stop-local-rag.ps1` 안전 종료 스크립트
- Docker Engine과 Docker Compose 사전 검증
- 필수 환경 변수 사전 검증
- Qdrant와 TEI 이미지 준비
- Docker NVIDIA GPU 사용 가능 여부 확인
- Qdrant 컨테이너 생성과 실행
- Qdrant `/readyz` 준비 상태 확인
- TEI CUDA 컨테이너 강제 재생성
- TEI 이미지와 GPU 할당 확인
- TEI 로그의 CUDA 오류와 CPU 폴백 확인
- 실제 `/embed` 요청 기반 GPU 임베딩 검증
- 인프라 준비 완료 후 FastAPI 자동 실행
- FastAPI 종료 시 Qdrant와 TEI 자동 정지
- 실행 실패 시 컨테이너 상태와 최근 로그 자동 출력

---

## 현재 제한 사항

### 지원 문서 형식

현재 기본 Parser Factory에는 PDF 파서만 등록되어 있습니다.

```text
지원됨: PDF
미지원: DOCX, XLSX, PPTX
```

`DOCX`, `XLSX`, `PPTX`는 문서 형식 열거형에는 정의될 수 있지만,
실제 파서가 등록되지 않은 상태에서는 지원하지 않는 문서 형식 오류를
반환합니다.

### OCR

현재 PDF 처리는 텍스트 레이어를 추출합니다.

이미지만 포함된 스캔 PDF에 대한 OCR은 수행하지 않습니다. 따라서 텍스트
레이어가 없는 PDF는 추출 결과가 비어 있거나 제한적일 수 있습니다.

### 로컬 인프라 범위

통합 실행 스크립트는 다음 요소를 자동 실행하지 않습니다.

- Docker Desktop 프로그램
- Local RAG MySQL 또는 MariaDB 서버
- 공유기 포트 포워딩
- DDNS 설정
- HTTPS Reverse Proxy

### Readiness 범위

`GET /api/v1/health/ready`는 Local RAG DB 연결 상태를 확인합니다.

Qdrant와 TEI의 준비 상태는 `start-local-rag.ps1`이 FastAPI 실행 전에
별도로 검증합니다. 현재 Readiness API 응답만으로 Qdrant와 TEI 상태까지
판단해서는 안 됩니다.

---

## 로컬 실행 구성

현재 개발 환경의 기본 포트 구성은 다음과 같습니다.

| 구성 요소        | 바인딩 또는 접근 주소         | 용도                                       |
| ---------------- | ----------------------------- | ------------------------------------------ |
| FastAPI Bind     | `0.0.0.0:8077`                | 모든 IPv4 인터페이스에서 요청 수신         |
| FastAPI Local    | `http://127.0.0.1:8077`       | RAG 실행 PC에서 로컬 접근                  |
| FastAPI External | `http://rag.example.com:9802` | DDNS와 공유기 포트 포워딩을 통한 외부 접근 |
| Qdrant REST      | `http://127.0.0.1:6333`       | 로컬 VectorDB REST API                     |
| Qdrant gRPC      | `127.0.0.1:6334`              | 향후 또는 선택적 gRPC 연결                 |
| TEI              | `http://127.0.0.1:18081`      | 로컬 CUDA 임베딩 API                       |
| Local RAG DB     | `127.0.0.1:3306`              | 로컬 MySQL 또는 MariaDB                    |

### 포트 포워딩 관계

현재 외부 접근은 다음 매핑을 전제로 합니다.

```text
외부 요청
http://rag.example.com:9802
        ↓
공유기 TCP 9802 포트 포워딩
        ↓
RAG 실행 PC의 내부 TCP 8077
        ↓
FastAPI 0.0.0.0:8077
```

`0.0.0.0`은 서버가 모든 IPv4 인터페이스에서 수신하도록 지정하는
바인딩 주소입니다. 브라우저나 HTTP 클라이언트에서 요청할 주소로
사용하지 않습니다.

RAG 실행 PC에서는 `http://127.0.0.1:8077`을 사용하고, 외부에서는
`http://rag.example.com:9802`를 사용합니다.

Qdrant와 TEI는 `127.0.0.1`에만 바인딩되므로 외부 네트워크에 직접
노출되지 않습니다.

---

## 요구 환경

### 필수 소프트웨어

- Windows 10 또는 Windows 11
- Windows PowerShell 5.1 이상 또는 PowerShell 7 이상
- Python 3.12
- `uv`
- Docker Desktop
- Docker Engine
- Docker Compose v2 Plugin
- NVIDIA GPU Driver
- Docker에서 NVIDIA GPU를 사용할 수 있는 환경
- MySQL 8.0 이상 또는 MariaDB 10.6 이상

### 현재 GPU와 TEI 이미지

현재 Docker Compose 구성은 다음 환경을 기준으로 작성되어 있습니다.

```text
GPU: NVIDIA GeForce RTX 3060 Ti
CUDA Compute Capability: 8.6
TEI Image: ghcr.io/huggingface/text-embeddings-inference:86-1.9
Embedding Model: Qwen/Qwen3-Embedding-0.6B
Embedding Dimension: 1024
```

다른 NVIDIA GPU 아키텍처를 사용할 경우 TEI 이미지 태그와 CUDA
Entrypoint가 해당 GPU에 적합한지 별도로 확인해야 합니다.

### 로컬 리소스

최초 실행 시 Hugging Face 모델 다운로드와 모델 Warmup이 수행될 수
있습니다. 충분한 디스크 공간, 네트워크 연결 및 GPU 메모리가 필요합니다.

Qdrant 데이터와 Hugging Face 모델 Cache는 Docker Named Volume에
보존됩니다. 두 번째 실행부터는 기존 데이터를 재사용하므로 일반적으로
최초 실행보다 빠릅니다.

---

## 설치

### 프로젝트 디렉터리 이동

PowerShell에서 RAG 프로젝트 루트로 이동합니다.

```powershell
Set-Location 'D:\path\to\INT2-Jipsa-Team04\RAG'
```

### Python 버전 확인

```powershell
python --version
```

예상 범위는 다음과 같습니다.

```text
Python 3.12.x
```

### uv 설치 여부 확인

```powershell
uv --version
```

### 프로젝트 의존성 동기화

```powershell
uv sync
```

`uv`는 다음 항목을 동기화합니다.

- 프로젝트 런타임 의존성
- 개발 및 테스트 의존성
- `uv.lock`에 고정된 패키지 버전
- `jipsa-rag` 실행 Entry Point

CI 또는 완전히 고정된 Lockfile 상태를 검증해야 하는 경우 다음 명령을
사용할 수 있습니다.

```powershell
uv sync --frozen
```

### Docker 확인

```powershell
docker version
docker compose version
```

Docker Client 정보만 출력되고 Server 연결에 실패하면 Docker Desktop이
실행되지 않았거나 Docker Engine 준비가 끝나지 않은 상태입니다.

### Docker GPU 확인

통합 실행 스크립트가 GPU를 자동 검증하지만, 사전에 Docker GPU 지원을
직접 확인해야 할 때는 다음 명령을 사용할 수 있습니다.

```powershell
docker run --rm --gpus all nvidia/cuda:12.9.0-base-ubuntu24.04 nvidia-smi
```

사용 중인 로컬 CUDA 검증 이미지가 이미 정해져 있다면 해당 이미지 태그를
사용합니다. 검증 이미지가 로컬에 없으면 Docker Registry에서 다운로드될
수 있습니다.

---

## 환경 변수

### 환경 파일

환경별 dotenv 파일을 사용합니다.

```text
.env.local
.env.development
.env.test
```

Git에는 작성 기준을 제공하는 다음 파일만 포함합니다.

```text
.env.example
```

실제 환경별 파일은 DB 비밀번호, 내부 토큰 및 내부 주소를 포함할 수 있으므로
Git에 커밋하지 않습니다.

### 실행 환경 선택

`JIPSA_RAG_APP_ENV` 값으로 실행 환경을 선택합니다.

#### local

```powershell
$env:JIPSA_RAG_APP_ENV = 'local'
```

로드되는 파일:

```text
.env.local
```

`JIPSA_RAG_APP_ENV`가 설정되지 않으면 기본값으로 `local`을 사용합니다.

#### development

```powershell
$env:JIPSA_RAG_APP_ENV = 'development'
```

로드되는 파일:

```text
.env.development
```

#### test

```powershell
$env:JIPSA_RAG_APP_ENV = 'test'
```

로드되는 파일:

```text
.env.test
```

### 주요 Application 설정

```dotenv
JIPSA_RAG_APP_NAME="Jipsa RAG Service"
JIPSA_RAG_APP_VERSION=0.1.0
JIPSA_RAG_API_V1_PREFIX=/api/v1
JIPSA_RAG_HOST=0.0.0.0
JIPSA_RAG_PORT=8077
JIPSA_RAG_DEBUG=true
```

### 외부 접근 설정

현재 개발 외부 주소를 사용하는 경우 환경 파일에 다음 값을 설정합니다.

```dotenv
JIPSA_RAG_EXTERNAL_BASE_URL=http://rag.example.com:9802
JIPSA_RAG_TUNNEL_PROVIDER=none
```

`JIPSA_RAG_EXTERNAL_BASE_URL`에는 `/ingest`, `/api/v1` 또는 `/docs` 같은
API 경로를 포함하지 않습니다. URL 끝에도 `/`를 붙이지 않습니다.

공유기 포트 포워딩과 DDNS만 사용하는 경우 Tunnel Provider는 `none`입니다.

### 내부 인증 설정

```dotenv
INTERNAL_TOKEN=CHANGE_ME_TO_SECURE_RANDOM_INTERNAL_TOKEN
RAG_INGEST_TOKEN=CHANGE_ME_TO_SECURE_RANDOM_RAG_INGEST_TOKEN
```

두 토큰의 용도는 서로 다릅니다.

| 환경 변수          | 사용 방향               | 사용 위치                                                 |
| ------------------ | ----------------------- | --------------------------------------------------------- |
| `INTERNAL_TOKEN`   | RAG → 애플리케이션 서버 | manifest 조회와 ingest-complete 콜백의 `X-Internal-Token` |
| `RAG_INGEST_TOKEN` | 애플리케이션 서버 → RAG | `POST /ingest` 요청의 `X-Internal-Token`                  |

실제 토큰은 최소 32자 이상의 예측하기 어려운 임의 문자열을 사용합니다.
애플리케이션 서버와 RAG 서버의 대응 값은 반드시 동일해야 합니다.

### Local RAG DB 설정

```dotenv
JIPSA_RAG_DATABASE_HOST=127.0.0.1
JIPSA_RAG_DATABASE_PORT=3306
JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG
JIPSA_RAG_DATABASE_USER=jipsa
JIPSA_RAG_DATABASE_PASSWORD=CHANGE_ME_MYSQL_PASSWORD
JIPSA_RAG_DATABASE_CHARSET=utf8mb4
JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true
JIPSA_RAG_DATABASE_ECHO=false
```

`JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true`이면 FastAPI lifespan 시작 중
`SELECT 1` 연결 검사를 수행합니다. DB 연결에 실패하면 FastAPI 시작도
실패합니다.

SQL과 바인딩 값이 로그에 노출될 수 있으므로 일반 실행에서는
`JIPSA_RAG_DATABASE_ECHO=false`를 유지합니다.

### 파일 다운로드 설정

```dotenv
JIPSA_RAG_S3_ALLOWED_KEY_PREFIX=files/
JIPSA_RAG_FILE_DOWNLOAD_ALLOWED_HOST_SUFFIXES=.amazonaws.com
JIPSA_RAG_FILE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS=5.0
JIPSA_RAG_FILE_DOWNLOAD_READ_TIMEOUT_SECONDS=60.0
JIPSA_RAG_FILE_DOWNLOAD_MAX_SIZE_BYTES=52428800
```

기본 최대 파일 크기 `52428800`은 50 MiB입니다.

### TEI 임베딩 설정

```dotenv
JIPSA_RAG_EMBEDDING_PROVIDER=tei
JIPSA_RAG_EMBEDDING_BASE_URL=http://127.0.0.1:18081
JIPSA_RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
JIPSA_RAG_EMBEDDING_DIM=1024
JIPSA_RAG_EMBEDDING_BATCH_SIZE=32
JIPSA_RAG_EMBEDDING_DISTANCE=cosine
JIPSA_RAG_EMBEDDING_TIMEOUT_SECONDS=60
```

통합 실행 스크립트는 현재 다음 정합성을 확인합니다.

- Embedding Provider가 `tei`인지 확인
- Embedding Base URL이 로컬 `18081` 포트를 사용하는지 확인
- Embedding Dimension이 `1024`인지 확인
- TEI 컨테이너 이미지가 예상 이미지인지 확인
- NVIDIA GPU가 컨테이너에 할당됐는지 확인
- TEI가 CPU로 폴백하지 않았는지 확인
- 실제 `/embed` 요청이 성공하는지 확인

### Qdrant 설정

```dotenv
JIPSA_RAG_VECTOR_DB_PROVIDER=qdrant
JIPSA_RAG_QDRANT_URL=http://127.0.0.1:6333
JIPSA_RAG_QDRANT_COLLECTION=rag_chunk_vector_qwen3_embedding_0_6b_1024
JIPSA_RAG_QDRANT_GRPC_PORT=6334
JIPSA_RAG_QDRANT_PREFER_GRPC=false
JIPSA_RAG_QDRANT_API_KEY=
JIPSA_RAG_QDRANT_TIMEOUT_SECONDS=10
```

임베딩 모델 또는 출력 차원을 변경하면 기존 Collection을 그대로 재사용하지
않고 별도의 Collection을 사용하는 것이 안전합니다.

### 애플리케이션 서버 설정

```dotenv
JIPSA_RAG_APP_SERVER_BASE_URL=http://127.0.0.1:8080
JIPSA_RAG_APP_SERVER_CONNECT_TIMEOUT_SECONDS=5.0
JIPSA_RAG_APP_SERVER_READ_TIMEOUT_SECONDS=30.0
JIPSA_RAG_APP_SERVER_MAX_ATTEMPTS=3
JIPSA_RAG_APP_SERVER_RETRY_INITIAL_DELAY_SECONDS=0.25
JIPSA_RAG_APP_SERVER_RETRY_MAX_DELAY_SECONDS=2.0
```

개발 애플리케이션 서버 주소를 전달받은 경우
`JIPSA_RAG_APP_SERVER_BASE_URL`을 실제 주소로 변경합니다.

URL 끝에는 `/`를 붙이지 않으며 `/internal` 같은 API 경로도 포함하지
않습니다.

---

## 로컬 RAG 통합 실행

### 통합 실행 스크립트

일반적인 로컬 개발에서는 다음 스크립트를 사용합니다.

```text
scripts/start-local-rag.ps1
```

이 스크립트는 단순히 Docker 컨테이너만 실행하지 않습니다. Qdrant와 TEI의
실제 준비 상태를 검증한 뒤 FastAPI를 Foreground Process로 실행하고,
FastAPI가 종료되면 Docker 컨테이너를 자동으로 정지합니다.

### 실행 전 체크

다음 항목이 준비되어 있어야 합니다.

- Docker Desktop 실행 완료
- Docker Engine 준비 완료
- Local RAG MySQL 또는 MariaDB 실행 완료
- 현재 환경의 dotenv 파일 작성 완료
- DB 계정과 비밀번호 확인 완료
- `INTERNAL_TOKEN` 설정 완료
- `RAG_INGEST_TOKEN` 설정 완료
- FastAPI 내부 포트 `8077` 사용 가능
- Qdrant 포트 `6333`, `6334` 사용 가능
- TEI 포트 `18081` 사용 가능
- NVIDIA Driver 정상
- Docker NVIDIA GPU 지원 정상
- 최초 모델 다운로드를 위한 네트워크 연결

### 권장 실행 명령

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\path\to\INT2-Jipsa-Team04\RAG'

$env:JIPSA_RAG_APP_ENV = 'development'

.\scripts\start-local-rag.ps1
```

로컬 환경을 사용할 때는 다음과 같이 실행 환경만 변경합니다.

```powershell
$env:JIPSA_RAG_APP_ENV = 'local'
.\scripts\start-local-rag.ps1
```

### PowerShell 실행 정책 오류

스크립트 실행이 Execution Policy로 차단된 경우 현재 PowerShell 프로세스에
한해서 다음 정책을 적용할 수 있습니다.

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass
```

이 설정은 현재 PowerShell 프로세스가 종료되면 유지되지 않습니다.

### 통합 실행 순서

`start-local-rag.ps1`은 다음 순서로 실행됩니다.

1. 프로젝트 루트 확인
2. Docker Compose 파일 확인
3. 종료 스크립트 확인
4. `uv` 명령 확인
5. Docker CLI 확인
6. `JIPSA_RAG_APP_ENV` 결정
7. 환경별 dotenv 파일 확인
8. 실제 Pydantic Settings를 이용한 환경 변수 검증
9. Embedding Provider와 VectorDB Provider 검증
10. TEI, Qdrant URL과 Docker Compose 포트 정합성 검증
11. Docker Engine 연결 확인
12. Docker Compose v2 Plugin 확인
13. Docker Compose 구성 검증
14. Qdrant와 TEI 이미지 준비
15. Docker NVIDIA GPU 사용 가능 여부 확인
16. Qdrant 컨테이너 생성과 실행
17. Qdrant `/readyz` 준비 상태 확인
18. TEI GPU 컨테이너 강제 재생성과 실행
19. TEI 이미지 확인
20. TEI NVIDIA GPU 할당 확인
21. TEI CUDA 초기화와 CPU 폴백 여부 확인
22. 실제 `/embed` 요청 실행
23. 최종 Docker Compose 상태 출력
24. `uv run jipsa-rag`로 FastAPI 실행
25. FastAPI 종료 후 Qdrant와 TEI 자동 정지
26. 임시 프로세스 환경 변수 복원
27. 실행 전 PowerShell 작업 경로 복원

### 준비 상태 제한 시간

통합 실행 스크립트는 다음 시간 동안 준비 완료를 기다립니다.

| 대상                                 | 최대 대기 시간 |
| ------------------------------------ | -------------: |
| Qdrant                               |          120초 |
| TEI 모델 다운로드·CUDA 초기화·Warmup |         1200초 |

TEI 최초 실행에서는 모델 다운로드로 인해 시간이 오래 걸릴 수 있습니다.
기존 Hugging Face Cache가 존재하면 일반적으로 더 빠르게 준비됩니다.

### 정상 실행 상태

정상적으로 준비되면 다음 의미의 메시지가 출력됩니다.

```text
Qdrant 및 TEI 인프라 준비 완료
Qdrant REST 요청 준비 완료
TEI NVIDIA GPU 실제 임베딩 요청 성공
FastAPI RAG 서버 실행
```

FastAPI 실행 이후에는 Uvicorn 로그와 애플리케이션 JSON 로그가 현재
PowerShell 창에 계속 출력됩니다.

해당 PowerShell 창은 FastAPI와 Docker 인프라의 생명주기를 함께 관리하므로
서버를 사용하는 동안 닫지 않습니다.

---

## 서버 접근 주소

### 로컬 주소

RAG 서버를 실행하는 PC에서는 다음 주소를 사용합니다.

| 기능         | 주소                                        |
| ------------ | ------------------------------------------- |
| Liveness     | `http://127.0.0.1:8077/api/v1/health/live`  |
| Readiness    | `http://127.0.0.1:8077/api/v1/health/ready` |
| Swagger UI   | `http://127.0.0.1:8077/docs`                |
| ReDoc        | `http://127.0.0.1:8077/redoc`               |
| OpenAPI JSON | `http://127.0.0.1:8077/openapi.json`        |
| Ingest       | `http://127.0.0.1:8077/ingest`              |

### 외부 주소

공유기 포트 포워딩과 DDNS가 정상적으로 구성된 경우 다음 주소를 사용합니다.

| 기능         | 주소                                              |
| ------------ | ------------------------------------------------- |
| Liveness     | `http://rag.example.com:9802/api/v1/health/live`  |
| Readiness    | `http://rag.example.com:9802/api/v1/health/ready` |
| Swagger UI   | `http://rag.example.com:9802/docs`                |
| ReDoc        | `http://rag.example.com:9802/redoc`               |
| OpenAPI JSON | `http://rag.example.com:9802/openapi.json`        |
| Ingest       | `http://rag.example.com:9802/ingest`              |

통합 실행 스크립트는 공유기 포트 포워딩, DDNS 해석 또는 외부 방화벽을
자동으로 설정하지 않습니다. FastAPI 로컬 실행이 성공해도 외부 주소 접근은
별도로 실패할 수 있습니다.

### Liveness

```http
GET /api/v1/health/live
```

Liveness는 FastAPI 프로세스가 요청에 응답할 수 있는지 확인합니다.
데이터베이스, Qdrant 또는 TEI와 같은 외부 의존성은 검사하지 않습니다.

### Readiness

```http
GET /api/v1/health/ready
```

Readiness는 Local RAG DB에 `SELECT 1`을 실행하여 데이터베이스 연결 상태를
확인합니다.

- DB 연결 정상: `200 OK`
- DB 연결 실패: `503 Service Unavailable`

DB 주소, 계정, 비밀번호 및 내부 예외 메시지는 외부 응답에 노출하지
않습니다.

### Ingest

```http
POST /ingest
X-Internal-Token: <RAG_INGEST_TOKEN>
```

`POST /ingest`는 `/api/v1` prefix를 사용하지 않는 루트 경로입니다.
애플리케이션 서버와 RAG 서버의 `RAG_INGEST_TOKEN` 값이 일치해야 합니다.

---

## 로컬 RAG 종료

### 정상 종료

통합 실행 중인 PowerShell 창에서 다음 키를 입력합니다.

```text
Ctrl+C
```

`Ctrl+C`가 입력되면 다음 순서로 종료됩니다.

1. Uvicorn 종료 신호 처리
2. FastAPI lifespan 종료 시작
3. Qdrant Client 정리
4. SQLAlchemy AsyncEngine과 연결 풀 정리
5. FastAPI 프로세스 종료
6. `stop-local-rag.ps1` 호출
7. Qdrant 컨테이너 정지
8. TEI 컨테이너 정지
9. 임시 프로세스 환경 변수 복원
10. 기존 PowerShell 작업 경로 복원

Windows PowerShell이 `Ctrl+C`를 `PipelineStoppedException`으로 전달하는
경우도 사용자 정상 종료 요청으로 처리하고 인프라 자동 정리를 계속합니다.

### 수동 종료

FastAPI 실행 창이 비정상적으로 닫혔거나 Qdrant와 TEI만 별도로 정지해야
하는 경우 다음 명령을 실행합니다.

```powershell
Set-Location 'D:\path\to\INT2-Jipsa-Team04\RAG'

.\scripts\stop-local-rag.ps1
```

수동 종료 대상은 다음과 같습니다.

```text
jipsa-qdrant
jipsa-embedding
```

정지할 컨테이너가 없으면 안내 메시지를 출력하고 정상 종료합니다.

### 비정상 종료 후 정리

다음 상황에서는 PowerShell `finally` 블록이 실행되지 않을 수 있습니다.

- PowerShell 창 강제 종료
- 작업 관리자에서 프로세스 강제 종료
- Windows 종료 또는 재부팅
- Docker Desktop 강제 종료
- 전원 장애

이 경우 새 PowerShell 창에서 다음 명령을 실행합니다.

```powershell
Set-Location 'D:\path\to\INT2-Jipsa-Team04\RAG'
.\scripts\stop-local-rag.ps1
```

### 종료 시 유지되는 리소스

종료 스크립트는 `docker compose stop`을 사용합니다.

다음 리소스는 삭제하지 않습니다.

- Qdrant Collection
- Qdrant Vector와 payload
- Qdrant Index
- Qdrant Snapshot
- Qdrant Storage Named Volume
- Qdrant Snapshot Named Volume
- Hugging Face 모델 Cache Named Volume
- Docker 이미지
- Docker 컨테이너 정의
- Docker Compose 네트워크

따라서 다음 실행에서 기존 Qdrant 데이터와 다운로드된 모델을 재사용할 수
있습니다.

### 사용하지 않는 삭제 명령

데이터 손실 방지를 위해 일반 종료 절차에서 다음 명령을 사용하지 않습니다.

```text
docker compose down --volumes
docker compose down -v
docker volume rm
```

---

## 실행 실패 시 자동 진단

### 진단 실행 조건

Qdrant 컨테이너 실행을 시도한 이후 오류가 발생하면
`start-local-rag.ps1`이 자동으로 실패 진단 정보를 출력합니다.

컨테이너 실행 전에 발생한 환경 변수 오류나 Docker Engine 연결 오류는
조회할 컨테이너 로그가 없을 수 있으므로 원본 오류 메시지만 출력합니다.

### 자동 출력 정보

실패 시 다음 순서로 출력됩니다.

```text
[RAG 로컬 서비스 실행 실패]
최초 실행 오류 메시지

[Docker Compose 상태]
docker compose ps --all 결과

[최근 Qdrant 로그]
jipsa-qdrant 최근 로그 200줄

[최근 TEI 로그]
jipsa-embedding 최근 로그 200줄
```

### Docker Compose 상태에서 확인할 항목

`docker compose ps --all` 결과로 다음 항목을 확인할 수 있습니다.

- 컨테이너 생성 여부
- 컨테이너 실행 여부
- 컨테이너 종료 여부
- Exit 상태
- Health 상태
- 컨테이너 이름
- 서비스 이름
- 포트 매핑

### Qdrant 로그에서 확인할 항목

- Qdrant 프로세스 시작 실패
- Storage Volume 접근 실패
- Snapshot 복구 실패
- 포트 바인딩 실패
- 설정 오류
- 프로세스 비정상 종료
- `/readyz` 준비 실패

### TEI 로그에서 확인할 항목

- NVIDIA GPU 접근 실패
- CUDA 초기화 실패
- 잘못된 CUDA 이미지
- GPU 할당 누락
- CPU Backend 폴백
- GPU 메모리 부족
- Hugging Face 모델 다운로드 실패
- 모델 Weight 로드 실패
- 모델 Warmup 실패
- `/embed` 요청 실패

### 원본 오류 보존

진단용 Docker 명령이나 로그 조회가 추가로 실패해도 최초 실행 오류를
덮어쓰지 않습니다.

각 진단 단계는 독립적으로 처리되며 조회 실패 시 Warning을 출력하고 다음
진단 단계로 계속 진행합니다.

### 실패 후 자동 정지

실패 진단 출력이 끝난 뒤 `finally` 블록에서 Qdrant와 TEI 정지를
시도합니다.

기존 실행 오류가 존재할 때는 인프라 정리 오류로 최초 오류를 덮어쓰지
않습니다. FastAPI가 정상 종료됐지만 인프라 정지만 실패한 경우에는 정리
실패를 최종 오류로 전달합니다.

### 컨테이너 실행 전 실패 예시

다음 오류는 컨테이너 실행 전에 발생할 수 있습니다.

- `uv` 명령 없음
- Docker CLI 없음
- Docker Engine 연결 실패
- Docker Compose v2 Plugin 없음
- Docker Compose 파일 없음
- 종료 스크립트 없음
- dotenv 파일 없음
- 필수 환경 변수 누락
- 잘못된 URL 또는 포트
- 지원하지 않는 Embedding Provider
- 지원하지 않는 VectorDB Provider
- 잘못된 임베딩 차원
- Docker Compose 구성 오류
- Docker NVIDIA GPU 사전 검증 실패

이 경우에는 오류가 발생한 단계와 원본 오류 메시지를 먼저 확인합니다.

---

## 문제 해결

### Docker Engine에 연결할 수 없음

증상:

```text
Docker Engine에 연결할 수 없습니다.
```

확인 순서:

1. Docker Desktop이 실행 중인지 확인합니다.
2. Docker Desktop 상태가 Engine Running인지 확인합니다.
3. 다음 명령을 실행합니다.

```powershell
docker version
```

4. Windows 재부팅 직후라면 Docker Engine 준비가 끝날 때까지 기다립니다.

### Docker Compose Plugin 실행 실패

확인 명령:

```powershell
docker compose version
```

`docker-compose` 구형 명령이 아니라 `docker compose` v2 Plugin을 사용합니다.

### NVIDIA GPU를 사용할 수 없음

확인 명령:

```powershell
nvidia-smi
docker info
```

확인 항목:

- Windows에서 `nvidia-smi`가 정상인지 확인
- NVIDIA Driver 설치 상태 확인
- Docker Desktop GPU 지원 상태 확인
- WSL2 Backend 상태 확인
- 다른 컨테이너가 GPU 메모리를 과도하게 점유하는지 확인

### TEI가 CPU로 실행됨

통합 실행 스크립트는 CPU 폴백을 정상 상태로 인정하지 않습니다.

TEI 로그에서 다음 유형의 메시지를 확인합니다.

```text
CUDA_ERROR_NO_DEVICE
Using CPU instead
Starting Qwen3 model on Cpu
```

정상적인 CUDA 실행에서는 GPU Device가 표시되고 실제 `/embed` 요청이
성공해야 합니다.

### GPU 메모리 부족

확인 명령:

```powershell
nvidia-smi
```

불필요한 GPU 프로세스와 컨테이너를 종료한 후 다시 실행합니다.

```powershell
docker ps --all
```

### Qdrant 준비 상태 Timeout

Qdrant는 최대 120초 동안 `/readyz` 응답을 기다립니다.

확인 명령:

```powershell
docker logs --tail 200 jipsa-qdrant
```

Storage Volume 오류, 포트 충돌 및 데이터 복구 오류를 확인합니다.

### TEI 준비 상태 Timeout

TEI는 최초 모델 다운로드와 Warmup을 고려해 최대 1200초 동안 준비를
기다립니다.

확인 명령:

```powershell
docker logs --tail 200 jipsa-embedding
```

확인 항목:

- 모델 다운로드 진행 여부
- 네트워크 연결
- Hugging Face 접근 오류
- CUDA 초기화 오류
- GPU 메모리 부족
- 모델 Warmup 오류

### 포트 충돌

확인 대상 포트:

```text
8077
6333
6334
18081
3306
```

PowerShell 확인 명령:

```powershell
Get-NetTCPConnection `
    -State Listen `
    -ErrorAction SilentlyContinue |
    Where-Object {
        $_.LocalPort -in 8077, 6333, 6334, 18081, 3306
    } |
    Sort-Object LocalPort |
    Format-Table `
        LocalAddress,
        LocalPort,
        OwningProcess `
        -AutoSize
```

프로세스 확인:

```powershell
Get-Process -Id <OwningProcess>
```

### FastAPI 시작 시 DB 연결 실패

`JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true`이면 FastAPI 시작 전에 DB
연결을 확인합니다.

확인 항목:

- MySQL 또는 MariaDB 실행 여부
- DB Host와 Port
- DB 이름
- DB 사용자
- DB 비밀번호
- 사용자 접근 권한
- 방화벽

### Local은 성공하지만 외부 접속 실패

확인 순서:

1. 로컬 Liveness를 확인합니다.

```powershell
Invoke-RestMethod `
    -Method Get `
    -Uri 'http://127.0.0.1:8077/api/v1/health/live'
```

2. FastAPI가 `0.0.0.0:8077`에 바인딩됐는지 확인합니다.
3. Windows Defender Firewall의 TCP 8077 허용 여부를 확인합니다.
4. 공유기 외부 TCP 9802 → 내부 TCP 8077 포트 포워딩을 확인합니다.
5. `INT2-jipsa.iptime.org`의 DNS 해석 결과를 확인합니다.

```powershell
Resolve-DnsName 'INT2-jipsa.iptime.org'
```

6. ISP의 CGNAT 또는 외부 인바운드 차단 여부를 확인합니다.
7. 외부 네트워크에서 다시 요청합니다.

### PowerShell 창을 닫은 뒤 GPU가 계속 점유됨

강제 종료로 자동 정리가 실행되지 않았을 수 있습니다.

```powershell
Set-Location 'D:\path\to\INT2-Jipsa-Team04\RAG'
.\scripts\stop-local-rag.ps1
```

이후 다음 명령으로 상태를 확인합니다.

```powershell
docker compose `
    --file .\infra\qdrant\compose.yaml `
    ps `
    --all
```

---

## FastAPI 단독 실행

### 프로젝트 Entry Point 실행

Qdrant와 TEI가 이미 준비된 상태에서 FastAPI 프로세스만 디버깅해야 하는
경우 다음 명령을 사용할 수 있습니다.

```powershell
$env:JIPSA_RAG_APP_ENV = 'development'
uv run jipsa-rag
```

이 명령은 `pyproject.toml`의 다음 Entry Point를 실행합니다.

```toml
[project.scripts]
jipsa-rag = "jipsa_rag.main:main"
```

`main()`은 다음 설정을 읽어 Uvicorn을 실행합니다.

- `JIPSA_RAG_HOST`
- `JIPSA_RAG_PORT`
- `JIPSA_RAG_DEBUG`

`JIPSA_RAG_DEBUG=true`이면 Uvicorn Reload를 활성화합니다.

### Uvicorn 직접 실행

```powershell
$env:JIPSA_RAG_APP_ENV = 'development'

uv run uvicorn jipsa_rag.main:app `
    --reload `
    --host 0.0.0.0 `
    --port 8077
```

### 단독 실행의 차이

`uv run jipsa-rag`와 Uvicorn 직접 실행은 다음 작업을 수행하지 않습니다.

- Qdrant 컨테이너 실행
- Qdrant `/readyz` 확인
- TEI 컨테이너 실행
- NVIDIA GPU 사전 확인
- TEI GPU 할당 확인
- CPU 폴백 확인
- 실제 `/embed` 검증
- FastAPI 종료 후 Docker 컨테이너 정지
- 실패 시 Docker 상태와 로그 자동 출력

일반적인 로컬 실행에서는 다음 통합 스크립트를 우선 사용합니다.

```powershell
.\scripts\start-local-rag.ps1
```

---

## Docker 수동 확인 명령

### Compose 구성 검증

```powershell
docker compose `
    --file .\infra\qdrant\compose.yaml `
    config `
    --quiet
```

### 전체 컨테이너 상태

```powershell
docker compose `
    --file .\infra\qdrant\compose.yaml `
    ps `
    --all
```

### Qdrant 로그

```powershell
docker logs `
    --tail 200 `
    jipsa-qdrant
```

### TEI 로그

```powershell
docker logs `
    --tail 200 `
    jipsa-embedding
```

### Qdrant 상태

```powershell
docker inspect `
    --format '{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|{{.RestartCount}}' `
    jipsa-qdrant
```

### TEI 상태

```powershell
docker inspect `
    --format '{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|{{.RestartCount}}' `
    jipsa-embedding
```

### TEI GPU 할당 확인

```powershell
docker inspect `
    --format '{{json .HostConfig.DeviceRequests}}' `
    jipsa-embedding
```

### Qdrant 준비 상태 직접 확인

```powershell
Invoke-WebRequest `
    -Method Get `
    -Uri 'http://127.0.0.1:6333/readyz' `
    -UseBasicParsing
```

### TEI 임베딩 직접 확인

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

### 컨테이너 수동 정지

직접 `docker compose stop`을 실행하는 대신 프로젝트 종료 스크립트를
사용합니다.

```powershell
.\scripts\stop-local-rag.ps1
```

종료 정책을 한 곳에서 유지하고 종료 후 상태 출력까지 동일하게 수행하기
위함입니다.

---

## 테스트

### 테스트 환경 선택

```powershell
$env:JIPSA_RAG_APP_ENV = 'test'
```

### 전체 테스트

```powershell
uv run pytest
```

상세 테스트 이름을 함께 확인하려면 다음 명령을 사용합니다.

```powershell
uv run pytest -v
```

### 단위 테스트

```powershell
uv run pytest tests/unit -v
```

### 통합 테스트

```powershell
uv run pytest tests/integration -v
```

DB 통합 테스트는 Local RAG DB에 `SELECT 1`을 실행하여 연결 가능 여부를
확인합니다. 데이터를 생성, 수정 또는 삭제하지 않습니다.

통합 테스트 전에 `.env.test`의 다음 값을 확인합니다.

```text
JIPSA_RAG_DATABASE_HOST
JIPSA_RAG_DATABASE_PORT
JIPSA_RAG_DATABASE_NAME
JIPSA_RAG_DATABASE_USER
JIPSA_RAG_DATABASE_PASSWORD
```

### 테스트 커버리지

```powershell
uv run pytest `
    --cov=src/jipsa_rag `
    --cov-report=term-missing
```

### PowerShell 스크립트 구문 검사

`start-local-rag.ps1`과 `stop-local-rag.ps1`을 실제 실행하지 않고
PowerShell Parser로 구문만 확인할 수 있습니다.

```powershell
$ScriptPaths = @(
    '.\scripts\start-local-rag.ps1',
    '.\scripts\stop-local-rag.ps1'
)

foreach ($ScriptPath in $ScriptPaths) {
    $Tokens = $null
    $Errors = $null

    [void] [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path -LiteralPath $ScriptPath).Path,
        [ref] $Tokens,
        [ref] $Errors
    )

    if ($Errors.Count -gt 0) {
        $Errors |
            Format-List

        throw "PowerShell 구문 검사 실패: $ScriptPath"
    }

    Write-Host "PowerShell 구문 검사 통과: $ScriptPath" `
        -ForegroundColor Green
}
```

---

## 코드 품질 검사

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

```powershell
uv run mypy src tests
```

### 권장 전체 검사 순서

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\path\to\INT2-Jipsa-Team04\RAG'

$env:JIPSA_RAG_APP_ENV = 'test'

uv sync --frozen

if ($LASTEXITCODE -ne 0) {
    throw "의존성 동기화 실패. 종료 코드: $LASTEXITCODE"
}

uv run ruff format --check .

if ($LASTEXITCODE -ne 0) {
    throw "Ruff 포맷 검사 실패. 종료 코드: $LASTEXITCODE"
}

uv run ruff check .

if ($LASTEXITCODE -ne 0) {
    throw "Ruff 린트 검사 실패. 종료 코드: $LASTEXITCODE"
}

uv run mypy src tests

if ($LASTEXITCODE -ne 0) {
    throw "Mypy 정적 타입 검사 실패. 종료 코드: $LASTEXITCODE"
}

uv run pytest

if ($LASTEXITCODE -ne 0) {
    throw "Pytest 실패. 종료 코드: $LASTEXITCODE"
}

Write-Host ''
Write-Host 'RAG 전체 품질 검사 통과' -ForegroundColor Green
```

---

## 의존성 확인

### 전체 의존성 트리

```powershell
uv tree
```

### HTTPX2 설치 상태

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

RAG 서버는 S3에 직접 접근하지 않으므로 다음 패키지가 의존성 트리에 없어야
합니다.

- `boto3`
- `boto3-stubs`
- `botocore`
- `s3transfer`

확인 명령:

```powershell
uv tree |
    Select-String `
        -Pattern 'boto3|botocore|s3transfer'
```

아무 내용도 출력되지 않으면 정상입니다.

---

## 보안 주의 사항

### Git에 커밋하지 않는 값

다음 값은 Git에 커밋하지 않습니다.

- Local RAG DB 실제 비밀번호
- `INTERNAL_TOKEN`
- `RAG_INGEST_TOKEN`
- 내부 API Key
- Presigned GET URL
- Presigned GET URL Query String
- 사용자 업로드 파일 내용
- 개인정보
- 세션과 인증 토큰
- 운영 환경 내부 주소

### 사용하지 않는 AWS 자격 증명

RAG 환경 변수에는 다음 값을 추가하지 않습니다.

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
```

### 외부 HTTP 노출

현재 개발 외부 주소는 다음과 같이 HTTP를 사용합니다.

```text
http://rag.example.com:9802
```

HTTP는 전송 구간을 암호화하지 않습니다. 외부 네트워크를 통과하는
`X-Internal-Token`과 요청 본문이 노출될 위험이 있습니다.

개발 검증 범위를 넘어 지속적으로 외부에 노출할 경우 HTTPS Reverse Proxy,
TLS 인증서, 접근 IP 제한 및 방화벽 정책을 적용해야 합니다.

### Qdrant와 TEI 외부 노출 금지

Qdrant와 TEI는 다음 Loopback 주소에만 바인딩합니다.

```text
Qdrant REST: 127.0.0.1:6333
Qdrant gRPC: 127.0.0.1:6334
TEI: 127.0.0.1:18081
```

공유기 포트 포워딩이나 Windows 방화벽 규칙으로 해당 포트를 외부에 직접
노출하지 않습니다.

### 로그 보안

로그에는 다음 값을 기록하지 않습니다.

- DB 비밀번호
- 내부 인증 토큰 원문
- Presigned GET URL 원문
- Presigned URL Query String
- 사용자 파일 원문
- 불필요한 개인정보

---

## 주요 파일과 책임

| 경로                                                       | 책임                                                          |
| ---------------------------------------------------------- | ------------------------------------------------------------- |
| `README.md`                                                | RAG 서비스 구조, 실행, 종료, 진단 및 개발 절차 문서           |
| `.env.example`                                             | 환경별 dotenv 파일 작성 기준                                  |
| `pyproject.toml`                                           | Python 의존성, 실행 Entry Point, Ruff, Mypy, Pytest 설정      |
| `uv.lock`                                                  | 재현 가능한 정확한 Python 의존성 버전                         |
| `scripts/start-local-rag.ps1`                              | Qdrant·TEI 준비, GPU 검증, FastAPI 실행, 실패 진단, 자동 정지 |
| `scripts/stop-local-rag.ps1`                               | Qdrant·TEI 안전 정지와 종료 후 상태 출력                      |
| `infra/qdrant/compose.yaml`                                | Qdrant, TEI, GPU Reservation 및 Named Volume 정의             |
| `infra/qdrant/cuda-entrypoint.sh`                          | TEI CUDA 실행을 위한 보정 Entrypoint                          |
| `src/jipsa_rag/main.py`                                    | FastAPI 생성, lifespan, Router 등록, Uvicorn 실행             |
| `src/jipsa_rag/core/config.py`                             | 환경 변수 로드와 타입 검증                                    |
| `src/jipsa_rag/core/logging.py`                            | 구조화 로그와 민감정보 보호                                   |
| `src/jipsa_rag/core/middleware.py`                         | Request ID와 요청 단위 로그 처리                              |
| `src/jipsa_rag/api/ingest.py`                              | 루트 `POST /ingest` API                                       |
| `src/jipsa_rag/api/v1/endpoints/health.py`                 | Liveness와 Readiness API                                      |
| `src/jipsa_rag/infrastructure/app_server/ingest_client.py` | manifest 조회와 ingest-complete 콜백                          |
| `src/jipsa_rag/infrastructure/document/parser.py`          | 공통 문서 Parser 계약                                         |
| `src/jipsa_rag/infrastructure/document/parser_factory.py`  | 문서 형식별 Parser 선택                                       |
| `src/jipsa_rag/infrastructure/document/parsers/pdf.py`     | PDF 페이지 단위 텍스트 추출                                   |
| `src/jipsa_rag/infrastructure/embedding`                   | TEI 임베딩 요청과 임베딩 모델                                 |
| `src/jipsa_rag/infrastructure/indexing`                    | Local RAG DB와 Qdrant 저장 구현                               |
| `src/jipsa_rag/services/file_indexing.py`                  | 색인 staging, 활성 전환, 멱등성, 동시성 및 보상 처리          |
| `tests/unit`                                               | 단위 테스트                                                   |
| `tests/integration`                                        | 실제 외부 의존성을 사용하는 통합 테스트                       |

---

## 계층별 책임

### `api`

- FastAPI Router 구성
- HTTP 요청과 응답 처리
- Header, Path, Query 및 Body 검증
- HTTP 상태 코드 관리
- 서비스 계층 호출
- 외부 응답 DTO 변환

API 계층에 SQLAlchemy 쿼리나 Qdrant 저장 로직을 직접 작성하지 않습니다.

### `services`

- 파일 처리 유스케이스 조정
- 문서 파싱, 청킹, 임베딩 및 저장 흐름 연결
- Local RAG DB와 Qdrant 사이의 처리 순서 관리
- 멱등성 처리
- 재색인 활성 전환
- 실패 보상 처리
- 애플리케이션 서버 콜백 시점 관리

### `domain`

- 파일, 문서, 청크 및 색인 실행의 핵심 모델
- 파일과 문서 처리 상태
- 파싱과 색인 상태
- 중복 처리 방지 규칙
- 외부 라이브러리와 분리된 비즈니스 규칙

### `infrastructure`

- SQLAlchemy와 Local RAG DB
- 애플리케이션 서버 HTTP Client
- Presigned GET URL 파일 다운로드 Client
- 임시 파일 시스템
- 문서 Parser 구현
- TEI Embedding Client
- Qdrant Client와 Vector 저장
- MySQL Advisory Lock
- 외부 시스템 오류 변환

AWS Access Key나 `boto3`를 이용한 S3 Client를 구성하지 않습니다.

### `core`

- 환경 설정
- 공통 오류 코드
- 공통 예외
- 전역 예외 처리
- Request ID 관리
- 요청 추적 Middleware
- 구조화 로그
- 민감정보 마스킹
- 전역 정책

### `schemas`

- FastAPI 요청 모델
- FastAPI 응답 모델
- 공통 성공과 오류 응답
- 애플리케이션 서버 통신 DTO
- Health Check 응답 모델

---

## 운영 체크리스트

### 최초 실행 전

- [ ] Python 3.12 설치 확인
- [ ] `uv` 설치 확인
- [ ] `uv sync` 완료
- [ ] Docker Desktop 실행 확인
- [ ] Docker Compose v2 확인
- [ ] NVIDIA Driver 확인
- [ ] Docker GPU 사용 가능 여부 확인
- [ ] Local RAG DB 실행 확인
- [ ] Local RAG DB Schema 적용 확인
- [ ] `.env.local` 또는 `.env.development` 작성
- [ ] DB 비밀번호 입력
- [ ] `INTERNAL_TOKEN` 입력
- [ ] `RAG_INGEST_TOKEN` 입력
- [ ] 애플리케이션 서버 주소 입력
- [ ] FastAPI, Qdrant, TEI 포트 충돌 확인
- [ ] DDNS와 포트 포워딩 확인

### 실행 후

- [ ] Qdrant `/readyz` 성공 확인
- [ ] TEI GPU Device 확인
- [ ] TEI CPU 폴백 없음 확인
- [ ] TEI `/embed` 성공 확인
- [ ] FastAPI Liveness 성공 확인
- [ ] FastAPI Readiness 성공 확인
- [ ] Swagger UI 접근 확인
- [ ] 외부 Liveness 접근 확인
- [ ] `POST /ingest` 내부 토큰 연동 확인

### 종료 후

- [ ] `Ctrl+C`로 FastAPI 정상 종료
- [ ] Qdrant 컨테이너 정지 확인
- [ ] TEI 컨테이너 정지 확인
- [ ] GPU 점유 해제 확인
- [ ] Qdrant Named Volume 유지 확인
- [ ] Hugging Face 모델 Cache 유지 확인

### PR 전

- [ ] `uv sync --frozen` 통과
- [ ] `uv run ruff format --check .` 통과
- [ ] `uv run ruff check .` 통과
- [ ] `uv run mypy src tests` 통과
- [ ] `uv run pytest` 통과
- [ ] PowerShell 스크립트 구문 검사 통과
- [ ] 실제 토큰과 비밀번호가 Git 변경 사항에 없는지 확인
- [ ] `.env.local`, `.env.development`, `.env.test`가 Stage되지 않았는지 확인
- [ ] 실행과 종료 절차를 실제로 검증했는지 확인
