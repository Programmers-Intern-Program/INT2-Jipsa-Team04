#Requires -Version 5.1

[CmdletBinding()]
param(
    # Ruff, Mypy 및 전체 Pytest 선행 검사를 생략합니다.
    #
    # 이미 동일한 Commit 상태에서 verify-rag-quality.ps1이 통과한 경우에만
    # 제한적으로 사용합니다.
    [switch] $SkipQualityGate,

    # E2E 종료 후 이 스크립트가 시작한 Qdrant와 CUDA TEI를 유지합니다.
    #
    # 실패 원인 분석을 위해 컨테이너 로그와 상태를 계속 확인해야 할 때
    # 사용합니다.
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
# - [SOURCE-N], cited_source_ids, sources 일치
#
# Mock 범위:
#
# - AWS Backend manifest API
# - AWS Backend ingest-complete callback API
# - Presigned GET URL을 제공하는 S3 HTTP 경계
#
# 환경 파일 사용 원칙:
#
# - 일반 단위·통합 테스트는 JIPSA_RAG_APP_ENV=test와 .env.test를 사용합니다.
# - 실제 E2E의 DB, Qdrant, CUDA TEI, Claude 설정은 .env.local에서 읽습니다.
# - .env.test의 qdrant.test, embedding.test Mock 주소는 수정하지 않습니다.
# - .env.local 값을 현재 PowerShell 프로세스에만 임시 주입한 뒤
#   JIPSA_RAG_APP_ENV=test를 다시 고정합니다.
# - 스크립트 종료 시 기존 프로세스 환경 변수를 모두 원래대로 복원합니다.
#
# 실제 Claude API 호출 비용이 발생합니다.
#
# 또한 E2E 전용 사용자 및 파일 범위의 Local RAG DB 행과 Qdrant Point를
# 테스트 시작 전과 종료 후 삭제합니다.
#
# 반드시 JIPSA_RAG_APP_ENV=test 환경에서만 E2E 본문을 실행합니다.
#
# 중요:
#
# Windows PowerShell 5.1에서 한글 주석과 출력 문자열을 안전하게 처리하려면
# 이 파일을 UTF-8 with BOM 형식으로 저장해야 합니다.
# ============================================================


# ============================================================
# 프로젝트 경로와 필수 파일
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

# 실제 Local RAG 인프라 연결 정보는 기존 .env.local을 사용합니다.
#
# 별도의 .env.e2e를 추가하지 않습니다.
$LocalEnvironmentFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath '.env.local'

$DatabaseConnectionTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/integration/test_database_connection.py'

$RealE2eTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/e2e/test_real_pdf_rag_e2e.py'

# Docker Compose도 애플리케이션과 동일한 .env.local의 임베딩 모델 설정을
# 사용해야 합니다.
$ComposeBaseArguments = @(
    'compose',
    '--env-file',
    $LocalEnvironmentFile,
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
# 실행 전 상태 보관
# ============================================================

# 스크립트가 변경하는 모든 프로세스 환경 변수의 원본 값을 보관합니다.
#
# .env.local의 키가 이후 추가되더라도 최초 값을 동적으로 저장하므로
# 스크립트 종료 후 호출자의 PowerShell 환경을 정확하게 복원할 수 있습니다.
$OriginalEnvironment = @{}

$OriginalOutputEncoding = $OutputEncoding
$OriginalConsoleInputEncoding = [Console]::InputEncoding
$OriginalConsoleOutputEncoding = [Console]::OutputEncoding

# Python과 외부 명령 출력에는 BOM 없는 UTF-8 인코딩을 사용합니다.
#
# 스크립트 파일 자체의 UTF-8 BOM 여부와 콘솔 출력 인코딩은 서로 다른
# 문제이므로 별도로 관리합니다.
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
# 프로세스 환경 변수 관리
# ============================================================

function Save-OriginalProcessEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    # 같은 환경 변수를 여러 번 변경하더라도 스크립트 진입 시점의 값만
    # 한 번 저장해야 정확한 원상 복구가 가능합니다.
    if (-not $OriginalEnvironment.ContainsKey($Name)) {
        $OriginalEnvironment[$Name] = (
            [Environment]::GetEnvironmentVariable(
                $Name,
                [EnvironmentVariableTarget]::Process
            )
        )
    }
}


function Set-ManagedProcessEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [AllowNull()]
        [string] $Value
    )

    Save-OriginalProcessEnvironmentValue -Name $Name

    [Environment]::SetEnvironmentVariable(
        $Name,
        $Value,
        [EnvironmentVariableTarget]::Process
    )
}


function Restore-ProcessEnvironment {
    foreach ($EnvironmentName in $OriginalEnvironment.Keys) {
        $OriginalValue = $OriginalEnvironment[$EnvironmentName]

        [Environment]::SetEnvironmentVariable(
            $EnvironmentName,
            $OriginalValue,
            [EnvironmentVariableTarget]::Process
        )
    }
}


# ============================================================
# .env.local 로더
# ============================================================

function Import-DotEnvFile {
    <#
    .SYNOPSIS
        UTF-8 dotenv 파일을 현재 PowerShell 프로세스 환경으로 가져옵니다.

    .DESCRIPTION
        Pydantic Settings는 OS 프로세스 환경 변수를 dotenv보다 우선하여
        읽습니다.

        따라서 .env.local의 실제 DB, Qdrant, CUDA TEI 및 Claude 설정을
        현재 프로세스에 임시 주입하면 JIPSA_RAG_APP_ENV=test 상태에서도
        .env.test의 Mock 전용 qdrant.test, embedding.test 주소 대신
        실제 Local RAG 인프라를 사용할 수 있습니다.

        빈 줄과 # 주석을 무시하며 KEY=VALUE 형식을 읽습니다.
        값 전체는 콘솔에 출력하지 않습니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    # UTF-8이 아닌 잘못된 바이트가 포함되면 조용히 대체 문자로 바꾸지 않고
    # 즉시 실패하도록 엄격한 UTF-8 디코더를 사용합니다.
    $Utf8Strict = [System.Text.UTF8Encoding]::new(
        $false,
        $true
    )

    $Lines = [System.IO.File]::ReadAllLines(
        $Path,
        $Utf8Strict
    )

    $LineNumber = 0
    $ImportedCount = 0

    foreach ($RawLine in $Lines) {
        $LineNumber += 1
        $Line = $RawLine.Trim()

        if (
            [string]::IsNullOrWhiteSpace($Line) -or
            $Line.StartsWith('#')
        ) {
            continue
        }

        $SeparatorIndex = $Line.IndexOf('=')

        if ($SeparatorIndex -le 0) {
            throw (
                '.env.local 형식이 올바르지 않습니다. ' +
                "줄 번호: $LineNumber"
            )
        }

        $Name = $Line.Substring(
            0,
            $SeparatorIndex
        ).Trim()

        $Value = $Line.Substring(
            $SeparatorIndex + 1
        ).Trim()

        if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw (
                '.env.local 환경 변수 이름이 올바르지 않습니다. ' +
                "줄 번호: $LineNumber"
            )
        }

        # dotenv에서 사용하는 "value" 또는 'value' 형식의 양끝 따옴표만
        # 제거합니다.
        #
        # 값 내부의 =, #, 공백은 비밀번호나 URL의 일부일 수 있으므로
        # 임의로 잘라내지 않습니다.
        if ($Value.Length -ge 2) {
            $WrappedByDoubleQuotes = (
                $Value.StartsWith('"') -and
                $Value.EndsWith('"')
            )

            $WrappedBySingleQuotes = (
                $Value.StartsWith("'") -and
                $Value.EndsWith("'")
            )

            if (
                $WrappedByDoubleQuotes -or
                $WrappedBySingleQuotes
            ) {
                $Value = $Value.Substring(
                    1,
                    $Value.Length - 2
                )
            }
        }

        Set-ManagedProcessEnvironmentValue `
            -Name $Name `
            -Value $Value

        $ImportedCount += 1
    }

    if ($ImportedCount -eq 0) {
        throw '.env.local에서 환경 변수를 한 개도 읽지 못했습니다.'
    }

    # 비밀값은 출력하지 않고 로드한 키의 개수만 알립니다.
    Write-Host (
        "실제 Local RAG 환경 변수 로드 완료: $ImportedCount 개"
    ) -ForegroundColor Green
}


# ============================================================
# 실제 E2E 환경 설정 검증
# ============================================================

function Get-RequiredProcessEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    $Value = [Environment]::GetEnvironmentVariable(
        $Name,
        [EnvironmentVariableTarget]::Process
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "실제 E2E 필수 환경 변수가 비어 있습니다: $Name"
    }

    return $Value.Trim()
}


function Assert-ExpectedProcessEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $ExpectedValue
    )

    $ActualValue = Get-RequiredProcessEnvironmentValue -Name $Name

    if ($ActualValue -ne $ExpectedValue) {
        throw (
            "실제 E2E 환경 변수 $Name 값이 올바르지 않습니다. " +
            "필수 값: $ExpectedValue"
        )
    }
}


function Get-AnthropicApiKey {
    # GenerationSettings는 공식 ANTHROPIC_API_KEY와
    # JIPSA_RAG_ANTHROPIC_API_KEY를 모두 허용하므로 두 이름을 순서대로
    # 확인합니다.
    $CandidateNames = @(
        'ANTHROPIC_API_KEY',
        'JIPSA_RAG_ANTHROPIC_API_KEY'
    )

    foreach ($CandidateName in $CandidateNames) {
        $CandidateValue = [Environment]::GetEnvironmentVariable(
            $CandidateName,
            [EnvironmentVariableTarget]::Process
        )

        if (-not [string]::IsNullOrWhiteSpace($CandidateValue)) {
            return $CandidateValue.Trim()
        }
    }

    throw (
        'Anthropic API Key가 비어 있습니다. ' +
        'ANTHROPIC_API_KEY 또는 JIPSA_RAG_ANTHROPIC_API_KEY가 필요합니다.'
    )
}


function Assert-RealE2eEnvironment {
    # 데이터 정리와 실제 E2E 실행은 test 프로필에서만 허용합니다.
    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_APP_ENV' `
        -ExpectedValue 'test'

    # Mock 전용 embedding.test가 실제 E2E로 유입되지 않도록
    # Docker Desktop이 공개한 루프백 주소를 강제합니다.
    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_EMBEDDING_PROVIDER' `
        -ExpectedValue 'tei'

    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_EMBEDDING_BASE_URL' `
        -ExpectedValue 'http://127.0.0.1:18081'

    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_EMBEDDING_MODEL' `
        -ExpectedValue 'Qwen/Qwen3-Embedding-0.6B'

    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_EMBEDDING_DIM' `
        -ExpectedValue '1024'

    # Mock 전용 qdrant.test가 실제 E2E로 유입되지 않도록
    # Windows 호스트의 실제 Qdrant 공개 포트를 강제합니다.
    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_VECTOR_DB_PROVIDER' `
        -ExpectedValue 'qdrant'

    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_QDRANT_URL' `
        -ExpectedValue 'http://127.0.0.1:6333'

    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_QDRANT_PREFER_GRPC' `
        -ExpectedValue 'false'

    Assert-ExpectedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_GENERATION_PROVIDER' `
        -ExpectedValue 'anthropic'

    # 실제 인프라 연결에 필요한 값은 존재 여부만 확인합니다.
    #
    # 비밀번호, 내부 토큰, API Key의 원문은 절대 출력하지 않습니다.
    $RequiredEnvironmentNames = @(
        'INTERNAL_TOKEN',
        'RAG_INGEST_TOKEN',
        'JIPSA_RAG_DATABASE_HOST',
        'JIPSA_RAG_DATABASE_PORT',
        'JIPSA_RAG_DATABASE_NAME',
        'JIPSA_RAG_DATABASE_USER',
        'JIPSA_RAG_DATABASE_PASSWORD',
        'JIPSA_RAG_QDRANT_COLLECTION',
        'JIPSA_RAG_ANTHROPIC_MODEL'
    )

    foreach ($EnvironmentName in $RequiredEnvironmentNames) {
        [void] (
            Get-RequiredProcessEnvironmentValue `
                -Name $EnvironmentName
        )
    }

    $AnthropicApiKey = Get-AnthropicApiKey

    if ($AnthropicApiKey.Length -lt 20) {
        throw (
            'Anthropic API Key가 실제 E2E 최소 길이 조건을 ' +
            '충족하지 않습니다.'
        )
    }

    $AnthropicModel = Get-RequiredProcessEnvironmentValue `
        -Name 'JIPSA_RAG_ANTHROPIC_MODEL'

    if (-not $AnthropicModel.StartsWith('claude-')) {
        throw 'JIPSA_RAG_ANTHROPIC_MODEL은 claude-로 시작해야 합니다.'
    }

    Write-Host (
        '실제 Qdrant, CUDA TEI, Local RAG DB 및 Claude 설정 검증 통과'
    ) -ForegroundColor Green
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
        # Windows PowerShell 5.1은 Native Command의 stderr를 ErrorRecord로
        # 변환할 수 있습니다.
        #
        # 종료 코드가 실제 성공 여부의 기준이므로 실행 중에는 Continue를
        # 사용하고 호출 직후 LASTEXITCODE를 검사합니다.
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
        [string] $ReadyUrl,

        [Parameter(Mandatory = $true)]
        [int] $TimeoutSeconds
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $LastErrorMessage = $null

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

            $LastErrorMessage = (
                "예상하지 못한 HTTP 상태 코드: $($Response.StatusCode)"
            )
        }
        catch {
            # 컨테이너 생성 직후 연결 거부나 일시적인 준비 실패는
            # 제한 시간 안에서 다시 확인합니다.
            $LastErrorMessage = $_.Exception.Message
        }

        Start-Sleep -Seconds 2
    }

    $FailureMessage = (
        'Qdrant가 제한 시간 안에 준비되지 않았습니다. ' +
        "제한 시간: $TimeoutSeconds 초"
    )

    if (-not [string]::IsNullOrWhiteSpace($LastErrorMessage)) {
        $FailureMessage += " 마지막 오류: $LastErrorMessage"
    }

    throw $FailureMessage
}


# ============================================================
# CUDA TEI 준비 상태 대기
# ============================================================

function Wait-EmbeddingReady {
    param(
        [Parameter(Mandatory = $true)]
        [string] $EmbedUrl,

        [Parameter(Mandatory = $true)]
        [int] $TimeoutSeconds
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $LastErrorMessage = $null

    # 단순 Health 응답만 확인하지 않고 실제 운영 임베딩 경로와 동일한
    # /embed 요청이 성공하는지 확인합니다.
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

            $LastErrorMessage = 'TEI /embed 응답이 비어 있습니다.'
        }
        catch {
            # 최초 모델 다운로드, Weight 로드, CUDA 초기화 및 Warmup 중에는
            # /embed 호출이 일시적으로 실패할 수 있으므로 다시 확인합니다.
            $LastErrorMessage = $_.Exception.Message
        }

        Start-Sleep -Seconds 5
    }

    $FailureMessage = (
        'CUDA TEI가 제한 시간 안에 실제 임베딩 요청을 처리하지 못했습니다. ' +
        "제한 시간: $TimeoutSeconds 초"
    )

    if (-not [string]::IsNullOrWhiteSpace($LastErrorMessage)) {
        $FailureMessage += " 마지막 오류: $LastErrorMessage"
    }

    throw $FailureMessage
}


# ============================================================
# 메인 E2E 흐름
# ============================================================

try {
    Push-Location -LiteralPath $ProjectRoot
    $LocationPushed = $true

    # 일반 품질 게이트는 기존 .env.test 계약을 그대로 사용해야 하므로
    # .env.local을 읽기 전에 test 프로필과 UTF-8 실행 환경만 설정합니다.
    Set-ManagedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_APP_ENV' `
        -Value 'test'

    Set-ManagedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_RUN_E2E' `
        -Value $null

    Set-ManagedProcessEnvironmentValue `
        -Name 'PYTHONUTF8' `
        -Value '1'

    Set-ManagedProcessEnvironmentValue `
        -Name 'PYTHONIOENCODING' `
        -Value 'utf-8'

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
        -Path $LocalEnvironmentFile `
        -Description 'RAG 로컬 환경 변수'

    Assert-RequiredFile `
        -Path $DatabaseConnectionTest `
        -Description 'Local RAG DB 연결 테스트'

    Assert-RequiredFile `
        -Path $RealE2eTest `
        -Description '실제 PDF RAG E2E 테스트'

    Write-Host "프로젝트 루트: $ProjectRoot"
    Write-Host "실제 인프라 환경 파일: $LocalEnvironmentFile"


    # ============================================================
    # 2. Ruff, Mypy 및 전체 Pytest 품질 게이트
    # ============================================================

    if (-not $SkipQualityGate) {
        Write-Step -Message 'Ruff, Mypy 및 전체 Pytest 선행 검증'

        # .env.local의 실제 인프라 값을 아직 주입하지 않았으므로
        # 일반 회귀 테스트는 기존 .env.test의 Mock 계약으로 실행됩니다.
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
    # 3. 실제 Local RAG 환경 변수 로드 및 검증
    # ============================================================

    Write-Step -Message '실제 Local RAG 환경 변수 로드 및 검증'

    Import-DotEnvFile -Path $LocalEnvironmentFile

    # .env.local 전체를 읽은 뒤에도 E2E 안전 프로필은 test로 고정합니다.
    #
    # 이 값은 Pydantic이 .env.test를 선택하게 하지만, .env.local에서
    # 프로세스 환경으로 주입한 실제 인프라 값이 더 높은 우선순위로
    # 적용됩니다.
    Set-ManagedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_APP_ENV' `
        -Value 'test'

    # 실제 E2E 본문 실행 전까지 opt-in 값을 제거합니다.
    Set-ManagedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_RUN_E2E' `
        -Value $null

    Set-ManagedProcessEnvironmentValue `
        -Name 'PYTHONUTF8' `
        -Value '1'

    Set-ManagedProcessEnvironmentValue `
        -Name 'PYTHONIOENCODING' `
        -Value 'utf-8'

    Assert-RealE2eEnvironment

    $QdrantBaseUrl = Get-RequiredProcessEnvironmentValue `
        -Name 'JIPSA_RAG_QDRANT_URL'

    $EmbeddingBaseUrl = Get-RequiredProcessEnvironmentValue `
        -Name 'JIPSA_RAG_EMBEDDING_BASE_URL'

    $QdrantReadyUrl = "$QdrantBaseUrl/readyz"
    $EmbeddingUrl = "$EmbeddingBaseUrl/embed"


    # ============================================================
    # 4. Docker Engine 및 Compose 확인
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
    # 5. 기존 실행 서비스 확인
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
        Write-Host (
            '스크립트 실행 전 동작 중인 RAG Compose 서비스가 없습니다.'
        )
    }


    # ============================================================
    # 6. Qdrant 및 CUDA TEI 실행
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

    # Windows PowerShell 5.1에서는 if 문을 일반 그룹 연산자 (...) 안에
    # 직접 넣어 표현식처럼 사용할 수 없습니다.
    #
    # 먼저 문자열을 계산한 뒤 Write-Host에 전달하여 PowerShell 5.1과
    # PowerShell 7에서 모두 동일하게 파싱되도록 합니다.
    if ($StartedServices.Count -gt 0) {
        $StartedServicesText = $StartedServices -join ', '
    }
    else {
        $StartedServicesText = '없음'
    }

    Write-Host (
        '현재 스크립트가 새로 실행한 서비스: ' +
        $StartedServicesText
    )


    # ============================================================
    # 7. Qdrant 및 TEI 준비 상태 확인
    # ============================================================

    Write-Step -Message 'Qdrant 준비 상태 확인'

    Wait-QdrantReady `
        -ReadyUrl $QdrantReadyUrl `
        -TimeoutSeconds $QdrantStartupTimeoutSeconds

    Write-Step -Message 'CUDA TEI 실제 임베딩 준비 상태 확인'

    Wait-EmbeddingReady `
        -EmbedUrl $EmbeddingUrl `
        -TimeoutSeconds $EmbeddingStartupTimeoutSeconds


    # ============================================================
    # 8. Local RAG DB 연결 검증
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
    # 9. 실제 PDF RAG E2E 활성화
    # ============================================================

    # 이 환경 변수는 실제 Claude API와 Local 인프라를 사용하는
    # tests/e2e/test_real_pdf_rag_e2e.py의 명시적 실행 동의입니다.
    Set-ManagedProcessEnvironmentValue `
        -Name 'JIPSA_RAG_RUN_E2E' `
        -Value '1'


    # ============================================================
    # 10. 실제 PDF 인제스트 및 Claude E2E 실행
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

    if ($SkipQualityGate) {
        $SuccessMessage = (
            '실제 PDF RAG E2E가 통과했습니다. ' +
            '이번 실행에서는 품질 게이트를 생략했습니다.'
        )
    }
    else {
        $SuccessMessage = (
            'Ruff, Mypy, 전체 Pytest 및 실제 PDF RAG E2E가 모두 ' +
            '통과했습니다.'
        )
    }

    Write-Host ''
    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray

    Write-Host $SuccessMessage -ForegroundColor Green

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
    # 11. 이 스크립트가 실행한 인프라 정리
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
