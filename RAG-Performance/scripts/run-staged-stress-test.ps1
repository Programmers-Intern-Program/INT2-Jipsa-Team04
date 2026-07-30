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
        # uv는 정상 정보도 stderr로 출력할 수 있으므로 종료 코드로 성공을 판정합니다.
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

$Utf8Encoding = [System.Text.UTF8Encoding]::new($false)
$LocationPushed = $false

try {
    [Console]::InputEncoding = $Utf8Encoding
    [Console]::OutputEncoding = $Utf8Encoding
    $OutputEncoding = $Utf8Encoding

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
    Write-Host "실행 방식: 외부 HTTP Black-box"
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
        ".\scripts\run-staged-stress-test.ps1 " +
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
    # Nullable[long] 매개변수는 실제 값이 전달되면 PowerShell 바인더가
    # System.Int64 자체로 언박싱할 수 있다. 따라서 Nullable 전용 Value 속성에
    # 접근하지 않고, 명시적으로 전달된 값을 Int64로 정규화한 뒤 문자열 인자로
    # 변환한다.
    #
    # PSBoundParameters를 함께 확인하여 매개변수가 생략된 경우에는 Python Runner가
    # 자체 Seed를 생성하도록 --random-seed 인자를 전달하지 않는다.
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

    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList $CampaignArguments `
        -FailureMessage '외부 단계형 Stress Campaign 실패.'

    Write-Step -Message '캠페인 완료'
    Write-Host "결과는 다음 경로 아래에 생성되었습니다: $ResolvedOutputRoot" -ForegroundColor Green
    Write-Host '각 실행 폴더의 report.html을 열면 표와 그래프를 볼 수 있습니다.' -ForegroundColor Green
    if (-not $SkipReadmeUpdate) {
        Write-Host 'README.md와 README.html의 마지막 검증 기록을 갱신했습니다.' -ForegroundColor Green
    }
}
finally {
    # 자동 주입한 환경 변수는 성공·실패·사용자 중단과 관계없이 원래 값으로 복원합니다.
    # 값 자체는 Console, 실행 명령, README 또는 결과 파일에 기록하지 않습니다.
    if ($null -eq $OriginalPerformanceToken) {
        Remove-Item -Path 'Env:JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN' -ErrorAction SilentlyContinue
    }
    else {
        $env:JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN = $OriginalPerformanceToken
    }
    if ($null -eq $OriginalPerformanceTarget) {
        Remove-Item -Path 'Env:JIPSA_RAG_PERFORMANCE_TARGET_BASE_URL' -ErrorAction SilentlyContinue
    }
    else {
        $env:JIPSA_RAG_PERFORMANCE_TARGET_BASE_URL = $OriginalPerformanceTarget
    }
    if ($null -eq $OriginalPerformanceQdrantUrl) {
        Remove-Item -Path 'Env:JIPSA_RAG_PERFORMANCE_QDRANT_URL' -ErrorAction SilentlyContinue
    }
    else {
        $env:JIPSA_RAG_PERFORMANCE_QDRANT_URL = $OriginalPerformanceQdrantUrl
    }
    if ($null -eq $OriginalPerformanceQdrantCollection) {
        Remove-Item `
            -Path 'Env:JIPSA_RAG_PERFORMANCE_QDRANT_COLLECTION' `
            -ErrorAction SilentlyContinue
    }
    else {
        $env:JIPSA_RAG_PERFORMANCE_QDRANT_COLLECTION = `
            $OriginalPerformanceQdrantCollection
    }

    if ($LocationPushed) {
        Pop-Location
    }
    $OutputEncoding = $OriginalOutputEncoding
    [Console]::InputEncoding = $OriginalConsoleInputEncoding
    [Console]::OutputEncoding = $OriginalConsoleOutputEncoding
}
