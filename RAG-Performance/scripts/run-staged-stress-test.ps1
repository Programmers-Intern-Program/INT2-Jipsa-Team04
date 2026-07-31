#Requires -Version 5.1

[CmdletBinding()]
param(
    # quick: 약 5분, standard: 약 30~45분, endurance: 약 6시간,
    # destructive: 외부 Endpoint에 고동시성·대형 Spike를 보내 최초 실패 경계를 찾습니다.
    [Parameter(Mandatory = $false)]
    [ValidateSet('quick', 'standard', 'endurance', 'destructive')]
    [string] $TestProfile = 'quick',

    # 기본값은 같은 저장소의 RAG/.env.local입니다. 외부 주소, Token, Qdrant, DB 설정을
    # 이 파일에서 자동으로 읽으므로 사용자가 Token이나 User/File IDX를 직접 입력하지 않습니다.
    [Parameter(Mandatory = $false)]
    [string] $RagEnvironmentPath,

    # 기본 실행에서는 사용하지 않습니다. 특정 User/File 범위를 재현해야 하는 경우에만
    # 수동 Target JSON을 지정합니다. 미지정 시 기존 Qdrant·DB·Snapshot에서 자동 선정합니다.
    [Parameter(Mandatory = $false)]
    [string] $TargetConfigPath,

    # auto 우선순위: 실행 중인 Qdrant → Local RAG DB → 최신 Qdrant Snapshot
    [Parameter(Mandatory = $false)]
    [ValidateSet('auto', 'qdrant', 'database', 'snapshot')]
    [string] $DataSource = 'auto',

    # Snapshot Source를 강제할 때만 지정합니다. 미지정 시 snapshots/, 저장소 Root와
    # RAG/ 아래에서 Collection 이름과 가장 가까운 최신 *.snapshot을 자동 탐색합니다.
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

    # 미지정 시 안전한 난수 Seed를 생성하고 결과에 기록합니다. 같은 선정을 재현할 때만 지정합니다.
    [Parameter(Mandatory = $false)]
    [Nullable[long]] $RandomSeed = $null,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 100000)]
    [int] $QdrantScanLimit = 4096,

    [Parameter(Mandatory = $false)]
    [string] $StressPlanPath,

    [Parameter(Mandatory = $false)]
    [string] $OutputRoot,

    [switch] $AllowDestructive,

    # 파괴적 Profile은 잘못된 Host를 공격하지 않도록 외부 RAG Host를 다시 확인합니다.
    [Parameter(Mandatory = $false)]
    [string] $ConfirmTargetHost,

    [switch] $AllowProductionTarget,

    # RAG 환경의 외부 주소가 HTTP이면 Test 환경에 한해 자동 적용됩니다. 수동 Target JSON에서
    # HTTP를 사용하는 경우에도 이 Switch를 사용할 수 있습니다.
    [switch] $AllowInsecureHttp,

    [switch] $AllowLoopbackTarget,
    [switch] $DisableTlsVerification,
    [switch] $SkipQualityGate,
    [switch] $SkipReadmeUpdate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Issue #159 외부 RAG 단계형 Stress Test
# ============================================================
#
# 1. Burst    - 외부 Endpoint에 순간 동시 요청
# 2. Interval - 일정 간격의 릴레이 요청
# 3. Batch    - 일정 간격의 그룹 Wave
# 4. Ramp     - 동시성을 높이며 정상 최대·최초 실패 탐색
# 5. Chaos    - 기준 TPS와 주기적 Spike 혼합
# 6. Soak     - Profile별 단·장시간 지속 부하
#
# Control Plane:
#   RAG/.env.local에서 외부 Origin과 RAG_INGEST_TOKEN을 자동 로드합니다.
#   Qdrant → DB → Snapshot 순서로 실제 Users_IDX/File_IDX를 읽기 전용 선정합니다.
#
# Load Plane:
#   모든 성능 요청은 JIPSA_RAG_EXTERNAL_BASE_URL의 외부 Search API로만 보냅니다.
#   Local RAG Process, 운영 Qdrant Collection, DB Row를 변경하거나 중단하지 않습니다.
#
# Windows 출력 인코딩:
#   Capacity Ladder처럼 이 Script가 다른 PowerShell 함수의 Pipeline 안에서 실행되더라도
#   Python stdout이 cp1252로 후퇴하지 않도록 PYTHONUTF8과 PYTHONIOENCODING을 현재 Process와
#   자식 Process에만 설정합니다. 종료 시 기존 값을 반드시 복원합니다.
# ============================================================

$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path -Path $PSScriptRoot -ChildPath '..'
    )
).Path
$RepositoryRoot = Split-Path -Path $ProjectRoot -Parent

if ([string]::IsNullOrWhiteSpace($RagEnvironmentPath)) {
    $RagEnvironmentPath = Join-Path `
        -Path $RepositoryRoot `
        -ChildPath 'RAG/.env.local'
}
if ([string]::IsNullOrWhiteSpace($StressPlanPath)) {
    $StressPlanPath = Join-Path `
        -Path $ProjectRoot `
        -ChildPath "configs/stress-plan-$TestProfile.json"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path -Path $ProjectRoot -ChildPath 'artifacts/external-stress'
}

function Write-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor DarkGray
    Write-Host "[$Message]" -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor DarkGray
}

function Assert-CommandAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    $Command = Get-Command -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $Command) {
        throw "필수 명령 '$Name'을 현재 PATH에서 찾을 수 없습니다."
    }
    Write-Host "$Name 실행 파일: $($Command.Source)"
}

function Assert-RequiredFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "필수 파일을 찾을 수 없습니다: $Path"
    }
}

function Get-RunDirectories {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Root
    )

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return @()
    }

    $Directories = Get-ChildItem `
        -LiteralPath $Root `
        -Directory `
        -ErrorAction SilentlyContinue
    if ($null -eq $Directories) {
        return @()
    }
    return @($Directories)
}

function Assert-GeneratedArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $DisplayName
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "성공 종료 후 필수 산출물이 없습니다: $DisplayName ($Path)"
    }

    $Item = Get-Item -LiteralPath $Path
    if ($Item.Length -le 0) {
        throw "성공 종료 후 필수 산출물이 비어 있습니다: $DisplayName ($Path)"
    }
}

function Assert-CampaignArtifacts {
    param(
        [Parameter(Mandatory = $true)]
        [string] $CampaignDirectory
    )

    # Python Runner가 정상 종료했다고 해도 파일 생성 도중의 회귀가 있으면 완료로 안내해서는
    # 안 됩니다. Campaign 요약 3개와 상세 Stress 입력·원시 결과·요약 보고서를 모두 확인합니다.
    $RequiredRelativePaths = @(
        'report.json',
        'report.md',
        'report.html',
        'external-stress/external_target.resolved.json',
        'external-stress/stress_plan.resolved.json',
        'external-stress/execution_command.txt',
        'external-stress/environment.json',
        'external-stress/requests.json',
        'external-stress/requests.csv',
        'external-stress/stage_summaries.json',
        'external-stress/stage_summaries.csv',
        'external-stress/capacity_boundaries.json',
        'external-stress/capacity_boundaries.csv',
        'external-stress/health_checks.json',
        'external-stress/progress.log',
        'external-stress/report.json',
        'external-stress/report.md',
        'external-stress/report.html'
    )

    foreach ($RelativePath in $RequiredRelativePaths) {
        $ArtifactPath = Join-Path -Path $CampaignDirectory -ChildPath $RelativePath
        Assert-GeneratedArtifact `
            -Path $ArtifactPath `
            -DisplayName $RelativePath
    }

    $CampaignReportPath = Join-Path -Path $CampaignDirectory -ChildPath 'report.json'
    $CampaignReport = Get-Content `
        -LiteralPath $CampaignReportPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json

    if ($null -ne $CampaignReport.execution_error_type) {
        throw (
            'Python Process는 종료되었지만 Campaign 보고서에 실행 오류가 기록되어 있습니다: ' +
            [string] $CampaignReport.execution_error_type
        )
    }
    if (-not [bool] $CampaignReport.preflight_health_passed) {
        throw 'Campaign 보고서의 사전 Health 검증이 통과하지 않았습니다.'
    }
    if (-not [bool] $CampaignReport.postflight_health_passed) {
        throw 'Campaign 보고서의 사후 Health 검증이 통과하지 않았습니다.'
    }
    if (@($CampaignReport.stage_summaries).Count -le 0) {
        throw 'Campaign 보고서에 완료된 Stress Stage가 없습니다.'
    }

    $DetailedReportPath = Join-Path `
        -Path $CampaignDirectory `
        -ChildPath 'external-stress/report.json'
    $DetailedReport = Get-Content `
        -LiteralPath $DetailedReportPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json

    if ($null -ne $DetailedReport.execution_error_type) {
        throw (
            '상세 Stress 보고서에 실행 오류가 기록되어 있습니다: ' +
            [string] $DetailedReport.execution_error_type
        )
    }
    if ([int] $DetailedReport.stage_count -le 0) {
        throw '상세 Stress 보고서의 Stage 수가 0입니다.'
    }
    if ([int] $DetailedReport.request_count -le 0) {
        throw '상세 Stress 보고서의 Request 수가 0입니다.'
    }

    return [PSCustomObject]@{
        RunId = [string] $CampaignReport.run_id
        CampaignDirectory = $CampaignDirectory
        CampaignMarkdown = Join-Path -Path $CampaignDirectory -ChildPath 'report.md'
        CampaignHtml = Join-Path -Path $CampaignDirectory -ChildPath 'report.html'
        DetailedMarkdown = Join-Path `
            -Path $CampaignDirectory `
            -ChildPath 'external-stress/report.md'
        DetailedHtml = Join-Path `
            -Path $CampaignDirectory `
            -ChildPath 'external-stress/report.html'
    }
}

function Restore-ProcessEnvironmentVariable {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $false)]
        [AllowNull()]
        [string] $OriginalValue
    )

    $EnvironmentPath = "Env:$Name"
    if ($null -eq $OriginalValue) {
        Remove-Item -Path $EnvironmentPath -ErrorAction SilentlyContinue
    }
    else {
        Set-Item -Path $EnvironmentPath -Value $OriginalValue
    }
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $Name,

        [switch] $Optional
    )

    $EscapedName = [Regex]::Escape($Name)
    $Pattern = "^\s*(?:export\s+)?$EscapedName\s*="
    $MatchedLine = Get-Content -LiteralPath $Path -Encoding UTF8 |
        Where-Object { $_ -match $Pattern } |
        Select-Object -Last 1

    if ($null -eq $MatchedLine) {
        if ($Optional) {
            return $null
        }
        throw "RAG 환경 변수 '$Name'을 찾을 수 없습니다: $Path"
    }

    $Value = ($MatchedLine -split '=', 2)[1].Trim()
    if ($Value.Length -ge 2) {
        $First = $Value.Substring(0, 1)
        $Last = $Value.Substring($Value.Length - 1, 1)
        if (($First -eq '"' -and $Last -eq '"') -or ($First -eq "'" -and $Last -eq "'")) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }
        elseif ($Value -match '\s+#') {
            $Value = ($Value -split '\s+#', 2)[0].Trim()
        }
    }

    if (-not $Optional -and [string]::IsNullOrWhiteSpace($Value)) {
        throw "RAG 환경 변수 '$Name' 값이 비어 있습니다: $Path"
    }
    return $Value
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(Mandatory = $true)]
        [string[]] $ArgumentList,

        [Parameter(Mandatory = $true)]
        [string] $FailureMessage
    )

    $PreviousPreference = $ErrorActionPreference
    $ExitCode = $null
    try {
        # uv는 정상 정보도 stderr로 출력할 수 있으므로 PowerShell ErrorRecord 개수 대신
        # Native Process 종료 코드만으로 성공 여부를 판정합니다.
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 0
        & $FilePath @ArgumentList
        $ExitCode = $global:LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousPreference
    }

    if ($null -eq $ExitCode) {
        throw "$FailureMessage 외부 프로그램 종료 코드를 확인할 수 없습니다."
    }
    if ($ExitCode -ne 0) {
        throw "$FailureMessage 종료 코드: $ExitCode"
    }
}

$ResolvedRagEnvironmentPath = (
    Resolve-Path -LiteralPath $RagEnvironmentPath
).Path
$ResolvedStressPlanPath = (
    Resolve-Path -LiteralPath $StressPlanPath
).Path
$ResolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$ResolvedTargetConfigPath = $null
$ResolvedSnapshotPath = $null

if (-not [string]::IsNullOrWhiteSpace($TargetConfigPath)) {
    $ResolvedTargetConfigPath = (
        Resolve-Path -LiteralPath $TargetConfigPath
    ).Path
}
if (-not [string]::IsNullOrWhiteSpace($SnapshotPath)) {
    $ResolvedSnapshotPath = (
        Resolve-Path -LiteralPath $SnapshotPath
    ).Path
}

$OriginalOutputEncoding = $OutputEncoding
$OriginalConsoleInputEncoding = [Console]::InputEncoding
$OriginalConsoleOutputEncoding = [Console]::OutputEncoding

# 자동으로 주입할 Process 환경 변수의 기존 값을 보존합니다. 스크립트 종료 시 정확히
# 원상 복구하여 같은 PowerShell 창에서 실행되는 다른 작업에 테스트 설정이 남지 않게 합니다.
$OriginalPerformanceToken = $env:JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN
$OriginalPerformanceTarget = $env:JIPSA_RAG_PERFORMANCE_TARGET_BASE_URL
$OriginalPerformanceQdrantUrl = $env:JIPSA_RAG_PERFORMANCE_QDRANT_URL
$OriginalPerformanceQdrantCollection = $env:JIPSA_RAG_PERFORMANCE_QDRANT_COLLECTION
$OriginalPythonUtf8 = $env:PYTHONUTF8
$OriginalPythonIoEncoding = $env:PYTHONIOENCODING

$Utf8Encoding = [System.Text.UTF8Encoding]::new($false)
$LocationPushed = $false

try {
    [Console]::InputEncoding = $Utf8Encoding
    [Console]::OutputEncoding = $Utf8Encoding
    $OutputEncoding = $Utf8Encoding

    # Console Encoding만 바꾸면 Python stdout이 PowerShell Pipeline 또는 파일로 Redirect될 때
    # Windows ANSI Code Page(cp1252 등)를 다시 선택할 수 있습니다. 두 환경 변수를 함께 지정해
    # 직접 실행과 Capacity Ladder 중첩 실행에서 동일하게 UTF-8을 사용하도록 강제합니다.
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    Write-Step -Message '필수 명령과 파일 확인'
    foreach ($CommandName in @('uv', 'git')) {
        Assert-CommandAvailable -Name $CommandName
    }

    foreach ($RequiredPath in @(
        (Join-Path -Path $ProjectRoot -ChildPath 'pyproject.toml'),
        (Join-Path -Path $ProjectRoot -ChildPath 'README.md'),
        (Join-Path -Path $ProjectRoot -ChildPath 'README.html'),
        $ResolvedRagEnvironmentPath,
        $ResolvedStressPlanPath
    )) {
        Assert-RequiredFile -Path $RequiredPath
    }
    if ($null -ne $ResolvedTargetConfigPath) {
        Assert-RequiredFile -Path $ResolvedTargetConfigPath
    }

    Write-Step -Message 'RAG 환경 자동 로드'
    $ExternalBaseUrl = Get-DotEnvValue `
        -Path $ResolvedRagEnvironmentPath `
        -Name 'JIPSA_RAG_EXTERNAL_BASE_URL'
    $ApiPrefix = Get-DotEnvValue `
        -Path $ResolvedRagEnvironmentPath `
        -Name 'JIPSA_RAG_API_V1_PREFIX'
    $IngestToken = Get-DotEnvValue `
        -Path $ResolvedRagEnvironmentPath `
        -Name 'RAG_INGEST_TOKEN'
    $QdrantUrl = Get-DotEnvValue `
        -Path $ResolvedRagEnvironmentPath `
        -Name 'JIPSA_RAG_QDRANT_URL'
    $QdrantCollection = Get-DotEnvValue `
        -Path $ResolvedRagEnvironmentPath `
        -Name 'JIPSA_RAG_QDRANT_COLLECTION'

    # Token과 Control Plane 주소는 현재 PowerShell Process와 자식 Process에만 설정합니다.
    # 원본 .env.local, README, JSON 보고서와 실행 명령 문자열에는 비밀값을 기록하지 않습니다.
    $env:JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN = $IngestToken
    $env:JIPSA_RAG_PERFORMANCE_TARGET_BASE_URL = $ExternalBaseUrl
    $env:JIPSA_RAG_PERFORMANCE_QDRANT_URL = $QdrantUrl
    $env:JIPSA_RAG_PERFORMANCE_QDRANT_COLLECTION = $QdrantCollection

    if ($null -ne $ResolvedTargetConfigPath) {
        $TargetConfig = Get-Content `
            -LiteralPath $ResolvedTargetConfigPath `
            -Raw `
            -Encoding UTF8 |
            ConvertFrom-Json
        $TargetUri = [Uri] $TargetConfig.target_base_url
        $TargetEnvironment = ([string] $TargetConfig.target_environment).ToLowerInvariant()
    }
    else {
        $TargetUri = [Uri] $ExternalBaseUrl
        $TargetEnvironmentValue = Get-DotEnvValue `
            -Path $ResolvedRagEnvironmentPath `
            -Name 'JIPSA_RAG_APP_ENV' `
            -Optional
        if ([string]::IsNullOrWhiteSpace($TargetEnvironmentValue)) {
            $TargetEnvironment = 'test'
        }
        else {
            $TargetEnvironment = $TargetEnvironmentValue.ToLowerInvariant()
        }
    }

    $TargetHost = $TargetUri.DnsSafeHost.ToLowerInvariant()
    $EffectiveAllowInsecureHttp = [bool] $AllowInsecureHttp
    if ($TargetUri.Scheme -eq 'http') {
        if ($TargetEnvironment -eq 'test') {
            # 제공된 RAG 환경처럼 Test Origin이 HTTP인 경우에는 사용자가 같은 승인 Switch를
            # 반복 입력하지 않도록 자동 허용하되, Token이 평문 전송된다는 경고는 남깁니다.
            $EffectiveAllowInsecureHttp = $true
            Write-Warning (
                'JIPSA_RAG_EXTERNAL_BASE_URL이 HTTP입니다. Test 환경이므로 ' +
                'AllowInsecureHttp를 자동 적용합니다. HTTPS 전환을 권장합니다.'
            )
        }
        elseif (-not $AllowInsecureHttp) {
            throw (
                'Staging 또는 Production HTTP Origin은 자동 허용하지 않습니다. ' +
                '승인된 환경에서만 -AllowInsecureHttp를 명시하세요.'
            )
        }
        else {
            Write-Warning (
                '승인된 비 Test HTTP Origin에 AllowInsecureHttp가 적용되었습니다. ' +
                'Token과 요청이 암호화되지 않습니다.'
            )
        }
    }

    $StressPlan = Get-Content `
        -LiteralPath $ResolvedStressPlanPath `
        -Raw `
        -Encoding UTF8 |
        ConvertFrom-Json
    if ([bool] $StressPlan.destructive -and -not $AllowDestructive) {
        throw (
            '선택한 Plan은 destructive=true입니다. 외부 Endpoint에 높은 동시성과 Spike를 ' +
            '전송하려면 -AllowDestructive를 명시해야 합니다.'
        )
    }
    if ([bool] $StressPlan.destructive) {
        $ConfirmedHost = if ([string]::IsNullOrWhiteSpace($ConfirmTargetHost)) {
            ''
        }
        else {
            $ConfirmTargetHost.Trim().ToLowerInvariant()
        }
        if ($ConfirmedHost -ne $TargetHost) {
            throw (
                '파괴적 Profile의 Host 확인값이 일치하지 않습니다. ' +
                "-ConfirmTargetHost '$TargetHost'를 정확히 지정하세요."
            )
        }
    }
    if (
        [bool] $StressPlan.destructive -and
        $TargetEnvironment -eq 'production' -and
        -not $AllowProductionTarget
    ) {
        throw (
            'Production 외부 Endpoint에 대한 파괴적 Profile은 기본 차단됩니다. ' +
            '정식 승인된 실행에서만 -AllowProductionTarget을 추가하세요.'
        )
    }

    Write-Host "성능 측정 프로그램: $ProjectRoot"
    Write-Host '실행 방식: 외부 HTTP Black-box'
    Write-Host "외부 Target Origin: $($TargetUri.Scheme)://$($TargetUri.Authority)"
    Write-Host "API Prefix: $ApiPrefix"
    Write-Host "Target 환경: $TargetEnvironment"
    Write-Host "Test Profile: $TestProfile"
    Write-Host "데이터 선정 Source: $DataSource"
    Write-Host "Qdrant Collection: $QdrantCollection"
    Write-Host "선정 File 수: 최대 $FilesPerUser"
    Write-Host "Query 수: $QueryCount"
    Write-Host "Stress Plan: $ResolvedStressPlanPath"
    Write-Host "RAG 환경 파일: $ResolvedRagEnvironmentPath"
    Write-Host "결과 Root: $ResolvedOutputRoot"
    Write-Host 'Token 자동 로드: True'
    Write-Host 'Python UTF-8 강제: True'
    Write-Host 'Local RAG Process 제어: 하지 않음'
    Write-Host '운영 Qdrant·DB 변경: 하지 않음'

    Push-Location -LiteralPath $ProjectRoot
    $LocationPushed = $true

    Write-Step -Message '독립 프로그램 의존성 동기화'
    $LockFile = Join-Path -Path $ProjectRoot -ChildPath 'uv.lock'
    $SyncArguments = if (Test-Path -LiteralPath $LockFile -PathType Leaf) {
        @('sync', '--frozen')
    }
    else {
        @('sync')
    }
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList $SyncArguments `
        -FailureMessage 'RAG-Performance uv sync 실패.'

    if (-not $SkipQualityGate) {
        Write-Step -Message 'Ruff Format 검사'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('run', 'ruff', 'format', '--check', 'src', 'tests') `
            -FailureMessage 'Ruff format 검사 실패.'

        Write-Step -Message 'Ruff Lint 검사'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('run', 'ruff', 'check', 'src', 'tests') `
            -FailureMessage 'Ruff lint 검사 실패.'

        Write-Step -Message 'Mypy Strict 검사'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('run', 'mypy', 'src', 'tests') `
            -FailureMessage 'Mypy 검사 실패.'

        Write-Step -Message '단위 테스트'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('run', 'pytest') `
            -FailureMessage 'Pytest 실패.'
    }

    Write-Step -Message "외부 단계형 Stress Campaign: $TestProfile"
    $HumanReadableCommand = (
        '.\scripts\run-staged-stress-test.ps1 ' +
        "-TestProfile '$TestProfile' " +
        "-DataSource '$DataSource' " +
        "-RagEnvironmentPath '$ResolvedRagEnvironmentPath'"
    )
    if ($null -ne $ResolvedTargetConfigPath) {
        $HumanReadableCommand += " -TargetConfigPath '$ResolvedTargetConfigPath'"
    }
    if ($null -ne $ResolvedSnapshotPath) {
        $HumanReadableCommand += " -SnapshotPath '$ResolvedSnapshotPath'"
    }
    if ($AllowDestructive) {
        $HumanReadableCommand += ' -AllowDestructive'
        $HumanReadableCommand += " -ConfirmTargetHost '$TargetHost'"
    }
    if ($AllowProductionTarget) {
        $HumanReadableCommand += ' -AllowProductionTarget'
    }
    if ($DisableTlsVerification) {
        $HumanReadableCommand += ' -DisableTlsVerification'
    }
    if ($SkipQualityGate) {
        $HumanReadableCommand += ' -SkipQualityGate'
    }
    if ($SkipReadmeUpdate) {
        $HumanReadableCommand += ' -SkipReadmeUpdate'
    }

    $CampaignArguments = @(
        'run',
        'jipsa-rag-stress',
        '--rag-env-file',
        $ResolvedRagEnvironmentPath,
        '--data-source',
        $DataSource,
        '--files-per-user',
        [string] $FilesPerUser,
        '--query-count',
        [string] $QueryCount,
        '--qdrant-scan-limit',
        [string] $QdrantScanLimit,
        '--stress-plan',
        $ResolvedStressPlanPath,
        '--output-root',
        $ResolvedOutputRoot,
        '--execution-command',
        $HumanReadableCommand,
        '--readme-markdown',
        (Join-Path -Path $ProjectRoot -ChildPath 'README.md'),
        '--readme-html',
        (Join-Path -Path $ProjectRoot -ChildPath 'README.html')
    )
    if ($null -ne $ResolvedTargetConfigPath) {
        $CampaignArguments += '--target-config'
        $CampaignArguments += $ResolvedTargetConfigPath
    }
    if ($null -ne $ResolvedSnapshotPath) {
        $CampaignArguments += '--snapshot-path'
        $CampaignArguments += $ResolvedSnapshotPath
    }
    foreach ($SearchRoot in $SnapshotSearchRoot) {
        if (-not [string]::IsNullOrWhiteSpace($SearchRoot)) {
            $CampaignArguments += '--snapshot-search-root'
            $CampaignArguments += [System.IO.Path]::GetFullPath($SearchRoot)
        }
    }

    # Nullable[long] 매개변수는 실제 값이 전달되면 PowerShell 바인더가 System.Int64 자체로
    # 언박싱할 수 있습니다. 따라서 Nullable 전용 Value 속성에 접근하지 않고, 명시적으로
    # 전달된 값을 Int64로 정규화한 뒤 문자열 인자로 변환합니다.
    $HasExplicitRandomSeed = (
        $PSBoundParameters.ContainsKey('RandomSeed') -and
        $null -ne $RandomSeed
    )
    if ($HasExplicitRandomSeed) {
        $CampaignArguments += '--random-seed'
        $CampaignArguments += [string] ([long] $RandomSeed)
    }
    if ($AllowDestructive) {
        $CampaignArguments += '--allow-destructive'
        $CampaignArguments += '--confirm-target-host'
        $CampaignArguments += $TargetHost
    }
    if ($AllowProductionTarget) {
        $CampaignArguments += '--allow-production-target'
    }
    if ($EffectiveAllowInsecureHttp) {
        $CampaignArguments += '--allow-insecure-http'
    }
    if ($AllowLoopbackTarget) {
        $CampaignArguments += '--allow-loopback-target'
    }
    if ($DisableTlsVerification) {
        $CampaignArguments += '--disable-tls-verification'
    }
    if ($SkipQualityGate) {
        $CampaignArguments += '--quality-gate-skipped'
    }
    if ($SkipReadmeUpdate) {
        $CampaignArguments += '--skip-readme-update'
    }

    # 새 Run 폴더를 정확히 식별하기 위해 실행 전 전체 경로를 저장합니다. 성공 후 단순히
    # LastWriteTime이 가장 최신인 폴더를 선택하면 이전 실패 폴더나 동시에 생성된 폴더를 잘못
    # 채택할 수 있으므로, 이번 실행에서 새로 생긴 폴더만 허용합니다.
    [System.IO.Directory]::CreateDirectory($ResolvedOutputRoot) | Out-Null
    $BeforeRunPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Directory in Get-RunDirectories -Root $ResolvedOutputRoot) {
        $BeforeRunPaths.Add($Directory.FullName) | Out-Null
    }

    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList $CampaignArguments `
        -FailureMessage '외부 단계형 Stress Campaign 실패.'

    $NewRunDirectories = @(Get-RunDirectories -Root $ResolvedOutputRoot) |
        Where-Object { -not $BeforeRunPaths.Contains($_.FullName) } |
        Sort-Object LastWriteTimeUtc -Descending
    $CampaignDirectory = $NewRunDirectories | Select-Object -First 1
    if ($null -eq $CampaignDirectory) {
        throw "정상 종료 후 새 Campaign 결과 폴더를 찾을 수 없습니다: $ResolvedOutputRoot"
    }

    Write-Step -Message '결과 파일 완전성 검증'
    $Artifacts = Assert-CampaignArtifacts -CampaignDirectory $CampaignDirectory.FullName

    Write-Step -Message '캠페인 완료'
    Write-Host "Run ID: $($Artifacts.RunId)" -ForegroundColor Green
    Write-Host "Campaign Markdown: $($Artifacts.CampaignMarkdown)" -ForegroundColor Green
    Write-Host "Campaign HTML: $($Artifacts.CampaignHtml)" -ForegroundColor Green
    Write-Host "상세 Stress Markdown: $($Artifacts.DetailedMarkdown)" -ForegroundColor Green
    Write-Host "상세 Stress HTML(표·그래프): $($Artifacts.DetailedHtml)" -ForegroundColor Green
    if (-not $SkipReadmeUpdate) {
        Write-Host 'README.md와 README.html의 마지막 검증 기록을 갱신했습니다.' -ForegroundColor Green
    }
}
finally {
    # 자동 주입한 환경 변수는 성공·실패·사용자 중단과 관계없이 원래 값으로 복원합니다.
    # 값 자체는 Console, 실행 명령, README 또는 결과 파일에 기록하지 않습니다.
    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN' `
        -OriginalValue $OriginalPerformanceToken
    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_PERFORMANCE_TARGET_BASE_URL' `
        -OriginalValue $OriginalPerformanceTarget
    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_PERFORMANCE_QDRANT_URL' `
        -OriginalValue $OriginalPerformanceQdrantUrl
    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_PERFORMANCE_QDRANT_COLLECTION' `
        -OriginalValue $OriginalPerformanceQdrantCollection
    Restore-ProcessEnvironmentVariable `
        -Name 'PYTHONUTF8' `
        -OriginalValue $OriginalPythonUtf8
    Restore-ProcessEnvironmentVariable `
        -Name 'PYTHONIOENCODING' `
        -OriginalValue $OriginalPythonIoEncoding

    if ($LocationPushed) {
        Pop-Location
    }
    $OutputEncoding = $OriginalOutputEncoding
    [Console]::InputEncoding = $OriginalConsoleInputEncoding
    [Console]::OutputEncoding = $OriginalConsoleOutputEncoding
}
