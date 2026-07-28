# CUDA·TEI·Qdrant·Local DB 실행 절차

> **문서 상태:** Active · 로컬 실행 Runbook  
> **주 독자:** Local RAG 개발자, 환경 구축·운영 담당자  
> **최종 검토:** 2026-07-28  
> **지원 기준:** Windows PowerShell 5.1+, Python 3.12, CUDA 12.9, Docker GPU


## 1. 실행 구성

| 구성요소 | 실행 위치 | 기본 주소 | 데이터 |
|---|---|---|---|
| FastAPI Local RAG | Windows Host | `0.0.0.0:8077` | 프로세스 메모리 |
| EasyOCR | Windows Host Python | `cuda:0` | `.cache/easyocr` 모델 |
| CUDA TEI | Docker GPU | `127.0.0.1:18081` | Hugging Face 모델 Volume |
| Qdrant | Docker | `127.0.0.1:6333`, `6334` | Vector·Snapshot Volume |
| Local RAG DB | MySQL/MariaDB | `127.0.0.1:3306` | `Jipsa_Local_RAG` |

## 2. 필수 환경

- Windows PowerShell 5.1 이상
- Python 3.12
- `uv`
- Docker Desktop
- Docker Compose Plugin
- NVIDIA Driver
- Docker의 NVIDIA GPU 지원
- CUDA 12.9용 PyTorch 설치가 가능한 환경
- Local MySQL 또는 MariaDB
- Local RAG DB DDL 적용
- 실제 실행용 `.env.local`
- 이미지 차트 렌더링 시 PowerPoint·Excel과 대화형 세션
- 실제 답변 생성 시 Anthropic API Key

`start-local-rag.ps1`은 Docker Desktop 프로그램과 Local DB 서버를 시작하지 않습니다.
두 서비스는 스크립트 실행 전에 별도로 시작합니다.

## 3. 환경 파일

[`.env.example`](../../.env.example)을 기준으로 로컬 전용 `.env.local`을 준비합니다.
실제 파일은 Git에 커밋하지 않습니다.

핵심 값:

```dotenv
JIPSA_RAG_DATABASE_HOST=127.0.0.1
JIPSA_RAG_DATABASE_PORT=3306
JIPSA_RAG_DATABASE_NAME=Jipsa_Local_RAG
JIPSA_RAG_DATABASE_USER=로컬_계정
JIPSA_RAG_DATABASE_PASSWORD=로컬_비밀번호

JIPSA_RAG_OCR_ENABLED=true
JIPSA_RAG_OCR_GPU=true
JIPSA_RAG_OCR_GPU_REQUIRED=true
JIPSA_RAG_OCR_DEVICE=cuda:0

JIPSA_RAG_EMBEDDING_PROVIDER=tei
JIPSA_RAG_EMBEDDING_BASE_URL=http://127.0.0.1:18081
JIPSA_RAG_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
JIPSA_RAG_EMBEDDING_DIM=1024

JIPSA_RAG_VECTOR_DB_PROVIDER=qdrant
JIPSA_RAG_QDRANT_URL=http://127.0.0.1:6333
JIPSA_RAG_QDRANT_COLLECTION=rag_chunk_vector_qwen3_embedding_0_6b_1024

JIPSA_RAG_GENERATION_PROVIDER=anthropic
ANTHROPIC_API_KEY=실제_API_Key

INTERNAL_TOKEN=Backend와_일치하는_32자_이상_비밀값
RAG_INGEST_TOKEN=Backend와_일치하는_별도_32자_이상_비밀값
```

비밀값은 출력하거나 문서·이슈·PR에 붙여넣지 않습니다.

## 4. 의존성 동기화

`RAG` 디렉터리에서 실행합니다.

```powershell
uv sync --frozen
```

`--frozen`은 `pyproject.toml`과 `uv.lock` 불일치를 자동 수정하지 않고 실패로 표시합니다.
PyTorch와 torchvision은 `pyproject.toml`의 `pytorch-cu129` 인덱스를 사용합니다.

## 5. 사전 확인

### Docker

```powershell
docker version
docker compose version
```

### NVIDIA GPU

```powershell
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.9.0-base-ubuntu24.04 nvidia-smi
```

GPU 컨테이너가 실패하면 TEI를 시작하기 전에 NVIDIA Driver, Docker Desktop GPU 통합과
WSL2 상태를 확인합니다.

### Python CUDA

```powershell
uv run python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

`JIPSA_RAG_OCR_GPU_REQUIRED=true`에서는 `torch.cuda.is_available()`이 `True`여야 합니다.

### Local RAG DB

Local DB 서버와 스키마는 별도로 준비합니다.
애플리케이션 시작 시 `JIPSA_RAG_DATABASE_CHECK_ON_STARTUP=true`이면 `SELECT 1` 연결을
검증합니다.

## 6. 표준 실행

```powershell
.\scripts\start-local-rag.ps1
```

스크립트 처리 순서:

1. 프로젝트 구조와 필수 도구 확인
2. 실행 환경 선택과 Pydantic Settings 검증
3. Compose 포트와 환경 변수 정합성 검증
4. Docker Engine과 Compose 확인
5. Qdrant와 TEI 이미지 준비
6. Docker NVIDIA GPU 사전 확인
7. Qdrant 시작과 `/readyz` 확인
8. CUDA TEI 시작과 GPU 할당 확인
9. TEI 로그의 CUDA 실패·CPU 폴백 검사
10. 실제 `/embed` 요청
11. FastAPI를 Foreground로 실행
12. 종료 시 스크립트가 시작한 Qdrant와 TEI 정지

FastAPI 문서는 기본적으로 다음 주소에서 확인합니다.

```text
http://127.0.0.1:8077/docs
```

## 7. Compose 구성

Compose 파일:

[`RAG/infra/qdrant/compose.yaml`](../../infra/qdrant/compose.yaml)

고정 이미지:

| 서비스 | 이미지 |
|---|---|
| Qdrant | `qdrant/qdrant:v1.18.2` |
| TEI | `ghcr.io/huggingface/text-embeddings-inference:86-1.9` |

TEI 이미지는 Ampere Compute Capability 8.6용입니다.
GPU 아키텍처를 변경하면 이미지 태그와 시작 스크립트의 고정값을 함께 검토합니다.

영속 Volume:

- `jipsa_qdrant_storage`
- `jipsa_qdrant_snapshots`
- `jipsa_embedding_cache`

컨테이너 정지는 위 Volume과 Docker 이미지를 삭제하지 않습니다.

## 8. 종료

FastAPI Foreground 프로세스에서 `Ctrl+C`를 사용하면 애플리케이션 lifespan을 종료한 뒤
Qdrant와 TEI를 정지합니다.

별도 정지:

```powershell
.\scripts\stop-local-rag.ps1
```

정지 후에도 다음 데이터는 유지됩니다.

- Qdrant Collection, 벡터와 payload
- Qdrant Snapshot
- Hugging Face 모델 Cache
- EasyOCR 모델 Cache
- Local RAG DB 데이터

## 9. 직접 Compose 실행 시 주의

표준 실행은 `start-local-rag.ps1`입니다. 직접 Compose를 실행하면 Settings 검증,
GPU 사전 검사, CPU 폴백 탐지와 FastAPI 종료 연동을 우회합니다.

장애 분석을 위해 직접 상태만 확인할 수 있습니다.

```powershell
docker compose --file .\infra\qdrant\compose.yaml ps
docker logs jipsa-qdrant
docker logs jipsa-embedding
```

로그를 공유할 때 토큰, URL query, API Key와 문서 원문이 포함되지 않았는지 확인합니다.

## 10. 준비 상태

### Qdrant

```text
GET http://127.0.0.1:6333/readyz
```

### TEI

시작 스크립트가 실제 `/embed` 요청으로 다음을 확인합니다.

- HTTP 성공
- 요청 개수와 벡터 개수 일치
- 벡터 차원 `1024`
- 모든 값이 유한한 숫자
- CUDA 초기화 오류 또는 CPU 폴백 없음

### FastAPI

OpenAPI에서 다음 엔드포인트를 확인합니다.

- `POST /ingest`
- `POST /api/v1/chunks/search`
- `POST /api/v1/rag/answers`

## 11. 자주 발생하는 문제

| 증상 | 확인 |
|---|---|
| Docker CLI는 있으나 연결 실패 | Docker Desktop Engine 실행 여부 |
| TEI 컨테이너 즉시 종료 | GPU 할당, 이미지 아키텍처, CUDA Entrypoint 로그 |
| EasyOCR CPU 실행 | `JIPSA_RAG_OCR_GPU_REQUIRED`, PyTorch CUDA wheel |
| TEI 벡터 차원 오류 | 모델 ID, `JIPSA_RAG_EMBEDDING_DIM`, Qdrant Collection |
| Qdrant 검색 실패 | Collection 차원·거리 함수와 API 주소 |
| DB 시작 검사 실패 | DB 서버, 스키마, 계정, 문자 집합 |
| Office 렌더링 skip | 대화형 세션과 PowerPoint·Excel 설치 |
| Backend 연결 실패 | `JIPSA_RAG_APP_SERVER_BASE_URL`, 토큰, 방화벽 |
| Presigned URL 거부 | 허용 호스트 suffix, 만료, 크기 제한 |

## 12. 운영 안전

- Qdrant와 TEI를 인터넷에 직접 공개하지 않습니다.
- Local RAG DB 계정에 필요한 최소 권한만 부여합니다.
- `.env.local`을 Git, 메신저와 화면 공유에 노출하지 않습니다.
- 시작 스크립트가 검출한 CPU 폴백을 무시하고 서비스하지 않습니다.
- 임베딩 모델이나 차원을 변경할 때 기존 Collection을 재사용하지 않습니다.
