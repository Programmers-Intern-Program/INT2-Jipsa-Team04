#Requires -Version 5.1

[CmdletBinding()]
param(
    # 기본 구조는 저장소 Root 아래의 RAG와 RAG-Performance가 같은 깊이에 있는 형태입니다.
    # 다른 위치에서 실행할 때만 측정 대상 RAG 경로를 명시합니다.
    [Parameter(Mandatory = $false)]
    [string] $RagRoot,

    # 기본 측정 계획 대신 별도 JSON 계획을 사용할 때 지정합니다.
    [Parameter(Mandatory = $false)]
    [string] $PlanPath,

    # 결과를 저장할 상위 디렉터리입니다. 생략하면 RAG-Performance/artifacts를 사용합니다.
    [Parameter(Mandatory = $false)]
    [string] $OutputRoot,

    # Claude API 비용 없이 인제스트와 검색까지만 측정합니다.
    [switch] $DisableAnswers,

    # 실패 분석을 위해 전용 Local RAG DB·Qdrant 데이터를 남깁니다.
    # 운영 데이터와 구분되는 Issue #159 전용 사용자·File_IDX만 사용합니다.
    [switch] $KeepTestData,

    # 측정 종료 후 Qdrant와 TEI 컨테이너를 정지하지 않습니다.
    [switch] $KeepInfrastructureRunning,

    # 이미 실행 중인 Qdrant와 TEI를 정지·재시작하지 않습니다.
    # 이 옵션을 사용하면 인프라 Cold Start 결과는 측정 대상에서 제외됩니다.
    [switch] $PreserveRunningInfrastructure,

    # 같은 Commit에서 독립 프로그램의 품질 검증이 이미 성공한 경우에만 사용합니다.
    [switch] $SkipQualityGate,

    # 일반 Local RAG 포트 8077과 분리된 성능 측정 전용 포트입니다.
    [ValidateRange(1, 65535)]
    [int] $TargetPort = 18077
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Jipsa RAG 독립 성능 측정 프로그램 실행기
# ============================================================
#
# 이 스크립트는 RAG 운영 Source Tree를 수정하지 않습니다.
# RAG-Performance 자체 uv 환경에서 부하 생성기와 자원 수집기를 실행하고,
# 측정 대상 FastAPI는 RAG의 uv 환경에서 별도 Process로 실행합니다.
#
# 처리 순서:
# 1. 경로와 필수 명령 확인
# 2. RAG-Performance 의존성 동기화
# 3. Ruff, Mypy, Pytest 품질 검증
# 4. 합성 PDF/DOCX/PPTX/XLSX/TXT/OCR Fixture 생성
# 5. Qdrant·CUDA TEI Cold Start 또는 기존 실행 상태 사용
# 6. 별도 Uvicorn RAG Target Process 시작
# 7. CPU, RAM, GPU, VRAM, Disk I/O, Network I/O 수집
# 8. 인제스트·검색·lookup·synthesis 부하와 포화 후보 측정
# 9. JSON, CSV, Markdown 결과 생성
# 10. 전용 데이터와 이 스크립트가 시작한 인프라 정리
#
# Windows PowerShell 5.1에서 한글 주석과 문자열을 안전하게 처리하려면
# 이 파일을 UTF-8 with BOM 형식으로 유지해야 합니다.
# ============================================================

$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path -Path $PSScriptRoot -ChildPath '..'
    )
).Path

if ([string]::IsNullOrWhiteSpace($RagRoot)) {
    $RagRoot = Join-Path -Path (Split-Path -Parent $ProjectRoot) -ChildPath 'RAG'
}
if ([string]::IsNullOrWhiteSpace($PlanPath)) {
    $PlanPath = Join-Path -Path $ProjectRoot -ChildPath 'configs/benchmark-plan.json'
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path -Path $ProjectRoot -ChildPath 'artifacts'
}

$ResolvedRagRoot = (Resolve-Path -LiteralPath $RagRoot).Path
$ResolvedPlanPath = (Resolve-Path -LiteralPath $PlanPath).Path
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
        # uv와 Docker는 정상 진행 정보도 stderr에 출력할 수 있습니다. 외부 명령 실행
        # 동안만 Continue를 사용하고 실제 성공 여부는 종료 코드로 판단합니다.
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
        $ResolvedPlanPath,
        (Join-Path -Path $ResolvedRagRoot -ChildPath 'pyproject.toml'),
        (Join-Path -Path $ResolvedRagRoot -ChildPath '.env.local'),
        (Join-Path -Path $ResolvedRagRoot -ChildPath 'infra/qdrant/compose.yaml')
    )) {
        Assert-RequiredFile -Path $RequiredPath
    }

    Write-Host "성능 측정 프로그램: $ProjectRoot"
    Write-Host "측정 대상 RAG: $ResolvedRagRoot"
    Write-Host "측정 계획: $ResolvedPlanPath"
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
        # 최초 배치에는 Lock 파일이 없을 수 있습니다. 이 경우 pyproject.toml을 기준으로
        # 한 번 해석하여 uv.lock을 생성하고, 이후 실행부터 --frozen을 사용합니다.
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
            -FailureMessage 'RAG-Performance Ruff format 검사 실패.'

        Write-Step -Message 'Ruff Lint 검사'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('run', 'ruff', 'check', 'src', 'tests') `
            -FailureMessage 'RAG-Performance Ruff lint 검사 실패.'

        Write-Step -Message 'Mypy Strict 검사'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('run', 'mypy', 'src', 'tests') `
            -FailureMessage 'RAG-Performance Mypy 검사 실패.'

        Write-Step -Message '단위 테스트'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('run', 'pytest') `
            -FailureMessage 'RAG-Performance 단위 테스트 실패.'
    }

    Write-Step -Message 'Local RAG 자원 사용량 및 처리 한계 측정'
    $BenchmarkArguments = @(
        'run',
        'jipsa-rag-benchmark',
        '--rag-root',
        $ResolvedRagRoot,
        '--plan',
        $ResolvedPlanPath,
        '--output-root',
        $ResolvedOutputRoot,
        '--target-port',
        [string] $TargetPort
    )

    if ($DisableAnswers) {
        $BenchmarkArguments += '--disable-answers'
    }
    if ($KeepTestData) {
        $BenchmarkArguments += '--keep-test-data'
    }
    if ($KeepInfrastructureRunning) {
        $BenchmarkArguments += '--keep-infrastructure-running'
    }
    if ($PreserveRunningInfrastructure) {
        $BenchmarkArguments += '--preserve-running-infrastructure'
    }

    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList $BenchmarkArguments `
        -FailureMessage 'Local RAG 성능 측정 실패.'

    Write-Step -Message '측정 완료'
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
