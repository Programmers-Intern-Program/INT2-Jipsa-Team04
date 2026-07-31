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
# 6. 하위 Stress Script의 표준 출력은 Out-Host로 즉시 표시하고 함수 반환 Pipeline에서
#    제거합니다. 따라서 출력 문자열이 Profile 결과 PSCustomObject에 섞이지 않습니다.
# 7. 각 Profile의 Campaign·상세 보고서와 Health·Stage를 다시 검증한 뒤 다음 단계로 진행합니다.
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

# PowerShell은 Nullable[T] 매개변수에 실제 값이 바인딩되면 값을 T 자체로 언박싱할 수
# 있습니다. 따라서 ``$RandomSeed.Value``처럼 Nullable 전용 속성에 접근하지 않습니다.
$HasExplicitRandomSeed = (
    $PSBoundParameters.ContainsKey('RandomSeed') -and
    $null -ne $RandomSeed
)
$EffectiveSeed = if ($HasExplicitRandomSeed) {
    [long] $RandomSeed
}
else {
    # Millisecond Epoch는 한 Ladder 안에서 고정되고 결과에 기록할 수 있는 양의 Int64입니다.
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

function Assert-RunArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $DisplayName
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Capacity Ladder Profile 산출물이 없습니다: $DisplayName ($Path)"
    }

    $Item = Get-Item -LiteralPath $Path
    if ($Item.Length -le 0) {
        throw "Capacity Ladder Profile 산출물이 비어 있습니다: $DisplayName ($Path)"
    }
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

function Assert-CompletedProfileReport {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.DirectoryInfo] $RunDirectory,

        [Parameter(Mandatory = $true)]
        [ValidateSet('quick', 'standard', 'destructive')]
        [string] $Profile
    )

    # 하위 실행 Script도 전체 산출물을 검증하지만, Ladder는 다음 Profile 실행 여부를 결정하는
    # 상위 제어기이므로 최소 핵심 파일과 JSON 상태를 한 번 더 검증합니다. 이렇게 하면 오래된
    # 실패 폴더나 부분 생성 폴더를 처리 한계 근거로 사용하는 일을 차단할 수 있습니다.
    $RequiredRelativePaths = @(
        'report.json',
        'report.md',
        'report.html',
        'external-stress/report.json',
        'external-stress/report.md',
        'external-stress/report.html',
        'external-stress/stage_summaries.json',
        'external-stress/capacity_boundaries.json',
        'external-stress/requests.json',
        'external-stress/progress.log'
    )
    foreach ($RelativePath in $RequiredRelativePaths) {
        Assert-RunArtifact `
            -Path (Join-Path -Path $RunDirectory.FullName -ChildPath $RelativePath) `
            -DisplayName $RelativePath
    }

    $ReportPath = Join-Path -Path $RunDirectory.FullName -ChildPath 'report.json'
    $Report = Get-Content `
        -LiteralPath $ReportPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json

    if ([string] $Report.profile -ne $Profile) {
        throw (
            "새 결과 폴더의 Profile이 요청과 다릅니다. expected=$Profile, " +
            "actual=$([string] $Report.profile)"
        )
    }
    if ($null -ne $Report.execution_error_type) {
        throw (
            "$Profile Campaign 보고서에 실행 오류가 기록되어 있습니다: " +
            [string] $Report.execution_error_type
        )
    }
    if (-not [bool] $Report.preflight_health_passed) {
        throw "$Profile Campaign의 사전 Health 검증이 통과하지 않았습니다."
    }
    if (-not [bool] $Report.postflight_health_passed) {
        throw "$Profile Campaign의 사후 Health 검증이 통과하지 않았습니다."
    }
    if (@($Report.stage_summaries).Count -le 0) {
        throw "$Profile Campaign에 완료된 Stress Stage가 없습니다."
    }

    $Boundary = Get-SearchBoundary -Report $Report
    return [PSCustomObject]@{
        Report = $Report
        Boundary = $Boundary
        DetailedReportHtml = Join-Path `
            -Path $RunDirectory.FullName `
            -ChildPath 'external-stress/report.html'
    }
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

    # 중요: 외부 Process의 stdout은 PowerShell Success Pipeline으로 전달됩니다. 이 호출을
    # 그대로 두면 Invoke-CapacityProfile 함수가 Console 문자열과 마지막 PSCustomObject를 함께
    # 반환해 $Result가 Object[]가 됩니다. Out-Host는 출력을 실시간 표시하면서 함수 반환값에서는
    # 제거하므로 Ladder 결과 형식을 안정적으로 유지합니다.
    & $RunnerPath @Arguments | Out-Host

    $NewRuns = @(Get-RunDirectories) |
        Where-Object { -not $BeforePaths.Contains($_.FullName) } |
        Sort-Object LastWriteTimeUtc -Descending
    $RunDirectory = $NewRuns | Select-Object -First 1

    # 각 Run ID는 UUID 일부가 포함된 고유 폴더이므로 이번 실행에서 새 폴더가 없으면 오래된
    # 최신 폴더로 Fallback하지 않습니다. 이전 실패 보고서를 새 Profile 결과로 오인하는 것보다
    # 즉시 실패시키는 편이 안전합니다.
    if ($null -eq $RunDirectory) {
        throw "$Profile 실행에서 새 결과 폴더를 찾을 수 없습니다: $ResolvedOutputRoot"
    }

    $Validated = Assert-CompletedProfileReport `
        -RunDirectory $RunDirectory `
        -Profile $Profile
    $Report = $Validated.Report
    $Boundary = $Validated.Boundary

    return [PSCustomObject]@{
        Profile = $Profile
        RunId = [string] $Report.run_id
        RunDirectory = $RunDirectory.FullName
        DetailedReportHtml = $Validated.DetailedReportHtml
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
    Write-Host (
        "[Capacity Ladder] $Profile 상세 HTML: $($Result.DetailedReportHtml)"
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
$MarkdownLines.Add('| Profile | Run ID | 정상 최대 | 최초 실패 | 상한 검열 | 상세 HTML |')
$MarkdownLines.Add('|---|---|---:|---:|---:|---|')
foreach ($Result in $Results) {
    $MarkdownLines.Add(
        "| $($Result.Profile) | ``$($Result.RunId)`` | " +
        "$($Result.NormalMaximumConcurrency) | $($Result.FirstFailureConcurrency) | " +
        "$($Result.UpperBoundCensored) | ``$($Result.DetailedReportHtml)`` |"
    )
}
$MarkdownLines.Add('')
$MarkdownLines.Add(
    '각 Run의 표·그래프·단계별 수치는 해당 실행 폴더의 ' +
    '`external-stress/report.html`을 확인합니다.'
)
$MarkdownLines |
    Set-Content -LiteralPath $SummaryMarkdownPath -Encoding UTF8

Write-Host ''
Write-Host '[Capacity Ladder] 완료' -ForegroundColor Cyan
Write-Host "JSON 요약: $SummaryJsonPath" -ForegroundColor Green
Write-Host "Markdown 요약: $SummaryMarkdownPath" -ForegroundColor Green
