Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Jipsa Local RAG Infrastructure Start Script
# ============================================================
#
# 이 스크립트는 로컬에서 사용하는 Jipsa RAG 인프라를
# 일관된 절차로 준비하고 실행합니다.
#
# 주요 처리 순서:
#
# 1. 프로젝트 루트 및 필수 파일 확인
# 2. uv, Docker CLI 및 Docker Compose 설치 확인
# 3. RAG 실행 환경 결정
# 4. 실제 Pydantic Settings를 이용한 환경 변수 검증
# 5. 애플리케이션 설정과 Docker Compose 포트 정합성 검증
# 6. Docker Engine 및 Docker Compose 실행 상태 확인
# 7. Qdrant 및 TEI 이미지 다운로드
# 8. Qdrant 및 TEI 컨테이너 생성·재생성·실행
# 9. TEI 컨테이너의 NVIDIA GPU 할당 확인
# 10. TEI 로그에서 CUDA 실패 및 CPU 폴백 확인
# 11. 실제 /embed 요청을 통한 임베딩 서버 준비 상태 확인
#
# 이 스크립트는 다음 항목을 수행하지 않습니다.
#
# - Docker Desktop 프로그램 자체 실행
# - FastAPI RAG 애플리케이션 실행
# - Local RAG MySQL 실행
#
# Docker Desktop은 Windows 로그인 시 자동 실행되도록
# Docker Desktop 설정에서 별도로 구성합니다.
#
# 중요:
#
# Windows PowerShell 5.1은 BOM이 없는 UTF-8 PowerShell 파일을
# 시스템 기본 ANSI 인코딩으로 잘못 읽을 수 있습니다.
#
# 이 파일은 반드시 UTF-8 with BOM 형식으로 저장해야 합니다.
# ============================================================


# ============================================================
# 프로젝트 경로 및 고정 설정
# ============================================================

# 현재 스크립트 위치:
#
# RAG/scripts/start-local-rag.ps1
#
# 따라서 $PSScriptRoot의 상위 디렉터리가 RAG 프로젝트 루트입니다.
$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path `
            -Path $PSScriptRoot `
            -ChildPath '..'
    )
).Path

# 기존 RAG Docker Compose 파일입니다.
$ComposeFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'infra/qdrant/compose.yaml'

# Compose 파일과 동일한 Docker 이미지 이름입니다.
#
# 이미지 Pull 실패 시 로컬 이미지가 존재하는지 확인하는 데 사용합니다.
$QdrantImage = 'qdrant/qdrant:v1.18.2'

# RTX 3060 Ti의 Ampere Compute Capability 8.6에 맞는
# Hugging Face TEI 전용 CUDA 이미지입니다.
$EmbeddingImage = 'ghcr.io/huggingface/text-embeddings-inference:86-1.9'

# 현재 프로젝트의 설정 코드에서 허용하는 실행 환경입니다.
#
# src/jipsa_rag/core/config.py의 SUPPORTED_ENVIRONMENTS와
# 동일한 값을 유지해야 합니다.
$SupportedEnvironments = @(
    'local',
    'development',
    'test'
)

# Docker Compose 명령에서 반복해서 사용할 공통 인수입니다.
$ComposeBaseArguments = @(
    'compose',
    '--file',
    $ComposeFile
)

# TEI 최초 실행 시 Hugging Face 모델 다운로드와
# 모델 Warmup에 시간이 걸릴 수 있으므로 최대 20분까지 기다립니다.
#
# 모델 Cache가 이미 존재하면 일반적으로 훨씬 빠르게 완료됩니다.
$EmbeddingStartupTimeoutSeconds = 1200

# 스크립트에서 일시적으로 변경하는 프로세스 환경 변수를
# 실행 종료 후 원래 값으로 복원하기 위해 현재 값을 보관합니다.
$OriginalAppEnvironment = [Environment]::GetEnvironmentVariable(
    'JIPSA_RAG_APP_ENV',
    'Process'
)

$OriginalEmbeddingModel = [Environment]::GetEnvironmentVariable(
    'JIPSA_RAG_EMBEDDING_MODEL',
    'Process'
)


# ============================================================
# 공통 출력 함수
# ============================================================

function Write-Step {
    <#
    .SYNOPSIS
    현재 실행 중인 주요 단계를 출력합니다.

    .DESCRIPTION
    이미지 다운로드나 TEI 모델 준비처럼 시간이 걸릴 수 있는 작업에서
    사용자가 현재 진행 위치를 확인할 수 있도록 단계 제목을 출력합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    Write-Host ''
    Write-Host "[$Message]" -ForegroundColor Cyan
}


# ============================================================
# 필수 명령 확인
# ============================================================

function Assert-CommandAvailable {
    <#
    .SYNOPSIS
    필수 명령이 현재 PATH에서 실행 가능한지 확인합니다.

    .DESCRIPTION
    uv 또는 Docker CLI가 설치되지 않은 상태에서
    후속 명령을 실행하지 않도록 사전에 검사합니다.
    #>

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
}


# ============================================================
# Native Command 실행 함수
# ============================================================

function Invoke-NativeCommand {
    <#
    .SYNOPSIS
    외부 프로그램을 실행하고 종료 코드를 검사합니다.

    .DESCRIPTION
    Windows PowerShell 5.1에서는 Docker나 uv가 stderr에 출력한 내용을
    PowerShell ErrorRecord로 변환할 수 있습니다.

    스크립트 전역의 ErrorActionPreference가 Stop인 상태에서는
    Native Command가 실제로 성공했더라도 stderr 출력 때문에
    PowerShell이 먼저 종료될 수 있습니다.

    따라서 외부 프로그램을 실행하는 동안에만
    ErrorActionPreference를 Continue로 변경합니다.

    외부 프로그램의 실제 성공 여부는 PowerShell ErrorRecord가 아니라
    프로그램이 반환한 $LASTEXITCODE를 기준으로 판정합니다.
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
    $ExitCode = $null

    try {
        # Native Command의 stderr가 PowerShell의 terminating error로
        # 전환되지 않도록 실행 중에만 Continue를 사용합니다.
        $ErrorActionPreference = 'Continue'

        & $FilePath @ArgumentList

        $ExitCode = $LASTEXITCODE
    }
    finally {
        # 외부 명령 실행 후 기존 오류 처리 정책을 복원합니다.
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($null -eq $ExitCode) {
        throw "$FailureMessage 외부 프로그램의 종료 코드를 확인할 수 없습니다."
    }

    if ($ExitCode -ne 0) {
        throw "$FailureMessage 종료 코드: $ExitCode"
    }
}


function Invoke-NativeCommandForOutput {
    <#
    .SYNOPSIS
    외부 프로그램의 출력을 반환하고 종료 코드를 검사합니다.

    .DESCRIPTION
    Docker 버전, 컨테이너 상태 또는 Python 설정 검증 결과처럼
    후속 로직에서 사용해야 하는 출력값을 가져올 때 사용합니다.

    Windows PowerShell 5.1의 NativeCommandError 문제를 방지하기 위해
    명령 실행 중에만 ErrorActionPreference를 Continue로 변경합니다.

    표준 출력과 표준 오류를 함께 수집하며,
    종료 코드가 0이 아니면 전체 출력을 표시한 뒤 실패 처리합니다.
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

        $Output = @(
            & $FilePath @ArgumentList 2>&1
        )

        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($null -eq $ExitCode) {
        throw "$FailureMessage 외부 프로그램의 종료 코드를 확인할 수 없습니다."
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
# 실행 환경 결정
# ============================================================

function Resolve-AppEnvironment {
    <#
    .SYNOPSIS
    현재 RAG 실행 환경을 결정합니다.

    .DESCRIPTION
    JIPSA_RAG_APP_ENV가 지정되지 않으면
    프로젝트 설정 코드와 동일하게 local을 기본값으로 사용합니다.

    지원하지 않는 값이면 잘못된 dotenv 파일을 선택하기 전에
    즉시 실행을 중단합니다.
    #>

    $EnvironmentValue = $env:JIPSA_RAG_APP_ENV

    if ([string]::IsNullOrWhiteSpace($EnvironmentValue)) {
        return 'local'
    }

    $NormalizedEnvironment = $EnvironmentValue.Trim().ToLowerInvariant()

    if ($NormalizedEnvironment -notin $SupportedEnvironments) {
        $SupportedEnvironmentText = $SupportedEnvironments -join ', '

        throw @"
지원하지 않는 JIPSA_RAG_APP_ENV 값입니다: $NormalizedEnvironment

지원 환경: $SupportedEnvironmentText
"@
    }

    return $NormalizedEnvironment
}


# ============================================================
# Docker 연결 URL 검증
# ============================================================

function Assert-LocalHttpEndpoint {
    <#
    .SYNOPSIS
    RAG 설정의 HTTP URL이 Docker Compose 포트와 일치하는지 확인합니다.

    .DESCRIPTION
    Docker Compose는 Qdrant와 TEI를 정해진 Host 포트에 노출합니다.

    환경 변수의 URL이 다른 호스트나 포트를 가리키면
    컨테이너가 정상적으로 실행돼도 RAG 애플리케이션은 접속할 수 없습니다.

    다음 항목을 검증합니다.

    - HTTP 스킴 사용
    - 127.0.0.1 또는 localhost 사용
    - Docker Compose와 동일한 포트 사용
    - API 경로, Query String 및 Fragment 미포함
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $SettingName,

        [Parameter(Mandatory = $true)]
        [string] $Value,

        [Parameter(Mandatory = $true)]
        [int] $ExpectedPort
    )

    [Uri] $ParsedUri = $null

    $IsValidUri = [Uri]::TryCreate(
        $Value,
        [UriKind]::Absolute,
        [ref] $ParsedUri
    )

    if (-not $IsValidUri -or $null -eq $ParsedUri) {
        throw "$SettingName 값이 올바른 절대 URL이 아닙니다: $Value"
    }

    if ($ParsedUri.Scheme -ne 'http') {
        throw @"
$SettingName은 로컬 Docker 연결을 위해 http 스킴을 사용해야 합니다.

현재 값: $Value
"@
    }

    $AllowedHosts = @(
        '127.0.0.1',
        'localhost'
    )

    if ($ParsedUri.Host -notin $AllowedHosts) {
        throw @"
$SettingName의 호스트가 Docker Compose 설정과 일치하지 않습니다.

현재 값: $Value
허용 호스트: 127.0.0.1, localhost
"@
    }

    if ($ParsedUri.Port -ne $ExpectedPort) {
        throw @"
$SettingName의 포트가 Docker Compose 설정과 일치하지 않습니다.

현재 값: $Value
필요한 포트: $ExpectedPort
"@
    }

    if (
        $ParsedUri.AbsolutePath -notin @('', '/') -or
        -not [string]::IsNullOrEmpty($ParsedUri.Query) -or
        -not [string]::IsNullOrEmpty($ParsedUri.Fragment)
    ) {
        throw @"
$SettingName에는 API 경로, Query String 또는 Fragment를 포함할 수 없습니다.

현재 값: $Value
"@
    }
}


# ============================================================
# 프로젝트 환경 변수 검증
# ============================================================

function Get-ValidatedRagSettings {
    <#
    .SYNOPSIS
    실제 RAG 애플리케이션의 Settings 객체로 환경 변수를 검증합니다.

    .DESCRIPTION
    PowerShell에 Pydantic 검증 규칙을 다시 구현하지 않고,
    RAG 애플리케이션에서 사용하는 get_settings()를 직접 호출합니다.

    이전 구현은 여러 줄짜리 Python 코드를 다음 방식으로 전달했습니다.

        uv run python -c $ValidationCode

    Windows PowerShell 5.1은 복잡한 Native Command 인수를 변환하는 과정에서
    Python 코드 내부 JSON Dictionary Key의 큰따옴표를 제거할 수 있습니다.

    예를 들어 다음 코드가:

        {"app_env": settings.app_env}

    Python에 다음과 같이 전달될 수 있습니다.

        {app_env: settings.app_env}

    이 경우 app_env가 문자열이 아니라 변수로 해석되어
    NameError가 발생합니다.

    이를 방지하기 위해 Python 코드를 임시 UTF-8 파일로 저장한 뒤
    파일 경로를 Python에 전달합니다.

    검증이 완료되거나 오류가 발생하면
    임시 Python 파일은 finally 블록에서 반드시 삭제합니다.

    출력에는 Docker 실행에 필요한 비민감 설정만 포함합니다.

    다음 값은 출력하지 않습니다.

    - 데이터베이스 비밀번호
    - INTERNAL_TOKEN
    - RAG_INGEST_TOKEN
    - Qdrant API Key
    #>

    # 사용자 임시 디렉터리에 충돌하지 않는 파일명을 생성합니다.
    $TemporaryScriptPath = Join-Path `
        -Path ([System.IO.Path]::GetTempPath()) `
        -ChildPath (
            'jipsa-rag-settings-validation-{0}.py' -f (
                [Guid]::NewGuid().ToString('N')
            )
        )

    # Python 설정 검증 코드입니다.
    #
    # PowerShell Native Command의 -c 인수로 직접 전달하지 않고
    # UTF-8 임시 Python 파일로 작성합니다.
    $ValidationCode = @'
import json

from jipsa_rag.core.config import get_settings


settings = get_settings()

# PowerShell 스크립트가 Docker Compose 설정과 비교해야 하는
# 비민감 설정만 JSON으로 출력합니다.
#
# JSON Key는 Python 임시 파일 안에 그대로 보존되므로
# Windows PowerShell 5.1의 Native Argument Quoting 영향을 받지 않습니다.
print(
    json.dumps(
        {
            "app_env": settings.app_env,
            "embedding_provider": settings.embedding_provider,
            "embedding_base_url": settings.embedding_base_url,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "vector_db_provider": settings.vector_db_provider,
            "qdrant_url": settings.qdrant_url,
            "qdrant_grpc_port": settings.qdrant_grpc_port,
        },
        # 출력되는 값은 ASCII 문자열이지만,
        # 향후 비ASCII 값이 추가돼도 JSON 형식이 안전하게 유지되도록 합니다.
        ensure_ascii=True,
        separators=(",", ":"),
    )
)
'@

    try {
        # Python 3은 UTF-8 소스 파일을 정상적으로 읽을 수 있습니다.
        #
        # 임시 파일은 PowerShell 스크립트와 달리 Windows PowerShell이
        # 직접 파싱하지 않으므로 UTF-8 BOM 없이 저장해도 안전합니다.
        $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)

        [System.IO.File]::WriteAllText(
            $TemporaryScriptPath,
            $ValidationCode,
            $Utf8WithoutBom
        )

        if (-not (Test-Path -LiteralPath $TemporaryScriptPath -PathType Leaf)) {
            throw "임시 Python 설정 검증 파일을 생성하지 못했습니다: $TemporaryScriptPath"
        }

        $OutputLines = @(
            Invoke-NativeCommandForOutput `
                -FilePath 'uv' `
                -ArgumentList @(
                    'run',
                    'python',
                    $TemporaryScriptPath
                ) `
                -FailureMessage 'RAG 환경 설정 검증에 실패했습니다.'
        )

        # uv 또는 Python이 부가 출력을 추가하더라도
        # JSON 객체로 시작하는 마지막 줄만 검증 결과로 사용합니다.
        $JsonLine = $OutputLines |
            ForEach-Object {
                [string] $_
            } |
            Where-Object {
                $_.TrimStart().StartsWith('{')
            } |
            Select-Object -Last 1

        if ([string]::IsNullOrWhiteSpace($JsonLine)) {
            $FullOutput = (
                $OutputLines |
                    ForEach-Object {
                        [string] $_
                    }
            ) -join [Environment]::NewLine

            throw @"
RAG 환경 설정 검증 결과에서 JSON 출력을 찾을 수 없습니다.

전체 출력:
$FullOutput
"@
        }

        try {
            return ConvertFrom-Json -InputObject $JsonLine
        }
        catch {
            throw @"
RAG 환경 설정 검증 결과를 JSON으로 해석할 수 없습니다.

JSON 출력:
$JsonLine

오류:
$($_.Exception.Message)
"@
        }
    }
    finally {
        # 설정값이나 프로젝트 경로가 포함된 임시 파일이
        # 사용자 임시 디렉터리에 남지 않도록 항상 삭제합니다.
        if (Test-Path -LiteralPath $TemporaryScriptPath -PathType Leaf) {
            Remove-Item `
                -LiteralPath $TemporaryScriptPath `
                -Force `
                -ErrorAction SilentlyContinue
        }
    }
}


# ============================================================
# Docker 이미지 관리
# ============================================================

function Test-DockerImageExists {
    <#
    .SYNOPSIS
    지정한 Docker 이미지가 로컬에 존재하는지 확인합니다.

    .DESCRIPTION
    Docker Registry 연결에 실패하더라도
    필요한 이미지가 이미 로컬에 있으면 실행을 계속할 수 있도록 합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $ImageName
    )

    $PreviousErrorActionPreference = $ErrorActionPreference
    $ExitCode = $null

    try {
        $ErrorActionPreference = 'Continue'

        & docker image inspect `
            $ImageName `
            1> $null `
            2> $null

        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    return $ExitCode -eq 0
}


function Update-RequiredDockerImages {
    <#
    .SYNOPSIS
    Qdrant 및 TEI 이미지를 Registry에서 확인하고 다운로드합니다.

    .DESCRIPTION
    동일한 이미지 태그에 CUDA 호환성 수정이 반영될 수 있으므로
    단순히 이미지가 없는 경우만 확인하지 않고 Pull을 먼저 시도합니다.

    Windows PowerShell 5.1에서는 Docker가 정상 진행 상황을
    stderr에 출력할 수 있으므로 실행 중에만
    ErrorActionPreference를 Continue로 변경합니다.

    Pull 실패 시 다음 정책을 적용합니다.

    - 필요한 이미지가 로컬에 모두 존재:
      경고를 출력하고 기존 로컬 이미지로 계속 진행

    - 필요한 이미지 중 하나 이상이 로컬에도 존재하지 않음:
      컨테이너를 생성할 수 없으므로 실행 중단
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string[]] $ComposeArguments
    )

    $PullArguments = $ComposeArguments + @(
        'pull',
        'qdrant',
        'embedding'
    )

    $PreviousErrorActionPreference = $ErrorActionPreference
    $PullOutput = @()
    $PullExitCode = $null

    try {
        $ErrorActionPreference = 'Continue'

        $PullOutput = @(
            & docker @PullArguments 2>&1
        )

        $PullExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    $PullOutputText = (
        $PullOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join [Environment]::NewLine

    if ($PullExitCode -eq 0) {
        if (-not [string]::IsNullOrWhiteSpace($PullOutputText)) {
            Write-Host $PullOutputText
        }

        return
    }

    Write-Warning @"
Docker Registry에서 이미지를 확인하거나 다운로드하지 못했습니다.

로컬에 필요한 이미지가 존재하는지 확인한 뒤
기존 이미지로 계속 실행할 수 있는지 판단합니다.

Docker Pull 출력:
$PullOutputText
"@

    $MissingImages = @()

    if (-not (Test-DockerImageExists -ImageName $QdrantImage)) {
        $MissingImages += $QdrantImage
    }

    if (-not (Test-DockerImageExists -ImageName $EmbeddingImage)) {
        $MissingImages += $EmbeddingImage
    }

    if ($MissingImages.Count -gt 0) {
        $MissingImageText = $MissingImages -join [Environment]::NewLine

        throw @"
필수 Docker 이미지를 다운로드하지 못했고 로컬에도 존재하지 않습니다.

누락된 이미지:
$MissingImageText

네트워크 및 Docker Registry 접근 상태를 확인한 후 다시 실행해 주세요.
"@
    }

    Write-Warning '필요한 이미지가 로컬에 모두 존재하므로 기존 이미지로 계속 실행합니다.'
}


# ============================================================
# Docker 컨테이너 상태 및 로그
# ============================================================

function Get-ContainerLogs {
    <#
    .SYNOPSIS
    지정한 Docker 컨테이너의 최근 로그를 문자열로 반환합니다.

    .DESCRIPTION
    TEI CUDA 초기화 실패, CPU 폴백 또는 모델 준비 실패 원인을
    사용자에게 출력하기 위해 사용합니다.

    Docker logs는 정상 로그도 stderr에 출력할 수 있으므로
    실행 중에만 ErrorActionPreference를 Continue로 변경합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $ContainerName,

        [int] $Tail = 200
    )

    $PreviousErrorActionPreference = $ErrorActionPreference
    $LogOutput = @()
    $LogExitCode = $null

    try {
        $ErrorActionPreference = 'Continue'

        $LogOutput = @(
            & docker logs `
                --tail $Tail `
                $ContainerName `
                2>&1
        )

        $LogExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($LogExitCode -ne 0) {
        $FailureOutput = (
            $LogOutput |
                ForEach-Object {
                    [string] $_
                }
        ) -join [Environment]::NewLine

        if ([string]::IsNullOrWhiteSpace($FailureOutput)) {
            return "컨테이너 로그를 조회할 수 없습니다. 종료 코드: $LogExitCode"
        }

        return @"
컨테이너 로그를 조회할 수 없습니다.
종료 코드: $LogExitCode

Docker 출력:
$FailureOutput
"@
    }

    return (
        $LogOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join [Environment]::NewLine
}


function Get-ContainerState {
    <#
    .SYNOPSIS
    Docker 컨테이너의 현재 실행 상태를 구조화하여 반환합니다.

    .DESCRIPTION
    TEI 컨테이너가 실행 중인지, 정상 종료됐는지,
    OOM으로 종료됐는지 확인할 때 사용합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $ContainerName
    )

    $StateOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList @(
                'inspect',
                '--format',
                '{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|{{.RestartCount}}',
                $ContainerName
            ) `
            -FailureMessage "$ContainerName 컨테이너 상태 조회에 실패했습니다."
    )

    $StateText = (
        $StateOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join ''

    $StateParts = $StateText.Split('|')

    if ($StateParts.Count -ne 4) {
        throw "$ContainerName 컨테이너 상태 출력 형식을 해석할 수 없습니다: $StateText"
    }

    return [PSCustomObject] @{
        Status       = $StateParts[0]
        ExitCode     = [int] $StateParts[1]
        OOMKilled    = [System.Convert]::ToBoolean($StateParts[2])
        RestartCount = [int] $StateParts[3]
    }
}


function Stop-EmbeddingContainerQuietly {
    <#
    .SYNOPSIS
    CUDA 실패 또는 CPU 폴백이 확인된 TEI 컨테이너를 정지합니다.

    .DESCRIPTION
    GPU 대신 CPU로 실행되는 TEI를 계속 유지하면
    CPU 사용률과 임베딩 응답 시간이 증가할 수 있습니다.

    오류 처리 도중 호출되는 함수이므로
    컨테이너 정지 자체가 실패하더라도 기존 오류를 덮어쓰지 않습니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string[]] $ComposeArguments
    )

    $StopArguments = $ComposeArguments + @(
        'stop',
        '--timeout',
        '30',
        'embedding'
    )

    $PreviousErrorActionPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = 'Continue'

        & docker @StopArguments 1> $null 2> $null
    }
    catch {
        # 기존 CUDA 또는 TEI 오류를 보존하기 위해
        # 정지 실패 예외는 다시 던지지 않습니다.
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}


# ============================================================
# TEI 이미지 및 GPU 할당 검증
# ============================================================

function Assert-EmbeddingContainerImage {
    <#
    .SYNOPSIS
    TEI 컨테이너가 RTX 3060 Ti용 이미지로 실행됐는지 확인합니다.

    .DESCRIPTION
    이전 범용 cuda-1.9 이미지로 생성된 컨테이너가
    재사용되지 않았는지 검사합니다.
    #>

    $ImageOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList @(
                'inspect',
                'jipsa-embedding',
                '--format',
                '{{.Config.Image}}'
            ) `
            -FailureMessage 'TEI 컨테이너 이미지 정보 조회에 실패했습니다.'
    )

    $ActualImage = (
        $ImageOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join ''

    if ($ActualImage -ne $EmbeddingImage) {
        throw @"
TEI 컨테이너 이미지가 RTX 3060 Ti용 설정과 일치하지 않습니다.

현재 이미지: $ActualImage
필요한 이미지: $EmbeddingImage
"@
    }

    Write-Host "TEI 이미지: $ActualImage"
}


function Assert-EmbeddingGpuReservation {
    <#
    .SYNOPSIS
    TEI 컨테이너에 NVIDIA GPU Device Request가 적용됐는지 확인합니다.

    .DESCRIPTION
    Compose 파일에 GPU 예약 설정이 있더라도
    기존 컨테이너가 잘못된 설정으로 생성돼 있으면
    GPU Device Request가 없을 수 있습니다.

    다음 항목을 확인합니다.

    - NVIDIA Driver 요청
    - gpu capability 요청
    - 컨테이너 내부 nvidia-smi 실행
    - RTX 3060 Ti 인식 여부
    #>

    $DeviceRequestOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList @(
                'inspect',
                'jipsa-embedding',
                '--format',
                '{{json .HostConfig.DeviceRequests}}'
            ) `
            -FailureMessage 'TEI 컨테이너 GPU 요청 정보 조회에 실패했습니다.'
    )

    $DeviceRequestJson = (
        $DeviceRequestOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join ''

    if (
        [string]::IsNullOrWhiteSpace($DeviceRequestJson) -or
        $DeviceRequestJson -eq 'null' -or
        $DeviceRequestJson -eq '[]'
    ) {
        throw @"
TEI 컨테이너에 GPU Device Request가 적용되지 않았습니다.

Docker Compose의 다음 설정을 확인해 주세요.

embedding.deploy.resources.reservations.devices
"@
    }

    if (
        $DeviceRequestJson -notmatch '"Driver":"nvidia"' -or
        $DeviceRequestJson -notmatch '"gpu"'
    ) {
        throw @"
TEI 컨테이너의 GPU 요청 정보가 올바르지 않습니다.

현재 Device Request:
$DeviceRequestJson
"@
    }

    Write-Host "GPU Device Request: $DeviceRequestJson"

    # 컨테이너에 Device Request 정보만 존재하는 것으로 끝내지 않고
    # 컨테이너 내부에서 실제 NVIDIA GPU가 조회되는지 확인합니다.
    $GpuNameOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList @(
                'exec',
                'jipsa-embedding',
                'nvidia-smi',
                '--query-gpu=name',
                '--format=csv,noheader'
            ) `
            -FailureMessage 'TEI 컨테이너 내부 NVIDIA GPU 확인에 실패했습니다.'
    )

    $GpuName = (
        $GpuNameOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join ''

    if ([string]::IsNullOrWhiteSpace($GpuName)) {
        throw 'TEI 컨테이너 내부에서 NVIDIA GPU 이름을 확인할 수 없습니다.'
    }

    if ($GpuName -notmatch 'RTX 3060 Ti') {
        throw @"
TEI 컨테이너가 예상한 GPU를 사용하지 않습니다.

현재 GPU:
$GpuName

예상 GPU:
NVIDIA GeForce RTX 3060 Ti
"@
    }

    Write-Host "TEI 컨테이너 GPU: $GpuName"
}


# ============================================================
# TEI CUDA 실행 및 준비 상태 검증
# ============================================================

function Wait-EmbeddingGpuReady {
    <#
    .SYNOPSIS
    TEI가 CPU로 폴백하지 않고 실제 임베딩 요청을 처리할 때까지 대기합니다.

    .DESCRIPTION
    컨테이너가 running 상태인 것만으로는
    GPU 추론이 정상이라고 판단할 수 없습니다.

    TEI는 CUDA 초기화에 실패하더라도
    자동으로 CPU Backend를 선택할 수 있습니다.

    따라서 다음 오류 로그를 명시적으로 검사합니다.

    - CUDA_ERROR_NO_DEVICE
    - CUDA is not available
    - Using CPU instead
    - Starting Qwen3 model on Cpu

    CPU 폴백이 확인되면 TEI 컨테이너를 즉시 정지하고
    시작 스크립트를 실패 처리합니다.

    CPU 폴백 로그가 없고 실제 POST /embed 요청이 성공해야
    TEI 준비 완료로 판정합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $EmbeddingBaseUrl,

        [Parameter(Mandatory = $true)]
        [string[]] $ComposeArguments,

        [Parameter(Mandatory = $true)]
        [int] $TimeoutSeconds
    )

    $CpuFallbackPatterns = @(
        'CUDA_ERROR_NO_DEVICE',
        'CUDA is not available',
        'Using CPU instead',
        'Starting Qwen3 model on Cpu'
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $LastRequestError = '아직 임베딩 요청을 실행하지 않았습니다.'

    # 실제 추론 Backend를 통과하는 최소 검증 요청입니다.
    #
    # 사용자 파일이나 개인정보를 포함하지 않습니다.
    $RequestBody = @{
        inputs = 'Jipsa RAG CUDA startup verification'
    } | ConvertTo-Json -Compress

    while ((Get-Date) -lt $Deadline) {
        $ContainerState = Get-ContainerState `
            -ContainerName 'jipsa-embedding'

        $EmbeddingLogs = Get-ContainerLogs `
            -ContainerName 'jipsa-embedding' `
            -Tail 300

        # CUDA 초기화 실패 후 CPU Backend로 전환된 흔적이 있는지
        # 실제 컨테이너 로그에서 확인합니다.
        foreach ($Pattern in $CpuFallbackPatterns) {
            if ($EmbeddingLogs -match [regex]::Escape($Pattern)) {
                Stop-EmbeddingContainerQuietly `
                    -ComposeArguments $ComposeArguments

                throw @"
TEI가 NVIDIA CUDA GPU를 사용하지 못하고 CPU로 폴백했습니다.

감지한 로그:
$Pattern

불필요한 CPU 연산을 방지하기 위해
TEI 컨테이너를 정지했습니다.

최근 TEI 로그:
$EmbeddingLogs
"@
            }
        }

        if ($ContainerState.OOMKilled) {
            Stop-EmbeddingContainerQuietly `
                -ComposeArguments $ComposeArguments

            throw @"
TEI 컨테이너가 메모리 부족으로 종료됐습니다.

Status: $($ContainerState.Status)
ExitCode: $($ContainerState.ExitCode)
RestartCount: $($ContainerState.RestartCount)

최근 TEI 로그:
$EmbeddingLogs
"@
        }

        if (
            $ContainerState.Status -in @(
                'exited',
                'dead'
            )
        ) {
            throw @"
TEI 컨테이너가 모델 준비를 완료하기 전에 종료됐습니다.

Status: $($ContainerState.Status)
ExitCode: $($ContainerState.ExitCode)
OOMKilled: $($ContainerState.OOMKilled)
RestartCount: $($ContainerState.RestartCount)

최근 TEI 로그:
$EmbeddingLogs
"@
        }

        if ($ContainerState.Status -eq 'running') {
            $RequestSucceeded = $false

            try {
                # /embed 요청이 성공하면 다음 과정이 모두 완료된 상태입니다.
                #
                # - 모델 Artifact 준비
                # - 모델 Weight 로드
                # - CUDA Backend 초기화
                # - 모델 Warmup
                # - HTTP 요청 처리
                $null = Invoke-RestMethod `
                    -Method Post `
                    -Uri "$EmbeddingBaseUrl/embed" `
                    -ContentType 'application/json' `
                    -Body $RequestBody `
                    -TimeoutSec 10

                $RequestSucceeded = $true
            }
            catch {
                # 최초 모델 다운로드와 Warmup 중에는
                # 연결 거부 또는 일시적인 오류가 발생할 수 있습니다.
                #
                # 제한 시간까지 재시도하므로 즉시 실패시키지 않습니다.
                $LastRequestError = $_.Exception.Message
            }

            if ($RequestSucceeded) {
                # 요청 처리 직후 로그를 다시 확인하여
                # 실제 추론 시점에 발생한 CPU 폴백도 감지합니다.
                $FinalEmbeddingLogs = Get-ContainerLogs `
                    -ContainerName 'jipsa-embedding' `
                    -Tail 300

                foreach ($Pattern in $CpuFallbackPatterns) {
                    if ($FinalEmbeddingLogs -match [regex]::Escape($Pattern)) {
                        Stop-EmbeddingContainerQuietly `
                            -ComposeArguments $ComposeArguments

                        throw @"
TEI 임베딩 요청은 처리됐지만
GPU가 아닌 CPU Backend를 사용했습니다.

감지한 로그:
$Pattern

TEI 컨테이너를 정지했습니다.

최근 TEI 로그:
$FinalEmbeddingLogs
"@
                    }
                }

                Write-Host ''
                Write-Host 'TEI CUDA 임베딩 요청 검증 완료' `
                    -ForegroundColor Green

                return
            }
        }

        Write-Host (
            'TEI 모델 준비 대기 중... ' +
            "Status=$($ContainerState.Status), " +
            "RestartCount=$($ContainerState.RestartCount)"
        )

        Start-Sleep -Seconds 3
    }

    $TimeoutLogs = Get-ContainerLogs `
        -ContainerName 'jipsa-embedding' `
        -Tail 300

    throw @"
제한 시간 안에 TEI 임베딩 서버가 준비되지 않았습니다.

제한 시간: $TimeoutSeconds초
마지막 요청 오류: $LastRequestError

최근 TEI 로그:
$TimeoutLogs
"@
}


# ============================================================
# 프로세스 환경 변수 복원
# ============================================================

function Restore-ProcessEnvironmentVariable {
    <#
    .SYNOPSIS
    스크립트에서 임시로 변경한 프로세스 환경 변수를 복원합니다.

    .DESCRIPTION
    스크립트를 일반 실행하거나 Dot Sourcing 방식으로 실행해도
    기존 PowerShell 세션의 환경 변수 값이 변경되지 않도록 합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [AllowNull()]
        [string] $OriginalValue
    )

    [Environment]::SetEnvironmentVariable(
        $Name,
        $OriginalValue,
        'Process'
    )
}


# ============================================================
# 메인 실행 흐름
# ============================================================

Push-Location -LiteralPath $ProjectRoot

try {
    # ============================================================
    # 1. 필수 파일 및 명령 확인
    # ============================================================

    Write-Step -Message '필수 파일 및 명령 확인'

    if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
        throw "Docker Compose 파일을 찾을 수 없습니다: $ComposeFile"
    }

    Assert-CommandAvailable `
        -CommandName 'uv' `
        -InstallMessage '프로젝트에서 사용하는 uv를 설치한 후 다시 실행해 주세요.'

    Assert-CommandAvailable `
        -CommandName 'docker' `
        -InstallMessage 'Docker Desktop과 Docker CLI를 설치한 후 다시 실행해 주세요.'

    Write-Host "프로젝트 루트: $ProjectRoot"
    Write-Host "Compose 파일: $ComposeFile"


    # ============================================================
    # 2. RAG 실행 환경 결정
    # ============================================================

    Write-Step -Message 'RAG 실행 환경 결정'

    $AppEnvironment = Resolve-AppEnvironment

    # Python의 get_settings()가 현재 실행 프로필을 선택할 수 있도록
    # 환경 변수 검증 하위 프로세스에 실행 환경을 전달합니다.
    $env:JIPSA_RAG_APP_ENV = $AppEnvironment

    $EnvironmentFile = Join-Path `
        -Path $ProjectRoot `
        -ChildPath ".env.$AppEnvironment"

    Write-Host "실행 환경: $AppEnvironment"

    if (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf) {
        Write-Host "환경 파일: $EnvironmentFile"
    }
    else {
        # 프로젝트 설정은 dotenv 파일이 없을 때
        # OS 환경 변수만 사용하는 방식도 허용합니다.
        #
        # 실제 필수값 누락 여부는 다음 get_settings() 호출이 검증합니다.
        Write-Warning @"
환경 파일을 찾을 수 없습니다: $EnvironmentFile

OS 프로세스 환경 변수만으로 설정 검증을 계속합니다.
"@
    }


    # ============================================================
    # 3. RAG 필수 환경 변수 검증
    # ============================================================

    Write-Step -Message 'RAG 필수 환경 변수 검증'

    $Settings = Get-ValidatedRagSettings

    if ([string] $Settings.embedding_provider -ne 'tei') {
        throw @"
지원하지 않는 임베딩 Provider입니다: $($Settings.embedding_provider)

현재 Docker Compose 구성은 tei만 지원합니다.
"@
    }

    if ([string] $Settings.vector_db_provider -ne 'qdrant') {
        throw @"
지원하지 않는 VectorDB Provider입니다: $($Settings.vector_db_provider)

현재 Docker Compose 구성은 qdrant만 지원합니다.
"@
    }

    if ([string]::IsNullOrWhiteSpace([string] $Settings.embedding_model)) {
        throw 'JIPSA_RAG_EMBEDDING_MODEL이 비어 있습니다.'
    }

    if ([int] $Settings.embedding_dim -ne 1024) {
        throw @"
현재 임베딩 차원 설정이 올바르지 않습니다.

현재 값: $($Settings.embedding_dim)
필요한 값: 1024
"@
    }

    Assert-LocalHttpEndpoint `
        -SettingName 'JIPSA_RAG_EMBEDDING_BASE_URL' `
        -Value ([string] $Settings.embedding_base_url) `
        -ExpectedPort 18081

    Assert-LocalHttpEndpoint `
        -SettingName 'JIPSA_RAG_QDRANT_URL' `
        -Value ([string] $Settings.qdrant_url) `
        -ExpectedPort 6333

    if ([int] $Settings.qdrant_grpc_port -ne 6334) {
        throw @"
JIPSA_RAG_QDRANT_GRPC_PORT가 Docker Compose 설정과 일치하지 않습니다.

현재 값: $($Settings.qdrant_grpc_port)
필요한 값: 6334
"@
    }

    # Compose의 embedding command에
    # 최종 검증된 임베딩 모델을 전달합니다.
    #
    # 현재 PowerShell 프로세스와 이 프로세스가 실행하는
    # Docker Compose 하위 프로세스에서만 사용됩니다.
    $env:JIPSA_RAG_EMBEDDING_MODEL = [string] $Settings.embedding_model

    Write-Host "Embedding Provider: $($Settings.embedding_provider)"
    Write-Host "Embedding URL: $($Settings.embedding_base_url)"
    Write-Host "Embedding Model: $($Settings.embedding_model)"
    Write-Host "Embedding Dimension: $($Settings.embedding_dim)"
    Write-Host "VectorDB Provider: $($Settings.vector_db_provider)"
    Write-Host "Qdrant URL: $($Settings.qdrant_url)"
    Write-Host "Qdrant gRPC Port: $($Settings.qdrant_grpc_port)"


    # ============================================================
    # 4. Docker Engine 및 Docker Compose 확인
    # ============================================================

    Write-Step -Message 'Docker Engine 및 Docker Compose 확인'

    $DockerServerVersionOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList @(
                'version',
                '--format',
                '{{.Server.Version}}'
            ) `
            -FailureMessage @'
Docker Engine에 연결할 수 없습니다.

Docker Desktop이 실행 중이고
Docker Engine 준비가 완료됐는지 확인해 주세요.
'@
    )

    $DockerServerVersion = (
        $DockerServerVersionOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join ''

    if ([string]::IsNullOrWhiteSpace($DockerServerVersion)) {
        throw 'Docker Engine 버전 정보를 확인할 수 없습니다.'
    }

    $DockerComposeVersionOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList @(
                'compose',
                'version',
                '--short'
            ) `
            -FailureMessage @'
Docker Compose Plugin을 실행할 수 없습니다.

Docker Desktop에 Docker Compose v2가 설치됐는지 확인해 주세요.
'@
    )

    $DockerComposeVersion = (
        $DockerComposeVersionOutput |
            ForEach-Object {
                [string] $_
            }
    ) -join ''

    Write-Host "Docker Engine: $DockerServerVersion"
    Write-Host "Docker Compose: $DockerComposeVersion"


    # ============================================================
    # 5. Docker Compose 구성 검증
    # ============================================================

    Write-Step -Message 'Docker Compose 구성 검증'

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
    # 6. Qdrant 및 TEI 이미지 준비
    # ============================================================

    Write-Step -Message 'Qdrant 및 TEI 이미지 준비'

    Update-RequiredDockerImages `
        -ComposeArguments $ComposeBaseArguments


    # ============================================================
    # 7. Qdrant 컨테이너 생성·실행
    # ============================================================

    Write-Step -Message 'Qdrant 컨테이너 생성·실행'

    # Qdrant는 Compose 설정이나 이미지가 변경된 경우에만
    # Docker Compose가 필요한 컨테이너 재생성을 수행합니다.
    #
    # Named Volume은 유지되므로 기존 Collection과 벡터 데이터는
    # 컨테이너 재생성과 관계없이 보존됩니다.
    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList (
            $ComposeBaseArguments + @(
                'up',
                '--detach',
                '--remove-orphans',
                'qdrant'
            )
        ) `
        -FailureMessage 'Qdrant 컨테이너 실행에 실패했습니다.'


    # ============================================================
    # 8. TEI 컨테이너 강제 재생성·실행
    # ============================================================

    Write-Step -Message 'TEI GPU 컨테이너 재생성·실행'

    # 기존 cuda-1.9 이미지 또는 잘못된 Restart Policy로 생성된
    # 컨테이너가 재사용되지 않도록 TEI 컨테이너를 강제로 재생성합니다.
    #
    # Hugging Face 모델 Cache는 Named Volume에 저장되므로
    # 컨테이너를 재생성해도 모델을 다시 받을 필요가 없습니다.
    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList (
            $ComposeBaseArguments + @(
                'up',
                '--detach',
                '--force-recreate',
                'embedding'
            )
        ) `
        -FailureMessage 'TEI GPU 컨테이너 실행에 실패했습니다.'


    # ============================================================
    # 9. TEI 이미지 및 GPU 예약 검증
    # ============================================================

    Write-Step -Message 'TEI 이미지 및 NVIDIA GPU 예약 검증'

    Assert-EmbeddingContainerImage

    Assert-EmbeddingGpuReservation


    # ============================================================
    # 10. TEI CUDA 추론 준비 상태 검증
    # ============================================================

    Write-Step -Message 'TEI CUDA 임베딩 준비 상태 검증'

    Wait-EmbeddingGpuReady `
        -EmbeddingBaseUrl ([string] $Settings.embedding_base_url) `
        -ComposeArguments $ComposeBaseArguments `
        -TimeoutSeconds $EmbeddingStartupTimeoutSeconds


    # ============================================================
    # 11. 최종 컨테이너 상태 출력
    # ============================================================

    Write-Step -Message '최종 RAG 인프라 컨테이너 상태'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList (
            $ComposeBaseArguments + @(
                'ps',
                '--all'
            )
        ) `
        -FailureMessage 'RAG 인프라 컨테이너 상태 조회에 실패했습니다.'

    Write-Host ''
    Write-Host 'Qdrant 및 TEI 인프라 준비가 완료됐습니다.' `
        -ForegroundColor Green

    Write-Host 'TEI가 CPU로 폴백하지 않고 임베딩 요청을 처리했습니다.' `
        -ForegroundColor Green
}
catch {
    $OriginalError = $_

    Write-Host ''
    Write-Host '[RAG 로컬 인프라 실행 실패]' -ForegroundColor Red
    Write-Host $OriginalError.Exception.Message -ForegroundColor Red

    # 실행 실패 시 현재 Compose 상태와 최근 TEI 로그를
    # 최대한 출력하여 원인 분석에 사용할 수 있도록 합니다.
    #
    # 진단 출력 자체가 실패해도 원래 예외는 유지합니다.
    try {
        Write-Host ''
        Write-Host '[Docker Compose 상태]' -ForegroundColor DarkYellow

        $StatusArguments = $ComposeBaseArguments + @(
            'ps',
            '--all'
        )

        $PreviousErrorActionPreference = $ErrorActionPreference

        try {
            $ErrorActionPreference = 'Continue'
            & docker @StatusArguments
        }
        finally {
            $ErrorActionPreference = $PreviousErrorActionPreference
        }
    }
    catch {
        Write-Warning '실패 후 Docker Compose 상태를 조회하지 못했습니다.'
    }

    try {
        Write-Host ''
        Write-Host '[최근 TEI 로그]' -ForegroundColor DarkYellow

        $FailureLogs = Get-ContainerLogs `
            -ContainerName 'jipsa-embedding' `
            -Tail 200

        Write-Host $FailureLogs
    }
    catch {
        Write-Warning '실패 후 TEI 로그를 조회하지 못했습니다.'
    }

    throw
}
finally {
    # 스크립트 실행 전 사용자가 위치했던 디렉터리로 복원합니다.
    Pop-Location

    # Compose에 전달하기 위해 임시로 변경한 프로세스 환경 변수를
    # 스크립트 실행 전 값으로 복원합니다.
    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_APP_ENV' `
        -OriginalValue $OriginalAppEnvironment

    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_EMBEDDING_MODEL' `
        -OriginalValue $OriginalEmbeddingModel
}