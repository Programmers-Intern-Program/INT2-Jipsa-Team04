# AWS Backend ↔ Local RAG 답변 API 계약

## 1. 문서 정보

| 항목           | 값                                                               |
| -------------- | ---------------------------------------------------------------- |
| 계약 버전      | `1.0.0`                                                          |
| 호출 방향      | AWS Backend → Local RAG                                          |
| HTTP Method    | `POST`                                                           |
| Path           | `/api/v1/rag/answers`                                            |
| Content-Type   | `application/json`                                               |
| 인증 헤더      | `X-Internal-Token`                                               |
| 요청 추적 헤더 | `X-Request-ID`                                                   |
| 담당 범위      | 선택 참조문서 기반 검색, Claude 답변 생성, 출처 반환             |
| 제외 범위      | AWS 사용자 인증, 파일 선택 UI 상태 관리, S3 접근, 전체 문서 검색 |

---

## 2. 시스템 경계

AWS Backend는 사용자 인증과 인가를 완료한 뒤, 질문 전송 시점에 선택된
`File.File_IDX` 목록을 확정하여 Local RAG에 전달한다.

Local RAG는 전달받은 `reference_file_idxs`만 현재 요청의 검색 범위로 사용한다.

```text
사용자 질문 전송
        ↓
AWS Backend가 사용자와 선택 문서 권한 검증
        ↓
AWS Backend가 reference_file_idxs 스냅샷 생성
        ↓
POST /api/v1/rag/answers
        ↓
Local RAG가 사용자·활성 상태·선택 문서 조건으로 Qdrant 검색
        ↓
검색 결과 없음 ──→ Claude 미호출 + insufficient_evidence
        ↓
검색 결과 있음
        ↓
근거 프롬프트 구성 및 Claude 호출
        ↓
답변 + 파일명 + 원본 위치 + 섹션 + 청크 발췌문 반환
```

Local RAG는 `reference_file_idxs`가 없거나 비어 있는 요청을 사용자의 전체 문서
검색으로 변환하지 않는다.

---

## 3. 요청 헤더

### 3.1 필수 헤더

| 헤더               | 필수 | 설명                                   |
| ------------------ | ---- | -------------------------------------- |
| `Content-Type`     | 예   | `application/json`                     |
| `X-Internal-Token` | 예   | AWS Backend → Local RAG 내부 인증 토큰 |

`X-Internal-Token`이 없거나 설정값과 일치하지 않으면 요청 본문 처리 전에
`401 UNAUTHORIZED`를 반환한다.

### 3.2 선택 헤더

| 헤더           | 필수   | 설명                       |
| -------------- | ------ | -------------------------- |
| `X-Request-ID` | 아니요 | 서비스 간 요청 추적용 UUID |

AWS Backend가 유효한 UUID 형식의 `X-Request-ID`를 전달하면 Local RAG는 같은 값을
응답 헤더에 반환한다. 헤더가 없거나 유효하지 않으면 Local RAG가 새 Request ID를
생성할 수 있다.

내부 인증 토큰, 사용자 질문, 청크 원문, 생성 프롬프트 및 Claude API Key는 로그에
기록하지 않는다.

---

## 4. 요청 본문

### 4.1 JSON Schema 수준 계약

| 필드                  | 타입             | 필수   | 기본값 | 제약                                                |
| --------------------- | ---------------- | ------ | ------ | --------------------------------------------------- |
| `user_idx`            | integer          | 예     | 없음   | `1` 이상의 정수                                     |
| `reference_file_idxs` | integer array    | 예     | 없음   | `1~20`개, 중복 없음, 각 값은 `1` 이상의 엄격한 정수 |
| `query`               | string           | 예     | 없음   | 앞뒤 공백 제거 후 `1~4096`자                        |
| `top_k`               | integer          | 아니요 | `5`    | `1~20`                                              |
| `score_threshold`     | number 또는 null | 아니요 | `null` | `-1.0~1.0`                                          |

정의되지 않은 추가 필드는 허용하지 않는다.

`reference_file_idxs`의 문자열, 실수 및 Boolean 값은 정수로 자동 변환하지 않는다.

### 4.2 정상 요청 예시

```json
{
  "user_idx": 45,
  "reference_file_idxs": [123, 456],
  "query": "프로젝트의 로컬 실행 절차를 알려줘",
  "top_k": 5,
  "score_threshold": 0.6
}
```

### 4.3 참조문서 스냅샷 규칙

`reference_file_idxs`는 질문 전송 시점의 선택 상태를 나타낸다.

```text
요청 1: [123]
질문 처리 중 화면 선택 변경: [123, 456]
요청 1의 검색 범위: [123]

요청 2: [123, 456]
요청 2의 검색 범위: [123, 456]

화면에서 123 해제
요청 3: [456]
요청 3의 검색 범위: [456]
```

참조문서 추가 또는 해제는 이미 처리 중인 요청에 영향을 주지 않으며 다음 요청부터
적용한다.

### 4.4 빈 참조문서 요청

다음 요청은 모두 같은 오류 계약으로 처리한다.

#### 필드 생략

```json
{
  "user_idx": 45,
  "query": "질문",
  "top_k": 5
}
```

#### null

```json
{
  "user_idx": 45,
  "reference_file_idxs": null,
  "query": "질문",
  "top_k": 5
}
```

#### 빈 배열

```json
{
  "user_idx": 45,
  "reference_file_idxs": [],
  "query": "질문",
  "top_k": 5
}
```

응답:

```json
{
  "success": false,
  "code": "REFERENCE_DOCUMENT_REQUIRED",
  "message": "At least one reference document must be selected.",
  "data": null
}
```

HTTP Status는 `422 Unprocessable Entity`다.

---

## 5. 공통 응답 Envelope

모든 응답은 다음 최상위 구조를 사용한다.

| 필드      | 타입             | 설명                                |
| --------- | ---------------- | ----------------------------------- |
| `success` | boolean          | 요청 처리 성공 여부                 |
| `code`    | string           | AWS Backend가 분기 처리할 응답 코드 |
| `message` | string           | 외부 공개 가능한 처리 결과 메시지   |
| `data`    | object 또는 null | 성공 데이터 또는 오류 세부 데이터   |

성공 응답의 공통 값은 다음과 같다.

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {}
}
```

`RAG_ANSWER_COMPLETED`는 Claude 호출 성공만 의미하지 않는다. 검색 결과가 없어
`insufficient_evidence`로 정상 종료된 경우에도 요청 처리는 성공했으므로 같은
최상위 성공 코드를 사용한다.

---

## 6. 정상 답변 응답

### 6.1 예시

```json
{
  "success": true,
  "code": "RAG_ANSWER_COMPLETED",
  "message": "The RAG answer request was processed.",
  "data": {
    "answer": "로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다. [SOURCE-1]",
    "status": "answered",
    "sources": [
      {
        "source_id": "SOURCE-1",
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "rag_document_idx": 100,
        "file_idx": 123,
        "folder_idx": 9,
        "file_name": "프로젝트 가이드.pdf",
        "file_type": "pdf",
        "chunk_index": 3,
        "score": 0.82,
        "page": 2,
        "slide_no": null,
        "sheet_name": null,
        "section_title": "로컬 실행 방법",
        "excerpt": "로컬 RAG 서버는 PowerShell 시작 스크립트로 실행합니다."
      }
    ],
    "model": "claude-sonnet-5",
    "usage": {
      "input_tokens": 1024,
      "output_tokens": 256
    },
    "stop_reason": "end_turn"
  }
}
```

### 6.2 `data` 필드

| 필드          | 타입        | nullable | 설명                                    |
| ------------- | ----------- | -------- | --------------------------------------- |
| `answer`      | string      | 아니요   | 문서 근거 기반 답변                     |
| `status`      | string enum | 아니요   | `answered` 또는 `insufficient_evidence` |
| `sources`     | array       | 아니요   | 답변에 사용한 출처 목록                 |
| `model`       | string      | 예       | Claude 실제 응답 모델 ID                |
| `usage`       | object      | 예       | Claude 입력·출력 토큰 사용량            |
| `stop_reason` | string      | 예       | Claude 응답 종료 사유                   |

`status=answered`인 경우 다음 조건을 만족해야 한다.

- `sources`에 하나 이상의 출처가 존재한다.
- `model`은 null이 아니다.
- `usage`는 null이 아니다.
- 각 `source_id`와 `chunk_id`는 응답 안에서 중복되지 않는다.
- 모든 출처의 `file_idx`는 요청의 `reference_file_idxs`에 포함된다.

---

## 7. 출처 객체 계약

| 필드               | 타입    | nullable | 제약 및 의미                             |
| ------------------ | ------- | -------- | ---------------------------------------- |
| `source_id`        | string  | 아니요   | `SOURCE-1` 형식의 요청 범위 인용 식별자  |
| `chunk_id`         | string  | 아니요   | Local RAG DB 청크 및 Qdrant Point 식별자 |
| `rag_document_idx` | integer | 아니요   | Local RAG DB `RAG_Document` 식별자       |
| `file_idx`         | integer | 아니요   | AWS DB `File.File_IDX`                   |
| `folder_idx`       | integer | 예       | AWS DB `Folder.Folder_IDX`               |
| `file_name`        | string  | 아니요   | 색인 시점 파일 표시명                    |
| `file_type`        | string  | 아니요   | 현재 `pdf`                               |
| `chunk_index`      | integer | 아니요   | 문서 내부 0 기반 청크 순번               |
| `score`            | number  | 아니요   | Cosine 관련도 점수 `-1.0~1.0`            |
| `page`             | integer | 예       | PDF 원본 페이지 번호                     |
| `slide_no`         | integer | 예       | PPTX 원본 슬라이드 번호                  |
| `sheet_name`       | string  | 예       | XLSX 원본 시트 이름                      |
| `section_title`    | string  | 예       | 파서가 추출한 섹션 제목                  |
| `excerpt`          | string  | 아니요   | 최대 1000자의 청크 발췌문                |

`page`, `slide_no`, `sheet_name` 중 하나만 설정할 수 있다.

AWS Backend는 출처 이동 또는 표시 시 `file_name`이 아니라 `file_idx`를 식별자로
사용해야 한다. `file_name`은 표시용 스냅샷이다.

---

## 8. 근거 부족 응답

선택 문서 범위에서 검색 결과가 없으면 Local RAG는 Claude API를 호출하지 않는다.

### 8.1 응답 예시

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

### 8.2 AWS Backend 처리

- HTTP 오류로 처리하지 않는다.
- `data.status`가 `insufficient_evidence`인지 확인한다.
- 출처 UI를 비우거나 숨긴다.
- `model`, `usage`, `stop_reason`이 null임을 허용한다.
- 같은 질문을 전체 문서 범위로 자동 재요청하지 않는다.

---

## 9. 요청 검증 오류

참조문서 미선택 이외의 스키마 오류는 공통 검증 오류로 반환한다.

### 9.1 예시

```json
{
  "success": false,
  "code": "REQUEST_VALIDATION_FAILED",
  "message": "Request validation failed.",
  "data": {
    "errors": [
      {
        "field": "body.reference_file_idxs.0",
        "message": "Input should be greater than 0",
        "error_type": "greater_than"
      }
    ]
  }
}
```

다음 입력이 포함된다.

- 중복된 `reference_file_idxs`
- 0 또는 음수 파일 식별자
- 문자열, 실수 또는 Boolean 파일 식별자
- 20개를 초과한 참조문서
- 빈 질문
- `top_k` 범위 위반
- `score_threshold` 범위 위반
- 정의되지 않은 추가 필드

AWS Backend는 검증 오류의 영문 `message` 텍스트를 분기 조건으로 사용하지 않고
`code`와 `data.errors[].field`를 사용한다.

---

## 10. 오류 응답 계약

| HTTP Status | 대표 `code`                     | 의미                                        | AWS 자동 재시도    |
| ----------- | ------------------------------- | ------------------------------------------- | ------------------ |
| `401`       | `UNAUTHORIZED`                  | 내부 토큰 누락 또는 불일치                  | 아니요             |
| `422`       | `REFERENCE_DOCUMENT_REQUIRED`   | 참조문서 미선택                             | 아니요             |
| `422`       | `REQUEST_VALIDATION_FAILED`     | 요청 스키마 위반                            | 아니요             |
| `429`       | `TOO_MANY_REQUESTS`             | 외부 생성 공급자 요청 제한                  | 제한된 지수 백오프 |
| `502`       | `EMBEDDING_REQUEST_REJECTED`    | TEI가 내부 요청을 거부                      | 기본적으로 아니요  |
| `502`       | `INVALID_EMBEDDING_RESPONSE`    | TEI 응답 계약 위반                          | 기본적으로 아니요  |
| `502`       | `VECTOR_SEARCH_FAILED`          | Qdrant 검색 요청 실패                       | 조건부             |
| `502`       | `INVALID_VECTOR_SEARCH_RESULT`  | Qdrant 검색 결과 계약 위반                  | 아니요             |
| `503`       | `EMBEDDING_SERVICE_UNAVAILABLE` | TEI 일시적 사용 불가                        | 예                 |
| `503`       | `VECTOR_DATABASE_UNAVAILABLE`   | Qdrant 일시적 사용 불가                     | 예                 |
| `503`       | `SERVICE_UNAVAILABLE`           | Local RAG 또는 생성 공급자 일시적 사용 불가 | 예                 |
| `504`       | `EMBEDDING_SERVICE_TIMEOUT`     | TEI 요청 시간 초과                          | 조건부             |
| `500`       | `INTERNAL_SERVER_ERROR`         | 분류되지 않은 내부 오류                     | 기본적으로 아니요  |

오류 응답은 다음 Envelope를 사용한다.

```json
{
  "success": false,
  "code": "SERVICE_UNAVAILABLE",
  "message": "The service is temporarily unavailable.",
  "data": null
}
```

응답에는 내부 예외 메시지, Stack Trace, 질문 원문, 청크 원문, 프롬프트, 벡터,
Qdrant payload, 내부 토큰 또는 Claude API Key가 포함되지 않는다.

---

## 11. AWS Backend 구현 체크리스트

- [ ] 사용자 인증과 파일 접근 권한을 AWS Backend에서 먼저 검증한다.
- [ ] 질문 전송 시점의 선택 파일을 `reference_file_idxs` 배열로 복사한다.
- [ ] 선택 파일이 0개이면 Local RAG를 호출하지 않고 사용자 입력을 차단한다.
- [ ] `reference_file_idxs`를 중복 제거한 뒤 보내는 대신 중복이 생기지 않도록 선택 상태를 관리한다.
- [ ] `X-Internal-Token`을 요청 헤더에 추가한다.
- [ ] `X-Request-ID`를 전달하고 응답 헤더 값을 로그 상관관계에 사용한다.
- [ ] `200` 응답에서도 `data.status`를 확인한다.
- [ ] `insufficient_evidence`를 실패 예외로 변환하지 않는다.
- [ ] 출처 연결에는 `file_idx`를 사용한다.
- [ ] `page`, `slide_no`, `sheet_name`의 nullable 처리를 적용한다.
- [ ] 오류 분기는 `message`가 아니라 HTTP Status와 `code`를 사용한다.
- [ ] 질문 원문, 내부 토큰 및 RAG 응답의 전체 청크 발췌문을 운영 로그에 기록하지 않는다.

---

## 12. 계약 테스트 필수 시나리오

| 시나리오                          | 기대 결과                                       |
| --------------------------------- | ----------------------------------------------- |
| `[123]` 선택 후 질문              | 123번 파일 청크만 검색                          |
| `[123, 456]`로 추가 후 다음 질문  | 123번과 456번 파일 청크 검색                    |
| `[456]`로 123 해제 후 다음 질문   | 456번 파일 청크만 검색                          |
| 처리 중 UI 선택 변경              | 이미 전송된 요청 범위는 변경되지 않음           |
| 미선택 파일 청크가 가장 높은 점수 | 검색 결과와 출처에서 제외                       |
| `reference_file_idxs` 생략        | `422 REFERENCE_DOCUMENT_REQUIRED`               |
| `reference_file_idxs: null`       | `422 REFERENCE_DOCUMENT_REQUIRED`               |
| `reference_file_idxs: []`         | `422 REFERENCE_DOCUMENT_REQUIRED`               |
| 중복 파일 식별자                  | `422 REQUEST_VALIDATION_FAILED`                 |
| 선택 문서 검색 결과 없음          | `200`, `insufficient_evidence`, Claude 미호출   |
| 정상 검색 결과 존재               | `200`, `answered`, 출처 및 생성 메타데이터 포함 |
| 내부 토큰 없음 또는 불일치        | `401 UNAUTHORIZED`, 서비스 미호출               |

---

## 13. 보안 및 비노출 계약

다음 값은 외부 오류 응답과 구조화 로그에 기록하지 않는다.

- 사용자 질문 원문
- 검색 청크 원문 전체
- Claude 시스템 프롬프트
- Claude 사용자 프롬프트
- Claude API Key
- `X-Internal-Token`
- 질의 또는 문서 임베딩 벡터
- Qdrant 원본 payload
- Presigned URL
- 하위 SDK의 원본 응답 본문

응답의 `sources[].excerpt`는 사용자에게 근거를 제공하기 위한 제한된 공개 필드이며
최대 길이는 1000자다.
