#Requires -Version 5.1

[CmdletBinding()]
param(
    # Ruff, Mypy 및 전체 Pytest 선행 검사를 생략합니다.
    #
    # 이미 같은 코드 상태에서 verify-rag-quality.ps1이 통과한 경우에만
    # 제한적으로 사용합니다.
    [switch] $SkipQualityGate,

    # E2E 종료 후 이 스크립트가 시작한 Qdrant와 TEI를 유지합니다.
    #
    # 실패 원인 분석을 위해 컨테이너 로그나 상태를 계속 확인해야 할 때
    # 사용할 수 있습니다.
    [switch] $KeepInfrastructureRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Jipsa 실제 PDF RAG E2E 실행 스크립트
# ============================================================
#
# 실제 검증 범위:
#
# - 결정적으로 생성한 텍스트 레이어 PDF
# - 실제 PDF 다운로드 및 파싱
# - 실제 청킹
# - NVIDIA CUDA TEI 임베딩
# - Local RAG MySQL 또는 MariaDB
# - Qdrant VectorDB
# - 실제 Anthropic Claude API
# - 단일·복수 참조문서 답변
# - SOURCE-N과 응답 sources 일치
#
# Mock 범위:
#
# - AWS Backend manifest API
# - AWS Backend ingest-complete callback API
# - Presigned GET URL을 제공하는 S3 HTTP 경계
#
# 실제 Claude API 호출 비용이 발생합니다.
#
# 또한 E2E 전용 사용자 및 파일 범위의 Local RAG DB 행과 Qdrant Point를
# 테스트 시작 전과 종료 후 삭제합니다.
#
# 반드시 JIPSA_RAG_APP_ENV=test 환경에서만 실행합니다.
#
# 중요:
#
# Windows PowerShell 5.1에서 한글 주석과 출력 문자열을 안전하게 처리하려면
# 이 파일을 UTF-8 with BOM 형식으로 저장해야 합니다.
# ============================================================


# ============================================================
# 프로젝트 경로와 파일
# ============================================================

$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path `
            -Path $PSScriptRoot `
            -ChildPath '..'
    )
).Path

$QualityGateScript = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'scripts/verify-rag-quality.ps1'

$ComposeFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'infra/qdrant/compose.yaml'

$TestEnvironmentFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath '.env.test'

$DatabaseConnectionTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/integration/test_database_connection.py'

$RealE2eTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/e2e/test_real_pdf_rag_e2e.py'

# Docker Compose는 .env.test의 임베딩 모델 설정을 읽어
# 애플리케이션과 동일한 TEI 모델을 실행해야 합니다.
$ComposeBaseArguments = @(
    'compose',
    '--env-file',
    $TestEnvironmentFile,
    '--file',
    $ComposeFile
)

# Qdrant는 비교적 빠르게 시작되지만 Docker Desktop 복구 상황을 고려하여
# 최대 2분 동안 준비 상태를 기다립니다.
$QdrantStartupTimeoutSeconds = 120

# TEI 최초 실행에서는 모델 다운로드, CUDA 초기화 및 Warmup이 발생할 수
# 있으므로 최대 20분 동안 실제 /embed 성공을 기다립니다.
$EmbeddingStartupTimeoutSeconds = 1200


# ============================================================
# 환경 변수 원본 보관
# ============================================================

$ManagedEnvironmentNames = @(
    'JIPSA_RAG_APP_ENV',
    'JIPSA_RAG_RUN_E2E',
    'PYTHONUTF8',
    'PYTHONIOENCODING'
)

$OriginalEnvironment = @{}

foreach ($EnvironmentName in $ManagedEnvironmentNames) {
    $OriginalEnvironment[$EnvironmentName] = (
        [Environment]::GetEnvironmentVariable(
            $EnvironmentName,
            [EnvironmentVariableTarget]::Process
        )
    )
}

$OriginalOutputEncoding = $OutputEncoding
$OriginalConsoleInputEncoding = [Console]::InputEncoding
$OriginalConsoleOutputEncoding = [Console]::OutputEncoding

$Utf8Encoding = [System.Text.UTF8Encoding]::new($false)

$LocationPushed = $false

# 스크립트 실행 전 이미 실행 중이던 서비스는 종료하지 않습니다.
$RunningServicesBefore = @()

# 이 스크립트가 실제로 새로 실행한 서비스만 기록합니다.
$StartedServices = @()

# E2E 본문에서 발생한 최초 오류를 보관합니다.
#
# 정리 과정에서 추가 오류가 발생하더라도 최초 실패 원인을 덮어쓰지 않기
# 위해 사용합니다.
$ExecutionError = $null


# ============================================================
# 공통 출력 함수
# ============================================================

function Write-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    Write-Host ''
    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray

    Write-Host "[$Message]" -ForegroundColor Cyan

    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray
}


# ============================================================
# 필수 명령 확인
# ============================================================

function Assert-CommandAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string] $CommandName,

        [Parameter(Mandatory = $true)]
        [string] $InstallMessage
    )

    $Command = Get-Command `
        -Name $CommandName `
        -ErrorAction SilentlyContinue

    if ($null -eq $Command) {
        throw @"
필수 명령 '$CommandName'을 찾을 수 없습니다.

$InstallMessage
"@
    }

    Write-Host "$CommandName 실행 파일: $($Command.Source)"
}


# ============================================================
# Native Command 실행 함수
# ============================================================

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(Mandatory = $true)]
        [string[]] $ArgumentList,

        [Parameter(Mandatory = $true)]
        [string] $FailureMessage
    )

    $PreviousErrorActionPreference = $ErrorActionPreference
    $ExitCode = $null

    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 0

        & $FilePath @ArgumentList

        $ExitCode = $global:LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($null -eq $ExitCode) {
        throw (
            "$FailureMessage " +
            '외부 프로그램의 종료 코드를 확인할 수 없습니다.'
        )
    }

    if ($ExitCode -ne 0) {
        throw "$FailureMessage 종료 코드: $ExitCode"
    }
}


function Invoke-NativeCommandForOutput {
    <#
    .SYNOPSIS
        외부 명령의 출력값을 반환하고 종료 코드를 검사합니다.

    .DESCRIPTION
        Docker Compose의 현재 실행 서비스 목록처럼 후속 제어 흐름에 필요한
        출력값을 가져올 때 사용합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(Mandatory = $true)]
        [string[]] $ArgumentList,

        [Parameter(Mandatory = $true)]
        [string] $FailureMessage
    )

    $PreviousErrorActionPreference = $ErrorActionPreference
    $Output = @()
    $ExitCode = $null

    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 0

        $Output = @(
            & $FilePath @ArgumentList 2>&1
        )

        $ExitCode = $global:LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($null -eq $ExitCode) {
        throw (
            "$FailureMessage " +
            '외부 프로그램의 종료 코드를 확인할 수 없습니다.'
        )
    }

    if ($ExitCode -ne 0) {
        $OutputText = (
            $Output |
                ForEach-Object {
                    [string] $_
                }
        ) -join [Environment]::NewLine

        if (-not [string]::IsNullOrWhiteSpace($OutputText)) {
            Write-Host ''
            Write-Host '[외부 명령 오류 출력]' -ForegroundColor DarkYellow
            Write-Host $OutputText -ForegroundColor DarkYellow
        }

        throw "$FailureMessage 종료 코드: $ExitCode"
    }

    return $Output
}


# ============================================================
# 필수 파일 확인
# ============================================================

function Assert-RequiredFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description 파일을 찾을 수 없습니다: $Path"
    }
}


# ============================================================
# Docker Compose 실행 서비스 조회
# ============================================================

function Get-RunningComposeServices {
    $Output = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList (
                $ComposeBaseArguments + @(
                    'ps',
                    '--services',
                    '--status',
                    'running'
                )
            ) `
            -FailureMessage 'Docker Compose 실행 서비스 조회에 실패했습니다.'
    )

    return @(
        $Output |
            ForEach-Object {
                ([string] $_).Trim()
            } |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_)
            }
    )
}


# ============================================================
# Qdrant 준비 상태 대기
# ============================================================

function Wait-QdrantReady {
    param(
        [Parameter(Mandatory = $true)]
        [int] $TimeoutSeconds
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $ReadyUrl = 'http://127.0.0.1:6333/readyz'

    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest `
                -Method Get `
                -Uri $ReadyUrl `
                -UseBasicParsing `
                -TimeoutSec 5

            if ($Response.StatusCode -eq 200) {
                Write-Host 'Qdrant /readyz 확인 성공' -ForegroundColor Green
                return
            }
        }
        catch {
            # 컨테이너 생성 직후 연결 거부나 일시적인 준비 실패는
            # 제한 시간 안에서 다시 확인합니다.
        }

        Start-Sleep -Seconds 2
    }

    throw (
        'Qdrant가 제한 시간 안에 준비되지 않았습니다. ' +
        "제한 시간: $TimeoutSeconds 초"
    )
}


# ============================================================
# CUDA TEI 준비 상태 대기
# ============================================================

function Wait-EmbeddingReady {
    param(
        [Parameter(Mandatory = $true)]
        [int] $TimeoutSeconds
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $EmbedUrl = 'http://127.0.0.1:18081/embed'

    # 단순 Health 응답만 확인하지 않고 운영 임베딩 경로와 동일한
    # 실제 /embed 요청이 성공하는지 확인합니다.
    $RequestBody = @{
        inputs = @(
            'Jipsa RAG real E2E CUDA readiness probe.'
        )
    } | ConvertTo-Json -Depth 5 -Compress

    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-RestMethod `
                -Method Post `
                -Uri $EmbedUrl `
                -ContentType 'application/json' `
                -Body $RequestBody `
                -TimeoutSec 60

            if ($null -ne $Response) {
                Write-Host (
                    'TEI 실제 /embed 요청 성공'
                ) -ForegroundColor Green

                return
            }
        }
        catch {
            # 최초 모델 다운로드, Weight 로드, CUDA 초기화 및 Warmup 중에는
            # /embed 호출이 일시적으로 실패할 수 있으므로 다시 확인합니다.
        }

        Start-Sleep -Seconds 5
    }

    throw (
        'CUDA TEI가 제한 시간 안에 실제 임베딩 요청을 처리하지 못했습니다. ' +
        "제한 시간: $TimeoutSeconds 초"
    )
}


# ============================================================
# 환경 변수 복원
# ============================================================

function Restore-ProcessEnvironment {
    foreach ($EnvironmentName in $ManagedEnvironmentNames) {
        $OriginalValue = $OriginalEnvironment[$EnvironmentName]

        [Environment]::SetEnvironmentVariable(
            $EnvironmentName,
            $OriginalValue,
            [EnvironmentVariableTarget]::Process
        )
    }
}


# ============================================================
# 메인 E2E 흐름
# ============================================================

try {
    Push-Location -LiteralPath $ProjectRoot
    $LocationPushed = $true

    $env:JIPSA_RAG_APP_ENV = 'test'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    $OutputEncoding = $Utf8Encoding
    [Console]::InputEncoding = $Utf8Encoding
    [Console]::OutputEncoding = $Utf8Encoding

    # ============================================================
    # 1. 필수 명령 및 파일 확인
    # ============================================================

    Write-Step -Message '실제 E2E 필수 명령 및 파일 확인'

    Assert-CommandAvailable `
        -CommandName 'uv' `
        -InstallMessage 'uv를 설치하고 PATH에 등록해 주세요.'

    Assert-CommandAvailable `
        -CommandName 'docker' `
        -InstallMessage @'
Docker Desktop과 Docker Compose Plugin을 설치하고
Docker Engine을 실행한 후 다시 시도해 주세요.
'@

    Assert-RequiredFile `
        -Path $QualityGateScript `
        -Description 'RAG 품질 검사 스크립트'

    Assert-RequiredFile `
        -Path $ComposeFile `
        -Description 'RAG Docker Compose'

    Assert-RequiredFile `
        -Path $TestEnvironmentFile `
        -Description 'RAG 테스트 환경 변수'

    Assert-RequiredFile `
        -Path $DatabaseConnectionTest `
        -Description 'Local RAG DB 연결 테스트'

    Assert-RequiredFile `
        -Path $RealE2eTest `
        -Description '실제 PDF RAG E2E 테스트'

    Write-Host "프로젝트 루트: $ProjectRoot"
    Write-Host "테스트 환경 파일: $TestEnvironmentFile"


    # ============================================================
    # 2. Ruff, Mypy 및 전체 Pytest 품질 게이트
    # ============================================================

    if (-not $SkipQualityGate) {
        Write-Step -Message 'Ruff, Mypy 및 전체 Pytest 선행 검증'

        # 품질 검사 스크립트는 실제 E2E 환경 변수를 제거한 상태에서
        # 일반 전체 회귀 테스트를 실행합니다.
        & $QualityGateScript

        if (-not $?) {
            throw 'RAG 전체 품질 검사 스크립트 실행에 실패했습니다.'
        }
    }
    else {
        Write-Step -Message '품질 게이트 생략 및 의존성 동기화'

        Write-Host (
            'SkipQualityGate가 지정되어 Ruff, Mypy 및 전체 Pytest를 ' +
            '생략합니다.'
        ) -ForegroundColor Yellow

        # 품질 검사를 생략하더라도 실행 의존성은 uv.lock 기준으로
        # 동기화되어 있어야 합니다.
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @(
                'sync',
                '--frozen'
            ) `
            -FailureMessage 'uv 의존성 동기화에 실패했습니다.'
    }


    # ============================================================
    # 3. Docker Engine 및 Compose 확인
    # ============================================================

    Write-Step -Message 'Docker Engine 및 Compose 확인'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @(
            'version'
        ) `
        -FailureMessage 'Docker Engine에 연결할 수 없습니다.'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @(
            'compose',
            'version'
        ) `
        -FailureMessage 'Docker Compose Plugin을 실행할 수 없습니다.'

    # 설정 전체를 출력하면 환경 변수 값이 노출될 수 있으므로
    # config --quiet으로 구문과 보간 가능 여부만 확인합니다.
    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList (
            $ComposeBaseArguments + @(
                'config',
                '--quiet'
            )
        ) `
        -FailureMessage 'Docker Compose 구성 검증에 실패했습니다.'


    # ============================================================
    # 4. 기존 실행 서비스 확인
    # ============================================================

    Write-Step -Message '기존 RAG 인프라 실행 상태 확인'

    $RunningServicesBefore = @(
        Get-RunningComposeServices
    )

    if ($RunningServicesBefore.Count -gt 0) {
        Write-Host (
            '스크립트 실행 전 이미 동작 중인 서비스: ' +
            ($RunningServicesBefore -join ', ')
        )
    }
    else {
        Write-Host '스크립트 실행 전 동작 중인 RAG Compose 서비스가 없습니다.'
    }


    # ============================================================
    # 5. Qdrant 및 CUDA TEI 실행
    # ============================================================

    Write-Step -Message 'Qdrant 및 CUDA TEI 실행'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList (
            $ComposeBaseArguments + @(
                'up',
                '--detach',
                'qdrant',
                'embedding'
            )
        ) `
        -FailureMessage 'Qdrant 또는 CUDA TEI 실행에 실패했습니다.'

    foreach ($ServiceName in @('qdrant', 'embedding')) {
        if ($ServiceName -notin $RunningServicesBefore) {
            $StartedServices += $ServiceName
        }
    }

    Write-Host (
        '현재 스크립트가 새로 실행한 서비스: ' +
        (
            if ($StartedServices.Count -gt 0) {
                $StartedServices -join ', '
            }
            else {
                '없음'
            }
        )
    )


    # ============================================================
    # 6. Qdrant 및 TEI 준비 상태 확인
    # ============================================================

    Write-Step -Message 'Qdrant 준비 상태 확인'

    Wait-QdrantReady `
        -TimeoutSeconds $QdrantStartupTimeoutSeconds

    Write-Step -Message 'CUDA TEI 실제 임베딩 준비 상태 확인'

    Wait-EmbeddingReady `
        -TimeoutSeconds $EmbeddingStartupTimeoutSeconds


    # ============================================================
    # 7. Local RAG DB 연결 검증
    # ============================================================

    Write-Step -Message 'Local RAG DB 연결 검증'

    # 실제 E2E는 전용 행을 생성하고 삭제하므로 테스트 환경 DB에 연결 가능한지
    # 먼저 SELECT 1 통합 테스트로 확인합니다.
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/integration/test_database_connection.py',
            '-vv'
        ) `
        -FailureMessage 'Local RAG DB 연결 검증에 실패했습니다.'

    Write-Host 'Local RAG DB 연결 검증 통과' -ForegroundColor Green


    # ============================================================
    # 8. 실제 PDF RAG E2E 활성화
    # ============================================================

    # 이 환경 변수는 실제 Claude API와 Local 인프라를 사용하는
    # tests/e2e/test_real_pdf_rag_e2e.py의 명시적 실행 동의입니다.
    $env:JIPSA_RAG_RUN_E2E = '1'


    # ============================================================
    # 9. 실제 PDF 인제스트 및 Claude E2E 실행
    # ============================================================

    Write-Step -Message '실제 PDF 인제스트 및 Claude RAG E2E 실행'

    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/e2e/test_real_pdf_rag_e2e.py',
            '-vv'
        ) `
        -FailureMessage '실제 PDF RAG E2E 테스트에 실패했습니다.'

    Write-Host ''
    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray

    Write-Host (
        'Ruff, Mypy, 전체 Pytest 및 실제 PDF RAG E2E가 모두 ' +
        '통과했습니다.'
    ) -ForegroundColor Green

    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray
}
catch {
    # 최초 E2E 실패 원인을 보존합니다.
    $ExecutionError = $_
}
finally {
    # ============================================================
    # 10. 이 스크립트가 실행한 인프라 정리
    # ============================================================

    if (
        -not $KeepInfrastructureRunning -and
        $StartedServices.Count -gt 0
    ) {
        try {
            Write-Step -Message 'E2E 실행 인프라 정지'

            # 실행 전부터 동작하던 서비스는 제외하고
            # 현재 스크립트가 새로 실행한 서비스만 정지합니다.
            Invoke-NativeCommand `
                -FilePath 'docker' `
                -ArgumentList (
                    $ComposeBaseArguments +
                    @(
                        'stop',
                        '--timeout',
                        '30'
                    ) +
                    $StartedServices
                ) `
                -FailureMessage 'E2E 실행 인프라 정지에 실패했습니다.'

            Write-Host (
                '정지한 서비스: ' +
                ($StartedServices -join ', ')
            ) -ForegroundColor Green
        }
        catch {
            # 본 실행이 이미 실패했다면 인프라 정리 오류가 최초 오류를
            # 덮어쓰지 않도록 경고만 출력합니다.
            if ($null -ne $ExecutionError) {
                Write-Warning (
                    'E2E 실패 후 인프라 정리 중 추가 오류가 발생했습니다. ' +
                    $_.Exception.Message
                )
            }
            else {
                $ExecutionError = $_
            }
        }
    }
    elseif ($KeepInfrastructureRunning) {
        Write-Host ''
        Write-Host (
            'KeepInfrastructureRunning이 지정되어 Qdrant와 TEI를 ' +
            '실행 상태로 유지합니다.'
        ) -ForegroundColor Yellow
    }

    if ($LocationPushed) {
        Pop-Location
    }

    Restore-ProcessEnvironment

    $OutputEncoding = $OriginalOutputEncoding
    [Console]::InputEncoding = $OriginalConsoleInputEncoding
    [Console]::OutputEncoding = $OriginalConsoleOutputEncoding
}

if ($null -ne $ExecutionError) {
    throw $ExecutionError
}