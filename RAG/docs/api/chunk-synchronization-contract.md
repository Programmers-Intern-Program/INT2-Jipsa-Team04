# 애플리케이션 서버–RAG 청크 동기화 API 계약

## 1. 문서 목적

이 문서는 파일 인제스트 또는 재색인이 성공한 뒤 RAG 서버가 생성한
최신 활성 청크 전체를 애플리케이션 서버에 전달하는 내부 API 계약을 정의한다.

이 계약의 핵심 목표는 다음과 같다.

- 애플리케이션 서버가 RAG의 최신 청크 원문 스냅샷을 AWS DB에 저장할 수 있다.
- 재색인 이후 이전 청크가 다시 전달되는 경쟁 조건을 방지한다.
- 동일 파일에 대한 중복 요청과 HTTP 재시도에도 같은 결과를 멱등하게 처리한다.
- 임베딩 벡터, Presigned URL 및 내부 비밀값을 AWS 서버 payload에 포함하지 않는다.

문서 검색, 폴더 검색, 내 드라이브 전체 검색 및 질의응답 API는 이 문서의 범위에
포함하지 않는다.

---

## 2. 서비스 역할

### 2.1 애플리케이션 서버

- 파일과 사용자 정보의 기준 데이터를 관리한다.
- S3 접근 권한과 Presigned GET URL 발급을 담당한다.
- RAG 서버에 파일 인제스트 시작을 요청한다.
- RAG 서버가 전달한 최신 청크 스냅샷을 AWS DB에 반영한다.
- AWS DB의 `Chunk_IDX`는 `BIGINT AUTO_INCREMENT` 기본 키를 유지한다.
- RAG 청크 식별자 `CHUNK_ID`와 색인 규칙 버전 `INDEX_VERSION`을
  별도 컬럼으로 저장한다.

### 2.2 RAG 서버

- 애플리케이션 서버에서 최신 manifest를 조회한다.
- Presigned GET URL로 원본 파일을 다운로드한다.
- 문서 파싱, 청킹, 임베딩 생성, Local RAG DB 저장 및 Qdrant 색인을 수행한다.
- 색인이 성공하면 해당 파일의 최신 `SUCCESS` 실행이 소유한
  활성 청크 전체를 조회한다.
- 최신 활성 청크 스냅샷을 애플리케이션 서버의
  `ingest-complete` API로 전달한다.
- 임베딩 벡터는 Qdrant에만 저장하고 청크 동기화 payload에는 포함하지 않는다.

---

## 3. 전체 호출 흐름

```text
애플리케이션 서버
    |
    | 1. POST /ingest
    |    X-Internal-Token: RAG_INGEST_TOKEN
    v
RAG 서버
    |
    | 2. GET /internal/files/{fileIdx}/manifest
    |    X-Internal-Token: INTERNAL_TOKEN
    v
애플리케이션 서버
    |
    | 3. 최신 manifest 및 Presigned GET URL 반환
    v
RAG 서버
    |
    | 4. 다운로드 → 파싱 → 청킹 → 임베딩 → Local RAG DB/Qdrant 색인
    |
    | 5. File_IDX lock 획득
    | 6. 최신 SUCCESS 실행의 활성 청크 전체 조회
    | 7. lock을 유지한 채 ingest-complete 성공 콜백 전송
    v
애플리케이션 서버
    |
    | 8. 최신 청크 스냅샷을 하나의 트랜잭션으로 반영
    | 9. 204 No Content 반환
    v
RAG 서버
```

# 애플리케이션 서버–RAG 청크 동기화 API 계약

## 1. 문서 목적

이 문서는 파일 인제스트 또는 재색인이 성공한 뒤 RAG 서버가 생성한
최신 활성 청크 전체를 애플리케이션 서버에 전달하는 내부 API 계약을 정의한다.

이 계약의 핵심 목표는 다음과 같다.

- 애플리케이션 서버가 RAG의 최신 청크 원문 스냅샷을 AWS DB에 저장할 수 있다.
- 재색인 이후 이전 청크가 다시 전달되는 경쟁 조건을 방지한다.
- 동일 파일에 대한 중복 요청과 HTTP 재시도에도 같은 결과를 멱등하게 처리한다.
- 임베딩 벡터, Presigned URL 및 내부 비밀값을 AWS 서버 payload에 포함하지 않는다.

문서 검색, 폴더 검색, 내 드라이브 전체 검색 및 질의응답 API는 이 문서의 범위에
포함하지 않는다.

---

## 2. 서비스 역할

### 2.1 애플리케이션 서버

- 파일과 사용자 정보의 기준 데이터를 관리한다.
- S3 접근 권한과 Presigned GET URL 발급을 담당한다.
- RAG 서버에 파일 인제스트 시작을 요청한다.
- RAG 서버가 전달한 최신 청크 스냅샷을 AWS DB에 반영한다.
- AWS DB의 `Chunk_IDX`는 `BIGINT AUTO_INCREMENT` 기본 키를 유지한다.
- RAG 청크 식별자 `CHUNK_ID`와 색인 규칙 버전 `INDEX_VERSION`을
  별도 컬럼으로 저장한다.

### 2.2 RAG 서버

- 애플리케이션 서버에서 최신 manifest를 조회한다.
- Presigned GET URL로 원본 파일을 다운로드한다.
- 문서 파싱, 청킹, 임베딩 생성, Local RAG DB 저장 및 Qdrant 색인을 수행한다.
- 색인이 성공하면 해당 파일의 최신 `SUCCESS` 실행이 소유한
  활성 청크 전체를 조회한다.
- 최신 활성 청크 스냅샷을 애플리케이션 서버의
  `ingest-complete` API로 전달한다.
- 임베딩 벡터는 Qdrant에만 저장하고 청크 동기화 payload에는 포함하지 않는다.

---

## 3. 전체 호출 흐름

```text
애플리케이션 서버
    |
    | 1. POST /ingest
    |    X-Internal-Token: RAG_INGEST_TOKEN
    v
RAG 서버
    |
    | 2. GET /internal/files/{fileIdx}/manifest
    |    X-Internal-Token: INTERNAL_TOKEN
    v
애플리케이션 서버
    |
    | 3. 최신 manifest 및 Presigned GET URL 반환
    v
RAG 서버
    |
    | 4. 다운로드 → 파싱 → 청킹 → 임베딩 → Local RAG DB/Qdrant 색인
    |
    | 5. File_IDX lock 획득
    | 6. 최신 SUCCESS 실행의 활성 청크 전체 조회
    | 7. lock을 유지한 채 ingest-complete 성공 콜백 전송
    v
애플리케이션 서버
    |
    | 8. 최신 청크 스냅샷을 하나의 트랜잭션으로 반영
    | 9. 204 No Content 반환
    v
RAG 서버
```

RAG 서버는 최신 활성 청크 조회부터 성공 콜백 응답 수신까지
같은 `File_IDX` lock을 유지한다.

따라서 같은 파일의 다음 재색인은 이전 성공 콜백이 종료된 뒤에만
색인 임계 구역에 진입할 수 있다.

---

## 4. 인증 계약

서비스 간 요청은 모두 `X-Internal-Token` 헤더를 사용한다.

| 호출 방향               | API                                              | 헤더 값                                                                   |
| ----------------------- | ------------------------------------------------ | ------------------------------------------------------------------------- |
| 애플리케이션 서버 → RAG | `POST /ingest`                                   | 애플리케이션 서버의 `RAG_INGEST_TOKEN`과 RAG 서버 설정값이 일치해야 한다. |
| RAG → 애플리케이션 서버 | `GET /internal/files/{fileIdx}/manifest`         | RAG 서버의 `INTERNAL_TOKEN`과 애플리케이션 서버 설정값이 일치해야 한다.   |
| RAG → 애플리케이션 서버 | `POST /internal/files/{fileIdx}/ingest-complete` | RAG 서버의 `INTERNAL_TOKEN`과 애플리케이션 서버 설정값이 일치해야 한다.   |

두 방향의 토큰은 서로 다른 비밀값으로 관리한다.

토큰 원문은 다음 위치에 기록하지 않는다.

- 요청 로그
- 응답 로그
- 예외 메시지
- 구조화 로그의 `extra` 필드
- 테스트 출력
- 애플리케이션 설정 객체의 문자열 표현

---

## 5. 청크 동기화 완료 API

### 5.1 요청

```http
POST /internal/files/{fileIdx}/ingest-complete
Content-Type: application/json
Accept: application/json
X-Internal-Token: <INTERNAL_TOKEN>
```

### 5.2 Path Parameter

| 이름      | 타입 | 필수 | 제약   | 설명                   |
| --------- | ---: | :--: | ------ | ---------------------- |
| `fileIdx` | 정수 |  O   | 1 이상 | AWS DB `File.File_IDX` |

요청 경로의 `fileIdx`와 payload가 나타내는 최신 활성 청크 스냅샷은
같은 파일을 가리켜야 한다.

### 5.3 성공 응답

애플리케이션 서버는 요청을 정상적으로 반영하면 본문 없이
다음 상태를 반환한다.

```http
HTTP/1.1 204 No Content
```

RAG 클라이언트는 `204 No Content`만 정상 성공으로 처리한다.

다른 `2xx` 응답을 성공으로 간주하지 않는다.

---

## 6. 성공 콜백 payload

### 6.1 예시

```json
{
  "success": true,
  "index_version": 2,
  "chunk_count": 2,
  "chunks": [
    {
      "chunk_id": "8d777f38-65d3-5b30-bc6c-4b8f8f2f8612",
      "chunk_index": 0,
      "content": "동기화할 첫 번째 청크 원문",
      "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "token_count": null,
      "source_metadata": {
        "page_number": 1
      }
    },
    {
      "chunk_id": "3f786850-e387-550f-bc6c-4b8f8f2f8612",
      "chunk_index": 1,
      "content": "동기화할 두 번째 청크 원문",
      "content_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "token_count": null,
      "source_metadata": {
        "page_number": 2
      }
    }
  ]
}
```

### 6.2 최상위 필드

| 필드            | 타입    | 필수 | 제약                           | 설명                                                 |
| --------------- | ------- | :--: | ------------------------------ | ---------------------------------------------------- |
| `success`       | boolean |  O   | 성공 콜백은 `true`             | 인제스트 및 색인 성공 여부                           |
| `index_version` | integer |  O   | 1 이상                         | 전달된 모든 청크에 적용된 Chunk ID 및 색인 규칙 버전 |
| `chunk_count`   | integer |  O   | 1 이상, `chunks.length`와 동일 | 전달된 최신 활성 청크 수                             |
| `chunks`        | array   |  O   | 1개 이상                       | 최신 활성 문서에 속한 전체 청크 스냅샷               |
| `error_message` | string  |  X   | 성공 payload에 포함 금지       | 실패 콜백에서만 사용                                 |

### 6.3 청크 필드

| 필드              | 타입              | 필수 | 제약                                  | 설명                                             |
| ----------------- | ----------------- | :--: | ------------------------------------- | ------------------------------------------------ |
| `chunk_id`        | string            |  O   | 표준 UUID 문자열, payload 안에서 유일 | RAG가 결정적으로 생성한 청크 식별자              |
| `chunk_index`     | integer           |  O   | 0부터 시작하며 중간 누락 없이 연속    | 문서 안에서의 청크 순서                          |
| `content`         | string            |  O   | 빈 문자열 금지                        | 검색 및 인용에 사용할 청크 원문                  |
| `content_hash`    | string            |  O   | SHA-256 소문자 16진수 64자            | `content` 원문의 무결성 해시                     |
| `token_count`     | integer 또는 null |  O   | 값이 있으면 0 이상                    | 임베딩 토크나이저 기준 토큰 수. 미계산 시 `null` |
| `source_metadata` | object            |  O   | JSON 직렬화 가능 값만 허용            | 페이지·슬라이드·시트·섹션 등 원문 위치 정보      |

### 6.4 `source_metadata` 값 범위

`source_metadata`의 값은 다음 형식만 사용한다.

- 문자열
- 정수
- 실수
- boolean
- `null`
- 위 스칼라 값으로만 구성된 배열

다음 값은 허용하지 않는다.

- 중첩 객체
- 임베딩 벡터
- 바이너리 데이터
- 임의 클래스 직렬화 결과
- 내부 데이터베이스 객체
- SQLAlchemy 전용 타입

PDF 청크 예시는 다음과 같다.

```json
{
  "page_number": 3
}
```

향후 문서 파서가 추가되면 다음과 같은 위치 정보를 사용할 수 있다.

```json
{
  "sheet_name": "매출현황",
  "section_path": ["2026년", "3분기"]
}
```

---

## 7. 성공 payload 검증 규칙

성공 콜백은 다음 조건을 모두 만족해야 한다.

1. `success`는 `true`다.
2. `error_message`는 존재하지 않는다.
3. `index_version`, `chunk_count`, `chunks`는 모두 함께 존재한다.
4. `chunk_count`는 실제 `chunks` 배열 길이와 같다.
5. `chunk_index`는 배열 순서와 동일하게
   `0..chunk_count-1`로 연속된다.
6. 하나의 payload 안에서 `chunk_id`는 중복되지 않는다.
7. 모든 청크는 같은 최신 활성 `RAG_Document` 스냅샷에 속한다.
8. 청크의 `content_hash`는 전달된 `content` 원문을 변경하지 않고
   계산한 SHA-256이다.
9. `content`에는 `strip()`, 줄바꿈 치환 또는 임의 정규화를 적용하지 않는다.
10. 임베딩 벡터는 포함하지 않는다.
11. Presigned URL 및 내부 인증 정보는 포함하지 않는다.
12. `source_metadata`는 JSON으로 직렬화할 수 있어야 한다.

검증에 실패한 불완전한 스냅샷은 성공 콜백으로 전송하지 않는다.

---

## 8. 실패 콜백 payload

### 8.1 예시

```json
{
  "success": false,
  "error_message": "INVALID_DOCUMENT: The document structure is invalid."
}
```

### 8.2 최상위 필드

| 필드            | 타입    | 필수 | 제약                         | 설명                                  |
| --------------- | ------- | :--: | ---------------------------- | ------------------------------------- |
| `success`       | boolean |  O   | 실패 콜백은 `false`          | 인제스트 처리 실패 여부               |
| `error_message` | string  |  O   | 빈 문자열 금지, 최대 4,000자 | 외부 공개가 가능한 안전한 실패 메시지 |
| `index_version` | integer |  X   | 포함 금지                    | 실패 시 동기화 버전을 전달하지 않음   |
| `chunk_count`   | integer |  X   | 포함 금지                    | 실패 시 청크 개수를 전달하지 않음     |
| `chunks`        | array   |  X   | 포함 금지                    | 실패 시 부분 청크를 전달하지 않음     |

### 8.3 실패 콜백 규칙

- `success`는 `false`다.
- `error_message`는 비어 있지 않은 외부 공개용 메시지다.
- `error_message` 최대 길이는 4,000자다.
- `index_version`은 포함하지 않는다.
- `chunk_count`는 포함하지 않는다.
- `chunks`는 포함하지 않는다.
- 부분적으로 생성된 청크를 포함하지 않는다.
- 이전 정상 색인의 청크를 실패 payload에 포함하지 않는다.

`error_message`에는 다음 값을 포함하지 않는다.

- 내부 SQL
- 파일 시스템 전체 경로
- Presigned GET URL
- Presigned URL Query String
- 내부 인증 토큰
- DB 접속 정보
- Qdrant 접속 정보
- 라이브러리 원본 오류 메시지
- 사용자 파일 원문
- 청크 원문

---

## 9. 재색인 동기화 규칙

재색인이 성공한 경우 RAG 서버는 현재 요청에서 반환된 특정
`RAG_Document_IDX`를 기준으로 청크를 읽지 않는다.

대신 다음 조건을 만족하는 최신 스냅샷을 조회한다.

- 요청의 `Users_IDX`와 `File_IDX`가 일치한다.
- `RAG_Index_Run.Status = 'SUCCESS'`다.
- 연결된 `RAG_Document.Index_Status = 'INDEXED'`다.
- `RAG_Document.Deleted_At IS NULL`이다.
- 같은 파일 범위에서 가장 큰 `RAG_Index_Run_IDX`를 가진 성공 실행이다.
- `RAG_Chunk.Index_Version`과 상위 문서의 `Index_Version`이 일치한다.
- `Chunk_Index` 오름차순으로 전체 청크를 조회한다.

따라서 이전 요청이 더 최신 재색인보다 늦게 콜백 단계에 도착해도
이전 청크가 아니라 콜백 시점의 최신 활성 청크 전체를 전달한다.

### 9.1 예시

다음과 같은 실행 순서가 발생할 수 있다.

```text
요청 A: RAG_Document_IDX = 100 색인 완료
요청 B: RAG_Document_IDX = 200 재색인 완료
요청 B: 최신 청크 성공 콜백 완료
요청 A: 성공 콜백 단계 도착
```

요청 A의 직접 처리 결과는 `RAG_Document_IDX = 100`이지만,
요청 A가 성공 콜백을 생성할 때 조회해야 하는 문서는
최신 활성 문서인 `RAG_Document_IDX = 200`이다.

요청 A의 성공 콜백에 문서 100의 이전 청크를 포함해서는 안 된다.

---

## 10. 동일 파일 중복 요청과 멱등성

동일 파일과 동일 색인 입력을 반복 처리하면 RAG는
결정적인 `chunk_id`를 재사용한다.

결정적 청크 식별에는 다음 입력이 반영된다.

- 사용자 식별자
- 파일 식별자
- 다운로드한 원본 파일의 SHA-256
- 파일 형식
- 색인 버전
- 파서 버전
- 임베딩 모델
- 청크 순번
- 청크 원문의 SHA-256

따라서 위 값이 모두 같으면 같은 청크는 같은 `chunk_id`를 가진다.

### 10.1 애플리케이션 서버 멱등 처리 요구사항

애플리케이션 서버는 다음 원칙으로 중복 요청을 처리한다.

- 같은 `fileIdx`, `index_version`, `chunk_id` 조합이 다시 전달되어도
  중복 청크를 생성하지 않는다.
- 동일 payload를 여러 번 반영해도 최종 AWS DB 상태가 달라지지 않아야 한다.
- 하나의 성공 payload를 해당 파일의 완전한 최신 청크 스냅샷으로 취급한다.
- payload에 없는 이전 청크는 최신 스냅샷에 속하지 않는 것으로 처리한다.
- 청크 반영과 파일 인제스트 상태 변경은 하나의 DB 트랜잭션 안에서 완료한다.
- 이미 동일한 최신 스냅샷이 반영된 경우에도 `204 No Content`를 반환할 수 있어야 한다.

### 10.2 RAG 서버 직렬화 요구사항

RAG 서버는 최신 청크 조회와 성공 콜백을
같은 `File_IDX` lock 안에서 수행한다.

동일 파일에 대해 다음 두 성공 콜백이 동시에 실행되어서는 안 된다.

```text
요청 A
    ├─ File_IDX lock 획득
    ├─ 최신 활성 청크 조회
    ├─ 성공 콜백 전송
    └─ File_IDX lock 해제

요청 B
    ├─ File_IDX lock 대기
    ├─ 요청 A의 lock 해제 후 획득
    ├─ 최신 활성 청크 재조회
    ├─ 성공 콜백 전송
    └─ File_IDX lock 해제
```

요청 B는 요청 A가 읽었던 스냅샷을 캐시하여 사용하지 않고,
lock을 획득한 뒤 최신 활성 청크를 다시 조회한다.

---

## 11. HTTP 재시도 계약

RAG 클라이언트는 다음 상황을 일시적 오류로 판단하여
설정된 최대 횟수까지 재시도할 수 있다.

- 연결 오류
- 요청 시간 초과
- `408 Request Timeout`
- `429 Too Many Requests`
- `500` 이상 서버 오류

따라서 애플리케이션 서버의 `ingest-complete` 처리는 반드시 멱등해야 한다.

### 11.1 재시도 간격

재시도 간격은 다음 형태의 지수 증가 지연을 사용한다.

```text
delay = min(
    initial_delay × 2^(attempt_number - 1),
    maximum_delay
)
```

### 11.2 재시도하지 않는 응답

다음 응답은 정상 성공으로 간주하지 않으며,
일반적인 서버 오류 재시도 대상에도 포함하지 않는다.

- `204`가 아닌 다른 `2xx`
- `3xx`
- 인증 실패를 포함한 일반 `4xx`
- 존재하지 않는 파일에 대한 `404`

애플리케이션 서버의 응답 본문은 RAG 로그에 기록하지 않는다.

---

## 12. 애플리케이션 서버 반영 요구사항

애플리케이션 서버는 성공 콜백을 수신하면 다음 작업을
원자적으로 수행해야 한다.

1. `X-Internal-Token`을 검증한다.
2. Path Parameter의 `fileIdx`가 1 이상의 정수인지 검증한다.
3. 대상 파일이 AWS DB에 존재하는지 확인한다.
4. 성공 payload의 필드 조합을 검증한다.
5. `chunk_count`와 실제 청크 배열 길이가 같은지 검증한다.
6. `chunk_index`가 0부터 연속되는지 검증한다.
7. payload 내부의 `chunk_id` 중복 여부를 검증한다.
8. 각 청크의 원문, 해시, 순번, 토큰 수 및 출처 정보를 저장한다.
9. 각 청크에 `CHUNK_ID`와 `INDEX_VERSION`을 저장한다.
10. 현재 payload에 포함되지 않은 이전 청크를 제거하거나 비활성화한다.
11. 파일의 RAG 인제스트 상태를 성공으로 갱신한다.
12. 모든 변경을 커밋한 뒤 `204 No Content`를 반환한다.

중간 단계가 실패하면 일부 청크만 저장된 상태를 남기지 않고
전체 트랜잭션을 rollback한다.

### 12.1 AWS DB 식별자 원칙

AWS DB의 내부 기본 키는 다음과 같이 유지한다.

```text
Chunk_IDX BIGINT AUTO_INCREMENT PRIMARY KEY
```

RAG에서 생성한 식별자는 별도 컬럼으로 저장한다.

```text
CHUNK_ID
INDEX_VERSION
```

`Chunk_IDX`를 RAG의 UUID 문자열로 교체하지 않는다.

### 12.2 권장 유일성 제약

애플리케이션 서버 DB에서 중복 반영을 방지하려면
파일과 RAG 청크 식별자의 조합에 유일성 제약을 적용한다.

개념적인 구성은 다음과 같다.

```text
UNIQUE (
    File_IDX,
    INDEX_VERSION,
    CHUNK_ID
)
```

실제 제약 이름과 컬럼명 대소문자는 애플리케이션 서버의
기존 DB 규칙을 따른다.

---

## 13. RAG 서버의 스냅샷 일관성 요구사항

RAG 서버가 성공 payload를 만들기 전에 조회한 문서와 청크는
다음 일관성 조건을 만족해야 한다.

- 모든 조회 행의 `RAG_Document_IDX`가 같다.
- 모든 조회 행의 `Users_IDX`가 같다.
- 모든 조회 행의 `File_IDX`가 같다.
- 모든 조회 행의 `Index_Version`이 같다.
- 모든 조회 행의 상위 `Chunk_Count`가 같다.
- 실제 조회된 행 수가 `Chunk_Count`와 같다.
- `Chunk_Index`가 0부터 연속된다.
- 같은 스냅샷 안에서 `Chunk_ID`가 중복되지 않는다.
- 하나 이상의 활성 청크가 존재한다.

조건을 만족하지 못하면 불완전한 성공 payload를 보내지 않고
Local RAG 저장소 일관성 오류로 처리한다.

---

## 14. 보안 및 로그 정책

청크 동기화 처리 중 로그에는 안전한 식별 정보만 기록한다.

기록 가능한 정보의 예시는 다음과 같다.

- 작업 종류
- `file_idx`
- `users_idx`
- 재시도 횟수
- HTTP 상태 코드
- 예외 클래스명
- 스냅샷 검증 실패 종류
- `rag_document_idx`
- 선언된 청크 개수
- 실제 청크 개수

다음 정보는 로그에 기록하지 않는다.

- `content`
- Presigned GET URL
- URL Query String
- `X-Internal-Token`
- `INTERNAL_TOKEN`
- `RAG_INGEST_TOKEN`
- DB 비밀번호
- 전체 DB 연결 URL
- 임베딩 벡터
- 애플리케이션 서버 응답 본문
- SQLAlchemy 오류 원문
- 사용자 파일 원문
- 임시 파일 전체 경로

---

## 15. 전송 금지 데이터

청크 동기화 payload에 다음 데이터를 포함하지 않는다.

- 임베딩 벡터
- Qdrant Point payload 전체
- `RAG_Document_IDX`
- `RAG_Index_Run_IDX`
- Presigned GET URL
- Presigned URL Query String
- S3 인증 정보
- `INTERNAL_TOKEN`
- `RAG_INGEST_TOKEN`
- Local RAG DB 접속 정보
- 내부 파일 경로
- SQLAlchemy 세션 또는 ORM 객체
- 사용자 파일 전체 원문
- 서버 내부 예외 상세

---

## 16. 호환성 정책

현재 `IngestCompleteRequest`는 이전 `success-only` 성공 콜백을
하위 호환 목적으로 허용한다.

```json
{
  "success": true
}
```

다만 실제 `POST /ingest` 성공 경로는 반드시 다음 세 필드를 모두 포함해야 한다.

- `index_version`
- `chunk_count`
- `chunks`

새 애플리케이션 서버 구현은 청크 동기화 필드가 포함된 성공 콜백을
정상 처리해야 한다.

하위 호환용 `success-only` 요청을 언제 제거할지는
애플리케이션 서버 배포 상태를 확인한 뒤 별도 이슈에서 결정한다.

---

## 17. 테스트 필수 시나리오

RAG 서버와 애플리케이션 서버는 최소한 다음 시나리오를 검증해야 한다.

### 17.1 RAG 서버

- 정상 성공 콜백에 최신 활성 청크 전체가 포함된다.
- `chunk_count`와 실제 청크 수가 일치한다.
- `chunk_index`가 0부터 연속된다.
- payload 안에서 `chunk_id`가 중복되지 않는다.
- `token_count=None`이 JSON `null`로 유지된다.
- `source_metadata`의 tuple이 JSON 배열로 변환된다.
- 임베딩 벡터가 payload에 포함되지 않는다.
- 실패 콜백에 청크 동기화 필드가 포함되지 않는다.
- 재색인 이후 이전 요청이 늦게 도착해도 최신 청크가 전달된다.
- 동일 파일 중복 요청의 성공 콜백이 직렬화된다.
- 동일 입력의 중복 요청이 같은 `chunk_id`를 전달한다.
- 성공 콜백 전송 중 lock이 해제되지 않는다.
- 내부 토큰과 청크 원문이 로그에 노출되지 않는다.

### 17.2 애플리케이션 서버

- 정상 성공 payload를 하나의 트랜잭션으로 저장한다.
- 동일 payload 재전송을 중복 없이 처리한다.
- 재색인 payload 반영 시 이전 청크를 제거하거나 비활성화한다.
- `chunk_count` 불일치를 거부한다.
- 불연속 `chunk_index`를 거부한다.
- 중복 `chunk_id`를 거부한다.
- 실패 콜백에 청크 데이터가 포함되면 거부한다.
- 내부 토큰 불일치 요청을 거부한다.
- 성공 반영 후 `204 No Content`를 반환한다.
- 트랜잭션 실패 시 일부 청크만 저장하지 않는다.

---

## 18. 관련 구성요소

RAG 서버의 관련 코드 위치는 다음과 같다.

```text
src/jipsa_rag/api/ingest.py
src/jipsa_rag/infrastructure/app_server/ingest_client.py
src/jipsa_rag/infrastructure/indexing/active_chunk_repository.py
src/jipsa_rag/infrastructure/indexing/chunk_snapshot_models.py
src/jipsa_rag/schemas/ingestion.py
src/jipsa_rag/services/active_chunk_snapshot.py
```

관련 테스트 위치는 다음과 같다.

```text
tests/unit/api/test_ingest_chunk_synchronization.py
tests/unit/infrastructure/app_server/test_ingest_client.py
tests/unit/infrastructure/indexing/test_active_chunk_repository.py
tests/unit/services/test_active_chunk_snapshot.py
tests/unit/schemas/test_ingestion.py
```

---

## 19. 관련 이슈

- Issue #40: RAG 인제스트 연동 및 재색인 안정성 개선
- PR #49: RAG 인제스트 연동 및 재색인 안정성 개선
- Issue #52: RAG 청크 데이터 동기화 구현
