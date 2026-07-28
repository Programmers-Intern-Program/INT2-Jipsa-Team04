# Local RAG API 거버넌스와 호환성

> **문서 상태:** Stable · 모든 Local RAG 내부 API 변경의 공통 기준  
> **주 독자:** AWS Backend·Local RAG API 개발자, QA, PR 리뷰어  
> **최종 검토:** 2026-07-28  
> **적용 API:** Local RAG inbound 7개, AWS Backend outbound 2개  
> **종합 Source of truth:** [종합 API 명세서](comprehensive-api-specification.md)

## 1. 목적

이 문서는 개별 필드보다 상위에 있는 버전, 호환성, 인증, 오류, 반복 호출, callback과
문서 동기화 원칙을 정의합니다. 전체 endpoint·schema·예시는 종합 API 명세서를 먼저
확인하고, 검색·답변·출처 세부 규칙은 개별 계약 문서를 사용합니다.

- [종합 API 명세서](comprehensive-api-specification.md)
- [관련 청크 검색 API](../chunk-search-api.md)
- [AWS Backend ↔ Local RAG 답변 API](rag-answer-api-contract.md)
- [답변·인용·Source Locator 상세](rag-answer-contract.md)

## 2. 공통 원칙

1. API는 브라우저가 아니라 AWS Backend가 호출하는 내부 서비스 계약입니다.
2. 사용자 인증·파일 권한은 Backend가 최종 판정합니다.
3. Local RAG는 `user_idx`와 `reference_file_idxs` 범위를 확대하지 않습니다.
4. 정의되지 않은 요청 필드는 허용하지 않습니다.
5. 오류 응답은 공통 envelope와 공개 가능한 오류 코드만 사용합니다.
6. 질문·청크·프롬프트·토큰·Presigned URL은 오류 응답과 로그에 포함하지 않습니다.
7. 스키마, OpenAPI, 종합·개별 문서와 회귀 테스트는 같은 변경 단위에서 갱신합니다.

## 3. endpoint 목록

| 방향 | 인증 | Method·Path | 성격 |
|---|---|---|---|
| Backend → Local RAG | 보호 | `POST /ingest` | 최신 manifest·색인·callback |
| Backend → Local RAG | 보호 | `POST /api/v1/files/process` | direct 파일 처리·색인 |
| Monitoring → Local RAG | 공개 | `GET /api/v1/health/live` | 프로세스 생존 |
| Monitoring → Local RAG | 공개 | `GET /api/v1/health/ready` | Local DB 준비 |
| Backend → Local RAG | 보호 | `GET /api/v1/diagnostics/network` | 네트워크 진단 |
| Backend → Local RAG | 보호 | `POST /api/v1/chunks/search` | 선택 문서 검색 |
| Backend → Local RAG | 보호 | `POST /api/v1/rag/answers` | 검색·생성·출처 검증 |
| Local RAG → Backend | 보호 | `GET /internal/files/{file_idx}/manifest` | 최신 manifest |
| Local RAG → Backend | 보호 | `POST /internal/files/{file_idx}/ingest-complete` | 상태·청크 동기화 |

## 4. 인증 방향

| 호출 방향 | 헤더 | Local RAG 환경 변수 |
|---|---|---|
| Backend → Local RAG | `X-Internal-Token` | `RAG_INGEST_TOKEN` |
| Local RAG → Backend | `X-Internal-Token` | `INTERNAL_TOKEN` |

두 토큰을 이름만 보고 서로 바꾸지 않습니다. 토큰 원문을 URL, 로그, 예외, OpenAPI 예시와
테스트 Fixture에 넣지 않습니다. 서버 토큰 미설정은 fail-closed `503`, 요청 토큰 오류는
`401`입니다.

## 5. 계약 버전

답변 계약 현재 버전은 `1.3.0`입니다. 종합 API 명세 버전은 `1.0.0`입니다.

| 변경 | 버전·호환성 판단 |
|---|---|
| 문장·예시·오탈자만 수정 | 계약 버전 유지 |
| 의미를 보존하는 optional 응답 필드 추가 | minor 변경 검토 |
| 필수 요청 필드 추가·삭제 | breaking |
| 필드 타입·범위·nullable·strict 변환 변경 | breaking |
| endpoint method·path 변경 | breaking |
| 오류 status·code 의미 변경 | breaking |
| Source Locator 위치 의미 변경 | breaking |
| legacy 위치 필드 제거 | breaking |
| 인용 순서 불변식 변경 | breaking |

현재 URL은 기본 `/api/v1`을 사용합니다. breaking 변경은 기존 소비자 영향 분석 없이 같은
경로에 적용하지 않습니다.

## 6. 하위 호환성

### 6.1 유지하는 legacy 위치 필드

- `page`
- `slide_no`
- `sheet_name`
- `section_title`

신규 소비자는 `source_locator`를 우선 사용합니다. legacy 값과 locator 값이 모두 있으면
서로 같아야 합니다.

### 6.2 요청 추가 필드

Pydantic 요청 모델은 정의되지 않은 필드를 거부합니다. 요청 필드 추가는 Backend와 Local
RAG를 호환 가능한 순서로 배포해야 합니다. 응답 optional 필드 추가도 Backend·Frontend의
strict 역직렬화 정책을 확인합니다.

## 7. 요청 추적

`X-Request-ID`는 선택적 요청 추적 헤더입니다.

```text
유효 UUID 수신 → 같은 UUID로 Local RAG 로그·응답 연결
누락·invalid UUID → Local RAG가 새 UUID 생성
```

현재 `ApplicationServerIngestClient`의 outbound header는 `X-Internal-Token`과 `Accept`를
명시하며 inbound `X-Request-ID`를 Backend manifest·callback에 자동 전달하지 않습니다.
따라서 cross-service Request ID 전파는 현재 보장 계약이 아니며 필요하면 별도 구현과
테스트를 추가해야 합니다.

Request ID는 인증 수단이 아니며 사용자·파일 권한을 대체하지 않습니다.

## 8. 반복 호출과 멱등성

### 8.1 `/ingest`

- 같은 `File_IDX`는 advisory lock으로 색인 임계 구역을 직렬화합니다.
- 동일 색인 정체성은 결정적 Chunk ID와 기존 정상 문서를 재사용합니다.
- 신규 색인이 실패하면 이전 정상 색인을 유지합니다.
- 별도 `Idempotency-Key` 헤더는 현재 계약에 정의되어 있지 않습니다.
- 성공 callback이 시작된 뒤 네트워크 오류가 발생하면 실패 callback을 연이어 보내지 않습니다.
- 호출자는 timeout만 보고 Backend 상태를 덮어쓰지 않고 파일 상태를 다시 확인합니다.

### 8.2 검색·답변

같은 활성 색인·질의·설정이면 검색은 의미상 반복 가능하지만 색인 상태가 바뀌면 결과가
달라질 수 있습니다. LLM 생성 문자열은 byte-for-byte 동일성을 보장하지 않습니다.
소비자는 상태, 선택 범위, 실제 출처와 인용 무결성을 검증합니다.

## 9. 재시도 판단

| 응답 | 기본 판단 |
|---|---|
| 401 | 토큰·호출 방향 수정 전 재시도 금지 |
| 413·415·422 | 입력·파일 수정 전 재시도 금지 |
| 429 | 제한 원인 확인 후 backoff 재시도 |
| 502 | upstream·계약 오류 확인 후 재시도 |
| 503 | readiness·의존성 확인 후 제한 재시도 |
| 504 | timeout·중복 상태 확인 후 제한 재시도 |
| 500 | Request ID와 실행 상태를 진단한 뒤 판단 |

Local RAG → Backend client는 transport·timeout, HTTP 408, 429, 5xx를 기본 최대 3회
지수 backoff로 재시도합니다. Backend → Local RAG 재시도 정책은 Backend 설정의 책임입니다.

## 10. 공통 envelope

성공:

```json
{
  "success": true,
  "code": "DOMAIN_COMPLETED",
  "message": "Public success message.",
  "data": {}
}
```

오류:

```json
{
  "success": false,
  "code": "PUBLIC_ERROR_CODE",
  "message": "Public error message.",
  "data": null
}
```

필드 검증 실패만 제한적으로 `data.errors`를 포함합니다. `message`는 내부 예외 원문이
아닙니다.

## 11. 인용 계약

```text
answer의 SOURCE-N 최초 등장 순서
=
cited_source_ids
=
sources[].source_id
```

최종 `sources`에는 실제로 인용한 출처만 남깁니다. source ID 형식, 순서, 미인용 후보 포함,
`source_locator` 위치 의미의 변경은 breaking 영향 검토가 필요합니다.

## 12. 변경 절차

1. 변경 목적과 소비자를 식별합니다.
2. breaking 여부를 판정합니다.
3. Pydantic 스키마와 서비스 불변식을 수정합니다.
4. FastAPI OpenAPI 설명과 오류 응답을 갱신합니다.
5. Backend DTO·클라이언트와 Frontend source UI 영향을 확인합니다.
6. 종합 API 명세와 관련 개별 문서를 같은 commit에서 갱신합니다.
7. 단위·통합·문서 회귀 테스트를 추가합니다.
8. 필요하면 실제 E2E를 실행합니다.
9. 미실행 검증을 PR에 명시합니다.

## 13. 변경 체크리스트

- [ ] 7개 inbound·2개 outbound API 목록과 호출 방향이 최신임
- [ ] public·protected endpoint dependency가 정확함
- [ ] 두 내부 토큰의 방향이 정확함
- [ ] strict 입력·기본값·범위·nullable이 정확함
- [ ] 빈 `reference_file_idxs`를 전체 검색으로 확대하지 않음
- [ ] 성공·결과 없음·근거 부족을 구분함
- [ ] 오류 HTTP 상태와 공개 code가 일치함
- [ ] Request ID 현재 전파 범위를 과장하지 않음
- [ ] callback success·failure·ambiguity가 일치함
- [ ] legacy 위치와 `source_locator` 호환성이 유지됨
- [ ] OpenAPI, 종합·개별 문서, Backend DTO와 테스트가 동기화됨
