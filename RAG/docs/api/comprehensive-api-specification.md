# Jipsa Local RAG 종합 API 명세서

> **문서 상태:** Stable · Local RAG API 통합 Source of truth  
> **주 독자:** AWS Backend·Local RAG·Frontend 연동 개발자, QA, SRE, 보안 리뷰어  
> **최종 검토:** 2026-07-28 · `docs/129` 구현 기준  
> **종합 명세 버전:** `1.0.0`  
> **답변 계약 버전:** `1.3.0`  
> **구현 기준:** Pydantic 스키마 → FastAPI 라우터 → 서비스·인프라 코드 → 테스트  
> **변경 시 함께 검토:** OpenAPI, Backend DTO, README, 개별 API 문서, 오류 코드, 회귀 테스트

이 문서는 Jipsa Local RAG가 제공하거나 호출하는 HTTP 계약을 한곳에서 확인할 수 있도록
정리한 **종합 API 명세서**입니다. 정상 요청만 나열하지 않고 인증 방향, 엄격한 입력 검증,
응답 envelope, 오류 코드, 재시도, 멱등성, 인제스트 콜백, 검색 범위, 답변 인용,
`source_locator`, 관측성과 알려진 비보장 사항까지 구현 수준으로 정의합니다.

> 이 문서는 Local RAG 범위만 다룹니다. 사용자 로그인, 최종 권한 판정, 파일 업로드,
> S3 객체 관리와 Backend 외부 API는 AWS Backend 문서의 책임입니다.

## 1. 60초 요약

### 1.1 핵심 계약

- Local RAG의 업무 API는 **7개**입니다.
- Local RAG가 AWS Backend에서 사용하는 내부 API는 **2개**입니다.
- `GET /api/v1/health/live`와 `GET /api/v1/health/ready`만 내부 토큰 없이 호출합니다.
- 나머지 Local RAG 업무 API는 `X-Internal-Token`이 필수입니다.
- 검색과 답변은 요청마다 전달된 `reference_file_idxs`만 사용합니다.
- 검색 범위는 `사용자 일치 + 활성 청크 + 선택 파일`의 교집합입니다.
- 답변의 `[SOURCE-N]`, `cited_source_ids`, `sources`는 최초 등장 순서가 같아야 합니다.
- `/ingest`는 최신 Backend manifest를 다시 조회하고 성공 또는 실패 콜백을 보냅니다.
- `/api/v1/files/process`는 전달된 manifest를 직접 처리하며 Backend 콜백을 보내지 않습니다.
- 모든 응답은 `X-Request-ID`를 포함하지만, 현재 Local RAG의 Backend outbound client는
  이 ID를 Backend 재호출 헤더로 전파하지 않습니다.

### 1.2 API 표면

| 방향 | 인증 | Method | Path | 핵심 목적 |
|---|---|---|---|---|
| Backend → Local RAG | 필요 | `POST` | `/ingest` | 최신 manifest 재조회, 색인, Backend callback |
| Backend → Local RAG | 필요 | `POST` | `/api/v1/files/process` | 전달 manifest 직접 처리·색인 |
| Monitoring → Local RAG | 불필요 | `GET` | `/api/v1/health/live` | FastAPI 프로세스 생존 확인 |
| Monitoring → Local RAG | 불필요 | `GET` | `/api/v1/health/ready` | Local RAG DB 연결 준비 확인 |
| Backend → Local RAG | 필요 | `GET` | `/api/v1/diagnostics/network` | bind·외부 주소·egress 진단 |
| Backend → Local RAG | 필요 | `POST` | `/api/v1/chunks/search` | 선택 문서 활성 청크 검색 |
| Backend → Local RAG | 필요 | `POST` | `/api/v1/rag/answers` | 선택 문서 기반 lookup·synthesis 답변 |
| Local RAG → Backend | 필요 | `GET` | `/internal/files/{file_idx}/manifest` | 최신 파일 manifest 조회 |
| Local RAG → Backend | 필요 | `POST` | `/internal/files/{file_idx}/ingest-complete` | 성공·실패와 최신 청크 동기화 |

## 2. 문서 사용법과 Source of truth

### 2.1 문서 우선순위

구현과 문서가 충돌하면 다음 순서로 판정합니다.

1. Pydantic 요청·응답 스키마와 validator
2. FastAPI 라우터의 path, dependency, response model
3. 서비스 계층의 범위·인용·부분 실패 불변식
4. 외부 HTTP client, DB, Qdrant와 TEI 구현
5. 단위·통합·실제 E2E 테스트
6. 이 종합 명세와 개별 상세 문서

문서 불일치는 구현을 추측해 변경하는 근거가 아닙니다. 구현 사실을 확인하고 같은 변경
단위에서 문서, OpenAPI 설명, 회귀 테스트를 함께 수정합니다.

### 2.2 관련 상세 문서

- [API 거버넌스·버전·호환성](api-governance-and-compatibility.md)
- [관련 청크 검색 API 상세](../chunk-search-api.md)
- [RAG 답변 API 연동 계약](rag-answer-api-contract.md)
- [답변·인용·Source Locator 상세](rag-answer-contract.md)
- [AWS Backend와 Local RAG 책임 경계](../architecture/responsibility-boundary.md)
- [재인제스트·보상·콜백 정책](../operations/ingest-recovery-policy.md)
- [환경 변수와 비밀정보](../security/environment-and-secrets.md)
- [관측성과 문제 해결](../operations/observability-and-troubleshooting.md)

## 3. 시스템 경계와 호출 방향

```text
사용자·Frontend
      │
      │ 사용자 인증, 파일 선택, 질문
      ▼
AWS Backend
  ├─ 사용자·파일 권한 최종 판정
  ├─ File_IDX·Users_IDX 관리
  ├─ S3 및 Presigned GET URL 발급
  └─ Local RAG 호출
      │  X-Internal-Token = RAG_INGEST_TOKEN
      ▼
Local RAG FastAPI
  ├─ 문서 다운로드·검증·파싱·OCR·청킹
  ├─ CUDA TEI 임베딩
  ├─ Local RAG DB·Qdrant 색인과 검색
  └─ Claude lookup·synthesis
      │  X-Internal-Token = INTERNAL_TOKEN
      ▼
AWS Backend /internal/files/**
```

### 3.1 신뢰 경계

| 책임 | AWS Backend | Local RAG |
|---|---:|---:|
| 사용자 인증·인가 | 최종 책임 | 수행하지 않음 |
| 파일 소유권·활성 상태 판정 | 최종 책임 | 요청 범위와 색인 payload를 재검증 |
| S3 IAM·장기 자격 증명 | 보유·관리 | 보유하지 않음 |
| Presigned GET URL 발급 | 수행 | 다운로드에만 사용 |
| Local DB·Qdrant | 직접 관리하지 않음 | 소유·관리 |
| OCR·임베딩·검색·생성 | 호출·중계 | 수행 |
| 최종 사용자 응답 | 전달 | 내부 API 응답 생성 |

## 4. Base URL, 버전과 프레임워크 경로

### 4.1 업무 API Base URL

코드 기본값은 다음과 같습니다.

```text
Local RAG bind: http://127.0.0.1:8000
API v1 prefix: /api/v1
AWS Backend base: http://127.0.0.1:8080
```

프로젝트의 실제 `.env.local`에서는 다른 host·port를 사용할 수 있습니다. 문서의 URL을
하드코딩해 운영 주소로 간주하지 말고 `JIPSA_RAG_HOST`, `JIPSA_RAG_PORT`,
`JIPSA_RAG_API_V1_PREFIX`, `JIPSA_RAG_APP_SERVER_BASE_URL` 설정을 확인합니다.

### 4.2 버전 경계

- `/ingest`는 Backend 기존 클라이언트 호환 때문에 v1 prefix를 사용하지 않습니다.
- 나머지 업무 API는 기본적으로 `/api/v1` 아래에 있습니다.
- `JIPSA_RAG_API_V1_PREFIX`를 바꾸면 v1 업무 경로도 함께 바뀝니다.
- 이 문서의 종합 명세 버전 `1.0.0`은 문서 통합 버전입니다.
- 답변 payload와 인용 계약 버전은 별도로 `1.3.0`을 사용합니다.

### 4.3 FastAPI 자동 문서 경로

FastAPI 기본 설정을 비활성화하지 않았기 때문에 현재 런타임에는 다음 프레임워크 경로가
생성됩니다.

| Path | 용도 | 안정성 |
|---|---|---|
| `/openapi.json` | OpenAPI schema | 구현 반영 확인용, 업무 API 안정 계약 아님 |
| `/docs` | Swagger UI | 개발·검토용 |
| `/redoc` | ReDoc | 개발·검토용 |

이 경로의 외부 노출 여부는 배포 네트워크 정책으로 제한해야 합니다. 소비자는 Swagger UI
존재를 서비스 가용성 판단이나 업무 계약으로 사용하지 않습니다.

## 5. 공통 HTTP 계약

### 5.1 Content-Type과 문자 인코딩

- JSON 요청은 `Content-Type: application/json`을 사용합니다.
- JSON 응답은 UTF-8로 직렬화됩니다.
- 파일 바이트를 Local RAG API body에 직접 업로드하지 않습니다.
- 파일은 Backend가 발급한 HTTPS Presigned GET URL로 다운로드합니다.
- Local RAG → Backend callback은 JSON을 사용하고 정상 수신 시 `204 No Content`를 기대합니다.

### 5.2 공통 요청 헤더

| 헤더 | 필수 | 적용 범위 | 의미 |
|---|---:|---|---|
| `X-Internal-Token` | 조건부 필수 | 보호 API | 서비스 간 공유 시크릿 |
| `X-Request-ID` | 선택 | 모든 inbound API | UUID 형식 요청 추적 ID |
| `Content-Type` | JSON body에서 필수 | POST JSON API | `application/json` |
| `Accept` | 권장 | 모든 API | `application/json` |

### 5.3 `X-Request-ID`

- 유효한 UUID를 전달하면 표준 UUID 문자열로 정규화해 같은 값을 사용합니다.
- 누락되거나 UUID 형식이 아니면 Local RAG가 UUIDv4를 새로 생성합니다.
- 성공·오류 응답 모두 `X-Request-ID` 헤더를 포함합니다.
- 구조화 로그의 요청 시작·완료·오류를 같은 값으로 연결합니다.
- 인증·인가 수단이 아니며 사용자 또는 파일 범위를 대체하지 않습니다.
- **현재 outbound 미전파:** 현재 구현은 Local RAG가 Backend manifest·callback을 호출할 때 inbound
  `X-Request-ID`를 outbound 헤더로 명시적으로 전파하지 않습니다.** 따라서 현재
  cross-service correlation은 자동 보장 계약이 아닙니다.

### 5.4 공통 성공 envelope

```json
{
  "success": true,
  "code": "DOMAIN_COMPLETED",
  "message": "Public success message.",
  "data": {}
}
```

| 필드 | 타입 | 규칙 |
|---|---|---|
| `success` | boolean | 성공이면 `true` |
| `code` | string | 비어 있지 않은 안정적 처리 코드 |
| `message` | string | 외부 공개 가능한 메시지 |
| `data` | object 또는 null | endpoint별 응답 데이터 |

### 5.5 공통 오류 envelope

```json
{
  "success": false,
  "code": "PUBLIC_ERROR_CODE",
  "message": "Public error message.",
  "data": null
}
```

내부 예외 원문, SQL, 로컬 경로, Presigned URL, API Key, 토큰, 프롬프트와 임베딩 벡터는
오류 body에 포함하지 않습니다.

### 5.6 요청 검증 오류

일반 요청 검증 실패는 HTTP `422`와 다음 구조를 사용합니다.

```json
{
  "success": false,
  "code": "REQUEST_VALIDATION_FAILED",
  "message": "Request validation failed.",
  "data": {
    "errors": [
      {
        "field": "body.reference_file_idxs.0",
        "message": "Input should be a valid integer",
        "error_type": "int_type"
      }
    ]
  }
}
```

검증 오류는 필드 위치·공개 메시지·Pydantic 오류 유형만 반환합니다. 전체 요청 body나
질문 원문을 복사하지 않습니다.

### 5.7 엄격한 입력 정책

- Pydantic 요청 모델은 `extra="forbid"`를 사용합니다.
- 정의되지 않은 필드는 무시하지 않고 `422`로 거부합니다.
- `reference_file_idxs`는 strict integer이므로 문자열, 실수와 boolean을 정수로 변환하지 않습니다.
- `NaN`, `Infinity`와 `-Infinity`는 점수 필드에서 허용하지 않습니다.
- 문자열은 모델별 정책에 따라 앞뒤 공백을 제거하되, Presigned URL 원문과 청크 원문은
  서명·Hash 무결성을 위해 임의 재작성하지 않습니다.

## 6. 인증·보안 계약

### 6.1 Backend → Local RAG

```http
X-Internal-Token: <RAG_INGEST_TOKEN>
```

Local RAG 설정 이름:

- `RAG_INGEST_TOKEN`
- 호환 별칭 `JIPSA_RAG_INGEST_TOKEN`

### 6.2 Local RAG → Backend

```http
X-Internal-Token: <INTERNAL_TOKEN>
```

Local RAG 설정 이름:

- `INTERNAL_TOKEN`
- 호환 별칭 `JIPSA_RAG_INTERNAL_TOKEN`

두 토큰은 각각 32자 이상 512자 이하이며 호출 방향이 다릅니다. 이름이 비슷하다는 이유로
서로 교체하지 않습니다.

### 6.3 인증 실패 의미

| 상황 | HTTP | Code | 재시도 판단 |
|---|---:|---|---|
| 보호 API 헤더 누락 | 401 | `UNAUTHORIZED` | 토큰 설정 전 재시도 금지 |
| 보호 API 토큰 불일치 | 401 | `UNAUTHORIZED` | 토큰 수정 전 재시도 금지 |
| Local RAG inbound 토큰 미설정 | 503 | `SERVICE_UNAVAILABLE` | 서버 설정 수정 후 재시도 |
| Local RAG outbound 토큰 미설정 | 503 | `SERVICE_UNAVAILABLE` | 서버 설정 수정 후 재시도 |

토큰 비교는 `secrets.compare_digest()`로 수행합니다. 헤더 누락과 불일치는 외부 응답에서
같은 인증 실패로 처리해 설정 상태 추측을 줄입니다.

### 6.4 공개 endpoint

다음 두 endpoint는 모니터링을 위해 내부 토큰 dependency가 없습니다.

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`

네트워크 진단은 구성 정보를 포함하므로 공개하지 않고 내부 토큰으로 보호합니다.

## 7. Inbound endpoint 종합표

| Method·Path | 인증 | Body | 성공 Code | 주요 외부 의존성 | 상태 변경 |
|---|---|---|---|---|---:|
| `POST /ingest` | 필요 | `FileProcessingRequest` handoff | `FILE_INDEXING_COMPLETED` | Backend·S3 URL·Office·OCR·TEI·DB·Qdrant | 예 |
| `POST /api/v1/files/process` | 필요 | `FileProcessingRequest` | `FILE_INDEXING_COMPLETED` | S3 URL·Office·OCR·TEI·DB·Qdrant | 예 |
| `GET /api/v1/health/live` | 불필요 | 없음 | `SUCCESS` | 없음 | 아니오 |
| `GET /api/v1/health/ready` | 불필요 | 없음 | `SUCCESS` | Local RAG DB | 아니오 |
| `GET /api/v1/diagnostics/network` | 필요 | 없음 | `SUCCESS` | 공인 IP 조회 서비스 | 아니오 |
| `POST /api/v1/chunks/search` | 필요 | `ChunkSearchRequest` | `CHUNK_SEARCH_COMPLETED` | TEI·Qdrant | 아니오 |
| `POST /api/v1/rag/answers` | 필요 | `RagAnswerRequest` | `RAG_ANSWER_COMPLETED` | TEI·Qdrant·Claude | 아니오 |

## 8. `POST /ingest`

### 8.1 목적

Backend가 파일 처리 시작을 Local RAG에 handoff합니다. 요청 body 전체를 최종 manifest로
신뢰하지 않고 `file_idx`로 Backend의 최신 manifest를 다시 조회한 뒤 처리합니다.

```text
inbound handoff
  → 최신 manifest GET
  → 다운로드·검증
  → 파싱·OCR·청킹
  → TEI 임베딩
  → Local DB·Qdrant 색인
  → 최신 활성 snapshot lock
  → ingest-complete callback
  → 현재 요청의 처리 결과 응답
```

### 8.2 요청

```http
POST /ingest
Content-Type: application/json
X-Internal-Token: <RAG_INGEST_TOKEN>
X-Request-ID: 6e5c8cc7-b650-4216-a64c-95d42ef172c1
```

```json
{
  "file_idx": 123,
  "user_idx": 45,
  "folder_idx": 9,
  "file_name": "2026 Q3 회의록.docx",
  "file_type": "docx",
  "download_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/files/example.docx?X-Amz-Signature=REDACTED",
  "url_expires_in": 900
}
```

> 현재 구현에서 `file_idx` 외 handoff 필드는 최신 manifest 조회 결과로 교체됩니다.
> 그래도 inbound body는 `FileProcessingRequest` 전체 스키마 검증을 통과해야 합니다.

### 8.3 핵심 의미론

1. manifest 조회 실패는 파일 처리 전 오류이므로 실패 callback을 추가 전송하지 않습니다.
2. 다운로드 이후 파싱·청킹·임베딩·저장 실패는 `success=false` callback을 시도합니다.
3. 실패 callback에는 청크, index version, chunk count를 넣지 않습니다.
4. 성공 callback은 현재 처리 run이 아니라 callback 시점의 **최신 활성 snapshot**을 사용합니다.
5. snapshot 조회부터 callback 전송까지 같은 `File_IDX` advisory lock을 유지합니다.
6. 성공 callback 전송을 시작한 뒤 오류가 나면 실패 callback을 연이어 보내지 않습니다.
7. endpoint 응답은 callback snapshot이 아니라 현재 요청의 파일 처리 결과를 반환합니다.

### 8.4 성공 응답

```json
{
  "success": true,
  "code": "FILE_INDEXING_COMPLETED",
  "message": "File download, parsing, chunking, embedding, and indexing completed.",
  "data": {
    "rag_document_idx": 8801,
    "file_idx": 123,
    "user_idx": 45,
    "folder_idx": 9,
    "file_name": "2026 Q3 회의록.docx",
    "file_type": "docx",
    "file_size_bytes": 284331,
    "page_count": 38,
    "text_unit_count": 36,
    "chunk_count": 74,
    "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
    "embedding_dim": 1024,
    "processing_status": "INDEXED"
  }
}
```

### 8.5 오류·재시도

| HTTP | 대표 Code | 의미 |
|---:|---|---|
| 400 | `INVALID_FILE_URL` | 다운로드 URL 구조·허용 host 검증 실패 |
| 401 | `UNAUTHORIZED` | 내부 토큰 누락·불일치 |
| 404 | `RESOURCE_NOT_FOUND` | Backend manifest 대상 파일 없음 |
| 413 | `FILE_TOO_LARGE` | 최대 파일 크기 초과 |
| 415 | `UNSUPPORTED_DOCUMENT_TYPE` | 지원하지 않는 형식·파서 |
| 422 | `INVALID_DOCUMENT` 등 | 손상·암호화·텍스트·청크 검증 실패 |
| 502 | `FILE_DOWNLOAD_FAILED` 등 | Backend·다운로드·TEI·Qdrant 계약 실패 |
| 503 | `SERVICE_UNAVAILABLE` 등 | 설정 또는 외부 서비스 일시 불가 |
| 504 | `APPLICATION_SERVER_TIMEOUT` 등 | Backend·다운로드·TEI timeout |
| 500 | `LOCAL_RAG_STORAGE_FAILED` 등 | 문서 읽기·청킹·DB·내부 처리 실패 |

같은 파일의 반복 호출은 advisory lock과 결정적 청크 ID를 사용하지만 HTTP 수준의
`Idempotency-Key` 계약은 없습니다. callback 반영 여부가 모호한 timeout 후에는 무조건
실패 상태로 덮어쓰지 말고 Backend 파일 상태와 활성 snapshot을 확인합니다.

## 9. `POST /api/v1/files/process`

### 9.1 목적과 `/ingest` 차이

이 endpoint는 전달된 `FileProcessingRequest`를 그대로 처리합니다.

| 항목 | `/ingest` | `/api/v1/files/process` |
|---|---|---|
| 최신 Backend manifest 재조회 | 예 | 아니오 |
| Backend ingest-complete callback | 예 | 아니오 |
| 다운로드·파싱·OCR·청킹·색인 | 예 | 예 |
| 응답 모델 | 동일 | 동일 |
| 권장 사용 | 실제 Backend handoff | 진단·내부 직접 처리·호환 경로 |

### 9.2 요청과 성공 응답

요청·성공 payload는 `/ingest`와 같은 `FileProcessingRequest` 및
`FileProcessingCompletedResponse`를 사용합니다. 차이는 orchestration과 callback입니다.

### 9.3 문서 처리 검증 순서

1. `file_type`에 등록된 parser 확인
2. URL·허용 host·redirect·size 검증
3. 확장자·MIME·Magic Byte·OOXML 구조 검증
4. 형식별 parser와 이미지 추출
5. OCR 후보 판정과 OCR 보강
6. 구조 보존 chunk 생성
7. TEI batch 임베딩
8. Local RAG DB와 Qdrant 안전 전환

지원 형식은 `pdf`, `docx`, `pptx`, `txt`, `xlsx`입니다.

## 10. `GET /api/v1/health/live`

### 10.1 의미

FastAPI 프로세스가 요청을 받아 응답할 수 있는지만 확인합니다. DB, Qdrant, TEI, Claude,
Office COM과 Backend는 검사하지 않습니다.

### 10.2 성공 응답

```json
{
  "success": true,
  "code": "SUCCESS",
  "message": "RAG service is running.",
  "data": {
    "status": "UP",
    "service": "Jipsa RAG Service",
    "version": "0.1.0",
    "environment": "local"
  }
}
```

### 10.3 모니터링 사용

- 프로세스 liveness probe에 사용합니다.
- 의존성 장애 때문에 프로세스를 불필요하게 재시작하지 않도록 readiness와 구분합니다.
- 이 API 성공만으로 검색·답변 가능 상태를 단정하지 않습니다.

## 11. `GET /api/v1/health/ready`

### 11.1 의미

현재 구현은 Local RAG DB에 `SELECT 1`을 실행해 연결 가능 여부만 검사합니다.
Qdrant, TEI, Claude, Office COM과 Backend는 readiness 검사 대상이 아닙니다.

### 11.2 성공 응답

```json
{
  "success": true,
  "code": "SUCCESS",
  "message": "RAG service is ready.",
  "data": {
    "status": "UP",
    "service": "Jipsa RAG Service",
    "version": "0.1.0",
    "environment": "local",
    "database": {
      "status": "UP"
    }
  }
}
```

### 11.3 실패 응답

Local RAG DB 연결 실패는 HTTP `503`, `SERVICE_UNAVAILABLE`, 공개 메시지
`RAG service is not ready.`를 반환합니다. DSN, 계정, 비밀번호와 SQL 오류 원문은
노출하지 않습니다.

## 12. `GET /api/v1/diagnostics/network`

### 12.1 목적

인증된 운영자가 현재 bind 주소, 설정된 외부 주소, outbound 공인 IP 조회 결과와 터널
설정을 확인합니다.

### 12.2 성공 응답

```json
{
  "success": true,
  "code": "SUCCESS",
  "message": "RAG network diagnostics completed.",
  "data": {
    "checked_at": "2026-07-28T04:39:00Z",
    "bind_host": "0.0.0.0",
    "bind_port": 8077,
    "external_base_url": "https://rag.example.com",
    "external_address_configured": true,
    "outbound_public_ip": "198.51.100.24",
    "outbound_ip_lookup_status": "AVAILABLE",
    "tunnel": {
      "enabled": true,
      "provider": "cloudflare"
    }
  }
}
```

예시 IP는 문서용 대역이며 실제 공인 IP가 아닙니다.

### 12.3 공인 IP 조회 실패

외부 조회 timeout, 비정상 status, JSON 오류, 누락 또는 non-global IP는 endpoint 전체를
실패시키지 않습니다.

```json
{
  "outbound_public_ip": null,
  "outbound_ip_lookup_status": "UNAVAILABLE"
}
```

### 12.4 터널·HTTPS

`tunnel.provider`는 `none`, `cloudflare`, `ngrok`, `tailscale`, `custom` 중 하나입니다.
현재 개발 설정은 HTTP 외부 주소도 허용하지만 HTTP에서는 `X-Internal-Token`이 암호화되지
않습니다. 운영 또는 장기 개발 환경은 HTTPS reverse proxy 또는 tunnel을 사용합니다.

## 13. `POST /api/v1/chunks/search`

### 13.1 요청

```json
{
  "user_idx": 45,
  "reference_file_idxs": [123, 456],
  "query": "두 문서의 장애 대응 절차를 찾아줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

| 필드 | 타입 | 필수 | 기본값 | 검증 |
|---|---|---:|---|---|
| `user_idx` | integer | 예 | 없음 | `> 0` |
| `reference_file_idxs` | integer array | 예 | 없음 | 1~20개, strict 양수, 중복 금지 |
| `query` | string | 예 | 없음 | trim 후 1~4096자 |
| `top_k` | integer | 아니오 | `5` | 1~20 |
| `score_threshold` | number 또는 null | 아니오 | `null` | -1.0~1.0, finite |

### 13.2 검색 범위 불변식

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

이 세 조건은 선택 사항이 아닙니다. 요청 문서가 없거나 검색 결과가 없다고 해서 사용자의
다른 문서로 범위를 확대하지 않습니다.

### 13.3 성공 응답

```json
{
  "success": true,
  "code": "CHUNK_SEARCH_COMPLETED",
  "message": "Relevant document chunks were retrieved.",
  "data": {
    "user_idx": 45,
    "result_count": 1,
    "results": [
      {
        "chunk_id": "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612",
        "score": 0.8421,
        "rag_document_idx": 8801,
        "file_idx": 123,
        "folder_idx": 9,
        "file_name": "운영 가이드.pdf",
        "file_type": "pdf",
        "chunk_index": 7,
        "content": "장애 발생 시 이전 정상 색인을 유지한다.",
        "token_count": 18,
        "page": 4,
        "slide_no": null,
        "sheet_name": null,
        "section_title": "복구 정책",
        "source_locator": {
          "file_type": "pdf",
          "kind": "pdf_page",
          "content_origin": "text",
          "unit_type": "paragraph",
          "structure_path": "page:4",
          "page": 4,
          "section_title": "복구 정책"
        },
        "parser_version": "pdf-2",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "index_version": 2
      }
    ]
  }
}
```

실제 `source_locator` 직렬화에는 형식별 선택 필드가 `null`로 함께 나타날 수 있습니다.
소비자는 정의된 필드 외 값을 기대하지 말고 `file_type`, `kind`, `content_origin`과
해당 형식의 위치 필드를 사용합니다.

### 13.4 결과 없음

검색 결과가 0개인 것은 정상 검색 결과입니다.

```json
{
  "success": true,
  "code": "CHUNK_SEARCH_COMPLETED",
  "message": "Relevant document chunks were retrieved.",
  "data": {
    "user_idx": 45,
    "result_count": 0,
    "results": []
  }
}
```

### 13.5 오류

| HTTP | 대표 Code | 의미 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 토큰 오류 |
| 422 | `REQUEST_VALIDATION_FAILED` | 범위·질문·검색 옵션 검증 실패 |
| 502 | `EMBEDDING_REQUEST_REJECTED`, `VECTOR_SEARCH_FAILED`, `INVALID_VECTOR_SEARCH_RESULT` | upstream 또는 payload 계약 오류 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE`, `VECTOR_DATABASE_UNAVAILABLE` | TEI·Qdrant 일시 불가 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | 질의 임베딩 timeout |
| 500 | `INTERNAL_SERVER_ERROR` | 분류되지 않은 검색 처리 실패 |

## 14. `POST /api/v1/rag/answers`

### 14.1 요청

```json
{
  "user_idx": 45,
  "reference_file_idxs": [123, 456],
  "query": "두 문서의 장애 복구 전략을 비교해줘",
  "top_k": 5,
  "score_threshold": 0.55
}
```

요청 필드와 범위는 청크 검색 API와 같습니다.

### 14.2 참조문서 필수 오류

`reference_file_idxs`가 누락, `null`, 빈 배열 또는 빈 tuple이면 다른 검증 오류와 구분해
HTTP `422`와 `REFERENCE_DOCUMENT_REQUIRED`를 반환합니다.

중복, 문자열 식별자, 0 이하 값 또는 20개 초과처럼 비어 있지 않지만 잘못된 배열은
`REQUEST_VALIDATION_FAILED`입니다.

### 14.3 lookup·synthesis 라우팅

| 조건 | 전략 |
|---|---|
| 참조문서 1개 | 항상 `lookup` |
| 참조문서 2개 이상, 명시적 비교·종합 표현 없음 | `lookup` |
| 참조문서 2개 이상 + 비교·대조·종합·통합·문서별 의도 | `synthesis` |

`lookup`은 선택 문서 전체를 한 번 검색하고 한 번 생성합니다. `synthesis`는 파일별 독립
검색과 부분 생성을 수행한 뒤 검증된 부분 결과만 최종 종합합니다.

현재 synthesis 기본 컨텍스트 제한:

- 문서별 최대 청크: 3개
- 전체 컨텍스트 최대 문자: 24,000자
- 단일 청크 최대 문자: 6,000자

### 14.4 문서별 부분 실패

synthesis에서 한 문서의 안전한 embedding·search·partial generation 실패는 다른 문서의
유효한 근거를 제거하지 않습니다. 단, 사용자 범위·선택 파일 범위 위반, 출처 ID 충돌,
최종 인용 불변식 위반 같은 전역 계약 오류는 전체 요청을 실패시킵니다.

### 14.5 answered 응답

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "문서 A는 이전 정상 색인 보존을 우선합니다. [SOURCE-1] 문서 B는 장애 확인 후 수동 재처리를 요구합니다. [SOURCE-2]",
    "status": "answered",
    "cited_source_ids": ["SOURCE-1", "SOURCE-2"],
    "sources": [
      {
        "source_id": "SOURCE-1",
        "chunk_id": "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612",
        "rag_document_idx": 8801,
        "file_idx": 123,
        "folder_idx": 9,
        "file_name": "운영 가이드.pdf",
        "file_type": "pdf",
        "chunk_index": 7,
        "score": 0.8421,
        "page": 4,
        "slide_no": null,
        "sheet_name": null,
        "section_title": "복구 정책",
        "source_locator": {
          "file_type": "pdf",
          "kind": "pdf_page",
          "content_origin": "text",
          "page": 4,
          "section_title": "복구 정책"
        },
        "excerpt": "장애 발생 시 이전 정상 색인을 유지한다."
      },
      {
        "source_id": "SOURCE-2",
        "chunk_id": "c0a31714-d092-5ed9-8782-d903cd3df9ae",
        "rag_document_idx": 8802,
        "file_idx": 456,
        "folder_idx": 9,
        "file_name": "운영 체크리스트.xlsx",
        "file_type": "xlsx",
        "chunk_index": 3,
        "score": 0.791,
        "page": null,
        "slide_no": null,
        "sheet_name": "장애대응",
        "section_title": null,
        "source_locator": {
          "file_type": "xlsx",
          "kind": "xlsx_cell_range",
          "content_origin": "text",
          "sheet_number": 1,
          "sheet_name": "장애대응",
          "cell_range": "B4:D8"
        },
        "excerpt": "장애 상태를 확인한 뒤 재처리한다."
      }
    ],
    "model": "claude-sonnet-5",
    "usage": {
      "input_tokens": 2140,
      "output_tokens": 386
    },
    "stop_reason": "end_turn"
  }
}
```

### 14.6 인용 순서 불변식

```text
answer 본문 [SOURCE-N]의 중복 제거 최초 등장 순서
=
cited_source_ids
=
sources[].source_id
```

추가 규칙:

- `answered`는 source, model, usage와 본문 인용이 하나 이상 있어야 합니다.
- `source_id`와 `chunk_id`는 각각 중복될 수 없습니다.
- `sources`에는 후보 전체가 아니라 **실제로 인용한 출처**만 포함합니다.
- `excerpt`는 1~1000자입니다.
- 존재하지 않는 `SOURCE-N`을 생성하거나 순서를 자동 교정하지 않습니다.

### 14.7 insufficient evidence 응답

검색 가능한 근거가 없거나 synthesis의 유효한 부분 근거가 하나도 없으면 최종 Claude
호출을 생략할 수 있습니다. HTTP는 성공 `200`이며 상태로 구분합니다.

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "제공된 문서 근거만으로는 답변할 수 없습니다.",
    "status": "insufficient_evidence",
    "cited_source_ids": [],
    "sources": [],
    "model": null,
    "usage": null,
    "stop_reason": null
  }
}
```

고정 문구, 빈 출처와 null 생성 메타데이터 중 하나라도 다르면 응답 모델 계약 위반입니다.

### 14.8 생성 제한

코드 기본값:

| 항목 | 기본값 |
|---|---:|
| Provider | `anthropic` |
| Model | `claude-sonnet-5` |
| 호출별 최대 출력 token | 4,096 |
| 호출 timeout | 60초 |
| 답변별 최대 Claude 호출 | 21 |
| 답변별 누적 입력 token | 400,000 |
| 답변별 누적 출력 token | 64,000 |
| 프로세스 최대 동시 provider 요청 | 2 |

답변별 호출 횟수 또는 누적 token budget를 Local RAG가 선제 차단하면 HTTP `429`,
`GENERATION_BUDGET_EXCEEDED`입니다.

### 14.9 오류

| HTTP | 대표 Code | 의미 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 내부 토큰 오류 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED`, `REQUEST_VALIDATION_FAILED` | 선택 문서·질문·옵션 오류 |
| 429 | `GENERATION_BUDGET_EXCEEDED` | Local RAG 비용·부하 보호 제한 |
| 502 | `VECTOR_SEARCH_FAILED`, `GENERATION_REQUEST_FAILED`, `INVALID_GENERATION_RESPONSE` | upstream·인용 계약 오류 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE`, `VECTOR_DATABASE_UNAVAILABLE`, `GENERATION_SERVICE_UNAVAILABLE` | 외부 의존성 일시 불가 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT`, `GENERATION_SERVICE_TIMEOUT` | TEI·Claude timeout |
| 500 | `GENERATION_FAILED`, `INTERNAL_SERVER_ERROR` | 분류되지 않은 생성·오케스트레이션 오류 |

## 15. 공통 `FileProcessingRequest`

| 필드 | 타입 | nullable | 범위·형식 | 의미 |
|---|---|---:|---|---|
| `file_idx` | integer | 아니오 | `> 0` | AWS `File.File_IDX`, 영구 조인 키 |
| `user_idx` | integer | 아니오 | `> 0` | AWS `Users.Users_IDX`, 검색 격리 키 |
| `folder_idx` | integer | 예 | null 또는 `> 0` | AWS `Folder.Folder_IDX` |
| `file_name` | string | 아니오 | 1~255, 경로 구분자 금지 | 표시명·확장자 검증 |
| `file_type` | enum | 아니오 | `pdf|docx|pptx|txt|xlsx` | parser 선택 |
| `download_url` | string | 아니오 | 1~8192, HTTPS | Presigned GET URL |
| `url_expires_in` | integer | 아니오 | `> 0`, 초 | 발급 시 설정된 유효 시간 |

### 15.1 파일명과 형식

`file_name` 마지막 확장자는 `file_type`과 정확히 일치해야 합니다. 대소문자는 정규화하지만
다중 확장자의 마지막 suffix만 판정합니다.

```text
file_type = docx + report.docx  → 허용
file_type = docx + report.pdf   → 422
file_type = pdf  + report.pdf.exe → 422
```

### 15.2 다운로드 URL

스키마 경계에서 다음을 요구합니다.

- HTTPS
- hostname 존재
- username·password 금지
- fragment 금지
- 명시 port는 443만 허용

다운로더 경계에서 추가로 허용 host suffix, redirect 금지, size, MIME, Magic Byte와 실제
문서 구조를 검사합니다. `url_expires_in`만으로 현재 만료 여부를 계산하지 않습니다.

## 16. `SourceLocator` 통합 계약

### 16.1 공통 필드

| 필드 | 의미 |
|---|---|
| `file_type` | 원본 파일 형식 |
| `kind` | 사용자가 확인할 대표 위치 종류 |
| `content_origin` | `text` 또는 `ocr` |
| `unit_type` | paragraph, table, ocr_image 등 parser unit |
| `structure_path` | 결정적 원본 구조 경로 |

`kind` 값:

- `document`
- `pdf_page`
- `docx_block`
- `pptx_slide`
- `pptx_shape`
- `xlsx_cell_range`
- `txt_line`

### 16.2 형식별 위치

| 형식 | 주요 필드 |
|---|---|
| PDF | `page` |
| DOCX | `section_index`, `block_index`, `paragraph_index`, `table_index`, `heading_level`, `column_number`, `row_count`, `column_count`, `section_title` |
| PPTX | `slide_no`, `shape_index`, `shape_id`, `shape_path`, `shape_name`, `shape_type_name`, `coordinate_space`, `shape_left_emu`, `shape_top_emu`, `shape_width_emu`, `shape_height_emu` |
| XLSX | `sheet_number`, `sheet_name`, `row_number`, `start_row`, `end_row`, `start_column`, `end_column`, `start_cell`, `end_cell`, `cell_range`, `cell_coordinates`, `merged_cell_ranges` |
| TXT | `line_number`, `line_start`, `line_end`, `char_start`, `char_end` |

### 16.3 OCR 필드

| 필드 | 규칙 |
|---|---|
| `image_ordinal` | 신규 표준 1-based 이미지 순번 |
| `image_index` | legacy 별칭, ordinal과 같아야 함 |
| `image_id` | 이미지 식별자 |
| `image_kind` | embedded, scan page, rendered chart 등 |
| `ocr_engine` | OCR 구현 식별자 |
| `ocr_mean_confidence` | 0.0~1.0 |

OCR locator는 이미지 순번만으로 충분하지 않습니다. PDF page, DOCX block, PPTX slide,
XLSX sheet 또는 TXT line 같은 원본 위치도 함께 있어야 합니다. 다른 형식의 대표 위치가
섞이면 모델 검증 단계에서 거부합니다.

### 16.4 legacy 위치

`page`, `slide_no`, `sheet_name`, `section_title`은 기존 소비자 호환을 위해 결과 top-level에
유지합니다. `source_locator`와 둘 다 존재하면 값이 같아야 하며 누락 legacy 값은 locator에서
채울 수 있습니다. 신규 UI는 `source_locator`를 우선 사용합니다.

## 17. Local RAG → AWS Backend 내부 API

### 17.1 공통 outbound 정책

| 항목 | 구현 정책 |
|---|---|
| Base URL | `JIPSA_RAG_APP_SERVER_BASE_URL`, 기본 `http://127.0.0.1:8080` |
| 인증 | `X-Internal-Token: <INTERNAL_TOKEN>` |
| Accept | `application/json` |
| Redirect | 따르지 않음 |
| System proxy | 사용하지 않음 (`trust_env=false`) |
| Connect timeout | 기본 5초 |
| Read·write timeout | 기본 30초 |
| 최대 시도 | 기본 3회 |
| Backoff | 기본 0.25초에서 지수 증가, 최대 2초 |
| 재시도 | transport·timeout, HTTP 408, 429, 5xx |
| Request ID 전달 | 현재 명시적 전달 없음 |

### 17.2 `GET /internal/files/{file_idx}/manifest`

#### 요청

```http
GET /internal/files/123/manifest
X-Internal-Token: <INTERNAL_TOKEN>
Accept: application/json
```

#### 기대 응답

- HTTP `200 OK`
- body는 공통 envelope가 아니라 **직접 `FileProcessingRequest` JSON**입니다.
- body의 `file_idx`는 path의 `file_idx`와 같아야 합니다.
- JSON·스키마·file_idx가 다르면 `INVALID_APPLICATION_SERVER_RESPONSE`입니다.

```json
{
  "file_idx": 123,
  "user_idx": 45,
  "folder_idx": 9,
  "file_name": "2026 Q3 회의록.docx",
  "file_type": "docx",
  "download_url": "https://example-bucket.s3.ap-northeast-2.amazonaws.com/files/example.docx?X-Amz-Signature=REDACTED",
  "url_expires_in": 900
}
```

Backend `404`는 Local RAG에서 `RESOURCE_NOT_FOUND`로 변환합니다. 그 외 예상하지 못한
non-retryable status와 2xx·3xx는 `APPLICATION_SERVER_REQUEST_REJECTED`입니다.

### 17.3 `POST /internal/files/{file_idx}/ingest-complete`

#### 성공 payload

실제 `/ingest` 성공 경로는 최신 활성 청크 전체를 전송합니다.

```json
{
  "success": true,
  "index_version": 2,
  "chunk_count": 2,
  "chunks": [
    {
      "chunk_id": "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612",
      "chunk_index": 0,
      "content": "첫 번째 동기화 청크 원문",
      "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "token_count": 17,
      "source_metadata": {
        "page_number": 1,
        "unit_type": "paragraph"
      }
    },
    {
      "chunk_id": "c0a31714-d092-5ed9-8782-d903cd3df9ae",
      "chunk_index": 1,
      "content": "두 번째 동기화 청크 원문",
      "content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "token_count": null,
      "source_metadata": {
        "page_number": 2,
        "unit_type": "ocr_image",
        "image_index": 1
      }
    }
  ]
}
```

성공 동기화 규칙:

- `index_version`, `chunk_count`, `chunks`는 모두 있거나 모두 없어야 합니다.
- 실제 `/ingest` 성공 경로에서는 모두 포함합니다.
- `chunk_count == len(chunks)`여야 합니다.
- `chunk_index`는 0부터 연속이어야 합니다.
- `chunk_id`는 유일한 표준 UUID 문자열입니다.
- `content_hash`는 content 원문의 SHA-256 소문자 64자리입니다.
- `token_count`를 계산하지 않았으면 명시적 `null`을 유지합니다.
- 임베딩 벡터는 전송하지 않습니다.

#### 실패 payload

```json
{
  "success": false,
  "error_message": "INVALID_DOCUMENT: The document structure is invalid."
}
```

실패 규칙:

- `error_message`는 필수, 최대 4000자입니다.
- `index_version`, `chunk_count`, `chunks`는 포함하지 않습니다.
- 내부 예외 원문 대신 안전한 공개 code와 message를 사용합니다.

#### 기대 응답

Backend는 성공적으로 반영한 경우 HTTP `204 No Content`를 반환해야 합니다. 예상하지 못한
status는 Local RAG에서 callback 실패로 처리합니다.

## 18. 오류 코드 카탈로그

이 표는 Local RAG 공통 `ErrorCode`의 공개 계약입니다. 특정 endpoint에서 실제 발생
가능한 코드는 해당 endpoint 오류 표를 우선합니다.

| HTTP | Code | 공개 의미 |
|---:|---|---|
| 400 | `INVALID_REQUEST` | 요청이 유효하지 않음 |
| 400 | `INVALID_FILE_URL` | 파일 URL이 유효하지 않음 |
| 401 | `UNAUTHORIZED` | 인증 필요 또는 토큰 불일치 |
| 403 | `FORBIDDEN` | 리소스 접근 권한 없음 |
| 404 | `RESOURCE_NOT_FOUND` | 요청 리소스 없음 |
| 405 | `METHOD_NOT_ALLOWED` | HTTP method 허용 안 됨 |
| 409 | `CONFLICT` | 현재 리소스 상태와 충돌 |
| 413 | `FILE_TOO_LARGE` | 최대 파일 크기 초과 |
| 415 | `UNSUPPORTED_DOCUMENT_TYPE` | 등록 parser가 없는 문서 형식 |
| 415 | `UNSUPPORTED_FILE_MEDIA_TYPE` | 다운로드 파일 media type 불일치 |
| 422 | `INVALID_DOCUMENT` | 문서 구조 손상 |
| 422 | `ENCRYPTED_DOCUMENT` | 암호화 문서 미지원 |
| 422 | `DOCUMENT_TEXT_EXTRACTION_FAILED` | 문서 텍스트 추출 실패 |
| 422 | `DOCUMENT_TEXT_NOT_FOUND` | 추출 가능한 텍스트 없음 |
| 422 | `DOCUMENT_CHUNKS_NOT_FOUND` | 검색 가능한 청크 생성 불가 |
| 422 | `REFERENCE_DOCUMENT_REQUIRED` | 답변 참조문서 미선택 |
| 422 | `REQUEST_VALIDATION_FAILED` | Pydantic 요청 검증 실패 |
| 422 | `INVALID_FILE` | 다운로드 파일 내용 검증 실패 |
| 422 | `FILE_HASH_MISMATCH` | 다운로드 Hash 불일치 |
| 429 | `TOO_MANY_REQUESTS` | 일반 요청 제한 |
| 429 | `GENERATION_BUDGET_EXCEEDED` | 답변별 Claude 호출·token budget 초과 |
| 500 | `DOCUMENT_READ_FAILED` | 임시 파일·문서 읽기 실패 |
| 500 | `DOCUMENT_CHUNKING_FAILED` | 내부 청킹 실패 |
| 500 | `EMBEDDING_GENERATION_FAILED` | 분류되지 않은 임베딩 실패 |
| 500 | `LOCAL_RAG_STORAGE_FAILED` | Local RAG DB 저장 실패 |
| 500 | `GENERATION_FAILED` | 분류되지 않은 생성 실패 |
| 500 | `INTERNAL_SERVER_ERROR` | 예상하지 못한 내부 오류 |
| 502 | `EMBEDDING_REQUEST_REJECTED` | TEI가 내부 요청 거부 |
| 502 | `INVALID_EMBEDDING_RESPONSE` | TEI 성공 응답 계약 불일치 |
| 502 | `VECTOR_STORAGE_FAILED` | Qdrant 저장 거부·계약 실패 |
| 502 | `VECTOR_SEARCH_FAILED` | Qdrant 검색 거부·계약 실패 |
| 502 | `INVALID_VECTOR_SEARCH_RESULT` | 검색 payload·범위·정렬 계약 불일치 |
| 502 | `GENERATION_REQUEST_FAILED` | Claude 요청 영구 실패 |
| 502 | `INVALID_GENERATION_RESPONSE` | 구조화 출력·인용 계약 불일치 |
| 502 | `FILE_DOWNLOAD_FAILED` | 원본 파일 다운로드 실패 |
| 502 | `APPLICATION_SERVER_REQUEST_REJECTED` | Backend 내부 API가 요청 거부 |
| 502 | `INVALID_APPLICATION_SERVER_RESPONSE` | Backend JSON·schema 계약 불일치 |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시 불가 |
| 503 | `VECTOR_DATABASE_UNAVAILABLE` | Qdrant 일시 불가 |
| 503 | `GENERATION_SERVICE_UNAVAILABLE` | Claude 인증·제한·5xx 등 일시 불가 |
| 503 | `APPLICATION_SERVER_UNAVAILABLE` | Backend 내부 API 일시 불가 |
| 503 | `SERVICE_UNAVAILABLE` | 설정 누락 또는 서비스 준비 안 됨 |
| 504 | `EMBEDDING_SERVICE_TIMEOUT` | TEI timeout |
| 504 | `GENERATION_SERVICE_TIMEOUT` | Claude timeout |
| 504 | `APPLICATION_SERVER_TIMEOUT` | Backend 내부 API timeout |
| 504 | `FILE_DOWNLOAD_TIMEOUT` | 원본 파일 다운로드 timeout |

## 19. 재시도·멱등성·상태 의미

### 19.1 소비자 기본 판단

| HTTP·상태 | 기본 판단 |
|---|---|
| 200 `answered` | 정상 사용자 답변 |
| 200 `insufficient_evidence` | 정상 처리, 근거 부족 UI 표시 |
| 200 chunk count 0 | 정상 검색, 결과 없음 |
| 204 callback | Backend 반영 완료 |
| 401 | 토큰 수정 전 재시도 금지 |
| 413·415·422 | 입력·파일 수정 전 재시도 금지 |
| 429 | 제한 원인과 backoff 확인 후 제한 재시도 |
| 502 | 계약 또는 upstream 거부 원인 확인 후 재시도 |
| 503 | readiness·외부 서비스 확인 후 backoff 재시도 |
| 504 | timeout·중복 처리 가능성 확인 후 재시도 |
| 500 | Request ID와 실행 상태 확인 후 결정 |

### 19.2 인제스트 멱등성

동일 파일 Hash, parser version, embedding model, index version이면 정상 문서와 결정적
청크 ID를 재사용할 수 있습니다. 정체성이 바뀌면 신규 staging을 생성하고 새 색인이
성공한 뒤에만 이전 정상 색인을 비활성화합니다.

이 동작은 저장소 수준 안전성이지 HTTP idempotency key 계약은 아닙니다.

### 19.3 callback ambiguity

성공 callback 요청이 전송된 뒤 timeout이 발생하면 Backend가 실제로 반영했는지 Local
RAG가 확정할 수 없습니다. 이때 실패 callback을 보내면 이미 성공한 상태를 `FAILED`로
덮어쓸 수 있으므로 보내지 않습니다. 운영자는 Backend 상태, Local 최신 활성 문서,
Request ID와 callback 로그를 함께 확인합니다.

## 20. 관측성·로그·민감정보

### 20.1 접근 로그

모든 HTTP 요청에서 다음 이벤트가 구조화 로그로 기록됩니다.

- `http_request_started`
- `http_request_completed`
- `http_request_failed`

주요 안전 필드:

- Request ID
- method
- path
- status code
- duration milliseconds
- 분류된 error code와 안전한 operation

### 20.2 기록 금지

- `X-Internal-Token` 원문
- Anthropic API Key
- DB 비밀번호·DSN
- Presigned URL 전체와 signature query
- 사용자 질문 원문
- 청크·OCR 원문 전체
- 생성 프롬프트·Claude 원문 응답
- 임베딩 벡터
- SQL·stack trace의 외부 API 노출

### 20.3 모니터링 해석

- liveness 성공은 외부 의존성 정상 의미가 아닙니다.
- readiness 성공은 현재 DB 연결만 의미합니다.
- diagnostics의 outbound IP `UNAVAILABLE`은 endpoint 실패가 아닙니다.
- 생성 latency는 검색, 문서별 partial generation과 최종 synthesis 횟수에 따라 달라집니다.

## 21. 성능·용량·기본 제한

| 영역 | 기본 또는 계약 상한 |
|---|---|
| 단일 다운로드 파일 | 기본 50 MiB, 설정 최대 1 GiB |
| 참조문서 수 | 1~20 |
| query | 1~4096자 |
| chunk search `top_k` | 1~20, 기본 5 |
| score threshold | -1.0~1.0 또는 null |
| TEI embedding batch | 최대 32 |
| embedding dimension | 기본 1024 |
| Qdrant timeout | 기본 10초 |
| RAG source excerpt | 최대 1000자 |
| callback error message | 최대 4000자 |
| answer Claude calls | 기본 최대 21 |
| answer input tokens | 기본 누적 400,000 |
| answer output tokens | 기본 누적 64,000 |

설정 기본값은 배포 환경에서 바뀔 수 있습니다. 변경 시 OpenAPI의 필드 계약과 서버 내부
운영 제한을 구분해 문서화합니다.

## 22. 소비자별 구현 체크리스트

### 22.1 AWS Backend

- [ ] 보호 API마다 올바른 `RAG_INGEST_TOKEN`을 전송함
- [ ] 사용자·파일 소유권을 Local RAG 호출 전에 검증함
- [ ] `reference_file_idxs`를 1~20개의 고유 양수 정수로 확정함
- [ ] manifest path `file_idx`와 body `file_idx`를 일치시킴
- [ ] Presigned URL은 HTTPS·충분한 유효 시간을 사용함
- [ ] ingest-complete 성공 payload를 transaction으로 전체 교체함
- [ ] callback 204 계약을 지킴
- [ ] 200 insufficient evidence를 서버 오류로 변환하지 않음
- [ ] `X-Request-ID` cross-service 전파는 현재 별도 구현이 필요함을 인지함

### 22.2 Frontend

- [ ] `status`를 먼저 확인하고 answer 문자열만으로 상태를 추측하지 않음
- [ ] `insufficient_evidence`를 정상 빈 상태로 표시함
- [ ] 실제 인용 순서대로 `sources`를 렌더링함
- [ ] 신규 위치 표시는 `source_locator`를 우선 사용함
- [ ] legacy `page`, `slide_no`, `sheet_name`은 fallback으로 사용함
- [ ] 문서 형식과 OCR 여부에 맞는 위치 라벨을 생성함
- [ ] 오류 `code`를 UI 분기 기준으로 사용하고 message를 기계 파싱하지 않음

### 22.3 QA

- [ ] 공개·보호 endpoint 인증 matrix 검사
- [ ] 정의되지 않은 요청 필드 422 검사
- [ ] strict reference ID 타입·중복·최대 개수 검사
- [ ] 다른 사용자·비활성·미선택 문서 exclusion 검사
- [ ] 검색 결과 0과 answer insufficient evidence 구분 검사
- [ ] SOURCE 최초 등장 순서·중복·미인용 source 검사
- [ ] 모든 문서 형식과 OCR locator 검사
- [ ] manifest mismatch와 callback all-or-none 검사
- [ ] timeout·503·429·invalid upstream response 장애 주입 검사

### 22.4 운영자

- [ ] live와 ready를 다른 probe로 사용함
- [ ] ready가 DB만 검사한다는 제한을 모니터링에 반영함
- [ ] Qdrant·TEI·Claude는 별도 readiness 또는 실제 요청으로 확인함
- [ ] 외부 노출 시 HTTPS와 네트워크 allowlist를 사용함
- [ ] Request ID와 file_idx로 로그를 상관 분석함
- [ ] callback ambiguity에서 자동 FAILED 덮어쓰기를 피함

## 23. 알려진 제한과 비보장 사항

1. Local RAG는 사용자 JWT를 검증하지 않습니다. Backend 내부 인증과 사전 권한 검증을 전제로 합니다.
2. readiness는 DB만 검사하며 전체 검색·답변 readiness가 아닙니다.
3. outbound 공인 IP 조회 실패는 diagnostics 성공 body의 `UNAVAILABLE`로 표현됩니다.
4. inbound `X-Request-ID`는 현재 Backend outbound 요청에 자동 전달되지 않습니다.
5. `/api/v1/files/process`는 Backend manifest 재조회·callback을 수행하지 않습니다.
6. LLM 답변 문자열은 반복 요청에서 byte-for-byte 동일성을 보장하지 않습니다.
7. HTTP `Idempotency-Key`는 현재 정의하지 않습니다.
8. OpenAPI UI 경로는 개발 도구이며 장기 업무 계약이 아닙니다.
9. Local RAG는 AWS Access Key·Secret Access Key를 보관하거나 S3 API를 직접 서명하지 않습니다.
10. Backend가 전달한 `user_idx`가 실제 로그인 사용자와 같은지는 Backend가 보장해야 합니다.

## 24. 변경 관리

### 24.1 Breaking change

다음은 호환성 영향 분석과 버전 변경이 필요합니다.

- endpoint method·path 삭제 또는 의미 변경
- 필수 요청 필드 추가·삭제
- 타입·범위·nullable·strict coercion 변경
- 성공·오류 HTTP status 또는 code 의미 변경
- `reference_file_idxs` 범위 확대
- `SOURCE-N` 형식·순서 불변식 변경
- Source Locator 위치 의미 변경
- legacy 위치 필드 제거
- callback success/failure 조합 변경

### 24.2 변경 절차

1. 소비자와 현재 계약을 식별합니다.
2. Pydantic·FastAPI·서비스 불변식을 수정합니다.
3. OpenAPI response 설명과 예시를 갱신합니다.
4. Backend DTO·Frontend source UI 영향을 확인합니다.
5. 이 종합 명세와 관련 상세 문서를 같은 commit에서 갱신합니다.
6. 단위·통합·문서 회귀 테스트를 추가합니다.
7. 외부 의존성 의미가 바뀌면 실제 E2E를 실행합니다.
8. 실행하지 않은 검증과 잔여 위험을 PR에 명시합니다.

## 25. 최종 계약 체크리스트

- [ ] 7개 inbound 업무 API와 2개 outbound Backend API가 모두 문서화됨
- [ ] public·protected endpoint가 명확함
- [ ] 두 내부 토큰 방향이 뒤바뀌지 않음
- [ ] 모든 응답 envelope와 `X-Request-ID`가 설명됨
- [ ] 요청 필드의 타입·범위·기본값·nullable이 구현과 일치함
- [ ] 사용자·활성·선택 문서 검색 범위가 고정됨
- [ ] lookup·synthesis·partial failure가 구분됨
- [ ] answered·insufficient evidence가 구분됨
- [ ] 인용 최초 등장 순서와 Source Locator가 완전함
- [ ] callback success·failure·ambiguity가 설명됨
- [ ] 오류 code와 재시도 판단이 공개 계약과 일치함
- [ ] 현재 비보장 사항을 구현된 기능처럼 과장하지 않음
- [ ] OpenAPI·개별 문서·회귀 테스트가 동기화됨
