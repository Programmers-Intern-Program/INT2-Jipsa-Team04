# 관련 청크 검색 API

## 1. 목적

이 API는 AWS에서 실행되는 Jipsa 애플리케이션 서버가 로컬 RAG 서버에
사용자 질의를 전달하고, 해당 사용자가 소유한 문서의 활성 청크 중 관련도가
높은 결과를 조회할 때 사용한다.

RAG 서버는 사용자 인증을 직접 수행하지 않는다. 사용자 인증과 권한 확인은
애플리케이션 서버가 담당하며, 애플리케이션 서버와 RAG 서버 사이의 호출은
공유 시크릿 기반 내부 인증으로 보호한다.

검색 처리 순서는 다음과 같다.

```text
애플리케이션 서버
        ↓  X-Internal-Token + 사용자 질의
POST /api/v1/chunks/search
        ↓
RAG 서버 내부 토큰 검증
        ↓
TEI CUDA 서버에서 질의 임베딩 생성
        ↓
Qdrant 검색
  - users_idx == 요청 user_idx
  - is_active == true
  - score >= score_threshold
  - 최대 top_k개
        ↓
관련도 점수 내림차순 청크 응답
```

## 2. 엔드포인트

```text
POST /api/v1/chunks/search
Content-Type: application/json
X-Internal-Token: <RAG_INGEST_TOKEN과 동일한 값>
```

`X-Internal-Token`에는 RAG 서버의 `RAG_INGEST_TOKEN` 환경 변수와 같은
값을 전달한다.

RAG 서버가 백엔드의 `/internal/**` API를 호출할 때 사용하는
`INTERNAL_TOKEN`과 방향이 다르므로 두 토큰을 혼용하지 않는다.

## 3. 요청 본문

```json
{
  "user_idx": 45,
  "query": "프로젝트의 배포 절차를 알려줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

### 요청 필드

| 필드              | 타입             | 필수 여부 | 기본값 | 제약                  | 설명                                                                               |
| ----------------- | ---------------- | --------- | ------ | --------------------- | ---------------------------------------------------------------------------------- |
| `user_idx`        | integer          | 필수      | 없음   | 1 이상                | AWS 서버 DB `Users.Users_IDX` 값이며 검색 사용자 범위를 제한한다.                  |
| `query`           | string           | 필수      | 없음   | 공백 제거 후 1~4096자 | 관련 청크를 찾을 사용자 질의다.                                                    |
| `top_k`           | integer          | 선택      | `5`    | 1~20                  | 관련도 순으로 반환할 최대 청크 수다.                                               |
| `score_threshold` | number 또는 null | 선택      | `null` | -1.0~1.0              | Qdrant Cosine 관련도에 적용할 최소 점수다. `null`이면 최소 점수를 적용하지 않는다. |

정의하지 않은 추가 필드는 허용하지 않는다. 예를 들어 요청에 `file_hash`나
`embedding` 같은 임의 필드를 추가하면 요청 검증 오류가 반환된다.

## 4. 검색 조건의 적용 순서

### 사용자 범위

Qdrant 필터에 다음 조건을 항상 `AND`로 적용한다.

```text
users_idx == request.user_idx
is_active == true
```

클라이언트는 이 필터를 제거하거나 다른 값으로 완화할 수 없다.

검색 저장소가 필터를 직접 생성하며, Qdrant 응답을 외부 응답으로 변환하기
전에도 `users_idx`와 `is_active`를 다시 확인한다.

### `top_k`

`top_k`는 Qdrant `query_points` 요청의 `limit`으로 전달된다.

API 스키마와 Qdrant 저장소가 모두 1~20 범위를 검증하므로 API 계층을
우회한 내부 호출도 20개를 초과할 수 없다.

`top_k`는 최대 개수다. 최소 점수 조건을 함께 사용하면 실제 반환 개수는
`top_k`보다 작을 수 있으며, 조건을 만족하는 결과가 없으면 빈 배열을
반환한다.

### `score_threshold`

`score_threshold`는 Qdrant `query_points` 요청의 `score_threshold`로
전달된다.

현재 Collection은 Cosine 거리를 사용하므로 값이 클수록 질의와 청크의
관련도가 높다는 의미다.

서비스 계층은 Qdrant가 반환한 각 결과가 임계값 이상인지 다시 검증한다.
따라서 저장소 구현 변경이나 잘못된 테스트 대역으로 인해 임계값 미만
결과가 반환되더라도 API 응답으로 전달되지 않는다.

### 정렬

응답의 `results`는 `score` 내림차순이다. 같은 점수의 상대 순서는 별도로
보장하지 않는다.

## 5. 성공 응답

HTTP 상태: `200 OK`

```json
{
  "success": true,
  "code": "CHUNK_SEARCH_COMPLETED",
  "message": "Relevant document chunks were retrieved.",
  "data": {
    "user_idx": 45,
    "result_count": 2,
    "results": [
      {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "score": 0.92,
        "rag_document_idx": 100,
        "file_idx": 123,
        "folder_idx": 9,
        "file_name": "프로젝트 가이드.pdf",
        "file_type": "pdf",
        "chunk_index": 3,
        "content": "로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다.",
        "token_count": 128,
        "page": 2,
        "slide_no": null,
        "sheet_name": null,
        "section_title": "로컬 실행 방법",
        "parser_version": "1.0.0",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "index_version": 2
      },
      {
        "chunk_id": "22222222-2222-2222-2222-222222222222",
        "score": 0.81,
        "rag_document_idx": 100,
        "file_idx": 123,
        "folder_idx": 9,
        "file_name": "프로젝트 가이드.pdf",
        "file_type": "pdf",
        "chunk_index": 4,
        "content": "TEI 컨테이너는 NVIDIA CUDA GPU를 사용하여 임베딩을 생성합니다.",
        "token_count": 96,
        "page": 3,
        "slide_no": null,
        "sheet_name": null,
        "section_title": "임베딩 서버",
        "parser_version": "1.0.0",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "index_version": 2
      }
    ]
  }
}
```

조건을 만족하는 청크가 없을 때도 요청 자체는 성공이다.

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

## 6. 오류 응답

모든 오류는 공통 응답 형식을 사용한다.

```json
{
  "success": false,
  "code": "ERROR_CODE",
  "message": "Public error message.",
  "data": null
}
```

| HTTP 상태 | 대표 코드                       | 발생 조건                                                          |
| --------- | ------------------------------- | ------------------------------------------------------------------ |
| `401`     | `UNAUTHORIZED`                  | `X-Internal-Token` 누락 또는 불일치                                |
| `422`     | `REQUEST_VALIDATION_FAILED`     | 필수값 누락, 빈 질의, `top_k` 또는 `score_threshold` 범위 오류     |
| `502`     | `EMBEDDING_REQUEST_REJECTED`    | TEI가 질의 임베딩 요청을 4xx로 거부                                |
| `502`     | `INVALID_EMBEDDING_RESPONSE`    | TEI 응답 JSON, 벡터 개수, 값 또는 차원 불일치                      |
| `502`     | `VECTOR_SEARCH_FAILED`          | Qdrant가 검색 요청을 영구 오류로 거부하거나 Collection 설정 불일치 |
| `502`     | `INVALID_VECTOR_SEARCH_RESULT`  | Qdrant 검색 payload 또는 검색 결과 계약 불일치                     |
| `503`     | `SERVICE_UNAVAILABLE`           | RAG 서버에 내부 인증 토큰이 설정되지 않음                          |
| `503`     | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 연결 실패, 429 또는 5xx 응답                                   |
| `503`     | `VECTOR_DATABASE_UNAVAILABLE`   | Qdrant 연결 실패, 408, 429 또는 5xx 응답                           |
| `504`     | `EMBEDDING_SERVICE_TIMEOUT`     | TEI 질의 임베딩 요청 시간 초과                                     |
| `500`     | `INTERNAL_SERVER_ERROR`         | 분류되지 않은 내부 처리 오류                                       |

요청 검증 오류의 `data`에는 실패한 필드 목록이 포함될 수 있다.

```json
{
  "success": false,
  "code": "REQUEST_VALIDATION_FAILED",
  "message": "Request validation failed.",
  "data": {
    "errors": [
      {
        "field": "body.top_k",
        "message": "Input should be less than or equal to 20",
        "error_type": "less_than_equal"
      }
    ]
  }
}
```

## 7. 보안 주의 사항

- 이 API는 브라우저나 모바일 클라이언트가 직접 호출하지 않는다.
- 애플리케이션 서버가 사용자 인증과 파일 접근 권한을 확인한 뒤 호출한다.
- `X-Internal-Token` 원문을 로그, 이슈, PR, 문서 예제 또는 오류 응답에 기록하지 않는다.
- 외부 네트워크를 통과하면 HTTPS, IP allowlist와 방화벽 정책을 적용한다.
- 사용자 질의 원문, 청크 원문, TEI 응답 본문과 임베딩 벡터를 오류 로그에 기록하지 않는다.
- RAG 서버는 AWS Access Key, Secret Access Key 또는 Session Token을 사용하지 않는다.
- Qdrant와 TEI 포트는 외부에 직접 노출하지 않고 로컬 루프백에서만 접근한다.

## 8. OpenAPI 확인

FastAPI 서버 실행 후 Swagger UI의 `Chunk Search` 태그에서 다음 API를
확인할 수 있다.

```text
POST /api/v1/chunks/search
```

OpenAPI에는 내부 인증 헤더, 요청 필드 제약, 성공 응답 모델과 상태 코드별
오류 설명이 함께 표시된다.

## 9. 테스트 범위

다음 계층을 각각 검증한다.

- 질의 임베딩 단위 테스트: Qwen3 instruction, TEI 요청과 응답 검증
- Qdrant 검색 저장소 단위 테스트: 사용자·활성 필터, `top_k`, 최소 점수, payload 검증
- 검색 서비스 단위 테스트: 계층 연결, 사용자 범위·정렬·임계값 방어 검증
- API 단위 테스트: 내부 인증, 요청 검증, 성공 응답과 예외 매핑
- 통합 테스트: 실제 Qdrant 로컬 모드에서 네 검색 조건 동시 검증

통합 테스트는 검색 로직의 결정성을 위해 고정 질의 벡터를 사용한다.

실제 CUDA 12.9 GPU와 TEI 연결은 로컬 RAG 실행 환경에서 별도로 확인한다.
