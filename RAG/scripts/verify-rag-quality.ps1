#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Jipsa RAG 전체 코드 품질 검증 스크립트
# ============================================================
#
# 이 스크립트는 RAG 프로젝트의 필수 품질 검사를 다음 순서로 수행합니다.
#
# 1. 프로젝트 구조 및 uv 설치 확인
# 2. uv.lock 기준 개발·테스트 의존성 동기화
# 3. Ruff 포맷 검사
# 4. Ruff 린트 검사
# 5. Mypy 전체 정적 타입 검사
# 6. 전체 Pytest 회귀 테스트
#
# 검사 중 하나라도 실패하면 즉시 비정상 종료합니다.
#
# 이 스크립트는 소스 파일을 자동 수정하지 않습니다.
#
# 따라서 다음 명령은 사용하지 않습니다.
#
# - ruff format .
# - ruff check --fix .
#
# 실제 PDF·TEI·Qdrant·Claude E2E 테스트는 비용과 로컬 인프라 상태 변경을
# 동반하므로 일반 전체 Pytest에서는 실행하지 않습니다.
#
# 전체 Pytest를 실행하는 동안 JIPSA_RAG_RUN_E2E 환경 변수를 제거하여
# tests/e2e/test_real_pdf_rag_e2e.py가 명시적으로 skip되도록 보장합니다.
#
# 중요:
#
# Windows PowerShell 5.1에서 한글 주석과 출력 문자열을 안전하게 처리하려면
# 이 파일을 UTF-8 with BOM 형식으로 저장해야 합니다.
# ============================================================


# ============================================================
# 프로젝트 경로
# ============================================================

# 현재 스크립트 위치:
#
# RAG/scripts/verify-rag-quality.ps1
#
# 따라서 스크립트 디렉터리의 상위 디렉터리를 RAG 프로젝트 루트로 사용합니다.
$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path `
            -Path $PSScriptRoot `
            -ChildPath '..'
    )
).Path

# 검사에 반드시 필요한 프로젝트 파일과 디렉터리입니다.
$RequiredPaths = @(
    @{
        Path = 'pyproject.toml'
        Type = 'Leaf'
    },
    @{
        Path = 'uv.lock'
        Type = 'Leaf'
    },
    @{
        Path = 'src'
        Type = 'Container'
    },
    @{
        Path = 'tests'
        Type = 'Container'
    },
    @{
        Path = 'tests/e2e/test_real_pdf_rag_e2e.py'
        Type = 'Leaf'
    }
)


# ============================================================
# 환경 변수 원본 보관
# ============================================================

# 품질 검증이 종료되면 호출자가 사용하던 프로세스 환경을 복원합니다.
#
# 실제 E2E 실행 여부, Python UTF-8 설정 및 RAG 실행 환경이
# 이 스크립트 실행 때문에 영구적으로 변경되지 않아야 합니다.
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

# 외부 프로그램 출력 인코딩도 실행 종료 후 원래 값으로 복원합니다.
$OriginalOutputEncoding = $OutputEncoding
$OriginalConsoleInputEncoding = [Console]::InputEncoding
$OriginalConsoleOutputEncoding = [Console]::OutputEncoding

$Utf8Encoding = [System.Text.UTF8Encoding]::new($false)

# Push-Location 성공 여부를 추적하여
# 경로 변경 전 실패한 경우 잘못된 Pop-Location을 실행하지 않게 합니다.
$LocationPushed = $false


# ============================================================
# 공통 출력 함수
# ============================================================

function Write-Step {
    <#
    .SYNOPSIS
        현재 실행 중인 품질 검사 단계를 출력합니다.

    .DESCRIPTION
        Ruff, Mypy 및 Pytest처럼 실행 시간이 서로 다른 검사 단계의
        현재 진행 위치를 사용자가 명확하게 확인할 수 있도록 출력합니다.
    #>

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
    <#
    .SYNOPSIS
        필수 명령이 현재 PATH에서 실행 가능한지 확인합니다.

    .DESCRIPTION
        uv가 설치되지 않은 상태에서 후속 검사를 실행하면
        각 단계에서 서로 다른 오류가 발생할 수 있습니다.

        실행 초기에 명령 존재 여부를 확인하여 명확한 오류를 반환합니다.
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

    Write-Host "$CommandName 실행 파일: $($Command.Source)"
}


# ============================================================
# Native Command 실행 함수
# ============================================================

function Invoke-NativeCommand {
    <#
    .SYNOPSIS
        외부 프로그램을 실행하고 실제 종료 코드를 검사합니다.

    .DESCRIPTION
        Windows PowerShell 5.1은 외부 프로그램이 stderr에 출력한 내용을
        PowerShell ErrorRecord로 변환할 수 있습니다.

        uv, Ruff, Mypy 및 Pytest는 정상적인 진행 정보나 경고를 stderr에
        출력할 수 있으므로 ErrorActionPreference만으로 성공 여부를
        판단하면 정상 실행도 실패로 오인할 수 있습니다.

        따라서 외부 명령 실행 중에만 ErrorActionPreference를 Continue로
        변경하고, 실제 성공 여부는 $LASTEXITCODE로 판단합니다.
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

        # 이전 Native Command의 종료 코드가 남아 현재 결과로 오인되지 않도록
        # 실행 전에 명시적으로 초기화합니다.
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


# ============================================================
# 프로젝트 구조 확인 함수
# ============================================================

function Assert-ProjectStructure {
    <#
    .SYNOPSIS
        품질 검사에 필요한 프로젝트 파일과 디렉터리를 확인합니다.

    .DESCRIPTION
        잘못된 작업 디렉터리나 불완전한 Checkout 상태에서
        Ruff·Mypy·Pytest가 일부 파일만 검사하는 문제를 방지합니다.
    #>

    foreach ($RequiredPath in $RequiredPaths) {
        $RelativePath = [string] $RequiredPath.Path
        $PathType = [string] $RequiredPath.Type

        if (
            -not (
                Test-Path `
                    -LiteralPath $RelativePath `
                    -PathType $PathType
            )
        ) {
            throw (
                '필수 프로젝트 경로를 찾을 수 없습니다: ' +
                $RelativePath
            )
        }
    }

    Write-Host '필수 프로젝트 파일과 테스트 디렉터리를 확인했습니다.'
}


# ============================================================
# 프로세스 환경 변수 복원 함수
# ============================================================

function Restore-ProcessEnvironment {
    <#
    .SYNOPSIS
        스크립트 실행 전에 존재하던 프로세스 환경 변수를 복원합니다.
    #>

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
# 메인 품질 검사 흐름
# ============================================================

try {
    Push-Location -LiteralPath $ProjectRoot
    $LocationPushed = $true

    # 일반 품질 검사는 반드시 test 환경 설정을 사용합니다.
    #
    # local 또는 development의 실제 서비스 설정을 잘못 읽어
    # 운영성 DB나 외부 서비스에 접근하는 것을 방지합니다.
    $env:JIPSA_RAG_APP_ENV = 'test'

    # Windows PowerShell과 Python의 입출력 인코딩을 UTF-8로 통일합니다.
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    $OutputEncoding = $Utf8Encoding
    [Console]::InputEncoding = $Utf8Encoding
    [Console]::OutputEncoding = $Utf8Encoding

    # 실제 E2E 환경 변수가 호출자 세션에 남아 있어도
    # 전체 Pytest에서 Claude·TEI·Qdrant 실제 호출이 발생하지 않게 합니다.
    Remove-Item `
        -LiteralPath 'Env:JIPSA_RAG_RUN_E2E' `
        -ErrorAction SilentlyContinue

    # ============================================================
    # 1. 필수 실행 도구 및 프로젝트 구조 확인
    # ============================================================

    Write-Step -Message '필수 실행 도구 및 프로젝트 구조 확인'

    Assert-CommandAvailable `
        -CommandName 'uv' `
        -InstallMessage @'
Python 패키지 및 실행 환경 관리 도구인 uv를 설치하고
현재 PowerShell PATH에서 실행할 수 있도록 설정해 주세요.
'@

    Assert-ProjectStructure

    Write-Host "프로젝트 루트: $ProjectRoot"


    # ============================================================
    # 2. 의존성 동기화
    # ============================================================

    Write-Step -Message 'uv.lock 기준 의존성 동기화'

    # --frozen을 사용하여 pyproject.toml과 uv.lock이 불일치할 때
    # uv.lock을 자동 변경하지 않고 즉시 실패하도록 합니다.
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'sync',
            '--frozen'
        ) `
        -FailureMessage 'uv 의존성 동기화에 실패했습니다.'

    Write-Host 'uv 의존성 동기화 통과' -ForegroundColor Green


    # ============================================================
    # 3. Ruff 포맷 검사
    # ============================================================

    Write-Step -Message 'Ruff 전체 포맷 검사'

    # 자동 포맷을 수행하지 않고 저장소 전체가 현재 Ruff 포맷 규칙을
    # 만족하는지만 확인합니다.
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'ruff',
            'format',
            '--check',
            '.'
        ) `
        -FailureMessage 'Ruff 포맷 검사에 실패했습니다.'

    Write-Host 'Ruff 포맷 검사 통과' -ForegroundColor Green


    # ============================================================
    # 4. Ruff 린트 검사
    # ============================================================

    Write-Step -Message 'Ruff 전체 린트 검사'

    # pyproject.toml에 정의된 E, F, I, B, UP, SIM, RUF 규칙을
    # src와 tests 전체에 적용합니다.
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'ruff',
            'check',
            '.'
        ) `
        -FailureMessage 'Ruff 린트 검사에 실패했습니다.'

    Write-Host 'Ruff 린트 검사 통과' -ForegroundColor Green


    # ============================================================
    # 5. Mypy 전체 정적 타입 검사
    # ============================================================

    Write-Step -Message 'Mypy 전체 정적 타입 검사'

    # 애플리케이션 코드와 테스트 코드 사이의 Protocol, Pydantic 모델,
    # Fixture 및 비동기 함수 타입 계약을 함께 검사합니다.
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'mypy',
            'src',
            'tests'
        ) `
        -FailureMessage 'Mypy 정적 타입 검사에 실패했습니다.'

    Write-Host 'Mypy 정적 타입 검사 통과' -ForegroundColor Green


    # ============================================================
    # 6. 전체 Pytest 회귀 테스트
    # ============================================================

    Write-Step -Message '전체 Pytest 회귀 테스트'

    # pyproject.toml의 testpaths 설정에 따라 tests 전체를 수집합니다.
    #
    # 이 시점에는 JIPSA_RAG_RUN_E2E가 제거되어 있으므로
    # 실제 Claude 비용이 발생하는 E2E 모듈은 skip되고,
    # 나머지 단위·통합·계약·보안 회귀 테스트가 모두 실행됩니다.
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest'
        ) `
        -FailureMessage '전체 Pytest 회귀 테스트에 실패했습니다.'

    Write-Host '전체 Pytest 회귀 테스트 통과' -ForegroundColor Green


    # ============================================================
    # 최종 결과
    # ============================================================

    Write-Host ''
    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray

    Write-Host (
        'Ruff, Mypy 및 전체 Pytest 검증이 모두 통과했습니다.'
    ) -ForegroundColor Green

    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray
}
finally {
    # 호출자가 사용하던 PowerShell 위치를 복원합니다.
    if ($LocationPushed) {
        Pop-Location
    }

    Restore-ProcessEnvironment

    $OutputEncoding = $OriginalOutputEncoding
    [Console]::InputEncoding = $OriginalConsoleInputEncoding
    [Console]::OutputEncoding = $OriginalConsoleOutputEncoding
}