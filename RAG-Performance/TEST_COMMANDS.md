# RAG-Performance 테스트 및 실행 명령어

> 기준 경로: `D:\Programming\INT2-Jipsa-Team04`  
> Shell: Windows PowerShell 5.1  
> Python: 3.12  
> 성능 측정 대상: `RAG`  
> 독립 측정 프로그램: `RAG-Performance`

## 1. PowerShell 실행 정책을 현재 프로세스에서만 허용

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force
```

이 설정은 현재 PowerShell 프로세스가 종료되면 사라집니다.

## 2. RAG-Performance 최초 의존성 동기화

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

uv sync
```

최초 `uv sync`가 현재 Windows·Python 3.12 환경에 맞는 `uv.lock`을 생성합니다.
이후부터는 다음 명령으로 Lock 파일과 실제 환경의 정합성을 고정합니다.

```powershell
uv sync --frozen
```

## 3. RAG-Performance 정적 검사와 단위 테스트

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

예상 결과:

```text
Ruff format: 변경 필요 없음
Ruff lint: 오류 없음
Mypy strict: 오류 없음
Pytest: 전체 통과
```

## 4. 측정 대상 RAG 품질 게이트

독립 측정 프로그램의 오류와 RAG 자체 오류를 구분하기 위해 성능 측정 전에 RAG 품질
게이트도 별도로 실행합니다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'

uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

실제 CUDA, Local RAG DB, Qdrant, Microsoft Office COM 및 Claude까지 포함하는 기존 전체
검증은 다음 스크립트를 사용합니다.

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'

& .\scripts\run-all-rag-tests.ps1
```

같은 Git Commit에서 `verify-rag-quality.ps1`이 이미 성공한 경우에만 다음처럼 일반 품질
게이트를 생략할 수 있습니다.

```powershell
& .\scripts\run-all-rag-tests.ps1 -SkipQualityGate
```

## 5. Claude 비용 없이 기본 측정

인제스트와 청크 검색의 자원 사용량·동시성 한계를 먼저 확인하는 권장 첫 실행입니다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-benchmark.ps1 `
    -DisableAnswers
```

이 실행은 다음 항목을 포함합니다.

- Git Branch와 Commit SHA
- CPU, RAM, GPU, VRAM
- Host Disk I/O와 Network I/O
- RAG Process Tree 자원 사용량
- TEI·Qdrant Docker Container 자원 사용량
- PDF, DOCX, PPTX, XLSX, TXT 형식별 인제스트
- 일반 텍스트와 OCR 문서 비교
- 파일 크기, 이미지 수, 실제 생성 청크 수 단계 증가
- Cold Start와 Warm Run 분리
- 청크 검색 API 단일·동시 요청
- 동시성 증가에 따른 최초 포화 후보

## 6. lookup·synthesis를 포함한 전체 측정

Claude API 호출 비용이 발생합니다.

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-benchmark.ps1
```

전체 실행은 다음 답변 측정을 추가합니다.

- 단일 문서 `lookup`
- 다중 문서 `synthesis`
- 각 유형의 Cold·Warm 응답 시간
- 동시 요청별 평균, p50, p95, p99
- 성공률, 오류율과 처리량
- Claude 입력·출력 Token 사용량

## 7. 이미 실행 중인 Qdrant·TEI를 유지한 Warm 중심 측정

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-benchmark.ps1 `
    -DisableAnswers `
    -PreserveRunningInfrastructure
```

이 모드는 기존 Qdrant와 TEI를 재시작하지 않으므로 인프라 Cold Start 결과로 해석하면
안 됩니다. RAG API Process와 요청 단위 Cold·Warm 구분은 계속 기록합니다.

## 8. 측정 종료 후 인프라 유지

연속 측정이나 실패 분석을 위해 Qdrant와 TEI를 그대로 유지합니다.

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-benchmark.ps1 `
    -DisableAnswers `
    -KeepInfrastructureRunning
```

## 9. 전용 테스트 데이터 유지

실패한 DB·Qdrant 상태를 직접 확인해야 할 때만 사용합니다.

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-benchmark.ps1 `
    -DisableAnswers `
    -KeepTestData `
    -KeepInfrastructureRunning
```

`JIPSA_RAG_APP_ENV=test`와 Issue #159 전용 `Users_IDX`·`File_IDX` 범위에서만 동작하지만,
일반 측정은 자동 정리를 사용하는 것이 안전합니다.

## 10. 별도 위치의 RAG를 대상으로 측정

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-benchmark.ps1 `
    -RagRoot 'D:\Programming\INT2-Jipsa-Team04\RAG' `
    -DisableAnswers
```

## 11. 사용자 정의 측정 계획 실행

기본 계획 파일을 복사해 동시성, 반복 횟수와 Fixture 크기를 변경한 뒤 다음처럼 실행합니다.

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

& .\scripts\run-benchmark.ps1 `
    -PlanPath 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance\configs\benchmark-plan.json' `
    -DisableAnswers
```

이번 이슈는 측정만 수행하므로 계획 변경은 부하 수준과 반복 횟수에 한정합니다. RAG의
청킹 정책, Timeout, 모델, 동시성 제한이나 Qdrant 설정은 자동으로 변경하지 않습니다.

## 12. 결과 확인

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

$LatestRun = Get-ChildItem `
    -LiteralPath '.\artifacts' `
    -Directory |
    Sort-Object -Property LastWriteTime -Descending |
    Select-Object -First 1

$LatestRun.FullName
Get-Content -LiteralPath (Join-Path $LatestRun.FullName 'report.md') -Encoding UTF8
```

대표 결과 파일:

```text
artifacts/<run-id>/
├─ environment.json
├─ benchmark_plan.resolved.json
├─ all_owned_fixtures.json
├─ fixtures/
├─ target.log
├─ request_records.csv
├─ level_summaries.csv
├─ resource_samples.jsonl
├─ resource_samples.csv
├─ resource_summaries.csv
├─ ingest_stage_events.csv
├─ ingest_stage_resource_summary.csv
├─ host_io_deltas.csv
├─ saturation_candidates.json
├─ report.json
└─ report.md
```

## 13. 권장 최종 검증 순서

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'
& .\scripts\run-benchmark.ps1 -DisableAnswers -SkipQualityGate
```

`-SkipQualityGate`는 같은 Commit과 같은 파일 상태에서 바로 앞의 품질 검사가 모두 성공한
경우에만 사용합니다.
