# Local RAG 세계적 수준 문서 품질 표준

> **문서 상태:** Stable · 문서 작성·리뷰 기준  
> **주 독자:** 작성자, PR 리뷰어, 아키텍트, QA  
> **최종 검토:** 2026-07-28  
> **통과 기준:** 필수 게이트 전부 통과 + 총점 97점 이상 + 모든 대분류 80% 이상

## 1. 목적

이 표준은 문서가 단순히 길거나 자세한지를 평가하지 않습니다.
독자가 **정확한 결정을 내리고, 안전하게 실행하고, 실패를 진단하며, 구현과 계약을
검증할 수 있는지**를 평가합니다.

## 2. 적용 범위

- `RAG/README.md`
- `RAG/README.html`
- `RAG/docs/**/*.md`
- `RAG/tests/regression/test_rag_documentation_contract.py`
- Local RAG의 API·운영·보안 변경을 설명하는 PR 문서

이 표준과 문서 회귀 테스트의 파일 시스템 경계는 `RAG` 디렉터리입니다. 상위 프로젝트
루트, Backend, Frontend 문서의 내용이나 완성도는 Local RAG 품질 게이트에 포함하지
않습니다.

## 3. 필수 게이트

아래 항목 중 하나라도 실패하면 점수와 관계없이 불합격입니다.

### G1. 구현 근거

- API path, 필드, 기본값, 범위와 상태가 구현 자료와 일치합니다.
- 자료에서 확인하지 못한 기능을 구현된 것처럼 서술하지 않습니다.
- 추론·권장사항은 현재 구현 사실과 구분합니다.

### G2. 책임 경계

- AWS Backend와 Local RAG의 인증·권한·S3·DB·검색 책임이 뒤바뀌지 않습니다.
- Local RAG가 AWS 장기 자격 증명을 보관하거나 Backend DB를 직접 수정한다고 쓰지 않습니다.
- 빈 `reference_file_idxs`를 전체 검색으로 확대하지 않습니다.

### G3. API 무결성

- `user_idx`, `reference_file_idxs`, `query`, `top_k`, `score_threshold` 계약이 일치합니다.
- `[SOURCE-N]`, `cited_source_ids`, `sources` 최초 등장 순서가 일치합니다.
- Source Locator와 legacy 위치 호환성이 명확합니다.
- 오류 HTTP 상태와 공개 코드가 충돌하지 않습니다.

### G4. 링크·경로

- 모든 내부 Markdown 상대 링크가 `RAG` 디렉터리 안의 실제 경로를 가리킵니다.
- 대소문자와 파일명이 실제 경로와 일치합니다.
- 문서 내 코드 예시를 링크로 잘못 검사하지 않습니다.

### G5. 보안·개인정보

- 실제 토큰, API Key, 비밀번호, Presigned URL과 서명 query가 없습니다.
- 질문·청크·OCR·전체 프롬프트를 로그 예시로 노출하지 않습니다.
- Local RAG에 AWS Access Key 사용을 권장하지 않습니다.

### G6. 검증 정직성

- 실행하지 않은 Ruff, Mypy, Pytest와 실제 E2E를 통과했다고 쓰지 않습니다.
- 일반 Pytest skip을 실제 인프라 E2E 성공으로 간주하지 않습니다.
- 잔여 위험과 미실행 항목을 공개합니다.

### G7. 종합 API 명세 완전성

- Local RAG inbound 7개와 AWS Backend outbound 2개를 빠짐없이 포함합니다.
- public·protected endpoint, 두 토큰 방향과 공통 envelope를 명시합니다.
- request field 타입·범위·기본값·nullable·strict 변환을 구현과 맞춥니다.
- callback success·failure·retry·ambiguity와 현재 비보장 사항을 구분합니다.
- Request ID의 Local 응답 계약과 현재 outbound 미전파를 과장 없이 설명합니다.

종합 API 명세 전용 통과 기준은 총점 98점 이상, 모든 영역 85% 이상입니다.

### G8. HTML 접근성·자립성

- `README.html`은 키보드만으로 탐색, 검색, 테마 전환과 본문 이동이 가능합니다.
- `lang`, viewport, semantic landmark, skip link와 visible focus를 제공합니다.
- 모든 ID가 유일하고 내부 anchor와 상대 링크가 유효합니다.
- 외부 CDN, 원격 JavaScript와 원격 스타일시트 없이 단독으로 렌더링됩니다.
- 프로젝트 Brand Primary `#00236F`와 Secondary `#00687A`를 사용하되 텍스트 대비를 보장합니다.
- `prefers-reduced-motion`, dark mode, mobile layout과 print layout을 지원합니다.

## 4. 100점 평가표

| 대분류 | 배점 | 평가 질문 |
|---|---:|---|
| 1. 구현 정확성과 근거 | 18 | 코드·스키마·스크립트가 말하는 사실과 일치하는가? |
| 2. 범위 완전성 | 12 | 정상·예외·제약·비목표까지 필요한 범위를 다루는가? |
| 3. 아키텍처·책임 경계 | 10 | 소유권, 신뢰 경계, 데이터 흐름과 호출 방향이 명확한가? |
| 4. API 계약 정밀성 | 14 | 요청·응답·인증·상태·오류·호환성이 구현 가능한 수준인가? |
| 5. 신뢰성·실패 의미론 | 10 | 재인제스트, 부분 실패, 동시성, 보상과 불변식이 명확한가? |
| 6. 실행·운영 가능성 | 10 | 전제조건, 실행, 준비 상태, 종료, 진단과 복구가 가능한가? |
| 7. 보안·개인정보 | 10 | 비밀값·민감 원문·네트워크·저장 최소화가 명확한가? |
| 8. 가독성·정보 구조 | 8 | 독자가 역할별로 빠르게 찾고 단계별로 이해할 수 있는가? |
| 9. 일관성·링크·용어 | 4 | 대소문자, 필드, 경로, 링크와 용어가 일관적인가? |
| 10. 검증·유지보수성 | 4 | 회귀 테스트, 변경 체크리스트, Source of truth가 있는가? |
| **합계** | **100** |  |

## 5. 점수 기준

| 점수 | 판정 |
|---:|---|
| 97~100 | 세계적 수준 통과 |
| 94~96.9 | 우수하나 병합 전 보완 필요 |
| 90~93.9 | 좋은 초안, 운영·계약 공백 존재 |
| 80~89.9 | 사용 가능하나 구조적 결함 존재 |
| 80 미만 | 재작성 필요 |

최종 통과 조건:

- 필수 게이트 8개 전부 통과
- 각 대분류가 배점의 80% 이상
- Critical·High 결함 0개
- 자동 구조 검사 성공
- 미실행 런타임 검증 명시

## 6. 대분류 상세 기준

### 6.1 구현 정확성과 근거 — 18점

- 실제 클래스·스키마·환경 변수·스크립트 이름 사용
- 구현된 기본값과 범위를 정확히 표기
- 현재 지원 형식과 OCR 범위 일치
- 구현되지 않은 관측성·배포 기능을 명시적으로 제외
- 기존 코드와 새 권장사항을 혼합하지 않음

### 6.2 범위 완전성 — 12점

- 목적, 독자와 비목표
- 정상 흐름과 실패 흐름
- 입력 제약과 출력 의미
- 제한, 전제조건과 환경 차이
- 관련 문서와 다음 행동
- 변경 시 함께 검토할 대상

### 6.3 아키텍처·책임 경계 — 10점

- 사용자·파일 권한 소유자
- S3와 Presigned URL 경계
- Local DB와 Qdrant 데이터 소유권
- 호출 방향별 토큰
- 네트워크 노출 범위
- Backend·Local RAG 장애 책임

### 6.4 API 계약 정밀성 — 14점

- Method·Path·Content-Type·인증
- 요청 필드 타입·필수·기본값·범위
- 성공·근거 부족·오류 envelope
- HTTP 상태와 오류 코드
- Source Locator
- 인용 순서 불변식
- 버전·하위 호환·breaking change 기준

### 6.5 신뢰성·실패 의미론 — 10점

- 동일 정체성 멱등 재사용
- 신규 staging과 활성 전환 순서
- 이전 정상 상태 보존
- 동시 요청 직렬화
- 실행 소유권 상실
- 보상 실패 시 최초 오류 보존
- callback ambiguity
- synthesis 문서별 부분 실패

### 6.6 실행·운영 가능성 — 10점

- 필수 도구와 버전
- `.env.local`과 `.env.test` 역할
- Docker·GPU·DB 사전 확인
- 표준 실행·종료 명령
- readiness 확인
- 증상별 진단
- 안전한 복구 원칙
- 실제 E2E 절차

### 6.7 보안·개인정보 — 10점

- 토큰 방향
- AWS 자격 증명 금지
- Presigned URL 미기록
- 민감 원문 로그 금지
- DB·Qdrant 저장 최소화
- 루프백 바인딩
- 비밀값 회전
- 노출 사고 대응

### 6.8 가독성·정보 구조 — 8점

- 문서 첫 화면에 상태·독자·목적
- 60초 요약 또는 빠른 진입점
- 역할별 읽는 순서
- 한 섹션 한 주제
- 비교는 표, 순서는 번호 목록, 흐름은 text diagram
- 긴 설명 뒤 검증·체크리스트
- 모호한 대명사와 중복 최소화

### 6.9 일관성·링크·용어 — 4점

- H1 하나
- heading level 건너뛰기 없음
- code fence 균형
- `RAG` 내부 링크 유효
- 용어집 준수
- 동일 계약 버전
- 과거 PDF 전용 문구 없음

### 6.10 검증·유지보수성 — 4점

- Source of truth 우선순위
- 문서 회귀 테스트
- 변경 체크리스트
- 미실행 항목 공개
- 품질 검토 보고서
- 검토 날짜와 구현 기준

## 7. 심각도

| 심각도 | 예 |
|---|---|
| Critical | 권한 경계 오류, 비밀값 노출, 다른 사용자 문서 검색 허용 |
| High | API 필수 필드 누락, 잘못된 오류 코드, 보상 정책 반대 서술 |
| Medium | 실행 순서 누락, 진단 절차 부족, 링크 일부 오류 |
| Low | 문장 중복, 표기 편차, 예시 개선 |

Critical·High는 병합 전 반드시 0개여야 합니다.

## 8. 반복 검토 절차

```text
1. 구현 자료 수집
2. 기준표로 1차 평가
3. Critical·High 먼저 수정
4. 정보 구조와 중복 개선
5. 링크·heading·code fence 자동 검사
6. 계약 용어와 버전 교차 검사
7. 보안 문자열 검사
8. 2차 평가
9. 잔여 Medium·Low 수정
10. 최종 평가와 미실행 항목 기록
```

점수가 통과해도 필수 게이트가 실패하면 다시 반복합니다.

## 9. 자동 검사 최소 항목

자동 검사는 `RAG` 디렉터리만 재귀 탐색합니다. 상위 프로젝트 문서를 검사 대상으로
확장하려면 Local RAG 회귀 테스트가 아니라 별도의 저장소 통합 문서 테스트를 둡니다.

- 필수 문서 존재·비어 있지 않음
- H1 정확히 하나
- heading level 연속성
- code fence 균형
- 내부 상대 링크 존재
- 지원 형식 5개와 OCR 문구
- 책임 경계 핵심 용어
- 검색 범위 3조건
- API 계약 버전 일치
- inbound 7개·outbound 2개 endpoint 완전성
- public·protected 인증 matrix와 두 토큰 방향
- Request ID의 응답·outbound 전파 범위
- callback success·failure·all-or-none·ambiguity
- Source Locator 5개 형식
- 재인제스트·보상 핵심 용어
- 실행·테스트 명령
- 비밀값·민감 원문 금지 문구
- stale PDF-only 문구 부재
- HTML landmark·skip link·고유 ID·내부 anchor
- HTML 외부 runtime asset 부재
- Project Brand palette와 반응형·dark·print 지원

## 10. 리뷰 결과 기록 형식

```text
[필수 게이트]
- G1 구현 근거: PASS/FAIL
- G2 책임 경계: PASS/FAIL
- G3 API 무결성: PASS/FAIL
- G4 링크·경로: PASS/FAIL
- G5 보안·개인정보: PASS/FAIL
- G6 검증 정직성: PASS/FAIL
- G7 종합 API 명세 완전성: PASS/FAIL
- G8 HTML 접근성·자립성: PASS/FAIL

[점수]
- 총점:
- 대분류 최저 점수:

[결함]
- Critical:
- High:
- Medium:
- Low:

[자동 검사]
- 문서 회귀 테스트:
- 링크:
- heading:
- code fence:

[미실행]
- Ruff:
- Mypy:
- 일반 Pytest:
- 실제 E2E:
```
