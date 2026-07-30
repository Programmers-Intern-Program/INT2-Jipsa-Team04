#Requires -Version 5.1

[CmdletBinding()]
param(
    # 기본 저장소 구조는 RAG와 RAG-Performance가 같은 깊이에 있는 형태입니다.
    [Parameter(Mandatory = $false)]
    [string] $RagRoot,

    # 기존 형식·OCR·동시성 기준선 측정 계획입니다.
    [Parameter(Mandatory = $false)]
    [string] $BenchmarkPlanPath,

    # 장시간 반복, 실패 Probe, 격리·정리 확인 계획입니다.
    [Parameter(Mandatory = $false)]
    [string] $ReliabilityPlanPath,

    # 캠페인 결과를 저장할 상위 디렉터리입니다.
    [Parameter(Mandatory = $false)]
    [string] $OutputRoot,

    # Claude lookup·synthesis 비용을 제외합니다. 신뢰성 Session은 검색 중심으로 항상 Claude를
    # 사용하지 않으며, 이 옵션은 앞단 기준선 실행에도 답변 API를 제외합니다.
    [switch] $DisableAnswers,

    # 기존 기준선 측정을 생략하고 장시간·실패·정리 검증만 수행합니다.
    [switch] $SkipBaseline,

    # 동일 Commit에서 정적 검사와 단위 테스트를 이미 통과한 경우에만 사용합니다.
    [switch] $SkipQualityGate,

    # 일반 Local RAG 8077과 분리된 성능 측정 전용 Port입니다.
    [ValidateRange(1, 65535)]
    [int] $TargetPort = 18077
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Issue #159 장시간 안정성·실패 경계 측정 실행기
# ============================================================
#
# 처리 순서:
# 1. 독립 RAG-Performance 품질 검사
# 2. 기존 형식·OCR·동시성 기준선 실행(선택)
# 3. 1시간 기본 Soak Test와 Window별 자원·지연 통계 수집
# 4. 안전한 Timeout·MemoryError 기록 Probe
# 5. TEI·Qdrant 중단·복구 실패 조건 기록
# 6. 전용 RAG Target 비정상 종료
# 7. Cleanup-only Process로 DB Row·Qdrant Collection·Temp 재검증
# 8. 캠페인 시작 전 Qdrant·TEI 상태 복원
# 9. RAG Source와 운영 제한 설정이 바뀌지 않았는지 Hash·Git 범위 검증
#
# 이 스크립트와 Python 실행기는 측정만 수행합니다. RAG 성능 최적화, Timeout·top_k·청킹
# 크기·OCR 동시성·Embedding Batch·Qdrant 제한값 변경을 수행하지 않습니다.
# ============================================================

$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path -Path $PSScriptRoot -ChildPath '..'
    )
).Path

if ([string]::IsNullOrWhiteSpace($RagRoot)) {
    $RagRoot = Join-Path -Path (Split-Path -Parent $ProjectRoot) -ChildPath 'RAG'
}
if ([string]::IsNullOrWhiteSpace($BenchmarkPlanPath)) {
    $BenchmarkPlanPath = Join-Path -Path $ProjectRoot -ChildPath 'configs/benchmark-plan.json'
}
if ([string]::IsNullOrWhiteSpace($ReliabilityPlanPath)) {
    $ReliabilityPlanPath = Join-Path -Path $ProjectRoot -ChildPath 'configs/reliability-plan.json'
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path -Path $ProjectRoot -ChildPath 'artifacts/reliability'
}

$ResolvedRagRoot = (Resolve-Path -LiteralPath $RagRoot).Path
$ResolvedBenchmarkPlanPath = (Resolve-Path -LiteralPath $BenchmarkPlanPath).Path
$ResolvedReliabilityPlanPath = (Resolve-Path -LiteralPath $ReliabilityPlanPath).Path
$ResolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)

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
        # uv·Docker는 정상 정보도 stderr로 출력할 수 있으므로 실제 성공 여부는 종료 코드로
        # 판정합니다. 비밀 환경 변수나 명령 인자를 별도 로그 파일에 복사하지 않습니다.
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

$OriginalOutputEncoding = $OutputEncoding
$OriginalConsoleInputEncoding = [Console]::InputEncoding
$OriginalConsoleOutputEncoding = [Console]::OutputEncoding
$Utf8Encoding = [System.Text.UTF8Encoding]::new($false)
$LocationPushed = $false

try {
    [Console]::InputEncoding = $Utf8Encoding
    [Console]::OutputEncoding = $Utf8Encoding
    $OutputEncoding = $Utf8Encoding

    Write-Step -Message '필수 명령과 파일 확인'
    foreach ($CommandName in @('uv', 'git', 'docker', 'nvidia-smi')) {
        Assert-CommandAvailable -Name $CommandName
    }

    foreach ($RequiredPath in @(
        (Join-Path -Path $ProjectRoot -ChildPath 'pyproject.toml'),
        $ResolvedBenchmarkPlanPath,
        $ResolvedReliabilityPlanPath,
        (Join-Path -Path $ResolvedRagRoot -ChildPath 'pyproject.toml'),
        (Join-Path -Path $ResolvedRagRoot -ChildPath '.env.local'),
        (Join-Path -Path $ResolvedRagRoot -ChildPath 'infra/qdrant/compose.yaml')
    )) {
        Assert-RequiredFile -Path $RequiredPath
    }

    Write-Host "성능 측정 프로그램: $ProjectRoot"
    Write-Host "측정 대상 RAG: $ResolvedRagRoot"
    Write-Host "기준선 계획: $ResolvedBenchmarkPlanPath"
    Write-Host "신뢰성 계획: $ResolvedReliabilityPlanPath"
    Write-Host "결과 Root: $ResolvedOutputRoot"
    Write-Host "전용 Target Port: $TargetPort"

    Push-Location -LiteralPath $ProjectRoot
    $LocationPushed = $true

    Write-Step -Message '독립 프로그램 의존성 동기화'
    $LockFile = Join-Path -Path $ProjectRoot -ChildPath 'uv.lock'
    if (Test-Path -LiteralPath $LockFile -PathType Leaf) {
        $SyncArguments = @('sync', '--frozen')
    }
    else {
        $SyncArguments = @('sync')
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

    Write-Step -Message '장시간 안정성·실패 경계 캠페인'
    $HumanReadableCommand = (
        ".\scripts\run-reliability-benchmark.ps1 " +
        "-RagRoot '$ResolvedRagRoot' " +
        "-BenchmarkPlanPath '$ResolvedBenchmarkPlanPath' " +
        "-ReliabilityPlanPath '$ResolvedReliabilityPlanPath' " +
        "-OutputRoot '$ResolvedOutputRoot' " +
        "-TargetPort $TargetPort"
    )
    if ($DisableAnswers) {
        $HumanReadableCommand += ' -DisableAnswers'
    }
    if ($SkipBaseline) {
        $HumanReadableCommand += ' -SkipBaseline'
    }
    if ($SkipQualityGate) {
        $HumanReadableCommand += ' -SkipQualityGate'
    }

    $CampaignArguments = @(
        'run',
        'jipsa-rag-reliability',
        '--rag-root',
        $ResolvedRagRoot,
        '--benchmark-plan',
        $ResolvedBenchmarkPlanPath,
        '--reliability-plan',
        $ResolvedReliabilityPlanPath,
        '--output-root',
        $ResolvedOutputRoot,
        '--target-port',
        [string] $TargetPort,
        '--execution-command',
        $HumanReadableCommand
    )
    if ($DisableAnswers) {
        $CampaignArguments += '--disable-answers'
    }
    if ($SkipBaseline) {
        $CampaignArguments += '--skip-baseline'
    }

    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList $CampaignArguments `
        -FailureMessage 'Issue #159 신뢰성 캠페인 실패.'

    Write-Step -Message '캠페인 완료'
    Write-Host "결과는 다음 경로 아래에 생성되었습니다: $ResolvedOutputRoot" -ForegroundColor Green
}
finally {
    if ($LocationPushed) {
        Pop-Location
    }
    $OutputEncoding = $OriginalOutputEncoding
    [Console]::InputEncoding = $OriginalConsoleInputEncoding
    [Console]::OutputEncoding = $OriginalConsoleOutputEncoding
}
