# Issue #159 장시간 안정성·실패 경계 테스트 가이드

## 1. 목적

이 가이드는 기존 형식·OCR·동시성 기준선 이후 다음 9개 검증을 한 캠페인으로 실행하는
방법을 설명합니다.

- 장시간 반복 중 자원 누수와 성능 저하
- Timeout, OOM, 외부 서비스 실패, 비정상 종료
- 평균, 최대, p50, p95, p99
- 정상 처리 최대와 최초 실패 분리
- JSON·CSV 저장
- 실행 조건·명령·결과 Markdown 문서화
- 테스트 전용 DB 데이터·Qdrant Collection 확인
- 기존 인프라 상태와 종료 정리 확인
- 성능 개선·운영 제한값 비변경 확인

## 2. 기본 계획

`configs/reliability-plan.json`의 기본값은 다음과 같습니다.

| 항목 | 기본값 | 의미 |
|---|---:|---|
| Soak 시간 | 3,600초 | 1시간 반복 검색 |
| Window | 300초 | 5분 단위 분석 |
| 동시성 | 4 | 고정 검색 Worker 수 |
| 요청 상한 | 0 | 시간 종료까지 제한 없음 |
| Timeout Delay | 2초 | 관리 Probe 지연 |
| Client Timeout | 0.25초 | 예상 Timeout 발생 |
| OOM Worker | 64 MiB | 안전한 별도 Worker MemoryError |
| 외부 Service | TEI, Qdrant | 한 번씩 정지·복구 |
| Qdrant Prefix | `rag_benchmark_issue_159_` | 실행별 전용 Collection |

Soak 보고 임계값은 운영 제한이 아닙니다. 첫 Window와 마지막 Window의 변화를 사람이
검토하기 위한 표시 기준입니다.

## 3. 권장 실행 순서

### 3.1 독립 프로그램 품질 검사

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

### 3.2 측정 대상 RAG 품질 검사

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'

uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

### 3.3 전체 캠페인

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-reliability-benchmark.ps1 `
    -DisableAnswers
```

`-DisableAnswers`는 앞단 기준선의 Claude 호출을 제외합니다. 신뢰성 Session 자체는 검색
중심이므로 Claude를 호출하지 않습니다.

### 3.4 기준선 생략

동일 Commit·동일 환경에서 기존 기준선을 이미 완료했을 때만 사용합니다.

```powershell
& .\scripts\run-reliability-benchmark.ps1 `
    -DisableAnswers `
    -SkipBaseline
```

## 4. 첫 실행용 짧은 계획

기본 1시간 실행 전에 제공된 `configs/reliability-plan-smoke.json`으로 5분 검증을 권장합니다.

```json
{
  "soak": {
    "duration_seconds": 300,
    "window_seconds": 60,
    "concurrency": 2,
    "max_requests": 200
  }
}
```

Smoke 계획은 측정 부하와 반복 시간만 줄이며 RAG 운영 제한값을 변경하지 않습니다.

```powershell
& .\scripts\run-reliability-benchmark.ps1 `
    -DisableAnswers `
    -ReliabilityPlanPath '.\configs\reliability-plan-smoke.json'
```

## 5. 실패 Probe 정의

### 5.1 Timeout

`/__benchmark__/fault/delay` 관리 요청을 2초 지연하고 Client는 0.25초만 기다립니다.
RAG의 Download, Embedding, Qdrant, Backend Timeout 설정은 변경하지 않습니다.

정상 기록:

```text
category=timeout
outcome=expected_failure_observed
recovered=true
```

### 5.2 OOM

실제 Host RAM이나 GPU VRAM을 고갈시키지 않습니다. 별도 Python Worker가 최대 256 MiB 범위
안에서 메모리를 할당한 뒤 의도적으로 `MemoryError` Exit Code를 반환합니다. Target Health가
유지되는지 확인합니다.

실제 CUDA OOM이 자연 발생하면 요청 오류 종류와 `target.log` 표식만
`passive_observation`으로 기록합니다.

### 5.3 TEI·Qdrant

각 Service를 한 번씩 `docker compose stop`하고 검색 실패를 기록한 뒤 즉시 `up -d`, Ready,
검색 성공을 확인합니다. 캠페인 종료 시 최초 Container 상태를 별도로 복원합니다.

### 5.4 비정상 종료

Health가 반환한 성능 측정 전용 Target PID와 그 자식 Process만 종료합니다. 일반 8077 RAG
Process나 다른 Python Process를 이름으로 일괄 종료하지 않습니다. 종료 후 RAG 환경의
Cleanup-only Process가 DB·Qdrant·Temp를 정리하고 결과를 JSON으로 남깁니다.

## 6. 데이터 격리

### Local RAG DB

- `JIPSA_RAG_APP_ENV=test`
- `Users_IDX >= 159000`
- `File_IDX >= 1590000`
- Manifest에 포함된 정확한 ID 목록만 DELETE
- `isolation_verification.json`에 DB 이름과 Table별 Row 수 기록

기본값은 `.env.local`의 Local RAG DB를 사용하되 전용 ID 데이터만 생성·정리합니다. 완전히
분리된 테스트 DB 스키마가 준비되어 있으면 계획의 `database_name_override`에 그 DB 이름을
지정할 수 있습니다. 스키마가 없는 이름을 지정하면 실행이 실패합니다.

### Qdrant

- 실행별 `rag_benchmark_issue_159_<campaign>_<hash>` Collection
- Prefix 밖 Collection 사용·삭제 거부
- 종료 시 전용 Collection 전체 삭제
- `cleanup_verification.json`에서 `qdrant_collection_absent=true` 확인

## 7. 인프라 복원

캠페인 시작 전에 다음 상태를 저장합니다.

- `jipsa-embedding`: running, stopped/exited, absent
- `jipsa-qdrant`: running, stopped/exited, absent

종료 시:

- 처음 running이면 다시 running
- 처음 stopped/exited이면 다시 비실행 상태
- 처음 absent이면 캠페인이 생성한 Container 제거

확인은 `infrastructure_state.csv`의 `restored=true`입니다. Docker 자체 오류로 최초 상태가
`unknown`이면 파괴적 복원을 수행하지 않고 실패로 기록합니다.

## 8. 결과 판정

### 장시간 안정성

`soak_windows.csv`에서 Window별 다음 열을 확인합니다.

- `latency_mean_ms`, `latency_max_ms`, `latency_p50_ms`, `latency_p95_ms`, `latency_p99_ms`
- `target_rss_*`
- `target_vram_*`
- `target_thread_*`
- `target_handle_*`

`soak_drift.json`은 첫 Window와 마지막 Window를 비교합니다. 후보가 표시되어도 즉시 누수로
단정하지 말고 시간축 표본, GC·Model 초기화, Container Cache와 함께 해석합니다.

### 처리 경계

`boundary_analysis.csv`에서:

- `normal_maximum_value`: 최초 실패 전 허용 오류율 안의 최대 관측값
- `first_failure_value`: 처음 실패한 관측값
- `observed_upper_bound_censored=true`: 계획 범위 안에서 실패를 찾지 못함

실패가 없을 때 정상 최대값을 운영 최대치로 간주하지 않습니다.

### 최종 정상 종료

다음 항목을 모두 확인합니다.

```text
cleanup_process_exit_code_zero=true
database_rows_zero=true
qdrant_collection_absent=true
temp_files_zero=true
target_process_stopped=true
infrastructure_state.csv restored=true
scope_guard.json passed=true
```

## 9. 범위 보호

`scope_guard.json`은 다음을 확인합니다.

- 측정 전후 `RAG/src`, `RAG/pyproject.toml`, Qdrant Compose, `.env.local` Hash 동일
- `main...HEAD`에서 보호 경로 변경 없음
- File Download 크기, Embedding Batch·Timeout, OCR 동시성, Chunk 크기·Overlap,
  Qdrant Timeout, Backend Retry·Timeout Override 없음

이 검증은 성능 개선이나 운영 제한값 변경이 섞인 결과를 기준선으로 사용하는 것을 방지하기
위한 것입니다.
