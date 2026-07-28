#Requires -Version 5.1

[CmdletBinding()]
param(
    # Ruff, Mypy와 일반 전체 Pytest 품질 게이트를 생략합니다.
    #
    # 같은 Commit에서 scripts/verify-rag-quality.ps1이 이미 성공한 경우에만
    # 사용합니다. 실제 다중 형식 E2E는 이 옵션과 관계없이 항상 실행합니다.
    [switch] $SkipQualityGate,

    # 스크립트가 새로 시작한 Qdrant와 CUDA TEI 컨테이너를 종료하지 않습니다.
    #
    # 실패 직후 컨테이너 상태나 로그를 추가로 확인해야 할 때 사용합니다.
    [switch] $KeepInfrastructureRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Issue #123 다중 형식·OCR Local RAG 전체 E2E 실행기
# ============================================================
#
# 이 스크립트는 AWS Backend를 실행하거나 수정하지 않습니다.
# Backend manifest, ingest-complete callback과 Presigned GET URL 경계는
# 테스트 내부의 결정적인 MockTransport가 담당합니다.
#
# 실제로 사용하는 로컬 구성요소:
#
# - PDF, DOCX, PPTX, XLSX, TXT 운영 파서
# - CUDA 12.9 PyTorch와 EasyOCR
# - CUDA TEI 문서·질의 임베딩
# - Local RAG MySQL 또는 MariaDB
# - Qdrant VectorDB
# - Anthropic Claude API
#
# 실행 순서:
#
# 1. 프로젝트와 필수 도구 확인
# 2. Ruff, Mypy, 일반 전체 Pytest 품질 게이트
# 3. .env.local을 현재 PowerShell 프로세스에만 주입
# 4. Qdrant와 CUDA TEI 준비
# 5. Issue #123 전체 파이프라인 E2E 실행
# 6. 이 스크립트가 시작한 컨테이너와 환경 변수 정리
#
# 실제 Claude 호출 비용과 GPU 추론 시간이 발생합니다.
# Local RAG DB와 Qdrant에서는 테스트 전용 사용자·파일 범위만 사용하며,
# 테스트 모듈이 시작 전과 종료 후 해당 범위를 정리합니다.
#
# Windows PowerShell 5.1에서 한글 주석과 문자열을 안전하게 읽으려면
# 이 파일은 UTF-8 with BOM으로 저장해야 합니다.
# ============================================================


# ============================================================
# 프로젝트 경로와 고정 파일
# ============================================================

$ProjectRoot = (
    Resolve-Path -LiteralPath (
        Join-Path -Path $PSScriptRoot -ChildPath '..'
    )
).Path

$QualityGateScript = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'scripts/verify-rag-quality.ps1'

$ComposeFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'infra/qdrant/compose.yaml'

$LocalEnvironmentFile = Join-Path `
    -Path $ProjectRoot `
    -ChildPath '.env.local'

$Issue123E2eTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/e2e/test_fixed_document_full_pipeline_e2e.py'

$Issue123FixtureManifest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/fixtures/e2e_documents/manifest.json'

$RequiredFiles = @(
    $QualityGateScript,
    $ComposeFile,
    $LocalEnvironmentFile,
    $Issue123E2eTest,
    $Issue123FixtureManifest
)

$ComposeBaseArguments = @(
    'compose',
    '--env-file',
    $LocalEnvironmentFile,
    '--file',
    $ComposeFile
)

$QdrantStartupTimeoutSeconds = 120
$EmbeddingStartupTimeoutSeconds = 1200
$ReadinessPollIntervalSeconds = 3


# ============================================================
# 실행 전 상태 보관
# ============================================================

$OriginalEnvironment = @{}
$OriginalOutputEncoding = $OutputEncoding
$OriginalConsoleInputEncoding = [Console]::InputEncoding
$OriginalConsoleOutputEncoding = [Console]::OutputEncoding
$Utf8Encoding = [System.Text.UTF8Encoding]::new($false)
$LocationPushed = $false
$StartedServices = [System.Collections.Generic.List[string]]::new()
$ExecutionError = $null


# ============================================================
# 공통 출력과 Native Command 실행
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


# ============================================================
# 프로세스 환경 변수 관리
# ============================================================

function Save-OriginalEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    if (-not $OriginalEnvironment.ContainsKey($Name)) {
        $OriginalEnvironment[$Name] = (
            [Environment]::GetEnvironmentVariable(
                $Name,
                [EnvironmentVariableTarget]::Process
            )
        )
    }
}

function Set-ManagedEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [AllowNull()]
        [string] $Value
    )

    Save-OriginalEnvironmentValue -Name $Name
    [Environment]::SetEnvironmentVariable(
        $Name,
        $Value,
        [EnvironmentVariableTarget]::Process
    )
}

function Restore-ProcessEnvironment {
    foreach ($Name in $OriginalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable(
            $Name,
            $OriginalEnvironment[$Name],
            [EnvironmentVariableTarget]::Process
        )
    }
}

function Import-DotEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $Utf8Strict = [System.Text.UTF8Encoding]::new($false, $true)
    $Lines = [System.IO.File]::ReadAllLines($Path, $Utf8Strict)
    $ImportedCount = 0
    $LineNumber = 0

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
            throw ".env.local 형식 오류가 있습니다. 줄 번호: $LineNumber"
        }

        $Name = $Line.Substring(0, $SeparatorIndex).Trim()
        $Value = $Line.Substring($SeparatorIndex + 1).Trim()

        if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw ".env.local 변수 이름 오류가 있습니다. 줄 번호: $LineNumber"
        }

        if ($Value.Length -ge 2) {
            $DoubleQuoted = $Value.StartsWith('"') -and $Value.EndsWith('"')
            $SingleQuoted = $Value.StartsWith("'") -and $Value.EndsWith("'")
            if ($DoubleQuoted -or $SingleQuoted) {
                $Value = $Value.Substring(1, $Value.Length - 2)
            }
        }

        Set-ManagedEnvironmentValue -Name $Name -Value $Value
        $ImportedCount += 1
    }

    # PowerShell은 큰따옴표 문자열에서 변수 이름 뒤에 한글이 바로 이어지면
    # "$ImportedCount개를" 전체를 하나의 변수 이름으로 해석할 수 있다.
    # Format 연산자를 사용하여 변수와 한글 조사의 경계를 명확히 분리한다.
    Write-Host (
        '.env.local 환경 변수 {0}개를 안전하게 주입했습니다.' `
            -f $ImportedCount
    )
}

function Get-RequiredEnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    $Value = [Environment]::GetEnvironmentVariable(
        $Name,
        [EnvironmentVariableTarget]::Process
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "필수 환경 변수 '$Name'이 .env.local에 없습니다."
    }

    return $Value.TrimEnd('/')
}


# ============================================================
# Docker Compose 상태와 준비 확인
# ============================================================

function Get-RunningComposeServices {
    $PreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 0
        $Arguments = @(
            $ComposeBaseArguments + @(
                'ps',
                '--services',
                '--status',
                'running'
            )
        )
        $Output = & docker @Arguments
        $ExitCode = $global:LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousPreference
    }

    if ($ExitCode -ne 0) {
        throw "Docker Compose 실행 서비스 조회에 실패했습니다. 종료 코드: $ExitCode"
    }

    return @(
        $Output |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

function Start-RequiredInfrastructure {
    $RunningBefore = @(Get-RunningComposeServices)

    foreach ($Service in @('qdrant', 'embedding')) {
        if ($RunningBefore -contains $Service) {
            Write-Host "$Service 서비스는 이미 실행 중입니다."
            continue
        }

        Invoke-NativeCommand `
            -FilePath 'docker' `
            -ArgumentList @(
                $ComposeBaseArguments + @(
                    'up',
                    '--detach',
                    $Service
                )
            ) `
            -FailureMessage "$Service 서비스 시작에 실패했습니다."

        $StartedServices.Add($Service)
    }
}

function Stop-StartedInfrastructure {
    if ($KeepInfrastructureRunning -or $StartedServices.Count -eq 0) {
        return
    }

    $ServicesToStop = @($StartedServices)
    [array]::Reverse($ServicesToStop)

    foreach ($Service in $ServicesToStop) {
        Invoke-NativeCommand `
            -FilePath 'docker' `
            -ArgumentList @(
                $ComposeBaseArguments + @(
                    'stop',
                    $Service
                )
            ) `
            -FailureMessage "$Service 서비스 정지에 실패했습니다."
    }
}

function Wait-QdrantReady {
    param(
        [Parameter(Mandatory = $true)]
        [string] $BaseUrl
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($QdrantStartupTimeoutSeconds)
    $ReadyUrl = "$BaseUrl/readyz"

    while ([DateTime]::UtcNow -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest `
                -Uri $ReadyUrl `
                -Method Get `
                -UseBasicParsing `
                -TimeoutSec 10

            if ($Response.StatusCode -eq 200) {
                Write-Host 'Qdrant 준비 상태 확인 완료' -ForegroundColor Green
                return
            }
        }
        catch {
            # 준비 중 연결 거부와 503은 정상적인 재시도 대상입니다.
        }

        Start-Sleep -Seconds $ReadinessPollIntervalSeconds
    }

    throw "Qdrant가 제한 시간 안에 준비되지 않았습니다: $BaseUrl"
}

function Wait-EmbeddingReady {
    param(
        [Parameter(Mandatory = $true)]
        [string] $BaseUrl
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($EmbeddingStartupTimeoutSeconds)
    $EmbedUrl = "$BaseUrl/embed"
    $Body = @{
        inputs = @('Issue 123 CUDA TEI readiness probe')
    } | ConvertTo-Json -Depth 3 -Compress

    while ([DateTime]::UtcNow -lt $Deadline) {
        try {
            $Response = Invoke-RestMethod `
                -Uri $EmbedUrl `
                -Method Post `
                -ContentType 'application/json; charset=utf-8' `
                -Body $Body `
                -TimeoutSec 60

            if ($null -ne $Response) {
                Write-Host 'CUDA TEI 실제 임베딩 확인 완료' -ForegroundColor Green
                return
            }
        }
        catch {
            # 최초 모델 다운로드, CUDA 초기화와 Warmup 동안 계속 재시도합니다.
        }

        Start-Sleep -Seconds $ReadinessPollIntervalSeconds
    }

    throw "CUDA TEI가 제한 시간 안에 준비되지 않았습니다: $BaseUrl"
}


# ============================================================
# 메인 실행 흐름
# ============================================================

try {
    Push-Location -LiteralPath $ProjectRoot
    $LocationPushed = $true

    $OutputEncoding = $Utf8Encoding
    [Console]::InputEncoding = $Utf8Encoding
    [Console]::OutputEncoding = $Utf8Encoding

    Write-Step -Message '필수 파일과 실행 도구 확인'

    foreach ($RequiredFile in $RequiredFiles) {
        if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
            throw "필수 파일을 찾을 수 없습니다: $RequiredFile"
        }
    }

    Assert-CommandAvailable -Name 'uv'
    Assert-CommandAvailable -Name 'docker'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @('version') `
        -FailureMessage 'Docker Engine 연결 확인에 실패했습니다.'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @('compose', 'version') `
        -FailureMessage 'Docker Compose 확인에 실패했습니다.'

    if (-not $SkipQualityGate) {
        Write-Step -Message 'Ruff, Mypy 및 일반 전체 Pytest 품질 게이트'
        & $QualityGateScript
    }
    else {
        Write-Step -Message 'uv.lock 기준 E2E 의존성 동기화'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('sync', '--frozen') `
            -FailureMessage 'uv 의존성 동기화에 실패했습니다.'
    }

    Write-Step -Message '.env.local 실제 Local RAG 설정 주입'
    Import-DotEnvFile -Path $LocalEnvironmentFile

    # 실제 인프라를 사용하되 E2E 정리 권한은 test 환경에서만 허용합니다.
    Set-ManagedEnvironmentValue -Name 'JIPSA_RAG_APP_ENV' -Value 'test'
    Set-ManagedEnvironmentValue -Name 'JIPSA_RAG_RUN_E2E' -Value '1'
    Set-ManagedEnvironmentValue -Name 'PYTHONUTF8' -Value '1'
    Set-ManagedEnvironmentValue -Name 'PYTHONIOENCODING' -Value 'utf-8'

    $QdrantUrl = Get-RequiredEnvironmentValue -Name 'JIPSA_RAG_QDRANT_URL'
    $EmbeddingUrl = Get-RequiredEnvironmentValue `
        -Name 'JIPSA_RAG_EMBEDDING_BASE_URL'

    Write-Step -Message 'Qdrant와 CUDA TEI 시작'
    Start-RequiredInfrastructure

    Write-Step -Message '실제 Local RAG 인프라 준비 상태 확인'
    Wait-QdrantReady -BaseUrl $QdrantUrl
    Wait-EmbeddingReady -BaseUrl $EmbeddingUrl

    Write-Step -Message 'Local RAG DB 연결 확인'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/integration/test_database_connection.py',
            '-q'
        ) `
        -FailureMessage 'Local RAG DB 연결 확인에 실패했습니다.'

    Write-Step -Message 'Issue #123 다중 형식·OCR 전체 E2E 실행'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/e2e/test_fixed_document_full_pipeline_e2e.py',
            '-ra',
            '-q'
        ) `
        -FailureMessage 'Issue #123 전체 E2E 테스트에 실패했습니다.'

    Write-Host ''
    Write-Host (
        'Ruff, Mypy, 일반 전체 Pytest와 Issue #123 실제 E2E가 모두 통과했습니다.'
    ) -ForegroundColor Green
}
catch {
    $ExecutionError = $_
}
finally {
    try {
        Stop-StartedInfrastructure
    }
    catch {
        if ($null -eq $ExecutionError) {
            $ExecutionError = $_
        }
        else {
            Write-Warning (
                '원래 테스트 오류를 보존했습니다. 추가 인프라 정리 오류: ' +
                $_.Exception.Message
            )
        }
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
