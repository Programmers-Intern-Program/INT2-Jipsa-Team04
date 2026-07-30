# Jipsa External RAG Performance Test Commands

## 1. PowerShell 준비

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'
```

## 2. 적용 파일 확인

```powershell
@(
    '.\scripts\run-staged-stress-test.ps1'
    '.\src\jipsa_rag_benchmark\rag_environment.py'
    '.\src\jipsa_rag_benchmark\test_data_discovery.py'
    '.\src\jipsa_rag_benchmark\external_target.py'
    '.\src\jipsa_rag_benchmark\stress_runner.py'
    '.\README.md'
    '.\README.html'
    '..\RAG\.env.local'
) |
    ForEach-Object {
        [PSCustomObject]@{
            Path   = $_
            Exists = Test-Path -LiteralPath $_ -PathType Leaf
        }
    } |
    Format-Table -AutoSize
```

모든 `Exists`가 `True`여야 합니다.

## 3. 전체 품질 게이트

```powershell
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
uv run python -m compileall -q src tests
```

## 4. 자동 환경 로드 확인

Token 값은 출력하지 않고 존재 여부만 확인합니다.

```powershell
$RagEnvPath = '..\RAG\.env.local'

$RequiredKeys = @(
    'JIPSA_RAG_EXTERNAL_BASE_URL'
    'JIPSA_RAG_API_V1_PREFIX'
    'RAG_INGEST_TOKEN'
    'JIPSA_RAG_QDRANT_URL'
    'JIPSA_RAG_QDRANT_COLLECTION'
)

$RequiredKeys |
    ForEach-Object {
        $Key = $_
        $Match = Select-String `
            -LiteralPath $RagEnvPath `
            -Pattern "^\s*$([Regex]::Escape($Key))=" `
            -ErrorAction Stop

        [PSCustomObject]@{
            Key = $Key
            Configured = $null -ne $Match
        }
    } |
    Format-Table -AutoSize
```

## 5. 외부 RAG 연결 확인

공개 Origin은 RAG 환경 파일에서 읽습니다.

```powershell
$ExternalBaseUrl = (
    Select-String `
        -LiteralPath '..\RAG\.env.local' `
        -Pattern '^JIPSA_RAG_EXTERNAL_BASE_URL=' |
    Select-Object -Last 1
).Line.Split('=', 2)[1].Trim().Trim('"').Trim("'")

$ApiPrefix = (
    Select-String `
        -LiteralPath '..\RAG\.env.local' `
        -Pattern '^JIPSA_RAG_API_V1_PREFIX=' |
    Select-Object -Last 1
).Line.Split('=', 2)[1].Trim().Trim('"').Trim("'")

Invoke-RestMethod `
    -Method Get `
    -Uri "$($ExternalBaseUrl.TrimEnd('/'))$ApiPrefix/health/live"

Invoke-RestMethod `
    -Method Get `
    -Uri "$($ExternalBaseUrl.TrimEnd('/'))$ApiPrefix/health/ready"
```

## 6. 자동 데이터 선정 단독 확인

실제 Stress Traffic을 보내기 전에 환경과 기존 데이터 선정만 검증합니다.

```powershell
uv run python -c @'
from pathlib import Path
from jipsa_rag_benchmark.rag_environment import load_rag_environment
from jipsa_rag_benchmark.test_data_discovery import discover_test_data

settings = load_rag_environment(Path('../RAG/.env.local'))
result = discover_test_data(
    settings,
    source='auto',
    files_per_user=2,
    query_count=8,
    random_seed=159,
    snapshot_path=None,
    snapshot_search_roots=(Path('snapshots'), Path('..'), Path('../RAG')),
)
print(result.to_public_dict())
'@
```

출력에는 Source, Seed, User IDX, File IDX와 후보 수만 표시되어야 하며 Token·Content·Query는
표시되지 않아야 합니다.

## 7. Quick

환경 변수, Token, User IDX와 File IDX를 직접 입력하지 않습니다.

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -SkipQualityGate
```

## 8. Standard

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile standard `
    -SkipQualityGate
```

## 9. Endurance

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile endurance `
    -SkipQualityGate
```

## 10. Destructive

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile destructive `
    -AllowDestructive `
    -ConfirmTargetHost 'int2-jipsa.iptime.org' `
    -SkipQualityGate
```

## 11. Qdrant Source 강제

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource qdrant `
    -SkipQualityGate
```

## 12. DB Source 강제

`mariadb` 또는 `mysql` Client가 PATH에 있어야 합니다.

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource database `
    -SkipQualityGate
```

## 13. Snapshot 자동 탐색

Snapshot을 다음 경로에 둡니다.

```text
D:\Programming\INT2-Jipsa-Team04\RAG-Performance\snapshots\*.snapshot
```

실행:

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource snapshot `
    -SkipQualityGate
```

## 14. 특정 Snapshot과 Seed 재현

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource snapshot `
    -SnapshotPath `
        '.\snapshots\rag_chunk_vector_qwen3_embedding_0_6b_1024.snapshot' `
    -RandomSeed 159 `
    -SkipQualityGate
```

## 15. 선택 파일 수 변경

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -FilesPerUser 4 `
    -QueryCount 12 `
    -SkipQualityGate
```

후보 User가 4개 파일을 보유하지 않으면 가장 많은 활성 파일을 가진 User 중 무작위로
선정하고 실제 가능한 파일 수만 사용합니다.

## 16. 결과 확인

```powershell
$LatestRun = Get-ChildItem `
    -LiteralPath '.\artifacts\external-stress' `
    -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

Get-Content `
    -LiteralPath (Join-Path $LatestRun.FullName 'report.md') `
    -Encoding UTF8

Invoke-Item `
    -LiteralPath (Join-Path $LatestRun.FullName 'report.html')

Invoke-Item `
    -LiteralPath (
        Join-Path $LatestRun.FullName 'external-stress\report.html'
    )
```

## 17. 자동 선정 정보 확인

```powershell
$PublicTarget = Get-Content `
    -LiteralPath (
        Join-Path $LatestRun.FullName `
            'external-stress\target_config.public.json'
    ) `
    -Raw `
    -Encoding UTF8 |
    ConvertFrom-Json

$PublicTarget |
    Select-Object `
        target_base_url,
        selection_source,
        selection_seed,
        candidate_user_count,
        candidate_file_count,
        candidate_chunk_count,
        test_user_idx,
        reference_file_idxs,
        query_count
```

## 18. README 자동 갱신 확인

```powershell
Select-String `
    -Path '.\README.md' `
    -Pattern `
        '16. 마지막 검증 기록|상태:|Run ID:|데이터 Source|선정 Seed'

Select-String `
    -Path '.\README.html' `
    -Pattern `
        '16. 마지막 검증 기록|status-passed|status-degraded|status-failed|데이터 Source'
```

## 19. Snapshot 임시 Container 정리 확인

정상·실패 여부와 관계없이 이름이 `jipsa-perf-snapshot-`으로 시작하는 임시 Container가
남아 있지 않아야 합니다.

```powershell
docker ps -a `
    --filter 'name=jipsa-perf-snapshot-' `
    --format '{{.Names}}'
```

정상 출력은 빈 문자열입니다.


---

## 13. Quick Soak 실제 2분 검증

Quick 실행 후 최신 결과에서 Soak가 최소 119초 이상 실행됐는지 확인합니다. 1초 허용치는
Thread 종료와 Clock 경계 차이를 위한 것입니다.

```powershell
$LatestRun = Get-ChildItem `
    -LiteralPath '.\artifacts\external-stress' `
    -Directory |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

$Soak = Get-Content `
    -LiteralPath (Join-Path $LatestRun.FullName 'external-stress\stage_summaries.json') `
    -Raw `
    -Encoding UTF8 |
    ConvertFrom-Json |
    Select-Object -ExpandProperty records |
    Where-Object { $_.mode -eq 'soak' } |
    Select-Object -First 1

if ($null -eq $Soak) {
    throw '최신 결과에서 Soak Stage를 찾을 수 없습니다.'
}
if ([double] $Soak.elapsed_seconds -lt 119.0) {
    throw "Soak가 목표 2분보다 일찍 종료되었습니다: $($Soak.elapsed_seconds)s"
}
if ($Soak.stop_reason -eq 'soak_max_requests_reached_before_duration') {
    throw 'Soak 요청 안전 상한이 목표 시간보다 먼저 소진되었습니다.'
}

$Soak |
    Select-Object `
        stage_id,
        elapsed_seconds,
        submitted_request_count,
        throughput_requests_per_second,
        status,
        stop_reason |
    Format-List
```


## 13-1. 모든 Profile Soak 설정 검증

Quick뿐 아니라 Standard, Endurance, Destructive도 요청 수 상한 없이 설정 시간 전체를
사용하는지 확인합니다.

```powershell
$ExpectedDurations = @{
    quick       = 120.0
    standard    = 1200.0
    endurance   = 18000.0
    destructive = 900.0
}

foreach ($Profile in $ExpectedDurations.Keys) {
    $Plan = Get-Content `
        -LiteralPath ".\configs\stress-plan-$Profile.json" `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json

    $Soak = $Plan.stages |
        Where-Object { $_.mode -eq 'soak' } |
        Select-Object -First 1

    if ($null -eq $Soak) {
        throw "$Profile Profile에 Soak Stage가 없습니다."
    }
    if ([double] $Soak.duration_seconds -ne $ExpectedDurations[$Profile]) {
        throw "$Profile Soak 시간이 계약과 다릅니다: $($Soak.duration_seconds)s"
    }
    if ([int64] $Soak.max_requests -ne 0) {
        throw "$Profile Soak의 요청 수 상한이 비활성화되지 않았습니다."
    }

    [PSCustomObject]@{
        Profile         = $Profile
        DurationSeconds = $Soak.duration_seconds
        MaxRequests     = $Soak.max_requests
        DurationFirst   = $Soak.max_requests -eq 0
    }
}
```

## 14. Quick → Standard Capacity Ladder

Quick C32에서 실패가 없으면 같은 자동 선정 데이터와 같은 Seed로 Standard C128까지 자동
승격합니다. Standard에서 최초 실패가 나오면 더 높은 Profile을 실행하지 않습니다.

```powershell
& .\scripts\run-capacity-ladder.ps1 `
    -SkipQualityGate
```

이미 Quick를 실행했다면 Standard부터 시작합니다.

```powershell
& .\scripts\run-capacity-ladder.ps1 `
    -StartProfile standard `
    -SkipQualityGate
```

Standard C128에서도 실패가 없고 승인된 Test 환경에서 C256까지 확인해야 하는 경우:

```powershell
& .\scripts\run-capacity-ladder.ps1 `
    -AllowDestructive `
    -ConfirmTargetHost 'int2-jipsa.iptime.org' `
    -SkipQualityGate
```

결과 요약은 다음 경로에 생성됩니다.

```text
artifacts/external-stress/capacity-ladder-*.json
artifacts/external-stress/capacity-ladder-*.md
```
