Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Jipsa Local RAG 통합 실행 스크립트
# ============================================================
#
# 이 스크립트는 로컬 Jipsa RAG 서비스를 실행하는 데 필요한
# 전체 생명주기를 하나의 PowerShell 프로세스에서 관리합니다.
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
# 8. Docker NVIDIA GPU 사용 가능 여부 사전 확인
# 9. Qdrant 컨테이너 생성 및 실행
# 10. Qdrant /readyz 준비 상태 확인
# 11. TEI GPU 컨테이너 생성 및 실행
# 12. TEI 컨테이너의 NVIDIA GPU 할당 확인
# 13. TEI 로그의 CUDA 실패 및 CPU 폴백 확인
# 14. 실제 /embed 요청을 통한 GPU 임베딩 준비 상태 확인
# 15. FastAPI RAG 서버 자동 실행
# 16. FastAPI 종료 시 Qdrant 및 TEI 컨테이너 자동 정지
#
# FastAPI 서버는 현재 PowerShell 콘솔의 Foreground Process로 실행됩니다.
#
# 따라서 사용자가 Ctrl+C로 FastAPI 서버를 종료하면
# Uvicorn의 정상 종료 절차가 먼저 실행된 후 다음 자원이 정리됩니다.
#
# - FastAPI lifespan 자원
# - Qdrant Client
# - SQLAlchemy AsyncEngine
# - Qdrant Docker 컨테이너
# - TEI Docker 컨테이너
#
# Docker 컨테이너를 정지하더라도 다음 데이터는 삭제하지 않습니다.
#
# - Qdrant Collection 및 Vector 데이터
# - Qdrant Snapshot
# - Hugging Face 모델 Cache
# - Docker 이미지
# - Docker Compose 네트워크
#
# 다음 항목은 이 스크립트에서 실행하지 않습니다.
#
# - Docker Desktop 프로그램 자체 실행
# - Local RAG MySQL 서버 실행
#
# Docker Desktop과 Local RAG MySQL은 이 스크립트를 실행하기 전에
# 사용자가 별도로 준비해야 합니다.
#
# 중요:
#
# Windows PowerShell 5.1은 BOM이 없는 UTF-8 PowerShell 파일을
# 시스템 기본 ANSI 인코딩으로 잘못 해석할 수 있습니다.
#
# 한글 주석과 문자열을 안전하게 사용하려면
# 이 파일을 반드시 UTF-8 with BOM 형식으로 저장해야 합니다.
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

# Qdrant와 TEI를 정의한 Docker Compose 파일입니다.
$ComposeFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'infra/qdrant/compose.yaml'

# FastAPI 종료 후 Qdrant와 TEI를 정지하는 기존 스크립트입니다.
#
# 자동 종료에서도 동일한 정지 정책을 적용하기 위해
# Docker Compose stop 명령을 중복 구현하지 않고 이 스크립트를 호출합니다.
$StopScript = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'scripts/stop-local-rag.ps1'

# Compose 파일에 정의된 Qdrant 이미지입니다.
#
# Registry Pull 실패 시 로컬 이미지 존재 여부를 확인하는 데 사용합니다.
$QdrantImage = 'qdrant/qdrant:v1.18.2'

# RTX 3060 Ti의 NVIDIA Ampere Compute Capability 8.6에 대응하는
# Hugging Face TEI CUDA 이미지입니다.
$EmbeddingImage = 'ghcr.io/huggingface/text-embeddings-inference:86-1.9'

# 현재 프로젝트의 config.py에서 지원하는 실행 환경입니다.
#
# src/jipsa_rag/core/config.py의 SUPPORTED_ENVIRONMENTS 값과
# 동일하게 유지해야 합니다.
$SupportedEnvironments = @(
    'local',
    'development',
    'test'
)

# Docker Compose 명령에서 공통으로 사용할 인수입니다.
$ComposeBaseArguments = @(
    'compose',
    '--file',
    $ComposeFile
)

# Qdrant는 일반적으로 빠르게 시작되지만,
# Docker Desktop 또는 Volume 복구 상황을 고려하여 최대 2분간 기다립니다.
$QdrantStartupTimeoutSeconds = 120

# TEI 최초 실행 시 다음 작업이 수행될 수 있습니다.
#
# - Hugging Face 모델 다운로드
# - 모델 Weight 로드
# - CUDA Backend 초기화
# - CUDA Kernel 준비
# - 모델 Warmup
#
# 최초 실행 시간을 고려하여 최대 20분간 기다립니다.
$EmbeddingStartupTimeoutSeconds = 1200

# 스크립트가 Compose와 FastAPI 하위 프로세스에 전달하기 위해
# 임시로 변경하는 환경 변수의 기존 값을 보관합니다.
$OriginalAppEnvironment = [Environment]::GetEnvironmentVariable(
    'JIPSA_RAG_APP_ENV',
    'Process'
)

$OriginalEmbeddingModel = [Environment]::GetEnvironmentVariable(
    'JIPSA_RAG_EMBEDDING_MODEL',
    'Process'
)

# Qdrant 또는 TEI의 실행을 시도한 이후에는
# 오류나 사용자 중단이 발생해도 자동 정지를 수행해야 합니다.
$InfrastructureMayBeRunning = $false

# FastAPI 실행과 자동 인프라 정지까지 정상적으로 완료됐는지 추적합니다.
#
# 정상 실행 후 인프라 정지만 실패한 경우
# 정지 실패를 최종 오류로 전달하기 위해 사용합니다.
$ScriptCompletedSuccessfully = $false


# ============================================================
# 공통 출력 함수
# ============================================================

function Write-Step {
    <#
    .SYNOPSIS
    현재 실행 중인 주요 단계를 출력합니다.

    .DESCRIPTION
    이미지 다운로드, 모델 Warmup, FastAPI 실행처럼
    시간이 걸릴 수 있는 작업의 진행 위치를 사용자가 확인할 수 있도록
    단계 제목을 구분하여 출력합니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $Message
    )

    Write-Host ''
    Write-Host "[$Message]" -ForegroundColor Cyan
}


# ============================================================
# 필수 명령 및 파일 확인
# ============================================================

function Assert-CommandAvailable {
    <#
    .SYNOPSIS
    필수 명령이 현재 PATH에서 실행 가능한지 확인합니다.

    .DESCRIPTION
    uv 또는 Docker CLI가 설치되지 않은 상태에서
    후속 명령을 실행하여 불명확한 오류가 발생하지 않도록
    실행 초기에 명령 존재 여부를 검사합니다.
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
    Windows PowerShell 5.1은 Docker나 uv가 stderr에 출력한 내용을
    PowerShell ErrorRecord로 변환할 수 있습니다.

    Docker는 정상적인 Pull 진행 정보도 stderr에 출력할 수 있으므로,
    전역 ErrorActionPreference가 Stop인 상태에서는
    실제 명령이 성공했는데도 PowerShell이 먼저 중단될 수 있습니다.

    따라서 외부 프로그램 실행 중에만
    ErrorActionPreference를 Continue로 변경합니다.

    명령의 실제 성공 여부는 PowerShell ErrorRecord가 아니라
    외부 프로그램이 반환한 $LASTEXITCODE를 기준으로 판정합니다.
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
        $ErrorActionPreference = 'Continue'

        & $FilePath @ArgumentList

        $ExitCode = $LASTEXITCODE
    }
    finally {
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
    Docker 버전, 컨테이너 상태, GPU 이름 또는 Python 설정 검증 결과처럼
    후속 로직에서 사용해야 하는 출력값을 가져올 때 사용합니다.

    표준 출력과 표준 오류를 모두 수집합니다.

    종료 코드가 0이 아니면 수집한 전체 출력을 먼저 표시한 뒤
    호출자가 지정한 오류 메시지와 함께 실행을 중단합니다.
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
    컨테이너가 정상적으로 실행돼도 FastAPI 애플리케이션은
    해당 서비스에 접속할 수 없습니다.

    다음 항목을 검증합니다.

    - HTTP 스킴 사용
    - 127.0.0.1 또는 localhost 사용
    - Docker Compose와 동일한 포트 사용
    - API 경로 미포함
    - Query String 미포함
    - Fragment 미포함
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
    PowerShell에 Pydantic 검증 규칙을 별도로 재구현하지 않고,
    RAG 애플리케이션이 사용하는 get_settings()를 직접 호출합니다.

    Windows PowerShell 5.1은 복잡한 Python -c 인수를 전달할 때
    Python 코드 내부의 큰따옴표를 손상시킬 수 있습니다.

    따라서 검증용 Python 코드를 UTF-8 임시 파일로 저장한 후
    uv run python 명령으로 실행합니다.

    임시 파일은 검증 성공 또는 실패 여부와 관계없이
    finally 블록에서 삭제합니다.

    출력에는 실행 자동화에 필요한 비민감 설정만 포함합니다.

    다음 값은 절대 출력하지 않습니다.

    - 데이터베이스 비밀번호
    - INTERNAL_TOKEN
    - RAG_INGEST_TOKEN
    - Qdrant API Key
    - Presigned URL
    #>

    $TemporaryScriptPath = Join-Path `
        -Path ([System.IO.Path]::GetTempPath()) `
        -ChildPath (
            'jipsa-rag-settings-validation-{0}.py' -f (
                [Guid]::NewGuid().ToString('N')
            )
        )

    $ValidationCode = @'
import json

from jipsa_rag.core.config import get_settings


settings = get_settings()

# PowerShell 실행 자동화에 필요한 비민감 설정만 출력한다.
print(
    json.dumps(
        {
            "app_name": settings.app_name,
            "app_env": settings.app_env,
            "host": settings.host,
            "port": settings.port,
            "debug": settings.debug,
            "embedding_provider": settings.embedding_provider,
            "embedding_base_url": settings.embedding_base_url,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "vector_db_provider": settings.vector_db_provider,
            "qdrant_url": settings.qdrant_url,
            "qdrant_grpc_port": settings.qdrant_grpc_port,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
)
'@

    try {
        # Python 임시 파일은 Windows PowerShell이 직접 파싱하지 않으므로
        # UTF-8 BOM 없이 저장해도 안전합니다.
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

        # uv가 부가 출력을 추가하더라도
        # JSON 객체로 시작하는 마지막 줄만 설정 검증 결과로 사용합니다.
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
        # 프로젝트 경로나 설정 코드가 포함된 임시 파일이
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
    지정한 Docker 이미지가 로컬 Docker Engine에 존재하는지 확인합니다.

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
    동일한 이미지 태그에 보안 또는 CUDA 호환성 수정이 반영될 수 있으므로
    로컬 이미지 존재 여부만 확인하지 않고 Pull을 먼저 시도합니다.

    Pull 실패 시 다음 정책을 적용합니다.

    - 필요한 이미지가 로컬에 모두 존재:
      경고를 출력하고 기존 로컬 이미지로 계속 진행

    - 필요한 이미지 중 하나 이상이 로컬에도 존재하지 않음:
      컨테이너 생성이 불가능하므로 실행 중단
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

로컬에 필요한 이미지가 존재하는지 확인한 후
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

네트워크와 Docker Registry 접근 상태를 확인한 후 다시 실행해 주세요.
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
    Qdrant 준비 실패, TEI CUDA 초기화 실패, CPU 폴백 또는
    모델 준비 실패 원인을 사용자에게 출력하기 위해 사용합니다.

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
    컨테이너가 실행 중인지, 정상 종료됐는지,
    OOM으로 종료됐는지 또는 반복 재시작 중인지 확인할 때 사용합니다.
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
        throw "$ContainerName 컨테이너 상태 출력을 해석할 수 없습니다: $StateText"
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
    CUDA 실패 또는 CPU 폴백이 확인된 TEI 컨테이너를 조용히 정지합니다.

    .DESCRIPTION
    GPU 대신 CPU로 실행되는 TEI를 계속 유지하면
    높은 CPU 사용률과 매우 긴 임베딩 응답 시간이 발생할 수 있습니다.

    기존 CUDA 오류 처리 중 호출되는 함수이므로
    컨테이너 정지 자체가 실패하더라도 원래 오류를 덮어쓰지 않습니다.
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
        # 정지 실패 예외는 다시 전달하지 않습니다.
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}


# ============================================================
# Docker NVIDIA GPU 사전 검증
# ============================================================

function Assert-DockerNvidiaGpuAvailable {
    <#
    .SYNOPSIS
    Docker 컨테이너에서 NVIDIA GPU를 사용할 수 있는지 사전 확인합니다.

    .DESCRIPTION
    Host에 NVIDIA GPU가 설치된 것만으로는
    Docker Desktop과 WSL2의 GPU 전달 기능이 정상이라는 보장이 없습니다.

    따라서 실제 TEI CUDA 이미지를 임시 컨테이너로 실행하고
    --gpus all 옵션을 통해 전달된 GPU에서 nvidia-smi를 실행합니다.

    이 검증이 성공하면 다음 항목이 준비된 상태입니다.

    - NVIDIA 그래픽 드라이버
    - WSL2 GPU 전달
    - Docker Desktop GPU 지원
    - NVIDIA Container Runtime 연동
    - TEI CUDA 이미지의 NVIDIA Runtime 접근

    임시 컨테이너는 --rm 옵션으로 실행되므로
    검증이 끝나면 자동으로 삭제됩니다.
    #>

    $GpuOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList @(
                'run',
                '--rm',
                '--gpus',
                'all',
                '--entrypoint',
                'nvidia-smi',
                $EmbeddingImage,
                '--query-gpu=name,driver_version',
                '--format=csv,noheader'
            ) `
            -FailureMessage @'
Docker 컨테이너에서 NVIDIA GPU를 사용할 수 없습니다.

다음 항목을 확인해 주세요.

- NVIDIA 그래픽 드라이버 설치 상태
- Windows에서 nvidia-smi 실행 가능 여부
- WSL2 업데이트 상태
- Docker Desktop WSL2 Backend 사용 여부
- Docker Desktop의 GPU 지원 상태
'@
    )

    $GpuLines = @(
        $GpuOutput |
            ForEach-Object {
                ([string] $_).Trim()
            } |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_)
            }
    )

    if ($GpuLines.Count -eq 0) {
        throw 'Docker NVIDIA GPU 검증 결과에서 GPU 정보를 확인할 수 없습니다.'
    }

    $GpuText = $GpuLines -join [Environment]::NewLine

    # 현재 TEI 이미지는 Compute Capability 8.6 전용이며
    # 프로젝트 로컬 개발 장비는 RTX 3060 Ti를 사용합니다.
    if ($GpuText -notmatch 'RTX 3060 Ti') {
        throw @"
Docker에서 NVIDIA GPU는 확인됐지만 예상한 RTX 3060 Ti가 아닙니다.

현재 GPU:
$GpuText

현재 TEI 이미지는 RTX 3060 Ti의 Compute Capability 8.6을 기준으로 구성됐습니다.
"@
    }

    Write-Host 'Docker NVIDIA GPU 사용 가능'
    Write-Host $GpuText
}


# ============================================================
# Qdrant 준비 상태 검증
# ============================================================

function Wait-QdrantReady {
    <#
    .SYNOPSIS
    Qdrant가 실제 요청을 받을 준비가 될 때까지 기다립니다.

    .DESCRIPTION
    Docker 컨테이너가 running 상태인 것만으로는
    Qdrant의 REST API가 요청을 받을 준비가 됐다고 판단할 수 없습니다.

    따라서 Qdrant 공식 준비 상태 엔드포인트인 /readyz를 반복 호출합니다.

    다음 조건을 모두 만족해야 준비 완료로 판정합니다.

    - jipsa-qdrant 컨테이너가 running 상태
    - 컨테이너가 OOM으로 종료되지 않음
    - /readyz 요청이 HTTP 성공 응답 반환

    컨테이너가 종료되거나 제한 시간을 초과하면
    최근 Qdrant 로그를 포함하여 오류를 발생시킵니다.
    #>

    param(
        [Parameter(Mandatory = $true)]
        [string] $QdrantBaseUrl,

        [Parameter(Mandatory = $true)]
        [int] $TimeoutSeconds
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $LastRequestError = '아직 Qdrant 준비 상태 요청을 실행하지 않았습니다.'

    while ((Get-Date) -lt $Deadline) {
        $ContainerState = Get-ContainerState `
            -ContainerName 'jipsa-qdrant'

        if ($ContainerState.OOMKilled) {
            $QdrantLogs = Get-ContainerLogs `
                -ContainerName 'jipsa-qdrant' `
                -Tail 200

            throw @"
Qdrant 컨테이너가 메모리 부족으로 종료됐습니다.

Status: $($ContainerState.Status)
ExitCode: $($ContainerState.ExitCode)
RestartCount: $($ContainerState.RestartCount)

최근 Qdrant 로그:
$QdrantLogs
"@
        }

        if (
            $ContainerState.Status -in @(
                'exited',
                'dead'
            )
        ) {
            $QdrantLogs = Get-ContainerLogs `
                -ContainerName 'jipsa-qdrant' `
                -Tail 200

            throw @"
Qdrant 컨테이너가 준비를 완료하기 전에 종료됐습니다.

Status: $($ContainerState.Status)
ExitCode: $($ContainerState.ExitCode)
OOMKilled: $($ContainerState.OOMKilled)
RestartCount: $($ContainerState.RestartCount)

최근 Qdrant 로그:
$QdrantLogs
"@
        }

        if ($ContainerState.Status -eq 'running') {
            try {
                # /readyz가 성공해야 Collection 및 Vector 요청을 처리할
                # 기본 서버 초기화가 완료된 것으로 판정합니다.
                $null = Invoke-RestMethod `
                    -Method Get `
                    -Uri "$QdrantBaseUrl/readyz" `
                    -TimeoutSec 5

                Write-Host ''
                Write-Host 'Qdrant REST API 준비 상태 확인 완료' `
                    -ForegroundColor Green

                return
            }
            catch {
                # 컨테이너가 막 시작된 시점에는
                # TCP 연결 또는 HTTP 요청이 일시적으로 실패할 수 있습니다.
                $LastRequestError = $_.Exception.Message
            }
        }

        Write-Host (
            'Qdrant 준비 대기 중... ' +
            "Status=$($ContainerState.Status), " +
            "RestartCount=$($ContainerState.RestartCount)"
        )

        Start-Sleep -Seconds 2
    }

    $TimeoutLogs = Get-ContainerLogs `
        -ContainerName 'jipsa-qdrant' `
        -Tail 200

    throw @"
제한 시간 안에 Qdrant REST API가 준비되지 않았습니다.

제한 시간: $TimeoutSeconds초
마지막 요청 오류: $LastRequestError

최근 Qdrant 로그:
$TimeoutLogs
"@
}


# ============================================================
# TEI 이미지 및 GPU 할당 검증
# ============================================================

function Assert-EmbeddingContainerImage {
    <#
    .SYNOPSIS
    TEI 컨테이너가 RTX 3060 Ti용 이미지로 실행됐는지 확인합니다.

    .DESCRIPTION
    이전 범용 CUDA 이미지 또는 다른 Architecture용 이미지로 생성된
    기존 컨테이너가 재사용되지 않았는지 검사합니다.
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
    실행된 TEI 컨테이너에 NVIDIA GPU가 실제로 할당됐는지 확인합니다.

    .DESCRIPTION
    Compose 파일에 GPU 예약 설정이 있어도
    기존 컨테이너가 잘못된 설정으로 생성된 경우
    GPU Device Request가 적용되지 않을 수 있습니다.

    다음 항목을 확인합니다.

    - NVIDIA Driver Device Request 존재
    - gpu capability 요청 존재
    - 컨테이너 내부 nvidia-smi 실행 성공
    - RTX 3060 Ti 인식
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

    # Docker Inspect 설정만 확인하는 것으로 끝내지 않고,
    # 실제 실행 중인 TEI 컨테이너 내부에서 GPU를 조회합니다.
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
    TEI가 CPU로 폴백하지 않고 실제 임베딩 요청을 처리할 때까지 기다립니다.

    .DESCRIPTION
    컨테이너가 running 상태인 것만으로는
    GPU 추론이 정상이라고 판단할 수 없습니다.

    TEI는 CUDA 초기화에 실패한 경우
    CPU Backend로 전환될 수 있으므로 다음 오류 로그를 검사합니다.

    - CUDA_ERROR_NO_DEVICE
    - CUDA is not available
    - Using CPU instead
    - Starting Qwen3 model on Cpu

    CPU 폴백이 확인되면 불필요한 CPU 연산을 막기 위해
    TEI 컨테이너를 즉시 정지하고 시작 스크립트를 실패 처리합니다.

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
    # 사용자 파일, 토큰 또는 개인정보는 포함하지 않습니다.
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
                # /embed 요청 성공은 다음 과정이 모두 완료됐음을 의미합니다.
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
                # 최초 모델 다운로드와 Warmup 중에는 연결 거부 또는
                # 일시적인 HTTP 오류가 발생할 수 있으므로 재시도합니다.
                $LastRequestError = $_.Exception.Message
            }

            if ($RequestSucceeded) {
                # 실제 추론 요청 직후 로그를 다시 확인하여
                # 요청 처리 시점에 발생한 CPU 폴백도 감지합니다.
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
# FastAPI 서버 실행
# ============================================================

function Invoke-FastApiServer {
    <#
    .SYNOPSIS
    검증된 설정을 사용하여 FastAPI RAG 서버를 Foreground로 실행합니다.

    .DESCRIPTION
    프로젝트의 pyproject.toml에 정의된 다음 명령을 실행합니다.

        uv run jipsa-rag

    해당 명령은 jipsa_rag.main:main을 호출하여
    Uvicorn 기반 FastAPI 서버를 시작합니다.

    Foreground로 실행하는 이유는 다음과 같습니다.

    - Uvicorn 로그를 현재 콘솔에서 직접 확인
    - Ctrl+C 신호를 Uvicorn에 직접 전달
    - FastAPI lifespan 종료 처리 보장
    - FastAPI 프로세스 종료 시점을 정확히 감지
    - 종료 직후 Qdrant 및 TEI 자동 정지

    정상적인 사용자 중단에서 발생할 수 있는 다음 종료 코드는
    오류가 아닌 정상 종료로 처리합니다.

    - 0: 정상 종료
    - 130: Unix 계열 SIGINT 종료
    - -1073741510: Windows Ctrl+C 종료 코드 0xC000013A
    #>

    param(
        [Parameter(Mandatory = $true)]
        [PSCustomObject] $Settings
    )

    $ConfiguredHost = [string] $Settings.host
    $DisplayHost = $ConfiguredHost

    # 0.0.0.0은 서버 Bind 주소이며 브라우저 접근 주소로는 부적합하므로
    # 사용자 안내에는 127.0.0.1을 표시합니다.
    if ($DisplayHost -eq '0.0.0.0') {
        $DisplayHost = '127.0.0.1'
    }

    $ServerUrl = "http://${DisplayHost}:$($Settings.port)"

    Write-Host "FastAPI 서비스: $($Settings.app_name)"
    Write-Host "실행 환경: $($Settings.app_env)"
    Write-Host "Bind 주소: $ConfiguredHost"
    Write-Host "FastAPI URL: $ServerUrl"
    Write-Host "Swagger UI: $ServerUrl/docs"
    Write-Host "Debug/Reload: $($Settings.debug)"

    Write-Host ''
    Write-Host 'FastAPI 서버를 종료하려면 Ctrl+C를 입력하세요.' `
        -ForegroundColor Yellow

    Write-Host (
        'FastAPI 종료 후 Qdrant와 TEI 컨테이너가 자동으로 정지됩니다.'
    ) -ForegroundColor Yellow

    Write-Host ''

    $PreviousErrorActionPreference = $ErrorActionPreference
    $ExitCode = $null

    try {
        # uv와 Uvicorn의 stderr 로그가 PowerShell terminating error로
        # 변환되지 않도록 Native Process 실행 중에만 Continue를 사용합니다.
        #
        # 출력을 Capture하지 않으므로 Uvicorn 로그가 현재 콘솔에
        # 실시간으로 그대로 표시됩니다.
        $ErrorActionPreference = 'Continue'

        & uv run jipsa-rag

        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($null -eq $ExitCode) {
        throw 'FastAPI 실행 프로세스의 종료 코드를 확인할 수 없습니다.'
    }

    $GracefulExitCodes = @(
        0,
        130,
        -1073741510
    )

    if ($ExitCode -notin $GracefulExitCodes) {
        throw "FastAPI RAG 서버가 비정상 종료됐습니다. 종료 코드: $ExitCode"
    }

    Write-Host ''
    Write-Host "FastAPI RAG 서버 종료 확인 완료: ExitCode=$ExitCode" `
        -ForegroundColor Green
}


# ============================================================
# 자동 인프라 정지
# ============================================================

function Invoke-LocalInfrastructureStop {
    <#
    .SYNOPSIS
    기존 stop-local-rag.ps1을 호출하여 Qdrant와 TEI를 정지합니다.

    .DESCRIPTION
    시작 스크립트와 수동 정지 스크립트가 서로 다른 종료 정책을 갖지 않도록
    Docker Compose stop 로직을 중복 작성하지 않습니다.

    stop-local-rag.ps1은 다음 리소스를 유지합니다.

    - Docker 이미지
    - Docker 컨테이너 정의
    - Docker Compose 네트워크
    - Qdrant 데이터 Volume
    - Qdrant Snapshot Volume
    - Hugging Face 모델 Cache Volume
    #>

    if (-not (Test-Path -LiteralPath $StopScript -PathType Leaf)) {
        throw "RAG 인프라 종료 스크립트를 찾을 수 없습니다: $StopScript"
    }

    & $StopScript
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
# 실패 후 진단 정보 출력
# ============================================================

function Write-FailureDiagnostics {
    <#
    .SYNOPSIS
    실행 실패 시 현재 Compose 상태와 최근 컨테이너 로그를 출력합니다.

    .DESCRIPTION
    진단 출력 자체가 실패하더라도
    원래 발생한 오류를 덮어쓰지 않도록 각 단계를 독립적으로 처리합니다.
    #>

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
        Write-Host '[최근 Qdrant 로그]' -ForegroundColor DarkYellow

        $QdrantFailureLogs = Get-ContainerLogs `
            -ContainerName 'jipsa-qdrant' `
            -Tail 200

        Write-Host $QdrantFailureLogs
    }
    catch {
        Write-Warning '실패 후 Qdrant 로그를 조회하지 못했습니다.'
    }

    try {
        Write-Host ''
        Write-Host '[최근 TEI 로그]' -ForegroundColor DarkYellow

        $EmbeddingFailureLogs = Get-ContainerLogs `
            -ContainerName 'jipsa-embedding' `
            -Tail 200

        Write-Host $EmbeddingFailureLogs
    }
    catch {
        Write-Warning '실패 후 TEI 로그를 조회하지 못했습니다.'
    }
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

    if (-not (Test-Path -LiteralPath $StopScript -PathType Leaf)) {
        throw "RAG 인프라 종료 스크립트를 찾을 수 없습니다: $StopScript"
    }

    Assert-CommandAvailable `
        -CommandName 'uv' `
        -InstallMessage '프로젝트에서 사용하는 uv를 설치한 후 다시 실행해 주세요.'

    Assert-CommandAvailable `
        -CommandName 'docker' `
        -InstallMessage 'Docker Desktop과 Docker CLI를 설치한 후 다시 실행해 주세요.'

    Write-Host "프로젝트 루트: $ProjectRoot"
    Write-Host "Compose 파일: $ComposeFile"
    Write-Host "종료 스크립트: $StopScript"


    # ============================================================
    # 2. RAG 실행 환경 결정
    # ============================================================

    Write-Step -Message 'RAG 실행 환경 결정'

    $AppEnvironment = Resolve-AppEnvironment

    # Python get_settings()와 FastAPI 하위 프로세스가
    # 동일한 실행 프로필을 선택하도록 프로세스 환경 변수에 설정합니다.
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

    # Compose의 embedding command에 검증된 임베딩 모델을 전달합니다.
    #
    # 현재 PowerShell 프로세스와 이 프로세스가 실행하는
    # Docker Compose 및 FastAPI 하위 프로세스에서만 사용됩니다.
    $env:JIPSA_RAG_EMBEDDING_MODEL = [string] $Settings.embedding_model

    Write-Host "FastAPI Host: $($Settings.host)"
    Write-Host "FastAPI Port: $($Settings.port)"
    Write-Host "FastAPI Debug: $($Settings.debug)"
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
    # 7. Docker NVIDIA GPU 사용 가능 여부 확인
    # ============================================================

    Write-Step -Message 'Docker NVIDIA GPU 사용 가능 여부 확인'

    Assert-DockerNvidiaGpuAvailable


    # ============================================================
    # 8. Qdrant 컨테이너 생성 및 실행
    # ============================================================

    Write-Step -Message 'Qdrant 컨테이너 생성·실행'

    # 이 시점부터 Docker Compose가 컨테이너를 일부라도 생성하거나
    # 실행했을 가능성이 있으므로, 이후 정상 종료나 오류 발생 시
    # finally 블록에서 자동 정지를 수행합니다.
    $InfrastructureMayBeRunning = $true

    # Compose 설정 또는 이미지가 변경된 경우에만
    # Docker Compose가 필요한 컨테이너 재생성을 수행합니다.
    #
    # Named Volume은 유지되므로 기존 Collection과 Vector 데이터는
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
    # 9. Qdrant 준비 상태 확인
    # ============================================================

    Write-Step -Message 'Qdrant REST API 준비 상태 검증'

    Wait-QdrantReady `
        -QdrantBaseUrl ([string] $Settings.qdrant_url) `
        -TimeoutSeconds $QdrantStartupTimeoutSeconds


    # ============================================================
    # 10. TEI GPU 컨테이너 강제 재생성 및 실행
    # ============================================================

    Write-Step -Message 'TEI GPU 컨테이너 재생성·실행'

    # 이전 CUDA 이미지, 잘못된 Entrypoint 또는 GPU 설정으로 생성된
    # 컨테이너가 재사용되지 않도록 TEI 컨테이너를 강제로 재생성합니다.
    #
    # Hugging Face 모델 Cache는 Named Volume에 저장되므로
    # 컨테이너를 재생성해도 모델을 다시 다운로드할 필요가 없습니다.
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
    # 11. TEI 이미지 및 NVIDIA GPU 할당 검증
    # ============================================================

    Write-Step -Message 'TEI 이미지 및 NVIDIA GPU 할당 검증'

    Assert-EmbeddingContainerImage

    Assert-EmbeddingGpuReservation


    # ============================================================
    # 12. TEI CUDA 추론 준비 상태 검증
    # ============================================================

    Write-Step -Message 'TEI CUDA 임베딩 준비 상태 검증'

    Wait-EmbeddingGpuReady `
        -EmbeddingBaseUrl ([string] $Settings.embedding_base_url) `
        -ComposeArguments $ComposeBaseArguments `
        -TimeoutSeconds $EmbeddingStartupTimeoutSeconds


    # ============================================================
    # 13. 최종 인프라 상태 출력
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

    Write-Host 'Qdrant가 REST 요청을 처리할 준비가 됐습니다.' `
        -ForegroundColor Green

    Write-Host 'TEI가 NVIDIA GPU로 실제 임베딩 요청을 처리했습니다.' `
        -ForegroundColor Green


    # ============================================================
    # 14. FastAPI RAG 서버 자동 실행
    # ============================================================

    Write-Step -Message 'FastAPI RAG 서버 자동 실행'

    # 이 호출은 FastAPI가 종료될 때까지 반환되지 않습니다.
    #
    # 사용자가 Ctrl+C를 입력하면 Uvicorn이 정상 종료된 후
    # 다음 finally 블록에서 Qdrant와 TEI가 자동 정지됩니다.
    Invoke-FastApiServer `
        -Settings $Settings

    # FastAPI가 정상 종료 코드로 반환됐음을 기록합니다.
    #
    # 이후 인프라 정지에 실패하면 정지 실패를 최종 오류로 전달합니다.
    $ScriptCompletedSuccessfully = $true
}
catch [System.Management.Automation.PipelineStoppedException] {
    # Windows PowerShell이 Ctrl+C를 Uvicorn 종료 코드 대신
    # PipelineStoppedException으로 전달하는 환경도 고려합니다.
    #
    # 사용자 중단은 정상적인 서버 종료 요청으로 처리하되,
    # finally 블록의 인프라 자동 정지는 그대로 수행합니다.
    Write-Host ''
    Write-Host '사용자 요청으로 FastAPI 실행이 중단됐습니다.' `
        -ForegroundColor Yellow

    $ScriptCompletedSuccessfully = $true
}
catch {
    $OriginalError = $_

    Write-Host ''
    Write-Host '[RAG 로컬 서비스 실행 실패]' -ForegroundColor Red
    Write-Host $OriginalError.Exception.Message -ForegroundColor Red

    # 컨테이너 실행을 시도한 이후에만 Docker 진단 정보를 출력합니다.
    if ($InfrastructureMayBeRunning) {
        Write-FailureDiagnostics
    }

    throw
}
finally {
    $CleanupError = $null

    # Qdrant 실행을 시도한 이후에는 FastAPI 정상 종료, Ctrl+C,
    # 시작 실패 또는 런타임 오류 여부와 관계없이 인프라를 정지합니다.
    if ($InfrastructureMayBeRunning) {
        try {
            Write-Step -Message 'FastAPI 종료 후 RAG 인프라 자동 정지'

            Invoke-LocalInfrastructureStop
        }
        catch {
            $CleanupError = $_

            Write-Host ''
            Write-Host '[RAG 인프라 자동 정지 실패]' -ForegroundColor Red
            Write-Host $CleanupError.Exception.Message -ForegroundColor Red
        }
    }

    # Compose와 FastAPI에 전달하기 위해 임시로 변경한
    # 프로세스 환경 변수를 실행 전 값으로 복원합니다.
    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_APP_ENV' `
        -OriginalValue $OriginalAppEnvironment

    Restore-ProcessEnvironmentVariable `
        -Name 'JIPSA_RAG_EMBEDDING_MODEL' `
        -OriginalValue $OriginalEmbeddingModel

    # 스크립트 실행 전에 사용자가 위치했던 디렉터리로 복원합니다.
    Pop-Location

    # FastAPI까지 정상적으로 종료됐지만 인프라 정지만 실패했다면
    # 정지 실패를 숨기지 않고 최종 오류로 전달합니다.
    #
    # 기존 실행 오류가 존재하는 경우에는 해당 오류를 보존하기 위해
    # Cleanup 오류로 덮어쓰지 않습니다.
    if (
        $null -ne $CleanupError -and
        $ScriptCompletedSuccessfully
    ) {
        throw $CleanupError
    }
}