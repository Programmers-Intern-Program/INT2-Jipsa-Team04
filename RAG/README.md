# Jipsa RAG Service

`jipsa-rag`는 Jipsa 애플리케이션 서버와 분리된 **Local RAG 서비스**입니다.

애플리케이션 서버가 전달한 파일 식별 정보와 Presigned GET URL을 기준으로 PDF를 다운로드하고, 파싱, 청킹, 임베딩 생성, Local RAG DB 저장, Qdrant VectorDB 저장, 검색 및 Claude 기반 답변 생성을 수행합니다.

RAG 서비스는 AWS 자격 증명을 보관하지 않으며 `boto3` 또는 AWS SDK로 S3에 직접 접근하지 않습니다. S3 접근과 Presigned GET URL 발급은 AWS에서 실행되는 애플리케이션 서버가 IAM Role을 사용하여 담당합니다.

> 현재 답변 대상 문서 형식은 **텍스트 레이어가 있는 PDF만 지원**합니다.  
> OCR, TXT, DOCX, XLSX, PPTX 기반 답변 생성은 지원하지 않습니다.

---

## 목차

1. [서비스 역할과 시스템 경계](#1-서비스-역할과-시스템-경계)
2. [전체 처리 구조](#2-전체-처리-구조)
3. [현재 구현 범위](#3-현재-구현-범위)
4. [질의 유형 라우팅](#4-질의-유형-라우팅)
5. [다중 PDF synthesis 처리](#5-다중-pdf-synthesis-처리)
6. [출처 및 근거 계약](#6-출처-및-근거-계약)
7. [Claude 호출 제한](#7-claude-호출-제한)
8. [지원 형식과 제한 사항](#8-지원-형식과-제한-사항)
9. [로컬 실행 구성](#9-로컬-실행-구성)
10. [요구 환경](#10-요구-환경)
11. [설치](#11-설치)
12. [환경 변수](#12-환경-변수)
13. [로컬 RAG 통합 실행](#13-로컬-rag-통합-실행)
14. [서버 접근 주소](#14-서버-접근-주소)
15. [로컬 RAG 종료](#15-로컬-rag-종료)
16. [테스트](#16-테스트)
17. [코드 품질 검사](#17-코드-품질-검사)
18. [보안 주의 사항](#18-보안-주의-사항)
19. [문제 해결](#19-문제-해결)
20. [주요 파일과 책임](#20-주요-파일과-책임)
21. [운영 체크리스트](#21-운영-체크리스트)

---

## 1. 서비스 역할과 시스템 경계

### RAG 서비스가 담당하는 작업

- 애플리케이션 서버와의 내부 인증 토큰 검증
- 애플리케이션 서버의 파일 manifest 조회
- Presigned GET URL을 이용한 PDF 다운로드
- S3 Object Key와 파일 메타데이터 검증
- 파일 크기, 확장자, MIME Type 및 Magic Byte 검증
- SHA-256 파일 해시 검증
- PDF 페이지 단위 텍스트 추출
- 문서 청킹과 청크 메타데이터 생성
- CUDA 기반 TEI를 통한 임베딩 생성
- Local RAG DB에 문서, 청크 및 색인 실행 이력 저장
- Qdrant에 임베딩 벡터와 검색용 payload 저장
- 신규 색인의 활성 전환과 이전 색인의 비활성 전환
- 실패 시 신규 색인 보상 처리
- 애플리케이션 서버에 ingest-complete 콜백 전송
- 사용자 질문의 `lookup` / `synthesis` 분류
- 선택한 PDF만 대상으로 청크 검색
- PDF별 부분 답변과 최종 종합 답변 생성
- 실제 사용한 출처만 최종 응답으로 반환
- Request ID 기반 구조화 로그 출력

### 애플리케이션 서버가 담당하는 작업

- 사용자 인증과 인가
- 사용자 파일 업로드
- IAM Role 기반 S3 접근
- Presigned GET URL 발급
- 파일 manifest 제공
- RAG 인제스트 요청 전송
- RAG 처리 완료 결과 수신
- 사용자가 선택한 참조문서 ID 전달
- 사용자에게 최종 답변과 처리 상태 제공

### RAG 서비스가 수행하지 않는 작업

- AWS Access Key 기반 S3 직접 접근
- AWS Secret Access Key 보관
- AWS Session Token 보관
- `boto3` 또는 AWS SDK 기반 파일 다운로드
- AWS Backend 데이터베이스 직접 수정
- 사용자 인증과 접근 권한 최종 판정
- Docker Desktop 프로그램 자체 실행
- Local RAG MySQL 또는 MariaDB 서버 자체 실행
- 공유기 포트 포워딩 설정
- DDNS 등록 또는 갱신
- TLS 인증서 발급과 HTTPS 종료
- OCR 처리
- TXT, DOCX, XLSX, PPTX 답변 생성

---

## 2. 전체 처리 구조

### PDF 인제스트 흐름

```text
사용자 파일 업로드
        ↓
애플리케이션 서버가 S3에 원본 PDF 저장
        ↓
애플리케이션 서버가 IAM Role로 Presigned GET URL 발급
        ↓
애플리케이션 서버가 RAG 서버의 POST /ingest 호출
        ↓
RAG 서버가 X-Internal-Token 검증
        ↓
RAG 서버가 파일 manifest 조회
        ↓
Presigned GET URL로 PDF 다운로드
        ↓
파일 크기·형식·해시·메타데이터 검증
        ↓
PDF 페이지 단위 텍스트 추출
        ↓
문서 청킹 및 청크 메타데이터 생성
        ↓
CUDA TEI 서버에서 임베딩 생성
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

### RAG 답변 흐름

```text
POST /api/v1/rag/answers
        ↓
내부 인증 및 요청 검증
        ↓
규칙 기반 질의 유형 분류
        ├─ lookup
        │    ↓
        │  선택 문서 범위에서 기존 단일 검색
        │    ↓
        │  기존 단일 프롬프트와 Claude 호출
        │    ↓
        │  기존 인용 검증과 응답 계약 유지
        │
        └─ synthesis
             ↓
           선택 PDF별 독립 검색
             ↓
           PDF별 청크 수와 전체 컨텍스트 제한
             ↓
           PDF별 부분 답변 생성 및 출처 검증
             ↓
           검증된 부분 답변만 최종 Claude 입력으로 구성
             ↓
           최종 종합 답변 생성
             ↓
           실제 인용한 출처만 응답으로 반환
```

---

## 3. 현재 구현 범위

### FastAPI 애플리케이션

- FastAPI 애플리케이션 팩토리
- FastAPI lifespan 기반 시작 및 종료 처리
- API v1 Router
- 루트 경로의 `POST /ingest`
- 내부 RAG 답변 API `POST /api/v1/rag/answers`
- 기존 청크 검색 API
- Liveness Health Check
- Readiness Health Check
- Swagger UI, ReDoc 및 OpenAPI JSON
- 전역 예외 처리
- 공통 성공 응답과 오류 응답
- 요청 검증 오류 변환
- 존재하지 않는 API의 공통 404 응답
- 내부 예외 정보 외부 응답 비노출

### 설정과 보안

- Python 3.12와 `uv` 기반 패키지 관리
- `pydantic-settings` 기반 환경별 설정
- `.env.local`, `.env.development`, `.env.test` 환경 분리
- 필수 환경 변수 타입과 형식 검증
- DB 비밀번호와 내부 토큰의 문자열 노출 방지
- `INTERNAL_TOKEN` 기반 RAG → 애플리케이션 서버 인증
- `RAG_INGEST_TOKEN` 기반 애플리케이션 서버 → RAG 인증
- Presigned URL과 민감 Query String 로그 마스킹
- Request ID 생성과 전달
- `X-Request-ID` 응답 헤더
- 구조화 JSON 로깅

### 파일 다운로드와 문서 처리

- Presigned GET URL 기반 HTTP Streaming 다운로드
- 연결 및 읽기 Timeout
- 일시적인 네트워크 오류 재시도
- 최대 파일 크기 제한
- 임시 파일 생성과 정리
- 파일 확장자 검증
- MIME Type 검증
- Magic Byte 기반 실제 파일 형식 검증
- 빈 파일과 손상된 파일 탐지
- SHA-256 Checksum 생성과 검증
- PDF 페이지 단위 텍스트 추출
- 결정적인 Chunk ID 생성
- 문서 및 청크 메타데이터 생성

### 임베딩과 VectorDB

- Hugging Face TEI 기반 임베딩 생성
- `Qwen/Qwen3-Embedding-0.6B`
- 1024차원 임베딩
- 임베딩 배치 처리
- Qdrant Collection 준비
- Qdrant Vector Upsert
- 사용자, 파일, 문서, 활성 상태 검색 payload 저장
- 신규 색인의 비활성 staging
- 신규 색인 활성화
- 이전 정상 색인 비활성화
- 실패한 신규 Point 보상 삭제
- 이전 정상 Point 복구
- Qdrant 클라이언트 지연 생성
- 불필요한 서버 버전 조회 경고 방지

### Local RAG DB와 색인 안정성

- SQLAlchemy AsyncIO와 AsyncMy 기반 비동기 MySQL 연결
- 요청 단위 `AsyncSession`
- 요청 실패 시 트랜잭션 Rollback
- 애플리케이션 종료 시 DB 연결 풀 정리
- 문서, 청크 및 색인 실행 이력 저장
- 동일 파일과 동일 버전에 대한 멱등 처리
- 파일 단위 동시 실행 직렬화
- 최신 색인 실행 소유권 검증
- Local RAG DB와 Qdrant 사이의 보상 처리
- 파서 버전과 임베딩 모델 변경을 고려한 재색인

---

## 4. 질의 유형 라우팅

### 질의 유형

| 유형 | 의미 | 처리 방식 |
| --- | --- | --- |
| `lookup` | 명시적인 다문서 비교·종합 의도가 없는 질문 | 기존 단일 검색·단일 생성 흐름 |
| `synthesis` | 두 개 이상의 PDF를 비교·대조·종합하려는 질문 | PDF별 부분 답변 후 최종 종합 |

### `lookup` 분류 조건

다음 조건 중 하나에 해당하면 `lookup`을 사용합니다.

- 참조문서가 한 개
- 여러 문서를 선택했지만 비교·대조·종합 의도가 명시되지 않음
- 단일 사실 조회 질문
- 기존 응답 흐름을 유지해야 하는 일반 질문

### `synthesis` 분류 조건

다음 조건을 모두 만족해야 합니다.

1. 선택한 참조문서가 두 개 이상
2. 질문에 명시적인 다문서 의도가 존재

규칙 기반 분류기는 다음 표현을 검사합니다.

#### 한국어 예시

- 비교
- 대조
- 차이점
- 공통점
- 유사점
- 상충
- 모순
- 종합
- 통합
- 취합
- 문서별
- 각 PDF
- 전체 문서 요약
- 여러 파일 분석

#### 영어 예시

- compare
- contrast
- difference
- similarity
- synthesize
- synthesis
- aggregate
- consolidate
- summarize all documents
- analyze multiple PDFs

### 하위 호환성

`lookup`은 기존 `RagAnswerService.answer()` 흐름을 그대로 사용합니다.

- 기존 검색 요청 구조 유지
- 기존 관련도 순서 유지
- 기존 단일 프롬프트 유지
- 기존 단일 Claude 호출 유지
- 기존 인용 검증 유지
- 기존 응답 필드 유지
- 기존 근거 부족 처리 유지

단순히 여러 PDF가 선택됐다는 이유만으로 `synthesis`로 변경하지 않습니다.

---

## 5. 다중 PDF synthesis 처리

### 5.1 PDF별 독립 검색

각 선택 PDF는 별도의 검색 요청으로 처리합니다.

```text
reference_file_idxs = [101, 202, 303]

검색 1: reference_file_idxs = [101]
검색 2: reference_file_idxs = [202]
검색 3: reference_file_idxs = [303]
```

이를 통해 다음 문제를 방지합니다.

- 특정 PDF가 전체 검색 결과를 독점하는 문제
- 선택하지 않은 PDF 청크가 섞이는 문제
- 여러 PDF의 청크가 하나의 부분 프롬프트에 혼입되는 문제
- PDF별 근거 존재 여부를 구분할 수 없는 문제

### 5.2 검색 결과 PDF별 그룹화

검색 결과는 `file_idx`를 기준으로 그룹화합니다.

한 그룹의 모든 청크는 다음 조건을 만족해야 합니다.

- 동일한 `file_idx`
- 동일한 `rag_document_idx`
- 동일한 `file_name`
- `file_type == PDF`
- 중복되지 않는 `chunk_id`

같은 파일명이더라도 `file_idx`가 다르면 별도 PDF로 취급합니다.

### 5.3 컨텍스트 제한

기본 정책은 다음과 같습니다.

| 제한 | 기본값 |
| --- | ---: |
| PDF별 최대 청크 수 | 3 |
| 청크별 최대 문자 수 | 6,000 |
| 전체 원문 컨텍스트 최대 문자 수 | 24,000 |

청크는 가능한 한 모든 PDF에 근거가 분배되도록 라운드 로빈 방식으로 선택합니다.

```text
1순위: PDF A 첫 번째 청크
2순위: PDF B 첫 번째 청크
3순위: PDF C 첫 번째 청크
4순위: PDF A 두 번째 청크
5순위: PDF B 두 번째 청크
...
```

전체 문자 예산이 부족하면 청크 원문만 제한하고 다음 메타데이터는 유지합니다.

- `chunk_id`
- `file_idx`
- `rag_document_idx`
- 페이지 정보
- 섹션 정보
- 파일명
- 관련도 점수

### 5.4 PDF별 부분 답변

각 PDF는 현재 PDF에 속한 청크만 사용하여 부분 답변을 생성합니다.

부분 생성 단계의 성공 조건은 다음과 같습니다.

- 현재 PDF가 전체 질문을 모두 답해야 하는 것은 아님
- 현재 PDF가 질문의 하위 항목 하나 이상을 직접 지원하면 `answered`
- 현재 PDF에서 확인되는 내용만 답변
- 다른 PDF가 필요한 나머지 항목은 추측하지 않음
- 어떤 하위 항목도 지원하지 못할 때만 `insufficient_evidence`

이 정책은 다음과 같은 질문에서 중요합니다.

```text
“PDF A의 보증 기간과 PDF B의 환불 조건을 비교해줘.”
```

PDF A가 보증 기간만 지원하고 PDF B가 환불 조건만 지원하더라도 두 부분 결과를 모두 보존한 뒤 최종 단계에서 결합합니다.

### 5.5 최종 종합 답변

최종 Claude 호출에는 원본 청크를 다시 전달하지 않습니다.

최종 입력에는 다음만 포함합니다.

- 사용자 질문
- 검증된 PDF별 부분 답변
- 부분 답변에서 실제 사용한 출처
- 전역으로 재매핑된 `SOURCE-N`

최종 모델은 다음을 수행합니다.

- PDF 간 공통점 정리
- PDF 간 차이점 정리
- 상충점 구분
- 질문에 필요한 결론 작성
- 실제 사용한 `SOURCE-N`만 인용
- 부분 답변에 없는 외부 지식 추가 금지

---

## 6. 출처 및 근거 계약

### 정상 답변 조건

정상 답변은 다음 조건을 모두 만족해야 합니다.

- `status == "answered"`
- 답변 본문에 한 개 이상의 `[SOURCE-N]` 존재
- `cited_source_ids`와 답변 본문의 최초 인용 순서 일치
- 모든 출처가 현재 프롬프트 후보에 존재
- 선택하지 않은 PDF 출처 미포함
- `sources`에는 실제 사용한 출처만 포함
- 중복 Source ID 없음
- 중복 Chunk ID 없음

### 전역 Source ID 재매핑

PDF별 부분 답변은 각각 로컬 `SOURCE-N`을 사용합니다.

```text
PDF A: SOURCE-1, SOURCE-2
PDF B: SOURCE-1
```

최종 종합 단계에서는 요청 전체에서 유일하도록 재매핑합니다.

```text
PDF A: SOURCE-1, SOURCE-2
PDF B: SOURCE-3
```

### 선택하지 않은 PDF 차단

다음 단계에서 모두 선택 범위를 검증합니다.

1. PDF별 검색 요청
2. 검색 응답
3. PDF 그룹
4. 부분 답변 출처
5. 최종 종합 후보
6. 최종 응답 `sources`

선택 범위를 벗어난 출처가 발견되면 정상 답변으로 반환하지 않습니다.

### 검색 결과 전체 부족

검색 결과가 전혀 없으면 Claude를 호출하지 않습니다.

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "제공된 문서 근거만으로는 답변할 수 없습니다.",
    "status": "insufficient_evidence",
    "sources": [],
    "model": null,
    "usage": null,
    "stop_reason": null
  }
}
```

### 일부 PDF 근거 부족

일부 PDF의 검색 결과가 없거나 부분 답변이 `insufficient_evidence`이면 해당 PDF만 최종 종합 후보에서 제외합니다.

하나 이상의 PDF가 검증된 부분 답변을 제공하면 최종 종합을 계속합니다.

### 모든 부분 답변 근거 부족

모든 PDF의 부분 답변이 `insufficient_evidence`이면 최종 Claude 호출을 생략합니다.

반환값:

- 고정 근거 부족 문구
- 빈 `sources`
- `model == null`
- `usage == null`
- `stop_reason == null`

---

## 7. Claude 호출 제한

### 답변 단위 제한

`lookup`의 단일 호출과 `synthesis`의 PDF별 부분 호출 및 최종 호출은 하나의 답변 예산으로 누적됩니다.

| 환경 변수 | 기본값 | 의미 |
| --- | ---: | --- |
| `JIPSA_RAG_ANTHROPIC_MAX_CALLS_PER_ANSWER` | 21 | 한 답변의 최대 Claude 호출 수 |
| `JIPSA_RAG_ANTHROPIC_MAX_INPUT_TOKENS_PER_ANSWER` | 400000 | 누적 입력 토큰 상한 |
| `JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS_PER_ANSWER` | 64000 | 누적 출력 토큰 상한 |
| `JIPSA_RAG_ANTHROPIC_MAX_CONCURRENT_REQUESTS` | 2 | 프로세스 동시 Claude 호출 수 |
| `JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS` | 4096 | 단일 호출 출력 토큰 상한 |

### 제한 방식

- 공급자 호출 전에 호출 횟수와 예상 입력 토큰 예약
- 남은 출력 예산에 맞춰 `max_tokens` 동적 축소
- 응답 후 실제 usage로 누적값 확정
- 공급자 오류 또는 취소 시 예약 토큰 반환
- 실제 usage가 예산을 초과하면 정상 답변 차단
- 프로세스 범위 공유 Semaphore로 동시 호출 제한
- 이벤트 루프별 Semaphore를 안전하게 관리

### 예산 초과 응답

```json
{
  "success": false,
  "code": "GENERATION_BUDGET_EXCEEDED",
  "message": "The generation budget for this answer was exceeded.",
  "data": null
}
```

HTTP 상태:

```text
429 Too Many Requests
```

---

## 8. 지원 형식과 제한 사항

### 지원 문서 형식

```text
지원됨: PDF
미지원: TXT, DOCX, XLSX, PPTX
```

현재 기본 Parser Factory에는 PDF 파서만 등록되어 있습니다.

### OCR

현재 PDF 처리는 텍스트 레이어를 추출합니다.

이미지만 포함된 스캔 PDF에는 OCR을 수행하지 않습니다. 텍스트 레이어가 없으면 문서 처리 또는 실제 E2E 계약에 따라 거부될 수 있습니다.

### Readiness 범위

`GET /api/v1/health/ready`는 Local RAG DB 연결 상태를 확인합니다.

Qdrant와 TEI의 준비 상태는 통합 실행 스크립트가 FastAPI 실행 전에 별도로 검증합니다.

### 로컬 인프라 범위

통합 실행 스크립트는 다음 요소를 자동 실행하지 않습니다.

- Docker Desktop
- Local RAG MySQL 또는 MariaDB
- 공유기 포트 포워딩
- DDNS 설정
- HTTPS Reverse Proxy

---

## 9. 로컬 실행 구성

| 구성 요소 | 기본 주소 | 용도 |
| --- | --- | --- |
| FastAPI Bind | `0.0.0.0:8077` | 모든 IPv4 인터페이스에서 요청 수신 |
| FastAPI Local | `http://127.0.0.1:8077` | RAG 실행 PC에서 로컬 접근 |
| FastAPI External | `http://rag.example.com:9802` | DDNS 및 포트 포워딩 예시 |
| Qdrant REST | `http://127.0.0.1:6333` | 로컬 VectorDB REST API |
| Qdrant gRPC | `127.0.0.1:6334` | 선택적 gRPC |
| TEI | `http://127.0.0.1:18081` | CUDA 임베딩 API |
| Local RAG DB | `127.0.0.1:3306` | 로컬 MySQL 또는 MariaDB |

Qdrant와 TEI는 Loopback 주소에만 바인딩하고 외부에 직접 노출하지 않습니다.

### 포트 포워딩 예시

```text
외부 요청
http://rag.example.com:9802
        ↓
공유기 TCP 9802 포트 포워딩
        ↓
RAG 실행 PC TCP 8077
        ↓
FastAPI 0.0.0.0:8077
```

---

## 10. 요구 환경

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

### 현재 GPU와 TEI 기준

```text
GPU: NVIDIA GeForce RTX 3060 Ti
CUDA Compute Capability: 8.6
TEI Image: ghcr.io/huggingface/text-embeddings-inference:86-1.9
Embedding Model: Qwen/Qwen3-Embedding-0.6B
Embedding Dimension: 1024
CUDA Validation Image: nvidia/cuda:12.9.0-base-ubuntu24.04
```

다른 NVIDIA GPU를 사용할 경우 TEI 이미지 태그와 CUDA Entrypoint가 해당 GPU 아키텍처에 적합한지 확인해야 합니다.

---

## 11. 설치

### 프로젝트 디렉터리 이동

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'
```

### Python 버전 확인

```powershell
python --version
```

예상 범위:

```text
Python 3.12.x
```

### uv 확인

```powershell
uv --version
```

### 의존성 동기화

```powershell
uv sync --frozen
```

`uv.lock`에 고정된 버전과 현재 환경의 의존성을 일치시킵니다.

### Docker 확인

```powershell
docker version
docker compose version
```

### Docker GPU 확인

```powershell
docker run --rm --gpus all `
    nvidia/cuda:12.9.0-base-ubuntu24.04 `
    nvidia-smi
```

---

## 12. 환경 변수

실제 값은 `.env.local`, `.env.development`, `.env.test` 또는 OS 환경 변수에 저장합니다.

Git에는 `.env.example`만 커밋합니다.

### 실행 환경 선택

```powershell
$env:JIPSA_RAG_APP_ENV = 'local'
```

허용 환경:

```text
local
development
test
```

### Application

```dotenv
JIPSA_RAG_APP_NAME="Jipsa RAG Service"
JIPSA_RAG_APP_VERSION=0.1.0
JIPSA_RAG_API_V1_PREFIX=/api/v1
JIPSA_RAG_HOST=0.0.0.0
JIPSA_RAG_PORT=8077
JIPSA_RAG_DEBUG=true
```

### 내부 인증

```dotenv
INTERNAL_TOKEN=CHANGE_ME_TO_SECURE_RANDOM_INTERNAL_TOKEN
RAG_INGEST_TOKEN=CHANGE_ME_TO_SECURE_RANDOM_RAG_INGEST_TOKEN
```

| 변수 | 방향 | 용도 |
| --- | --- | --- |
| `INTERNAL_TOKEN` | RAG → 애플리케이션 서버 | manifest 및 ingest-complete |
| `RAG_INGEST_TOKEN` | 애플리케이션 서버 → RAG | 인제스트 및 내부 API 인증 |

### Local RAG DB

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

### 파일 다운로드

```dotenv
JIPSA_RAG_S3_ALLOWED_KEY_PREFIX=files/
JIPSA_RAG_FILE_DOWNLOAD_ALLOWED_HOST_SUFFIXES=.amazonaws.com
JIPSA_RAG_FILE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS=5.0
JIPSA_RAG_FILE_DOWNLOAD_READ_TIMEOUT_SECONDS=60.0
JIPSA_RAG_FILE_DOWNLOAD_MAX_SIZE_BYTES=52428800
```

### TEI 임베딩

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
JIPSA_RAG_QDRANT_API_KEY=
JIPSA_RAG_QDRANT_TIMEOUT_SECONDS=10
```

### Claude

```dotenv
JIPSA_RAG_GENERATION_PROVIDER=anthropic
ANTHROPIC_API_KEY=CHANGE_ME_TO_ANTHROPIC_API_KEY
JIPSA_RAG_ANTHROPIC_MODEL=claude-sonnet-5
JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS=4096
JIPSA_RAG_ANTHROPIC_TIMEOUT_SECONDS=60
JIPSA_RAG_ANTHROPIC_MAX_CALLS_PER_ANSWER=21
JIPSA_RAG_ANTHROPIC_MAX_INPUT_TOKENS_PER_ANSWER=400000
JIPSA_RAG_ANTHROPIC_MAX_OUTPUT_TOKENS_PER_ANSWER=64000
JIPSA_RAG_ANTHROPIC_MAX_CONCURRENT_REQUESTS=2
```

실제 `ANTHROPIC_API_KEY`는 README, 소스 코드, 테스트 코드, Git 커밋 및 로그에 기록하지 않습니다.

### 애플리케이션 서버

```dotenv
JIPSA_RAG_APP_SERVER_BASE_URL=http://127.0.0.1:8080
JIPSA_RAG_APP_SERVER_API_V1_PREFIX=/api/v1
JIPSA_RAG_APP_SERVER_CONNECT_TIMEOUT_SECONDS=5.0
JIPSA_RAG_APP_SERVER_READ_TIMEOUT_SECONDS=30.0
JIPSA_RAG_APP_SERVER_MAX_ATTEMPTS=3
JIPSA_RAG_APP_SERVER_RETRY_INITIAL_DELAY_SECONDS=0.25
JIPSA_RAG_APP_SERVER_RETRY_MAX_DELAY_SECONDS=2.0
```

---

## 13. 로컬 RAG 통합 실행

### 실행 전 확인

- Docker Desktop 실행
- Docker Engine 준비
- Local RAG DB 실행
- 현재 환경의 dotenv 파일 작성
- DB 계정과 비밀번호 확인
- 내부 토큰 설정
- Anthropic API Key 설정
- 포트 `8077`, `6333`, `6334`, `18081`, `3306` 사용 가능
- NVIDIA Driver 정상
- Docker NVIDIA GPU 지원 정상
- 최초 모델 다운로드용 네트워크 연결

### 권장 실행

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location `
    -LiteralPath 'D:\Programming\INT2-Jipsa-Team04\RAG'

$env:JIPSA_RAG_APP_ENV = 'local'

.\scripts\start-local-rag.ps1
```

### 실행 스크립트 처리 순서

1. 프로젝트 루트 확인
2. 필수 파일과 환경 변수 확인
3. 실제 Pydantic Settings 검증
4. Embedding Provider와 VectorDB Provider 검증
5. 포트 및 URL 정합성 검증
6. Docker Engine과 Compose 확인
7. Qdrant와 TEI 이미지 준비
8. Docker NVIDIA GPU 검증
9. Qdrant 컨테이너 시작
10. Qdrant `/readyz` 확인
11. TEI CUDA 컨테이너 시작
12. TEI 이미지와 GPU 할당 확인
13. CUDA 초기화 및 CPU 폴백 검사
14. 실제 `/embed` 요청 실행
15. FastAPI Foreground 실행
16. FastAPI 종료 후 Qdrant와 TEI 자동 정지
17. 임시 환경 변수 및 작업 경로 복원

### 준비 상태 제한 시간

| 대상 | 최대 대기 시간 |
| --- | ---: |
| Qdrant | 120초 |
| TEI 모델 다운로드·CUDA 초기화·Warmup | 1200초 |

---

## 14. 서버 접근 주소

### 로컬 주소

| 기능 | 주소 |
| --- | --- |
| Liveness | `http://127.0.0.1:8077/api/v1/health/live` |
| Readiness | `http://127.0.0.1:8077/api/v1/health/ready` |
| Swagger UI | `http://127.0.0.1:8077/docs` |
| ReDoc | `http://127.0.0.1:8077/redoc` |
| OpenAPI JSON | `http://127.0.0.1:8077/openapi.json` |
| Ingest | `http://127.0.0.1:8077/ingest` |
| RAG Answer | `http://127.0.0.1:8077/api/v1/rag/answers` |

### RAG 답변 요청 예시

```json
{
  "user_idx": 45,
  "reference_file_idxs": [123, 456],
  "query": "두 PDF를 비교하여 공통점과 차이점을 알려줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

### 주요 요청 제약

| 필드 | 타입 | 필수 | 제약 |
| --- | --- | --- | --- |
| `user_idx` | integer | 예 | 0보다 큰 정수 |
| `reference_file_idxs` | integer array | 예 | 1개 이상 20개 이하, 중복 금지 |
| `query` | string | 예 | 1자 이상 4096자 이하 |
| `top_k` | integer | 아니요 | 기본값 5, 1 이상 20 이하 |
| `score_threshold` | number/null | 아니요 | -1.0 이상 1.0 이하 |

상세 계약:

```text
docs/api/rag-answer-contract.md
```

---

## 15. 로컬 RAG 종료

### 정상 종료

FastAPI를 실행한 PowerShell 창에서 `Ctrl+C`를 입력합니다.

FastAPI가 정상 종료되면 통합 실행 스크립트가 Qdrant와 TEI 컨테이너를 자동으로 정지합니다.

### 수동 종료

```powershell
Set-Location `
    -LiteralPath 'D:\Programming\INT2-Jipsa-Team04\RAG'

.\scripts\stop-local-rag.ps1
```

### 종료 후 유지되는 리소스

- Qdrant Collection
- Qdrant Vector와 payload
- Qdrant Index
- Qdrant Snapshot
- Qdrant Storage Named Volume
- Qdrant Snapshot Named Volume
- Hugging Face 모델 Cache Named Volume
- Docker 이미지
- Docker 컨테이너 정의

### 사용하지 않는 삭제 명령

```text
docker compose down --volumes
docker compose down -v
docker volume rm
```

일반 종료 절차에서 Volume을 삭제하지 않습니다.

---

## 16. 테스트

### 일반 전체 테스트

실제 E2E 환경 변수의 잔존으로 테스트 범위가 오염되지 않도록 먼저 제거합니다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location `
    -LiteralPath 'D:\Programming\INT2-Jipsa-Team04\RAG'

Remove-Item `
    -LiteralPath 'Env:JIPSA_RAG_RUN_E2E' `
    -ErrorAction SilentlyContinue

$env:JIPSA_RAG_APP_ENV = 'test'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

uv run pytest `
    -W 'error:Failed to obtain server version.*:UserWarning'
```

최신 검증 결과:

```text
594 passed, 12 skipped
```

`12 skipped`는 실제 PDF·Qdrant·CUDA TEI·Claude E2E가 명시적 Opt-in 없이 실행되지 않은 정상 결과입니다.

### 질의 라우팅 및 생성 제한 집중 테스트

```powershell
uv run pytest `
    tests/unit/services/test_query_routing.py `
    tests/unit/services/test_query_routing_evidence_contract.py `
    tests/unit/infrastructure/generation/test_limited.py `
    tests/integration/test_rag_generation_budget_contract.py `
    tests/unit/infrastructure/indexing/test_qdrant_lazy_client.py `
    -W 'error:Failed to obtain server version.*:UserWarning' `
    -vv
```

검증 대상:

- lookup/synthesis 분류
- 기존 lookup 하위 호환성
- PDF별 그룹화
- 컨텍스트 제한
- PDF별 부분 답변
- 일부 하위 항목만 지원하는 PDF의 부분 근거 보존
- 최종 종합 답변
- 실제 출처 재매핑
- 선택하지 않은 PDF 차단
- 일부·전체 근거 부족
- Claude 호출 횟수 제한
- 입력·출력 토큰 제한
- 동시성 제한
- 민감 원문 로그 비노출
- Qdrant 지연 생성
- Qdrant 버전 조회 실패 경고 재발 방지

### 실제 Local RAG E2E

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-real-rag-e2e.ps1' `
    -SkipQualityGate
```

실제 E2E 범위:

- 실제 PDF 다운로드와 파싱
- 실제 청킹
- Local RAG DB 저장 상태
- Qdrant Point와 payload
- CUDA TEI 실제 `/embed`
- 실제 Claude 단일 PDF 답변
- 실제 Claude 다중 PDF synthesis 답변
- 일부·전체 근거 부족
- 생성 예산 제한
- 실제 인용 정합성
- 선택하지 않은 PDF 차단
- 질문·청크·프롬프트·API Key 로그 비노출
- 테스트 종료 후 인프라 정리

최신 검증 결과:

```text
Local RAG DB 연결: 1 passed
실제 PDF E2E: 12 passed
Qdrant /readyz: 성공
CUDA TEI /embed: 성공
Qdrant 서버 버전 조회 실패 경고: 없음
```

---

## 17. 코드 품질 검사

### 전체 품질 게이트

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location `
    -LiteralPath 'D:\Programming\INT2-Jipsa-Team04\RAG'

Remove-Item `
    -LiteralPath 'Env:JIPSA_RAG_RUN_E2E' `
    -ErrorAction SilentlyContinue

$env:JIPSA_RAG_APP_ENV = 'test'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

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
    throw "Mypy 타입 검사 실패. 종료 코드: $LASTEXITCODE"
}

uv run pytest `
    -W 'error:Failed to obtain server version.*:UserWarning'

if ($LASTEXITCODE -ne 0) {
    throw "Pytest 실패. 종료 코드: $LASTEXITCODE"
}

uv lock --check

if ($LASTEXITCODE -ne 0) {
    throw "uv.lock 검사 실패. 종료 코드: $LASTEXITCODE"
}

git diff --check

if ($LASTEXITCODE -ne 0) {
    throw "Git whitespace 검사 실패. 종료 코드: $LASTEXITCODE"
}

Write-Host 'RAG 전체 품질 검사 통과' `
    -ForegroundColor Green
```

최신 품질 결과:

```text
Ruff format: 156 files already formatted
Ruff check: All checks passed
Mypy: Success, no issues found in 156 source files
Pytest: 594 passed, 12 skipped
uv.lock: 통과
git diff --check: 통과
```

---

## 18. 보안 주의 사항

### Git에 커밋하지 않는 값

- Local RAG DB 실제 비밀번호
- `INTERNAL_TOKEN`
- `RAG_INGEST_TOKEN`
- `ANTHROPIC_API_KEY`
- Presigned GET URL
- Presigned URL Query String
- 사용자 질문 원문
- 사용자 업로드 파일 원문
- 검색 청크 원문
- PDF별 부분 답변 원문
- 최종 Claude 응답 원문
- 개인정보
- 세션 및 인증 토큰
- 운영 환경 내부 주소

### 사용하지 않는 AWS 자격 증명

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
```

RAG는 AWS 자격 증명을 보관하거나 사용하지 않습니다.

### 로그 비노출 대상

다음 값은 정상, 근거 부족, 예산 초과 및 공급자 실패 경로 모두에서 로그와 예외 메시지에 포함하지 않습니다.

- 사용자 질문
- 검색 청크
- 출처 발췌문
- 시스템 프롬프트
- 사용자 프롬프트
- PDF별 부분 답변
- 최종 답변
- 구조화 출력 JSON
- Anthropic API Key
- 내부 인증 토큰
- Presigned URL 및 Query String

로그에는 다음과 같은 안전한 메타데이터만 기록합니다.

- 이벤트 이름
- 사용자 식별자
- 참조문서 개수
- 검색 결과 개수
- PDF 그룹 개수
- 부분 답변 개수
- 출처 개수
- 안전한 오류 유형
- HTTP 상태 코드

### 외부 HTTP 노출

HTTP는 전송 구간을 암호화하지 않습니다.

개발 검증을 넘어 지속적으로 외부에 노출할 경우 다음을 적용해야 합니다.

- HTTPS Reverse Proxy
- TLS 인증서
- 접근 IP 제한
- Windows 방화벽
- 공유기 방화벽
- 강한 내부 인증 토큰
- 토큰 정기 교체

---

## 19. 문제 해결

### Docker Engine 연결 실패

```powershell
docker version
```

확인:

1. Docker Desktop 실행
2. Engine Running 상태
3. WSL2 Backend 정상
4. Windows 재부팅 직후 준비 완료 대기

### NVIDIA GPU 사용 불가

```powershell
nvidia-smi
docker info
docker run --rm --gpus all `
    nvidia/cuda:12.9.0-base-ubuntu24.04 `
    nvidia-smi
```

### TEI CPU 폴백

통합 실행 스크립트는 CPU 폴백을 정상 상태로 인정하지 않습니다.

오류 예시:

```text
CUDA_ERROR_NO_DEVICE
Using CPU instead
Starting Qwen3 model on Cpu
```

### GPU 메모리 부족

```powershell
nvidia-smi
docker ps --all
```

불필요한 GPU 프로세스와 컨테이너를 종료한 후 다시 실행합니다.

### Qdrant 준비 Timeout

```powershell
docker logs --tail 200 jipsa-qdrant
```

확인:

- Storage Volume
- Snapshot 복구
- 포트 충돌
- 설정 오류
- `/readyz`

### TEI 준비 Timeout

```powershell
docker logs --tail 200 jipsa-embedding
```

확인:

- 모델 다운로드
- CUDA 초기화
- GPU 메모리
- Weight 로드
- Warmup
- `/embed`

### 포트 충돌

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

### Local RAG DB 연결 실패

확인:

- DB 프로세스 실행 여부
- Host와 Port
- DB 이름
- 사용자 계정
- 비밀번호
- 접근 권한
- 방화벽
- `JIPSA_RAG_DATABASE_CHECK_ON_STARTUP`

### 일반 Pytest에서 실제 E2E가 실행됨

현재 PowerShell 세션에 `JIPSA_RAG_RUN_E2E=1`이 남아 있을 수 있습니다.

```powershell
Remove-Item `
    -LiteralPath 'Env:JIPSA_RAG_RUN_E2E' `
    -ErrorAction SilentlyContinue
```

### Qdrant 서버 버전 조회 경고

현재 구현은 Qdrant 클라이언트를 실제 저장 또는 검색 시점에 지연 생성합니다.

다음 경고를 테스트에서 오류로 승격하여 재발 여부를 검사합니다.

```powershell
uv run pytest `
    -W 'error:Failed to obtain server version.*:UserWarning'
```

이 검사가 통과하면 버전 조회 실패 경고가 발생하지 않은 것입니다.

---

## 20. 주요 파일과 책임

| 경로 | 책임 |
| --- | --- |
| `README.md` | Local RAG 구조, 실행, 테스트, 보안 및 장애 대응 |
| `README.html` | README의 독립 실행형 HTML 문서 |
| `.env.example` | 환경별 dotenv 작성 기준 |
| `pyproject.toml` | Python 의존성, Entry Point, Ruff, Mypy, Pytest |
| `uv.lock` | 재현 가능한 의존성 버전 |
| `scripts/start-local-rag.ps1` | Qdrant·TEI 준비, GPU 검증, FastAPI 실행 |
| `scripts/stop-local-rag.ps1` | Qdrant·TEI 안전 정지 |
| `scripts/run-real-rag-e2e.ps1` | 실제 PDF·DB·Qdrant·CUDA TEI·Claude E2E |
| `infra/qdrant/compose.yaml` | Qdrant, TEI, GPU Reservation, Volume |
| `infra/qdrant/cuda-entrypoint.sh` | TEI CUDA 실행 보정 |
| `src/jipsa_rag/main.py` | FastAPI 생성, lifespan, Router 등록 |
| `src/jipsa_rag/core/config.py` | 공통 환경 변수 로드와 검증 |
| `src/jipsa_rag/core/generation_config.py` | Claude 모델과 답변 예산 설정 |
| `src/jipsa_rag/core/logging.py` | 구조화 로그와 민감정보 보호 |
| `src/jipsa_rag/api/ingest.py` | 루트 `POST /ingest` |
| `src/jipsa_rag/api/v1/endpoints/rag_answer.py` | RAG 답변 API, 제한기 주입, 오류 변환 |
| `src/jipsa_rag/infrastructure/document/parsers/pdf.py` | PDF 텍스트 추출 |
| `src/jipsa_rag/infrastructure/embedding` | TEI 임베딩 |
| `src/jipsa_rag/infrastructure/generation/claude.py` | Anthropic Claude 클라이언트 |
| `src/jipsa_rag/infrastructure/generation/limited.py` | 호출 수, 토큰, 동시성 제한 |
| `src/jipsa_rag/infrastructure/indexing/qdrant_store.py` | Qdrant 저장과 지연 클라이언트 |
| `src/jipsa_rag/infrastructure/indexing/qdrant_search.py` | Qdrant 검색과 지연 클라이언트 |
| `src/jipsa_rag/services/file_indexing.py` | 인제스트, 활성 전환, 멱등성, 보상 |
| `src/jipsa_rag/services/prompt_builder.py` | 근거 프롬프트와 출처 후보 구성 |
| `src/jipsa_rag/services/rag_answer.py` | 기존 lookup 답변 및 인용 검증 |
| `src/jipsa_rag/services/query_routing.py` | lookup/synthesis 분류, PDF별 부분 답변, 최종 종합 |
| `docs/api/rag-answer-contract.md` | RAG 답변 요청·응답 및 오류 계약 |
| `tests/unit` | 단위 테스트 |
| `tests/integration` | 통합 계약 테스트 |
| `tests/e2e` | 실제 Local RAG E2E |

---

## 21. 운영 체크리스트

### 최초 실행 전

- [ ] Python 3.12 설치
- [ ] `uv` 설치
- [ ] `uv sync --frozen` 완료
- [ ] Docker Desktop 실행
- [ ] Docker Compose v2 확인
- [ ] NVIDIA Driver 확인
- [ ] Docker GPU 사용 가능
- [ ] Local RAG DB 실행
- [ ] Local RAG DB Schema 적용
- [ ] `.env.local` 또는 대상 환경 파일 작성
- [ ] 실제 DB 비밀번호 설정
- [ ] 내부 인증 토큰 설정
- [ ] Anthropic API Key 설정
- [ ] Qdrant, TEI, FastAPI 포트 사용 가능
- [ ] 외부 주소 사용 시 방화벽과 포트 포워딩 확인

### 기능 검증

- [ ] PDF 인제스트 성공
- [ ] Local RAG DB 문서·청크 저장
- [ ] Qdrant Point와 payload 저장
- [ ] TEI CUDA `/embed` 성공
- [ ] lookup 답변 성공
- [ ] synthesis 답변 성공
- [ ] 실제 인용 출처 일치
- [ ] 선택하지 않은 PDF 출처 차단
- [ ] 일부 근거 부족 처리
- [ ] 전체 근거 부족 시 Claude 미호출
- [ ] 호출·토큰·동시성 제한
- [ ] 질문·청크·프롬프트 로그 비노출

### PR 전 검증

- [ ] `uv sync --frozen`
- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run mypy src tests`
- [ ] `uv run pytest`
- [ ] `uv lock --check`
- [ ] `git diff --check`
- [ ] 실제 PDF E2E
- [ ] Qdrant 버전 조회 경고 재발 없음
- [ ] README와 API 계약 문서 일치

---

## 참고 문서

- RAG 답변 API 계약: `docs/api/rag-answer-contract.md`
- 환경 변수 예시: `.env.example`
- 실제 E2E 실행: `scripts/run-real-rag-e2e.ps1`
- Local RAG 통합 실행: `scripts/start-local-rag.ps1`
- Local RAG 종료: `scripts/stop-local-rag.ps1`
