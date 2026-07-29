# Local RAG 환경 변수와 비밀정보 관리

> **문서 상태:** Active · 보안·설정 기준  
> **주 독자:** Local RAG 개발자, QA, 운영 담당자  
> **최종 검토:** 2026-07-29  
> **원칙:** 환경 변수는 설정 전달 수단이며 로그·Git·테스트 결과의 비밀 저장소가 아님

## 1. 환경 파일 역할

| 파일 | 목적 | Git 커밋 |
|---|---|---:|
| `.env.example` | 변수 설명과 안전한 예시 | 허용 |
| `.env.local` | 실제 로컬 실행 | 금지 |
| `.env.development` | 개발 프로필 | 금지 |
| `.env.test` | 일반 테스트 프로필 | 금지 |

실제 값은 각 개발자의 로컬 환경에만 저장합니다.

## 2. 반드시 비밀로 취급하는 값

- `INTERNAL_TOKEN`
- `RAG_INGEST_TOKEN`
- `ANTHROPIC_API_KEY`
- Local RAG DB 비밀번호
- Presigned URL 전체 문자열
- AWS 서명 query
- 사용자 질문과 문서 원문
- 청크 텍스트와 OCR 텍스트
- Claude 전체 프롬프트와 응답 원문
- 임베딩 벡터
- HTTP 요청·응답 본문
- 원본 파일과 임시 파일의 절대 경로

Local RAG는 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`을 직접
사용하지 않습니다. S3 접근 권한과 Presigned GET URL 발급은 AWS Backend 책임이며,
Local RAG `.env.local`에 AWS 장기 자격 증명을 추가하지 않습니다.

금지 예:

```dotenv
AWS_ACCESS_KEY_ID=실제_Access_Key
AWS_SECRET_ACCESS_KEY=실제_Secret_Key
AWS_SESSION_TOKEN=실제_Session_Token
```

위 변수는 예시 이름을 문서화하기 위한 것이며 실제 값을 저장하거나 커밋해서는 안 됩니다.

## 3. 호출 방향별 토큰

`INTERNAL_TOKEN`과 `RAG_INGEST_TOKEN`은 호출 방향과 책임이 다르므로 혼용하지 않습니다.
정확한 Header 이름과 호출 주체는 종합 API 명세와 실제 HTTP client 코드를 기준으로
확인합니다.

| 방향 | 목적 | 검증 위치 |
|---|---|---|
| AWS Backend → Local RAG | `/ingest`, 검색, 답변 등 보호 API 호출 | Local RAG 인증 의존성 |
| Local RAG → AWS Backend | manifest 조회와 ingest-complete callback | Backend 내부 API |

토큰 운영 원칙:

- 개발자별 로컬 비밀 저장소 또는 `.env.local`에만 저장합니다.
- README, Issue, PR, 테스트 로그와 화면 캡처에 값을 복사하지 않습니다.
- 방향이 다른 토큰을 같은 값으로 재사용하지 않습니다.
- 인증 실패 분석 시 값 자체가 아니라 변수 존재 여부, Header 이름, 응답 상태와 공개 오류
  코드만 기록합니다.
- 토큰 회전 후 Local RAG와 Backend를 같은 배포 단위로 간주하지 말고 호출 방향별 반영
  상태를 각각 확인합니다.

## 4. 환경별 설정 분리

### `.env.example`

- 모든 공개 환경 변수 이름과 안전한 기본 예시를 제공
- 실제 호스트, 토큰, DB 비밀번호와 API Key를 포함하지 않음
- 신규 환경 변수 추가 시 Settings, 문서와 회귀 테스트를 함께 갱신

### `.env.local`

- 실제 Windows Local RAG 실행용
- CUDA 12.9, Local DB, Qdrant, TEI와 Claude 설정 포함 가능
- 개인 PC에만 보관하고 Git 추적 금지

### `.env.test`

- 일반 단위·통합·회귀 테스트용
- 실제 Claude, 운영 DB와 공용 Qdrant에 연결하지 않음
- `JIPSA_RAG_APP_ENV=test` 안전장치 유지

### PowerShell 프로세스 환경

E2E 스크립트는 `.env.local` 값을 현재 PowerShell 프로세스에만 임시 주입하고 종료 시
기존 값을 복원해야 합니다. 전체 환경을 콘솔에 출력하지 않습니다.

## 5. 로그 설정

로그 관련 환경 변수는 비밀값이 아니지만 출력량, 운영 분석과 개인정보 노출 위험에 직접
영향을 줍니다.

```dotenv
JIPSA_RAG_LOG_LEVEL=INFO
JIPSA_RAG_LOG_FORMAT=console
JIPSA_RAG_LOG_CONSOLE_TIMEZONE=local
JIPSA_RAG_LOG_COLOR=auto
JIPSA_RAG_LOG_REQUEST_ID_LENGTH=8
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=WARNING
JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS=5000
```

운영 수집기:

```dotenv
JIPSA_RAG_LOG_FORMAT=json
JIPSA_RAG_LOG_COLOR=never
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=WARNING
```

DEBUG는 장애 분석 기간에만 사용하고 정상 운영에서 장기간 유지하지 않습니다.

## 6. 로그 허용 필드

- Request ID
- `users_idx`, `file_idx`
- `rag_document_idx`, `rag_index_run_idx`
- 문서 형식
- Parser Type·Version
- 청크 수, 구조 단위 수
- Embedding 차원과 Batch 수
- 처리 시간
- HTTP method, path, status code
- 공개 오류 코드와 예외 클래스
- 보상·callback 상태

## 7. 로그 금지 필드

- 사용자 질문: `question`, `query_text`
- 청크 텍스트: `chunk_text`
- OCR 텍스트: `ocr_text`
- `prompt`
- `response_body`
- `request_body`
- `embedding`
- `vector`
- `authorization`
- `api_key`
- `token`
- `presigned_url`
- `database_url`
- `dsn`
- `password`
- 원본·임시 파일 절대 경로

금지된 원문 필드는 가능하면 로그 Record에서 제거합니다. 진단상 필드 존재가 필요한
인증값·URL·DSN은 안전한 마스킹 결과만 허용합니다.

Console과 JSON 모두 같은 보호 정책을 적용합니다. JSON이 구조화되어 있다는 이유로 질문,
OCR 텍스트, 임베딩 벡터 또는 HTTP 본문을 추가하지 않습니다. `DEBUG` 레벨도 비밀정보
정책을 우회하지 않습니다.

허용되는 마스킹 예:

```text
Authorization: [MASKED]
Presigned URL: https://example.invalid/object?[REDACTED]
DB DSN: mysql+asyncmy://user:[MASKED]@127.0.0.1:3306/Jipsa_Local_RAG
```

허용되지 않는 예:

```text
question=사용자가 입력한 전체 질문
ocr_text=문서 이미지에서 추출한 전체 OCR 텍스트
embedding=[0.018, -0.224, ...]
presigned_url=https://...X-Amz-Signature=실제서명
```

## 8. 예외와 stack trace

예외 클래스, 공개 오류 코드, 안전한 context와 stack trace는 장애 분석을 위해 기록할 수
있습니다. 단 다음 정보가 trace 또는 context에 포함되지 않도록 합니다.

- 요청·응답 본문
- Presigned URL
- DB DSN
- API Key와 토큰
- 질문·청크·OCR 원문
- 로컬 사용자 홈 경로가 포함된 외부 파일명

외부 예외 message를 그대로 최종 로그 message로 사용하지 않습니다.

## 9. PowerShell 출력 주의

PowerShell에서 환경 변수를 확인할 때 전체 환경을 출력하지 않습니다.

금지 예:

```text
Get-ChildItem Env:
docker inspect ... 전체 Environment 출력
```

필요한 비밀이 아닌 변수 이름만 선택적으로 확인합니다.

```powershell
Select-String `
    -Path '.env.local' `
    -Pattern '^JIPSA_RAG_LOG_FORMAT='
```

비밀값 확인이 필요하면 콘솔 캡처나 공유 로그에 값을 포함하지 않습니다.

## 10. Git 검사

병합 전에 확인:

- `.env.local`, `.env.development`, `.env.test` 미추적
- API Key·토큰·비밀번호 미포함
- Presigned URL 미포함
- 테스트 Fixture에 실제 사용자 문서 미포함
- 로그 Golden Test에 실제 비밀값 미포함
- README와 HTML에 예시용 가짜 값만 사용

## 11. 테스트 계약

민감정보 회귀 테스트는 다음을 검증합니다.

- 토큰·API Key·DB DSN 마스킹
- Presigned URL query 제거
- 질문·청크·OCR·프롬프트·벡터 필드 제거
- 요청·응답 본문 비출력
- 예외 stack trace의 안전한 정제
- ANSI·개행·제어 문자 정제
- Console·JSON 모두 동일한 보호 정책

실행:

```powershell
uv run pytest `
    tests/unit/core/test_sensitive_logging.py `
    tests/unit/core/test_logging_observability.py `
    -v
```

## 12. 장애 공유와 증거 보존

Issue, PR, 메신저와 장애 보고서에 로그를 첨부할 때 다음 순서를 사용합니다.

1. Request ID, event, level, status code와 duration만 우선 추출합니다.
2. `user_idx`, `file_idx`, run ID는 업무상 필요한 최소 범위만 공유합니다.
3. Presigned URL query, Token, API Key와 DB DSN을 다시 마스킹합니다.
4. 질문, 청크, OCR 텍스트, 프롬프트, 응답 본문과 벡터를 제거합니다.
5. 원본 로컬 경로에 사용자 이름이나 홈 디렉터리가 포함되지 않았는지 확인합니다.
6. 실제 비밀이 노출되었다면 문서 수정만으로 끝내지 않고 해당 비밀을 즉시 폐기·회전합니다.

장애 재현에 원문이 필요하더라도 운영 사용자 문서를 공유하지 않습니다. 비식별화된 고정
Fixture 또는 별도 테스트 문서를 사용합니다.

## 13. 관련 문서

- [관측성과 문제 해결](../operations/observability-and-troubleshooting.md)
- [Local RAG 실행](../operations/local-runtime.md)
- [PowerShell E2E](../testing/powershell-e2e.md)
- [종합 API 명세](../api/comprehensive-api-specification.md)
