# RAG-Performance UTF-8·보고서 완전성 수정 후 테스트 명령

> 기준 경로: `D:\Programming\INT2-Jipsa-Team04\RAG-Performance`
>
> 아래 명령은 파일을 수정하지 않습니다. 품질 검사, 실제 외부 Quick Campaign, 산출물 검증,
> Capacity Ladder 순서로 실행합니다.

## 1. PowerShell 현재 Process 실행 정책과 기본 설정

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'
```

## 2. 전체 정적 품질 게이트

```powershell
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
uv run python -m compileall -q src tests
```

정상 기준:

```text
ruff format --check : 모든 파일 formatted
ruff check          : All checks passed
mypy                 : Success: no issues found
pytest               : 전체 통과
compileall           : 오류 출력 없음
```

## 3. 이번 오류 집중 계약 테스트

```powershell
uv run pytest `
    tests/test_stress_runner_contract.py `
    tests/test_capacity_ladder_contract.py `
    tests/test_soak_duration_contract.py `
    -vv
```

검증 대상:

```text
PYTHONUTF8=1 적용과 복원
PYTHONIOENCODING=utf-8 적용과 복원
18개 Campaign·상세 산출물 계약
새 Run 폴더만 선택
Capacity Ladder 하위 stdout Out-Host 분리
이전 실패 폴더 Fallback 금지
Quick Soak 120초·요청 수 조기 종료 없음
```

## 4. 직접 Quick Campaign 실제 실행

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -RandomSeed 8179069822024929128 `
    -SkipQualityGate
```

정상 기준:

```text
UnicodeEncodeError가 발생하지 않음
Burst·Interval·Batch·Ramp·Chaos·Soak 실행
[결과 파일 완전성 검증] 단계 통과
Campaign Markdown·HTML 경로 출력
상세 Stress Markdown·HTML 경로 출력
상세 Stress HTML 경로가 <RUN_ID>\external-stress\report.html임
```

Soak가 `degraded`여도 Campaign Process가 정상 완료되고 `execution_error_type`이 비어 있으면
실행 자체는 완료입니다. 성능 기준 충족 여부는 Stage 상태와 p95·오류율로 별도 판단합니다.

## 5. 최신 Quick 결과 18개 파일 수동 검증

```powershell
$OutputRoot = Join-Path (Get-Location) 'artifacts\external-stress'
$LatestRun = Get-ChildItem `
    -LiteralPath $OutputRoot `
    -Directory |
    Where-Object { $_.Name -match '^\d{8}T\d{6}Z-[0-9a-f]{8}$' } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $LatestRun) {
    throw "검증할 Run 폴더가 없습니다: $OutputRoot"
}

$RequiredRelativePaths = @(
    'report.json',
    'report.md',
    'report.html',
    'external-stress\external_target.resolved.json',
    'external-stress\stress_plan.resolved.json',
    'external-stress\execution_command.txt',
    'external-stress\environment.json',
    'external-stress\requests.json',
    'external-stress\requests.csv',
    'external-stress\stage_summaries.json',
    'external-stress\stage_summaries.csv',
    'external-stress\capacity_boundaries.json',
    'external-stress\capacity_boundaries.csv',
    'external-stress\health_checks.json',
    'external-stress\progress.log',
    'external-stress\report.json',
    'external-stress\report.md',
    'external-stress\report.html'
)

$ArtifactResults = foreach ($RelativePath in $RequiredRelativePaths) {
    $Path = Join-Path -Path $LatestRun.FullName -ChildPath $RelativePath
    $Exists = Test-Path -LiteralPath $Path -PathType Leaf
    $Length = if ($Exists) {
        (Get-Item -LiteralPath $Path).Length
    }
    else {
        0
    }

    [PSCustomObject]@{
        RelativePath = $RelativePath
        Exists = $Exists
        Length = $Length
        Passed = $Exists -and $Length -gt 0
    }
}

$ArtifactResults | Format-Table -AutoSize

$FailedArtifacts = @($ArtifactResults | Where-Object { -not $_.Passed })
if ($FailedArtifacts.Count -gt 0) {
    throw "누락되거나 비어 있는 산출물이 $($FailedArtifacts.Count)개 있습니다."
}

if ($ArtifactResults.Count -ne 18) {
    throw "검증 대상 파일 수가 18개가 아닙니다: $($ArtifactResults.Count)"
}

Write-Host "18개 산출물 검증 통과: $($LatestRun.FullName)" -ForegroundColor Green
```

## 6. Campaign·상세 JSON 상태 검증

```powershell
$CampaignReportPath = Join-Path $LatestRun.FullName 'report.json'
$DetailedReportPath = Join-Path $LatestRun.FullName 'external-stress\report.json'

$CampaignReport = Get-Content `
    -LiteralPath $CampaignReportPath `
    -Raw `
    -Encoding UTF8 |
    ConvertFrom-Json

$DetailedReport = Get-Content `
    -LiteralPath $DetailedReportPath `
    -Raw `
    -Encoding UTF8 |
    ConvertFrom-Json

$Summary = [PSCustomObject]@{
    RunId = $CampaignReport.run_id
    Profile = $CampaignReport.profile
    ExecutionError = $CampaignReport.execution_error_type
    PreflightHealth = $CampaignReport.preflight_health_passed
    PostflightHealth = $CampaignReport.postflight_health_passed
    CampaignStages = @($CampaignReport.stage_summaries).Count
    DetailedStages = $DetailedReport.stage_count
    DetailedRequests = $DetailedReport.request_count
    DetailedErrors = $DetailedReport.error_count
}

$Summary | Format-List

if ($null -ne $CampaignReport.execution_error_type) {
    throw "Campaign 실행 오류가 기록되었습니다: $($CampaignReport.execution_error_type)"
}
if (-not [bool] $CampaignReport.preflight_health_passed) {
    throw '사전 Health 검증 실패'
}
if (-not [bool] $CampaignReport.postflight_health_passed) {
    throw '사후 Health 검증 실패'
}
if (@($CampaignReport.stage_summaries).Count -le 0) {
    throw 'Campaign Stage가 없습니다.'
}
if ([int] $DetailedReport.stage_count -le 0) {
    throw '상세 Stage 수가 0입니다.'
}
if ([int] $DetailedReport.request_count -le 0) {
    throw '상세 Request 수가 0입니다.'
}

Write-Host 'Campaign·상세 JSON 상태 검증 통과' -ForegroundColor Green
```

## 7. 상세 HTML 생성과 링크 검증

```powershell
$CampaignHtmlPath = Join-Path $LatestRun.FullName 'report.html'
$DetailedHtmlPath = Join-Path $LatestRun.FullName 'external-stress\report.html'

$CampaignHtml = Get-Content `
    -LiteralPath $CampaignHtmlPath `
    -Raw `
    -Encoding UTF8
$DetailedHtml = Get-Content `
    -LiteralPath $DetailedHtmlPath `
    -Raw `
    -Encoding UTF8

if ($CampaignHtml -notmatch "href='external-stress/report.html'") {
    throw 'Campaign HTML에서 상세 HTML 상대 링크를 찾을 수 없습니다.'
}
if ($DetailedHtml -notmatch '<h2>단계별 결과</h2>') {
    throw '상세 HTML에서 단계별 결과 표를 찾을 수 없습니다.'
}
if ($DetailedHtml -notmatch '<h2>단계별 오류율</h2>') {
    throw '상세 HTML에서 오류율 그래프를 찾을 수 없습니다.'
}
if ($DetailedHtml -notmatch '<h2>처리 한계</h2>') {
    throw '상세 HTML에서 처리 한계 표를 찾을 수 없습니다.'
}

Write-Host "Campaign HTML: $CampaignHtmlPath" -ForegroundColor Green
Write-Host "상세 HTML: $DetailedHtmlPath" -ForegroundColor Green
Start-Process $DetailedHtmlPath
```

## 8. Capacity Ladder 실제 회귀 테스트

> Quick 완료 후 같은 Seed로 Quick → Standard 승격을 다시 검증합니다. 외부 부하 시간이
> 길어질 수 있으므로 승인된 Test 환경에서 실행합니다.

```powershell
& .\scripts\run-capacity-ladder.ps1 `
    -RandomSeed 8179069822024929128 `
    -SkipQualityGate
```

정상 기준:

```text
하위 Python 한글 로그 출력 중 UnicodeEncodeError 없음
Quick Profile 결과가 문자열 배열이 아니라 단일 결과 객체로 처리됨
Quick 상한 검열 시 Standard 자동 실행
각 Profile에서 새 Run 폴더만 선택
각 Profile의 Campaign·상세 보고서 검증 통과
capacity-ladder-<UTC>.json 생성
capacity-ladder-<UTC>.md 생성
Markdown 요약에 각 Run의 external-stress/report.html 경로 포함
```

## 9. Capacity Ladder 최신 요약 검증

```powershell
$LatestLadderJson = Get-ChildItem `
    -LiteralPath '.\artifacts\external-stress' `
    -Filter 'capacity-ladder-*.json' `
    -File |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1

if ($null -eq $LatestLadderJson) {
    throw 'Capacity Ladder JSON 요약을 찾을 수 없습니다.'
}

$Ladder = Get-Content `
    -LiteralPath $LatestLadderJson.FullName `
    -Raw `
    -Encoding UTF8 |
    ConvertFrom-Json

$Ladder | ConvertTo-Json -Depth 10

if (@($Ladder.runs).Count -le 0) {
    throw 'Capacity Ladder 실행 결과가 없습니다.'
}
foreach ($Run in @($Ladder.runs)) {
    if (-not (Test-Path -LiteralPath $Run.DetailedReportHtml -PathType Leaf)) {
        throw "상세 HTML이 없습니다: $($Run.DetailedReportHtml)"
    }
}

Write-Host "Capacity Ladder 요약 검증 통과: $($LatestLadderJson.FullName)" -ForegroundColor Green
```

## 10. Git 상태 확인

README의 마지막 검증 기록은 실제 Campaign 실행 후 자동 변경되는 것이 정상입니다.

```powershell
git status --short
git diff --check
git diff --stat
```

정상적으로 예상되는 변경:

```text
scripts/run-staged-stress-test.ps1
scripts/run-capacity-ladder.ps1
tests/test_stress_runner_contract.py
tests/test_capacity_ladder_contract.py
README.md
README.html
```

---

## 11. 단일 Standard 실제 실행

> Capacity Ladder 대신 Standard Profile만 독립적으로 재현할 때 사용합니다.

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile standard `
    -RandomSeed 8179069822024929128 `
    -SkipQualityGate
```

## 12. Endurance 실제 실행

> 약 6시간이 소요될 수 있습니다. Quick·Standard와 산출물 완전성 검증을 먼저 통과한 뒤
> 승인된 시간대에 실행합니다.

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile endurance `
    -RandomSeed 8179069822024929128 `
    -SkipQualityGate
```

## 13. 승인된 Destructive 실제 실행

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile destructive `
    -AllowDestructive `
    -ConfirmTargetHost 'int2-jipsa.iptime.org' `
    -RandomSeed 8179069822024929128 `
    -SkipQualityGate
```

Production으로 표시된 Target에는 정식 승인 후 다음 Switch도 필요합니다.

```powershell
-AllowProductionTarget
```

## 14. 최종 합격 기준

```text
1. Ruff Format·Lint 통과
2. Mypy Strict 통과
3. 전체 Pytest 통과
4. Compileall 오류 없음
5. UTF-8·산출물·Capacity 집중 계약 테스트 통과
6. 직접 Quick에서 UnicodeEncodeError 없음
7. Quick Run의 18개 파일 존재·0 Byte 없음
8. Campaign·상세 JSON의 execution_error_type 없음
9. 사전·사후 Health 통과
10. 상세 HTML에 단계별 결과·오류율·처리 한계 포함
11. Capacity Ladder에서 Quick 결과 객체 오염 없음
12. Quick 상한 검열 시 Standard 자동 승격
13. README.md·README.html 마지막 검증 기록 자동 갱신
```
