Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Jipsa Local RAG Infrastructure Stop Script
# ============================================================
#
# 이 스크립트는 Docker Compose로 관리되는
# 로컬 RAG 인프라 컨테이너를 안전하게 정지합니다.
#
# 정지 대상:
#
# - jipsa-qdrant
# - jipsa-embedding
#
# 다음 리소스는 삭제하지 않습니다.
#
# - Docker 이미지
# - Docker 컨테이너
# - Docker Compose 네트워크
# - Qdrant 데이터 Volume
# - Qdrant Snapshot Volume
# - Hugging Face 모델 Cache Volume
#
# 따라서 다음 시작 시 기존 Qdrant 데이터와
# 다운로드된 Qwen3 모델을 그대로 재사용할 수 있습니다.
#
# 데이터 손실을 방지하기 위해 다음 명령은 사용하지 않습니다.
#
# - docker compose down --volumes
# - docker compose down -v
# - docker volume rm
#
# 중요:
#
# Windows PowerShell 5.1에서 한글 주석과 문자열을 안전하게 읽도록
# 이 파일은 UTF-8 with BOM 형식으로 저장해야 합니다.
# ============================================================


# ============================================================
# 프로젝트 경로 및 Compose 설정
# ============================================================

# 현재 스크립트 위치:
#
# RAG/scripts/stop-local-rag.ps1
#
# 따라서 $PSScriptRoot의 상위 디렉터리가 RAG 프로젝트 루트입니다.
$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path `
            -Path $PSScriptRoot `
            -ChildPath '..'
    )
).Path

# 시작 스크립트와 동일한 Docker Compose 파일입니다.
$ComposeFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'infra/qdrant/compose.yaml'

# Docker Compose 명령에서 반복해서 사용할 공통 인수입니다.
$ComposeBaseArguments = @(
    'compose',
    '--file',
    $ComposeFile
)


# ============================================================
# 공통 출력 함수
# ============================================================

function Write-Step {
    <#
    .SYNOPSIS
    현재 실행 중인 종료 단계를 출력합니다.
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
    Windows PowerShell 5.1에서는 Docker가 stderr에 출력한 내용을
    PowerShell ErrorRecord로 변환할 수 있습니다.

    따라서 Docker 실행 중에만 ErrorActionPreference를 Continue로
    변경하고 실제 성공 여부는 $LASTEXITCODE로 판단합니다.
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
    Docker Engine 버전이나 컨테이너 ID처럼
    후속 처리에 필요한 출력을 수집할 때 사용합니다.

    Windows PowerShell 5.1의 NativeCommandError 문제를 방지하기 위해
    명령 실행 중에만 ErrorActionPreference를 Continue로 변경합니다.
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
# 메인 종료 흐름
# ============================================================

Push-Location -LiteralPath $ProjectRoot

try {
    # ============================================================
    # 1. 필수 파일 및 Docker CLI 확인
    # ============================================================

    Write-Step -Message '필수 파일 및 Docker CLI 확인'

    if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
        throw "Docker Compose 파일을 찾을 수 없습니다: $ComposeFile"
    }

    Assert-CommandAvailable `
        -CommandName 'docker' `
        -InstallMessage 'Docker Desktop과 Docker CLI를 설치한 후 다시 실행해 주세요.'

    Write-Host "프로젝트 루트: $ProjectRoot"
    Write-Host "Compose 파일: $ComposeFile"


    # ============================================================
    # 2. Docker Engine 및 Docker Compose 확인
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
            -FailureMessage 'Docker Compose Plugin을 실행할 수 없습니다.'
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
    # 3. Docker Compose 구성 검증
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
    # 4. 관리 대상 컨테이너 존재 여부 확인
    # ============================================================

    Write-Step -Message 'RAG 인프라 컨테이너 확인'

    $ContainerIdOutput = @(
        Invoke-NativeCommandForOutput `
            -FilePath 'docker' `
            -ArgumentList (
                $ComposeBaseArguments + @(
                    'ps',
                    '--all',
                    '--quiet',
                    'qdrant',
                    'embedding'
                )
            ) `
            -FailureMessage 'RAG 인프라 컨테이너 조회에 실패했습니다.'
    )

    $ExistingContainerIds = @(
        $ContainerIdOutput |
            ForEach-Object {
                ([string] $_).Trim()
            } |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_)
            }
    )

    if ($ExistingContainerIds.Count -eq 0) {
        Write-Host ''
        Write-Host '정지할 Qdrant 또는 TEI 컨테이너가 없습니다.' `
            -ForegroundColor Yellow

        return
    }


    # ============================================================
    # 5. Qdrant 및 TEI 컨테이너 정지
    # ============================================================

    Write-Step -Message 'Qdrant 및 TEI 컨테이너 정지'

    # docker compose stop은 컨테이너만 정지합니다.
    #
    # 다음 리소스는 그대로 유지합니다.
    #
    # - 컨테이너 정의
    # - Docker 이미지
    # - Compose 네트워크
    # - Named Volume
    #
    # --timeout 30은 정상 종료 신호를 전달한 후
    # 강제 종료까지 최대 30초간 기다리도록 설정합니다.
    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList (
            $ComposeBaseArguments + @(
                'stop',
                '--timeout',
                '30',
                'qdrant',
                'embedding'
            )
        ) `
        -FailureMessage 'Qdrant 및 TEI 컨테이너 정지에 실패했습니다.'


    # ============================================================
    # 6. 종료 후 컨테이너 상태 출력
    # ============================================================

    Write-Step -Message '종료 후 RAG 인프라 컨테이너 상태'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList (
            $ComposeBaseArguments + @(
                'ps',
                '--all'
            )
        ) `
        -FailureMessage '종료 후 컨테이너 상태 조회에 실패했습니다.'

    Write-Host ''
    Write-Host 'Qdrant 및 TEI 컨테이너가 정지됐습니다.' `
        -ForegroundColor Green

    Write-Host 'Qdrant 데이터와 Hugging Face 모델 Cache는 유지됩니다.' `
        -ForegroundColor Green
}
finally {
    # 스크립트 실행 전 사용자가 위치했던 디렉터리로 복원합니다.
    Pop-Location
}