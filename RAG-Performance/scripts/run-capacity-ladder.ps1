#Requires -Version 5.1

[CmdletBinding()]
param(
    # quick에서 시작하면 C32 상한이 검열된 경우 Standard C128까지 자동 승격합니다.
    # 이미 Quick를 완료한 뒤 바로 C128부터 확인하려면 standard를 지정할 수 있습니다.
    [Parameter(Mandatory = $false)]
    [ValidateSet('quick', 'standard')]
    [string] $StartProfile = 'quick',

    [Parameter(Mandatory = $false)]
    [string] $RagEnvironmentPath,

    [Parameter(Mandatory = $false)]
    [ValidateSet('auto', 'qdrant', 'database', 'snapshot')]
    [string] $DataSource = 'auto',

    [Parameter(Mandatory = $false)]
    [string] $SnapshotPath,

    [Parameter(Mandatory = $false)]
    [string[]] $SnapshotSearchRoot = @(),

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 20)]
    [int] $FilesPerUser = 2,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 64)]
    [int] $QueryCount = 8,

    # 하나의 Seed를 Quick·Standard·Destructive에 공통 적용하여 같은 User/File/Query 범위를
    # 사용합니다. 미지정 시 이번 Ladder 전용 Seed를 한 번 생성하고 모든 Profile에 재사용합니다.
    [Parameter(Mandatory = $false)]
    [Nullable[long]] $RandomSeed = $null,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 100000)]
    [int] $QdrantScanLimit = 4096,

    [Parameter(Mandatory = $false)]
    [string] $OutputRoot,

    # Standard C128에서도 최초 실패가 없을 때만 Destructive C256을 실행합니다.
    # 높은 외부 Traffic이므로 이 Switch와 Host 확인값이 모두 있어야 합니다.
    [switch] $AllowDestructive,

    [Parameter(Mandatory = $false)]
    [string] $ConfirmTargetHost,

    [switch] $AllowProductionTarget,
    [switch] $AllowInsecureHttp,
    [switch] $AllowLoopbackTarget,
    [switch] $DisableTlsVerification,
    [switch] $SkipQualityGate,
    [switch] $SkipReadmeUpdate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Issue #159 외부 RAG Capacity Ladder
# ============================================================
#
# 1. Quick C32를 실행하거나 기존 선택에 따라 Standard C128부터 시작합니다.
# 2. Ramp에 최초 실패가 기록되면 즉시 종료합니다.
# 3. Quick 상한까지 실패가 없으면 Standard로 자동 승격합니다.
# 4. Standard 상한까지 실패가 없을 때 Destructive는 명시적 승인 후에만 실행합니다.
# 5. 모든 Profile은 같은 Random Seed를 사용하므로 서로 다른 데이터가 결과를 왜곡하지 않습니다.
# ============================================================

$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path -Path $PSScriptRoot -ChildPath '..'
    )
).Path
$RunnerPath = Join-Path -Path $PSScriptRoot -ChildPath 'run-staged-stress-test.ps1'

if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) {
    throw "단계형 Stress 실행기를 찾을 수 없습니다: $RunnerPath"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path -Path $ProjectRoot -ChildPath 'artifacts/external-stress'
}
$ResolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
[System.IO.Directory]::CreateDirectory($ResolvedOutputRoot) | Out-Null

# PowerShell은 Nullable[T] 매개변수에 실제 값이 바인딩되면 값을 T 자체로
# 언박싱할 수 있다. 따라서 ``$RandomSeed.Value``처럼 Nullable 전용 속성에
# 접근하면 Set-StrictMode 환경에서 Int64에 Value 속성이 없다는 오류가 발생한다.
#
# 사용자가 Seed를 명시했는지는 값의 런타임 타입이 아니라 PSBoundParameters로
# 판별한다. 명시된 값은 Int64로 정규화하고, 생략되거나 명시적으로 null이면
# Ladder 전체에서 재사용할 새 Seed를 한 번 생성한다.
$HasExplicitRandomSeed = (
    $PSBoundParameters.ContainsKey('RandomSeed') -and
    $null -ne $RandomSeed
)
$EffectiveSeed = if ($HasExplicitRandomSeed) {
    [long] $RandomSeed
}
else {
    # Millisecond Epoch는 한 Ladder 안에서 고정되고 결과에 기록할 수 있는 양의 Int64다.
    [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
}

function Get-RunDirectories {
    $Directories = Get-ChildItem `
        -LiteralPath $ResolvedOutputRoot `
        -Directory `
        -ErrorAction SilentlyContinue
    if ($null -eq $Directories) {
        return @()
    }
    return @($Directories)
}

function Get-SearchBoundary {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject] $Report
    )

    $Boundary = @($Report.capacity_boundaries) |
        Where-Object { $_.operation -eq 'search' } |
        Select-Object -First 1
    if ($null -eq $Boundary) {
        throw '외부 Stress 보고서에서 search 처리 한계를 찾을 수 없습니다.'
    }
    return $Boundary
}

function Invoke-CapacityProfile {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('quick', 'standard', 'destructive')]
        [string] $Profile,

        [Parameter(Mandatory = $true)]
        [bool] $SkipGateForThisRun
    )

    $BeforePaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Directory in Get-RunDirectories) {
        $BeforePaths.Add($Directory.FullName) | Out-Null
    }

    $Arguments = @{
        TestProfile = $Profile
        DataSource = $DataSource
        FilesPerUser = $FilesPerUser
        QueryCount = $QueryCount
        RandomSeed = $EffectiveSeed
        QdrantScanLimit = $QdrantScanLimit
        OutputRoot = $ResolvedOutputRoot
    }
    if (-not [string]::IsNullOrWhiteSpace($RagEnvironmentPath)) {
        $Arguments.RagEnvironmentPath = $RagEnvironmentPath
    }
    if (-not [string]::IsNullOrWhiteSpace($SnapshotPath)) {
        $Arguments.SnapshotPath = $SnapshotPath
    }
    if ($SnapshotSearchRoot.Count -gt 0) {
        $Arguments.SnapshotSearchRoot = $SnapshotSearchRoot
    }
    if ($SkipGateForThisRun) {
        $Arguments.SkipQualityGate = $true
    }
    if ($SkipReadmeUpdate) {
        $Arguments.SkipReadmeUpdate = $true
    }
    if ($AllowInsecureHttp) {
        $Arguments.AllowInsecureHttp = $true
    }
    if ($AllowLoopbackTarget) {
        $Arguments.AllowLoopbackTarget = $true
    }
    if ($DisableTlsVerification) {
        $Arguments.DisableTlsVerification = $true
    }
    if ($AllowProductionTarget) {
        $Arguments.AllowProductionTarget = $true
    }
    if ($Profile -eq 'destructive') {
        if (-not $AllowDestructive) {
            throw 'Destructive 자동 승격에는 -AllowDestructive가 필요합니다.'
        }
        if ([string]::IsNullOrWhiteSpace($ConfirmTargetHost)) {
            throw 'Destructive 자동 승격에는 -ConfirmTargetHost가 필요합니다.'
        }
        $Arguments.AllowDestructive = $true
        $Arguments.ConfirmTargetHost = $ConfirmTargetHost
    }

    Write-Host ''
    Write-Host "[Capacity Ladder] $Profile 실행" -ForegroundColor Cyan
    & $RunnerPath @Arguments

    $NewRuns = @(Get-RunDirectories) |
        Where-Object { -not $BeforePaths.Contains($_.FullName) } |
        Sort-Object LastWriteTimeUtc -Descending
    $RunDirectory = $NewRuns | Select-Object -First 1
    if ($null -eq $RunDirectory) {
        # 파일 시스템 Timestamp 정밀도나 외부 정리 작업으로 신규 폴더 구분이 실패한 경우에도
        # 방금 수정된 최신 결과를 한 번 더 확인한다.
        $RunDirectory = Get-RunDirectories |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
    }
    if ($null -eq $RunDirectory) {
        throw "$Profile 실행 결과 폴더를 찾을 수 없습니다: $ResolvedOutputRoot"
    }

    $ReportPath = Join-Path -Path $RunDirectory.FullName -ChildPath 'report.json'
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
        throw "$Profile 실행 보고서를 찾을 수 없습니다: $ReportPath"
    }
    $Report = Get-Content `
        -LiteralPath $ReportPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json
    $Boundary = Get-SearchBoundary -Report $Report

    return [PSCustomObject]@{
        Profile = $Profile
        RunId = [string] $Report.run_id
        RunDirectory = $RunDirectory.FullName
        NormalMaximumConcurrency = $Boundary.normal_maximum_concurrency
        FirstFailureConcurrency = $Boundary.first_failure_concurrency
        FirstFailureStageId = $Boundary.first_failure_stage_id
        FirstFailureReason = $Boundary.first_failure_reason
        UpperBoundCensored = [bool] $Boundary.upper_bound_censored
        ErrorCount = (@($Report.stage_summaries) |
            Measure-Object -Property error_count -Sum).Sum
        CompletedAtUtc = [string] $Report.completed_at_utc
    }
}

$Results = [System.Collections.Generic.List[object]]::new()
$Profiles = [System.Collections.Generic.List[string]]::new()
if ($StartProfile -eq 'quick') {
    $Profiles.Add('quick')
}
$Profiles.Add('standard')

$QualityGateAlreadyHandled = [bool] $SkipQualityGate
foreach ($Profile in $Profiles) {
    $Result = Invoke-CapacityProfile `
        -Profile $Profile `
        -SkipGateForThisRun $QualityGateAlreadyHandled
    $Results.Add($Result)
    $QualityGateAlreadyHandled = $true

    Write-Host (
        "[Capacity Ladder] $Profile 결과: normal_max=$($Result.NormalMaximumConcurrency), " +
        "first_failure=$($Result.FirstFailureConcurrency), " +
        "censored=$($Result.UpperBoundCensored)"
    ) -ForegroundColor Green

    if ($null -ne $Result.FirstFailureConcurrency) {
        break
    }
    if (-not $Result.UpperBoundCensored) {
        break
    }
}

$LastResult = $Results[$Results.Count - 1]
if (
    $LastResult.Profile -eq 'standard' -and
    $LastResult.UpperBoundCensored -and
    $null -eq $LastResult.FirstFailureConcurrency
) {
    if ($AllowDestructive) {
        $DestructiveResult = Invoke-CapacityProfile `
            -Profile 'destructive' `
            -SkipGateForThisRun $true
        $Results.Add($DestructiveResult)
        $LastResult = $DestructiveResult
    }
    else {
        Write-Warning (
            'Standard C128까지 최초 실패가 없습니다. C256 Destructive는 자동 실행하지 ' +
            '않았습니다. 승인 후 -AllowDestructive와 -ConfirmTargetHost를 추가하세요.'
        )
    }
}

$CompletedAtUtc = [DateTimeOffset]::UtcNow.ToString('o')
$Summary = [ordered]@{
    schema_version = 1
    selection_seed = $EffectiveSeed
    start_profile = $StartProfile
    destructive_allowed = [bool] $AllowDestructive
    completed_at_utc = $CompletedAtUtc
    final_profile = $LastResult.Profile
    final_normal_maximum_concurrency = $LastResult.NormalMaximumConcurrency
    final_first_failure_concurrency = $LastResult.FirstFailureConcurrency
    final_upper_bound_censored = $LastResult.UpperBoundCensored
    runs = @($Results)
}

$Timestamp = [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$SummaryJsonPath = Join-Path `
    -Path $ResolvedOutputRoot `
    -ChildPath "capacity-ladder-$Timestamp.json"
$SummaryMarkdownPath = Join-Path `
    -Path $ResolvedOutputRoot `
    -ChildPath "capacity-ladder-$Timestamp.md"

$Summary |
    ConvertTo-Json -Depth 10 |
    Set-Content -LiteralPath $SummaryJsonPath -Encoding UTF8

$MarkdownLines = [System.Collections.Generic.List[string]]::new()
$MarkdownLines.Add('# 외부 RAG Capacity Ladder 결과')
$MarkdownLines.Add('')
$MarkdownLines.Add("- 선정 Seed: ``$EffectiveSeed``")
$MarkdownLines.Add("- 시작 Profile: ``$StartProfile``")
$MarkdownLines.Add("- 최종 Profile: ``$($LastResult.Profile)``")
$MarkdownLines.Add(
    "- 최종 정상 최대 동시성: ``$($LastResult.NormalMaximumConcurrency)``"
)
$MarkdownLines.Add(
    "- 최초 실패 동시성: ``$($LastResult.FirstFailureConcurrency)``"
)
$MarkdownLines.Add(
    "- 상한 검열 여부: ``$($LastResult.UpperBoundCensored)``"
)
$MarkdownLines.Add('')
$MarkdownLines.Add('| Profile | Run ID | 정상 최대 | 최초 실패 | 상한 검열 |')
$MarkdownLines.Add('|---|---|---:|---:|---:|')
foreach ($Result in $Results) {
    $MarkdownLines.Add(
        "| $($Result.Profile) | ``$($Result.RunId)`` | " +
        "$($Result.NormalMaximumConcurrency) | $($Result.FirstFailureConcurrency) | " +
        "$($Result.UpperBoundCensored) |"
    )
}
$MarkdownLines.Add('')
$MarkdownLines.Add('각 Run의 상세 수치는 해당 실행 폴더의 report.html을 확인합니다.')
$MarkdownLines |
    Set-Content -LiteralPath $SummaryMarkdownPath -Encoding UTF8

Write-Host ''
Write-Host '[Capacity Ladder] 완료' -ForegroundColor Cyan
Write-Host "JSON 요약: $SummaryJsonPath" -ForegroundColor Green
Write-Host "Markdown 요약: $SummaryMarkdownPath" -ForegroundColor Green
