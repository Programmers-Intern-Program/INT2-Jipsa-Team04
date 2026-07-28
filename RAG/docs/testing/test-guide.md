# Ruff·Mypy·Pytest·실제 E2E 실행 절차

> **문서 상태:** Active · 검증 진입점  
> **주 독자:** 개발자, QA, PR 리뷰어  
> **최종 검토:** 2026-07-28  
> **판정 원칙:** 일반 Pytest 통과와 실제 CUDA·인프라 E2E 통과를 구분


## 1. 테스트 계층

| 계층 | 외부 인프라 | 목적 |
|---|---|---|
| Ruff format | 없음 | 포맷 편차 방지 |
| Ruff lint | 없음 | 정적 코드 품질 |
| Mypy strict | 없음 | 타입 계약 |
| 일반 Pytest | Stub·Mock 중심 | 단위·통합·회귀 |
| Office COM 통합 | PowerPoint·Excel | 차트·SmartArt 렌더링 |
| 고정 다중 형식 E2E | CUDA, DB, Qdrant, Claude | PDF·DOCX·PPTX·XLSX·TXT와 OCR |
| 실제 PDF E2E | CUDA, DB, Qdrant, Claude | 실제 PDF 인제스트·답변 |
| 전체 실제 E2E | 위 항목 전체 | 최종 서비스 파이프라인 |

## 2. 일반 품질 게이트

표준 진입점:

```powershell
.\scripts\verify-rag-quality.ps1
```

실행 순서:

```text
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

스크립트는 소스 파일을 자동 수정하지 않습니다.
일반 Pytest에서는 `JIPSA_RAG_RUN_E2E`를 제거하여 GPU, Local DB, Qdrant, Office COM과
Claude가 필요한 opt-in E2E를 명시적으로 skip합니다.

## 3. 개별 검사

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

문서 회귀 테스트만 실행:

```powershell
uv run pytest tests/regression/test_rag_documentation_contract.py
```

특정 테스트를 실행했을 때는 전체 품질 게이트를 통과한 것으로 기록하지 않습니다.

## 4. 고정 다중 형식·OCR E2E

```powershell
.\scripts\run-issue-123-e2e.ps1
```

주요 테스트:

```text
tests/e2e/test_fixed_document_full_pipeline_e2e.py
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
.\scripts\run-issue-123-e2e.ps1 -SkipQualityGate
.\scripts\run-issue-123-e2e.ps1 -KeepInfrastructureRunning
```

`-SkipQualityGate`는 같은 Commit에서 품질 게이트가 이미 성공한 경우에만 사용합니다.

## 5. 전체 실제 E2E

```powershell
.\scripts\run-all-rag-tests.ps1
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
.\scripts\run-all-rag-tests.ps1 -SkipQualityGate
.\scripts\run-all-rag-tests.ps1 -KeepInfrastructureRunning
```

실제 Claude API 비용과 GPU 추론 시간이 발생합니다.

## 6. 실제 PDF 전용 E2E

```powershell
.\scripts\run-real-rag-e2e.ps1
```

이 경로는 실제 PDF 인제스트, Local RAG DB, Qdrant와 Claude 답변을 집중 검증합니다.
전체 다중 형식 최종 검증은 `run-all-rag-tests.ps1`을 사용합니다.

상세 절차는
[PowerShell 실제 E2E 상세 가이드](powershell-e2e.md)를 참조합니다.

## 7. 실행 전 체크

- [ ] Windows PowerShell 5.1 이상
- [ ] Python 3.12와 `uv`
- [ ] Docker Desktop과 Compose
- [ ] NVIDIA GPU와 Docker GPU 지원
- [ ] PyTorch CUDA 사용 가능
- [ ] Local RAG DB와 전용 테스트 데이터 범위
- [ ] `.env.local`과 `.env.test`
- [ ] Qdrant·TEI 루프백 주소
- [ ] Office COM 테스트 시 PowerPoint·Excel과 대화형 세션
- [ ] 실제 E2E용 Anthropic API Key
- [ ] `JIPSA_RAG_APP_ENV=test` 안전장치

## 8. 테스트 데이터 안전

- E2E 정리는 `JIPSA_RAG_APP_ENV=test`에서만 허용합니다.
- `JIPSA_RAG_RUN_E2E=1`이 없으면 실제 E2E 모듈을 skip합니다.
- 테스트 전용 사용자와 명시적 `File_IDX`만 정리합니다.
- 운영 DB와 팀 공용 Qdrant Collection을 사용하지 않습니다.
- 테스트 시작 전 이미 실행 중이던 컨테이너는 종료하지 않습니다.
- `-KeepInfrastructureRunning`은 장애 분석 후 수동 정리가 필요합니다.

## 9. 결과 판정

### 품질 게이트 성공

다음이 모두 종료 코드 0이어야 합니다.

- uv lock 동기화 확인
- Ruff format
- Ruff lint
- Mypy strict
- 일반 전체 Pytest

### 실제 E2E 성공

일반 테스트 성공만으로 실제 E2E 성공을 대체할 수 없습니다.
실제 E2E 스크립트의 최종 종료 코드와 각 인프라 검증 결과를 확인합니다.

### skip

opt-in E2E의 skip은 일반 Pytest에서는 정상입니다.
다만 실제 서비스 준비 여부를 판단할 때는 skip된 항목을 별도 실행해야 합니다.

## 10. 실패 분석

| 단계 | 우선 확인 |
|---|---|
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
| 문서 회귀 | 링크, 경로, 표준 용어, API 계약 |

## 11. 테스트 결과 기록

PR에는 다음을 구분해 기록합니다.

```text
[통과]
- verify-rag-quality.ps1
- run-issue-123-e2e.ps1
- run-all-rag-tests.ps1

[미실행]
- 없음

[환경]
- Python 3.12
- CUDA 12.9
- GPU 모델
- Local DB 종류
```

실제로 실행하지 않은 항목은 반드시 `미실행`으로 표시합니다.
