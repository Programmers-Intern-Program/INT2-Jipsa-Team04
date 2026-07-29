# Ruff·Mypy·Pytest·실제 E2E 실행 절차

> **문서 상태:** Active · 검증 진입점  
> **주 독자:** 개발자, QA, PR 리뷰어  
> **최종 검토:** 2026-07-29  
> **판정 원칙:** 일반 Pytest 통과와 실제 CUDA·인프라 E2E 통과를 구분

## 1. PowerShell 실행 준비

프로젝트 스크립트를 실행하기 전에 현재 PowerShell 프로세스에만 실행 정책 예외를
적용합니다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'
```

이 설정은 현재 창을 닫으면 자동으로 사라집니다. `LocalMachine` 범위나
`Unrestricted` 정책은 사용하지 않습니다.

## 2. 테스트 계층

| 계층 | 외부 인프라 | 목적 |
|---|---|---|
| Ruff format | 없음 | 포맷 편차 방지 |
| Ruff lint | 없음 | 정적 코드 품질 |
| Mypy strict | 없음 | 타입 계약 |
| 일반 Pytest | Stub·Mock 중심 | 단위·통합·회귀 |
| 로그 집중 테스트 | 없음 | Console·JSON·마스킹·요청 흐름 |
| Office COM 통합 | PowerPoint·Excel | 차트·SmartArt 렌더링 |
| 고정 다중 형식 E2E | CUDA, DB, Qdrant, Claude | 5개 형식과 OCR |
| 실제 PDF E2E | CUDA, DB, Qdrant, Claude | 실제 PDF 인제스트·답변 |
| 전체 실제 E2E | 위 항목 전체 | 최종 서비스 파이프라인 |

## 3. 일반 품질 게이트

```powershell
& .\scripts\verify-rag-quality.ps1
```

실행 순서:

```text
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

스크립트는 소스 파일을 자동 수정하지 않습니다. 일반 Pytest에서는
`JIPSA_RAG_RUN_E2E`를 제거하여 GPU, Local DB, Qdrant, Office COM과 Claude가 필요한
opt-in E2E를 명시적으로 skip합니다.

## 4. 개별 검사

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

문서 회귀 테스트:

```powershell
uv run pytest `
    tests/regression/test_rag_documentation_contract.py `
    tests/regression/test_documentation_runtime_guidance.py `
    -v
```

특정 테스트만 실행했을 때는 전체 품질 게이트를 통과한 것으로 기록하지 않습니다.

## 5. 로그 집중 테스트

```powershell
uv run pytest `
    tests/unit/core/test_logging_observability.py `
    tests/unit/core/test_sensitive_logging.py `
    tests/unit/core/test_request_logging_middleware.py `
    tests/unit/api/test_ingest_stage_logging.py `
    tests/unit/api/v1/endpoints/test_file_processing_stage_logging.py `
    tests/unit/diagnostics/test_logging_performance.py `
    -v
```

검증 범위:

- 로컬 Console과 운영 JSON
- RFC 3339 UTC JSON timestamp
- Console 로컬 시간과 UTC Offset
- 전체 UUID JSON Request ID와 축약 Console Request ID
- 요청 완료·실패 상태 코드와 처리 시간
- manifest, 다운로드, OCR, 청킹, 임베딩, 색인과 callback 단계
- 지연 단계 WARNING
- HTTP 5xx와 예외 로그
- 서드파티 정상 요청 로그 억제
- 민감정보·원문·벡터·HTTP 본문 비노출
- ANSI·개행·제어 문자 정제
- 로그 출력량과 성능 회귀

## 6. 고정 다중 형식·OCR E2E

```powershell
& .\scripts\run-issue-123-e2e.ps1
```

검증 범위:

- PDF, DOCX, PPTX, XLSX, TXT 고정 Fixture
- 페이지, 문단, 슬라이드, 시트, 셀과 줄 위치
- 문서 내부 이미지와 스캔 PDF
- 실제 CUDA EasyOCR
- OCR 이미지 일부 실패
- CUDA TEI 임베딩과 1024차원
- Local RAG DB와 Qdrant 저장
- lookup과 다중 형식 synthesis
- 텍스트·OCR 혼합 답변
- `[SOURCE-N]`, `cited_source_ids`, `sources` 순서
- 선택하지 않은 문서와 다른 사용자 차단
- 근거 부족 시 Claude 미호출
- 재인제스트, 재색인, soft delete와 보상
- 중복·동시 인제스트 수렴
- 임시 파일과 추출 이미지 정리
- 민감 원문과 인증정보 로그 비노출

옵션:

```powershell
& .\scripts\run-issue-123-e2e.ps1 -SkipQualityGate
& .\scripts\run-issue-123-e2e.ps1 -KeepInfrastructureRunning
```

`-SkipQualityGate`는 같은 Commit에서 품질 게이트가 이미 성공한 경우에만 사용합니다.

## 7. 전체 실제 E2E

```powershell
& .\scripts\run-all-rag-tests.ps1
```

실행 순서:

1. Ruff, Mypy와 일반 전체 Pytest
2. `.env.local`을 현재 PowerShell 프로세스에만 주입
3. Docker Engine과 Compose 검증
4. Qdrant와 CUDA TEI 준비
5. PyTorch CUDA 장치 확인
6. Local RAG DB 연결 확인
7. PowerPoint·Excel Office COM 렌더링
8. 고정 다중 형식·OCR 전체 파이프라인
9. 실제 PDF·Claude·생성 제한 E2E
10. 실제 DOCX·PPTX·XLSX·TXT E2E
11. 스크립트가 시작한 인프라와 환경 변수 복원

옵션:

```powershell
& .\scripts\run-all-rag-tests.ps1 -SkipQualityGate
& .\scripts\run-all-rag-tests.ps1 -KeepInfrastructureRunning
```

실제 Claude API 비용과 GPU 추론 시간이 발생합니다.

## 8. 문서·로그 회귀 테스트 상세

문서 수정 시 다음 두 회귀 테스트를 함께 실행합니다.

```powershell
uv run pytest `
    tests/regression/test_rag_documentation_contract.py `
    tests/regression/test_documentation_runtime_guidance.py `
    -v
```

검증 범위:

- README 역할별 문서 탐색 경로
- PDF, DOCX, PPTX, XLSX, TXT와 OCR 지원 범위
- 사용자·활성·선택 문서 검색 필터
- lookup·synthesis와 공개 인용 순서
- `source_locator` 형식별 위치 계약
- Windows 실행 정책 안내
- Local Runtime, 관측성, 보안과 E2E Runbook 완전성
- HTML README 접근성, 검색, 테마, 인쇄와 내부 링크
- 실제 비밀값 패턴과 깨진 상대 링크 미포함

문서 테스트가 실패하면 요구 문자열만 최소 추가하지 않습니다. 관련 기능 설명, 운영 판정
기준과 장애 대응 절차가 함께 보존되도록 문서 전체 맥락을 복원합니다.

## 9. 테스트 선택 기준

| 변경 범위 | 최소 집중 테스트 | 최종 필수 검증 |
|---|---|---|
| Python 로직 | 해당 unit·integration | 전체 품질 게이트 |
| 로그 Formatter | logging·sensitive·middleware | 전체 품질 게이트와 E2E |
| 파서·OCR | 형식별 unit·fixture | 고정 문서·전체 E2E |
| DB·Qdrant | repository·indexing | Local DB·Qdrant E2E |
| API 계약 | endpoint·schema·regression | 전체 품질 게이트 |
| README·docs | 문서 회귀 테스트 | 전체 품질 게이트 |
| PowerShell | script contract·수동 실행 | 전체 실제 E2E |

특정 테스트만 통과한 결과를 전체 검증 완료로 기록하지 않습니다. 변경 영향이 CUDA,
Local DB, Qdrant, Office COM 또는 Claude에 도달하면 실제 인프라 E2E가 필요합니다.

## 10. 실행 전 체크

- [ ] Windows PowerShell 5.1 이상
- [ ] `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force` 적용
- [ ] Python 3.12와 `uv`
- [ ] Docker Desktop과 Compose
- [ ] NVIDIA GPU와 Docker GPU 지원
- [ ] PyTorch CUDA 사용 가능
- [ ] Local RAG DB와 전용 테스트 데이터 범위
- [ ] `.env.local`과 `.env.test`
- [ ] Qdrant·TEI 루프백 주소
- [ ] PowerPoint·Excel과 Windows 대화형 세션
- [ ] 실제 E2E용 Anthropic API Key
- [ ] `JIPSA_RAG_APP_ENV=test` 안전장치

## 11. 테스트 데이터 안전

- E2E 정리는 `JIPSA_RAG_APP_ENV=test`에서만 허용합니다.
- `JIPSA_RAG_RUN_E2E=1`이 없으면 실제 E2E 모듈을 skip합니다.
- 테스트 전용 사용자와 명시적 `File_IDX`만 정리합니다.
- 운영 DB와 팀 공용 Qdrant Collection을 사용하지 않습니다.
- 테스트 시작 전 이미 실행 중이던 컨테이너는 종료하지 않습니다.
- `-KeepInfrastructureRunning`은 장애 분석 후 수동 정리가 필요합니다.

## 12. 결과 판정

### 품질 게이트 성공

다음이 모두 종료 코드 0이어야 합니다.

- uv lock 기준 의존성 동기화
- Ruff format
- Ruff lint
- Mypy strict
- 일반 전체 Pytest

### 실제 E2E 성공

일반 테스트 성공만으로 실제 E2E 성공을 대체할 수 없습니다. 실제 E2E 스크립트의 최종
종료 코드와 각 인프라 검증 결과를 확인합니다.

### skip

opt-in E2E의 skip은 일반 Pytest에서는 정상입니다. 실제 서비스 준비 여부를 판단할 때는
skip된 항목을 `run-all-rag-tests.ps1`에서 별도 실행해야 합니다.

### 의도된 WARNING·ERROR

실패·보상·부분 실패 시나리오 테스트는 의도적으로 WARNING 또는 ERROR 로그를
발생시킬 수 있습니다. 해당 테스트가 `PASSED`이고 최종 종료 코드가 0이면 예상된
관측성 검증입니다.

## 13. 결과 해석 예시

| 출력 | 판정 |
|---|---|
| `passed` | 해당 테스트 성공 |
| `skipped` | 조건부 테스트 미실행, 실패 아님 |
| `xfailed` | 명시된 예상 실패 |
| `WARNING is_slow_stage=true` | 정상 완료했지만 임계값 초과 |
| 의도된 `application_exception` 후 테스트 `PASSED` | 오류 변환·보상 시나리오 성공 |
| Ruff `Would reformat` | 포맷 게이트 실패 |
| Mypy `Incompatible types` | 타입 게이트 실패 |
| 최종 PowerShell 종료 코드 0 | 스크립트 전체 성공 후보 |

최종 종료 코드만 보지 않고 각 실제 인프라 단계가 실행되었는지 확인합니다. 예를 들어 일반
Pytest에서 E2E가 skip된 상태는 `run-all-rag-tests.ps1` 통과를 대체하지 않습니다.

## 14. 실패 분석

| 단계 | 우선 확인 |
|---|---|
| PowerShell 정책 | `Get-ExecutionPolicy -List`, Process Bypass |
| `uv sync --frozen` | `pyproject.toml`과 `uv.lock` 일치 |
| Ruff format | 포맷 대상 파일 |
| Ruff lint | 오류 코드와 정확한 파일·줄 |
| Mypy | strict 타입 오류와 Stub 격리 |
| 일반 Pytest | 최초 실패와 Fixture 환경 |
| Docker 준비 | Engine, Compose, NVIDIA runtime |
| TEI | CUDA 로그, 모델 Cache, 벡터 차원 |
| EasyOCR | CUDA 장치, 모델 Cache, 이미지 제한 |
| DB | 연결, 스키마, 테스트 전용 계정 |
| Qdrant | Collection 차원·거리 함수·활성 payload |
| Office COM | 대화형 세션, PowerPoint·Excel |
| Claude | API Key, 모델 ID, 호출 예산 |
| 문서 회귀 | 링크, 경로, 실행 정책, 로그 계약 |

## 15. 테스트 결과 기록

PR에는 다음을 구분해 기록합니다.

```text
[통과]
- test_rag_documentation_contract.py
- test_documentation_runtime_guidance.py
- 로그 집중 회귀 테스트
- verify-rag-quality.ps1
- run-all-rag-tests.ps1

[미실행]
- 없음

[환경]
- Python 3.12
- CUDA 12.9
- GPU 모델
- Local DB 종류
- PowerShell 버전
```

실제로 실행하지 않은 항목은 반드시 `미실행`으로 표시합니다.

## 16. 관련 문서

- [PowerShell 실제 E2E 상세 가이드](powershell-e2e.md)
- [Windows Local RAG 실행](../operations/local-runtime.md)
- [관측성과 문제 해결](../operations/observability-and-troubleshooting.md)
- [환경 변수와 비밀정보](../security/environment-and-secrets.md)
