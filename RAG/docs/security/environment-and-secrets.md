# 환경 변수와 민감정보 관리

> **문서 상태:** Stable · 보안 기준  
> **주 독자:** 개발자, 운영자, 보안 리뷰어  
> **최종 검토:** 2026-07-28  
> **핵심 원칙:** Local RAG는 AWS 장기 자격 증명을 저장하거나 사용하지 않음


## 1. 환경 파일 정책

| 파일 | 목적 | Git 커밋 |
|---|---|---:|
| `.env.example` | 변수 설명과 안전한 예시 | 허용 |
| `.env.local` | 실제 로컬 실행 | 금지 |
| `.env.development` | 개발 프로필 | 금지 |
| `.env.test` | 일반 테스트 격리 | 금지 |
| `.env` | 사용하지 않거나 로컬 전용 | 금지 |

[RAG 전용 `.gitignore`](../../.gitignore)는 `.env.*`를 제외하고 `.env.example`만 허용합니다.
실제 비밀값을 `.env.example`에 넣지 않습니다.

## 2. 실행 환경 선택

```text
JIPSA_RAG_APP_ENV 미지정       → local
JIPSA_RAG_APP_ENV=development → development
JIPSA_RAG_APP_ENV=test        → test
```

프로세스 환경 변수는 dotenv보다 우선합니다.
E2E 스크립트는 `.env.local` 값을 현재 프로세스에만 임시 주입하고 종료 시 복원합니다.

## 3. 비밀값 분류

### 내부 서비스 토큰

| 변수 | 방향 |
|---|---|
| `RAG_INGEST_TOKEN` | AWS Backend → Local RAG |
| `INTERNAL_TOKEN` | Local RAG → AWS Backend |

- 최소 32자 이상의 예측하기 어려운 임의 문자열을 사용합니다.
- URL, query parameter, 로그와 오류 응답에 넣지 않습니다.
- 호출 방향이 다르므로 혼용하지 않습니다.
- 운영에서는 서로 다른 값과 독립적 회전 주기를 권장합니다.

### 외부 API

- `ANTHROPIC_API_KEY`
- 인증이 활성화된 경우 `JIPSA_RAG_QDRANT_API_KEY`

### Local DB

- `JIPSA_RAG_DATABASE_PASSWORD`
- 계정과 데이터베이스 이름도 로그에 전체 DSN으로 결합해 출력하지 않습니다.

### AWS 자격 증명

Local RAG에는 다음 값을 저장하지 않습니다.

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN`

S3 원본은 AWS Backend가 IAM Role로 발급한 Presigned GET URL을 통해서만 다운로드합니다.

## 4. 비밀값을 넣지 말아야 하는 위치

- Git 커밋
- 이슈와 PR 본문
- 테스트 Fixture
- 스크린샷
- PowerShell transcript
- CI 로그
- 애플리케이션 구조화 로그
- 오류 응답
- 문서 예제
- 채팅과 메신저
- Qdrant payload
- Local RAG DB 청크 메타데이터

## 5. 로그 금지 데이터

다음 원문은 로그 필드와 예외 문자열에 기록하지 않습니다.

- 사용자 질문
- 문서 청크
- OCR 텍스트
- 원본 이미지 바이트
- Claude 전체 프롬프트
- Claude 전체 응답
- 임베딩 벡터
- 내부 인증 토큰
- Anthropic API Key
- Qdrant API Key
- Presigned URL
- AWS 서명 query parameter
- Local DB DSN과 비밀번호
- S3 Object Key 전체 값이 민감한 경우 원문

허용 로그:

- Request ID
- `users_idx`, `file_idx`, `rag_document_idx`, `rag_index_run_idx`
- 공개 오류 코드
- 오류 클래스 이름
- 처리 단계와 개수
- 모델 ID와 벡터 차원
- 안전한 상태 값

## 6. Presigned URL

- 허용 호스트 suffix를 검증합니다.
- URL 전체를 로그에 남기지 않습니다.
- 서명 query parameter는 마스킹보다 원문 미기록을 우선합니다.
- 만료, 권한과 잘못된 요청 오류를 무분별하게 재시도하지 않습니다.
- 다운로드 크기와 연결·읽기 제한 시간을 적용합니다.
- S3 Object Key가 허용 prefix에 속하는지 확인합니다.

## 7. 데이터 저장 최소화

### Local RAG DB

필요한 문서·청크·색인 이력과 Source Locator만 저장합니다.
사용자 인증정보와 AWS 자격 증명을 저장하지 않습니다.

### Qdrant

검색 필터와 출처 생성에 필요한 payload만 저장합니다.

- 사용자 식별자
- 파일·문서·청크 식별자
- 활성 상태
- 파일 형식
- Source Locator
- 파서·임베딩·색인 버전

내부 토큰, Presigned URL과 DB 접속 정보를 저장하지 않습니다.

### 답변 API

최종 `sources`에는 실제 인용한 출처와 제한된 `excerpt`만 포함합니다.

## 8. 네트워크

- Qdrant와 TEI는 `127.0.0.1`에 바인딩합니다.
- Local RAG DB는 인터넷에 공개하지 않습니다.
- Backend에서 Local RAG로 외부 네트워크를 통과하면 HTTPS를 사용합니다.
- 방화벽과 IP allowlist를 적용합니다.
- Local RAG OpenAPI UI는 운영에서 접근 범위를 제한합니다.

## 9. 비밀값 회전

1. 새 비밀값을 안전한 채널로 생성합니다.
2. 호출자와 수신자에 같은 계약 값으로 배포합니다.
3. 두 서비스를 제한된 순서로 재시작합니다.
4. 내부 API 인증 성공을 확인합니다.
5. 이전 비밀값을 폐기합니다.
6. 로그와 Git 이력에 노출 흔적이 없는지 확인합니다.

토큰을 바꿀 때 `INTERNAL_TOKEN`과 `RAG_INGEST_TOKEN`의 방향을 뒤바꾸지 않습니다.

## 10. 노출 사고 대응

비밀값이 Git, 로그, 화면 공유 또는 메시지에 노출되면 다음을 수행합니다.

1. 노출된 값을 즉시 폐기·회전합니다.
2. 영향받는 서비스와 호출 방향을 식별합니다.
3. 접근 로그에서 비정상 호출을 확인합니다.
4. 필요하면 Git 이력과 배포 산출물에서 값을 제거합니다.
5. 테스트 Fixture와 문서 예제를 재검사합니다.
6. 재발 방지 회귀 테스트 또는 secret scanning 규칙을 추가합니다.

단순히 파일을 삭제한 뒤 같은 비밀값을 계속 사용하지 않습니다.

## 11. 개발·테스트 주의

- `.env.test`에는 실제 운영 API Key를 사용하지 않습니다.
- 일반 Pytest는 실제 Claude·TEI·Qdrant에 접근하지 않게 합니다.
- 실제 E2E는 테스트 전용 사용자, `File_IDX`, DB와 Collection 범위를 사용합니다.
- `JIPSA_RAG_DATABASE_ECHO=false`를 유지합니다.
- E2E Assertion 실패 메시지에 응답 본문을 그대로 포함하지 않습니다.
- PowerShell 환경 로더는 값 자체가 아니라 주입 변수 개수만 출력합니다.

## 12. 검토 체크리스트

- [ ] `.env.example`만 추적됨
- [ ] Local RAG에 AWS 자격 증명이 없음
- [ ] 내부 토큰 방향이 정확함
- [ ] Qdrant·TEI가 루프백에 바인딩됨
- [ ] 질문·청크·OCR·프롬프트 원문 로그가 없음
- [ ] Presigned URL이 로그와 오류 응답에 없음
- [ ] DB echo가 비활성화됨
- [ ] 문서 예제의 모든 비밀값이 명백한 placeholder임
