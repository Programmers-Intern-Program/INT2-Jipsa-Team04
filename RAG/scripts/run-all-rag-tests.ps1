#Requires -Version 5.1

[CmdletBinding()]
param(
    # 동일한 Commit에서 verify-rag-quality.ps1이 이미 성공한 경우에만
    # 일반 Ruff, Mypy 및 전체 Pytest 품질 게이트를 생략합니다.
    [switch] $SkipQualityGate,

    # 실패 후 Qdrant와 CUDA TEI 상태 및 로그를 확인할 수 있도록
    # 이 스크립트가 새로 시작한 인프라를 종료하지 않습니다.
    [switch] $KeepInfrastructureRunning
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# Jipsa Local RAG 전체 테스트 실행기
# ============================================================
#
# 이 스크립트는 다음 검증을 처음부터 끝까지 순차 실행합니다.
#
# 1. uv.lock 기준 의존성 동기화
# 2. Ruff format/check, Mypy, 일반 전체 Pytest
# 3. .env.local 실제 Local RAG 설정 로드
# 4. Docker Engine 및 Compose 설정 검증
# 5. Qdrant와 CUDA TEI 시작 및 준비 상태 확인
# 6. PyTorch CUDA 장치 확인
# 7. 실제 Local RAG DB 연결 검증
# 8. 실제 Microsoft Office COM 이미지·차트 렌더링 검증
# 9. Issue #123 고정 다중 형식·OCR 전체 파이프라인 E2E
# 10. 실제 PDF·Claude·생성 제한 E2E
# 11. 실제 DOCX·PPTX·XLSX·TXT 다중 형식 E2E
#
# AWS Backend는 실행하거나 수정하지 않습니다. E2E 테스트가 Backend manifest,
# callback과 Presigned GET URL 경계만 결정적인 테스트 대역으로 대체합니다.
#
# 실제 Claude API 호출 비용과 CUDA GPU 추론 시간이 발생합니다. 또한 Office COM
# 테스트는 Microsoft PowerPoint와 Excel이 설치된 Windows 대화형 세션이 필요합니다.
#
# Windows PowerShell 5.1에서 한글 주석과 문자열을 안전하게 읽을 수 있도록
# 이 파일은 UTF-8 with BOM으로 저장해야 합니다.
# ============================================================


# ============================================================
# 프로젝트 경로와 필수 파일
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

$DatabaseConnectionTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/integration/test_database_connection.py'

$OfficeIntegrationTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/integration/test_document_image_extractors.py'

$Issue123E2eTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/e2e/test_fixed_document_full_pipeline_e2e.py'

$RealPdfE2eTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/e2e/test_real_pdf_rag_e2e.py'

$RealPdfLimitE2eTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/e2e/test_rag_answer_limits_real_pdf_e2e.py'

$RealNonPdfE2eTest = Join-Path `
    -Path $ProjectRoot `
    -ChildPath 'tests/e2e/test_real_non_pdf_multiformat_rag_e2e.py'

$RequiredFiles = @(
    $QualityGateScript,
    $ComposeFile,
    $LocalEnvironmentFile,
    $DatabaseConnectionTest,
    $OfficeIntegrationTest,
    $Issue123E2eTest,
    $RealPdfE2eTest,
    $RealPdfLimitE2eTest,
    $RealNonPdfE2eTest
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
        # Windows PowerShell 5.1은 Native Command의 stderr를 PowerShell 오류로
        # 승격할 수 있으므로 외부 프로그램 실행 중에만 Continue를 사용합니다.
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

    # 잘못된 UTF-8 바이트를 대체 문자로 조용히 바꾸지 않고 즉시 실패시킵니다.
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

        # 첫 번째 등호만 변수명과 값의 경계로 사용합니다. API Key나 DSN 값에
        # 추가 등호가 포함되어 있어도 나머지 문자열은 그대로 보존됩니다.
        $SeparatorIndex = $Line.IndexOf('=')
        if ($SeparatorIndex -le 0) {
            throw (
                '.env.local의 KEY=VALUE 형식이 올바르지 않습니다. ' +
                ('줄 번호: {0}' -f $LineNumber)
            )
        }

        $Name = $Line.Substring(0, $SeparatorIndex).Trim()
        $Value = $Line.Substring($SeparatorIndex + 1).Trim()

        if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw (
                '.env.local 환경 변수 이름이 올바르지 않습니다. ' +
                ('줄 번호: {0}' -f $LineNumber)
            )
        }

        # "value" 또는 'value' 형식의 양끝 따옴표만 제거합니다. 값 내부의
        # 공백, 등호와 # 문자는 비밀값의 일부일 수 있으므로 변경하지 않습니다.
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

    # "$ImportedCount개를"처럼 변수 뒤에 한글이 바로 붙으면 PowerShell이
    # 전체 문자열을 변수명으로 해석할 수 있습니다. Format 연산자로 경계를
    # 명시하여 Set-StrictMode에서도 안전하게 출력합니다.
    Write-Host (
        '.env.local 환경 변수 {0}개를 현재 프로세스에 주입했습니다.' `
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
    $ExitCode = $null
    $Output = @()

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
        $Output = @(& docker @Arguments)
        $ExitCode = $global:LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousPreference
    }

    if ($ExitCode -ne 0) {
        throw (
            'Docker Compose 실행 서비스 조회에 실패했습니다. ' +
            ('종료 코드: {0}' -f $ExitCode)
        )
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

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @(
            $ComposeBaseArguments + @(
                'stop',
                '--timeout',
                '30'
            ) + $ServicesToStop
        ) `
        -FailureMessage '전체 테스트 인프라 정지에 실패했습니다.'
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
            # 시작 중 연결 거부 또는 503은 정상적인 준비 재시도 대상입니다.
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
        inputs = @('Jipsa complete CUDA TEI readiness probe')
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
        Assert-RequiredFile -Path $RequiredFile
    }

    Assert-CommandAvailable -Name 'uv'
    Assert-CommandAvailable -Name 'docker'

    if (-not $SkipQualityGate) {
        Write-Step -Message '1. Ruff, Mypy 및 일반 전체 Pytest'
        & $QualityGateScript
    }
    else {
        Write-Step -Message '1. 품질 게이트 생략 및 uv.lock 동기화'
        Invoke-NativeCommand `
            -FilePath 'uv' `
            -ArgumentList @('sync', '--frozen') `
            -FailureMessage 'uv 의존성 동기화에 실패했습니다.'
    }

    Write-Step -Message '2. 실제 Local RAG 환경 변수 로드'
    Import-DotEnvFile -Path $LocalEnvironmentFile

    # 실제 인프라 설정은 .env.local에서 사용하되 테스트 전용 데이터 정리 보호를
    # 위해 실행 환경을 test로 고정합니다.
    Set-ManagedEnvironmentValue -Name 'JIPSA_RAG_APP_ENV' -Value 'test'
    Set-ManagedEnvironmentValue -Name 'JIPSA_RAG_RUN_E2E' -Value '1'
    Set-ManagedEnvironmentValue `
        -Name 'JIPSA_RAG_RUN_OFFICE_COM_INTEGRATION' `
        -Value '1'
    Set-ManagedEnvironmentValue -Name 'PYTHONUTF8' -Value '1'
    Set-ManagedEnvironmentValue -Name 'PYTHONIOENCODING' -Value 'utf-8'

    $QdrantUrl = Get-RequiredEnvironmentValue -Name 'JIPSA_RAG_QDRANT_URL'
    $EmbeddingUrl = Get-RequiredEnvironmentValue `
        -Name 'JIPSA_RAG_EMBEDDING_BASE_URL'

    Write-Step -Message '3. Docker Engine 및 Compose 확인'
    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @('version') `
        -FailureMessage 'Docker Engine 연결 확인에 실패했습니다.'

    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @('compose', 'version') `
        -FailureMessage 'Docker Compose 확인에 실패했습니다.'

    # config --quiet을 사용하여 비밀값을 출력하지 않고 구문과 변수 보간만 검증합니다.
    Invoke-NativeCommand `
        -FilePath 'docker' `
        -ArgumentList @(
            $ComposeBaseArguments + @('config', '--quiet')
        ) `
        -FailureMessage 'Docker Compose 구성 검증에 실패했습니다.'

    Write-Step -Message '4. Qdrant와 CUDA TEI 시작'
    Start-RequiredInfrastructure

    Write-Step -Message '5. 실제 인프라 준비 상태 확인'
    Wait-QdrantReady -BaseUrl $QdrantUrl
    Wait-EmbeddingReady -BaseUrl $EmbeddingUrl

    Write-Step -Message '6. PyTorch CUDA 장치 확인'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'python',
            '-c',
            (
                'import torch; ' +
                'assert torch.cuda.is_available(), "CUDA is not available"; ' +
                'print("CUDA available:", torch.cuda.is_available()); ' +
                'print("CUDA device:", torch.cuda.get_device_name(0)); ' +
                'print("PyTorch CUDA:", torch.version.cuda)'
            )
        ) `
        -FailureMessage 'PyTorch CUDA 장치 확인에 실패했습니다.'

    Write-Step -Message '7. 실제 Local RAG DB 연결 검증'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/integration/test_database_connection.py',
            '-vv',
            '-s',
            '-ra'
        ) `
        -FailureMessage 'Local RAG DB 연결 검증에 실패했습니다.'

    Write-Step -Message '8. 실제 Office COM 이미지·차트 검증'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/integration/test_document_image_extractors.py',
            '-vv',
            '-s',
            '-ra'
        ) `
        -FailureMessage 'Office COM 이미지·차트 검증에 실패했습니다.'

    Write-Step -Message '9. Issue #123 고정 문서 전체 파이프라인 E2E'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/e2e/test_fixed_document_full_pipeline_e2e.py',
            '-vv',
            '-s',
            '-ra'
        ) `
        -FailureMessage 'Issue #123 전체 파이프라인 E2E에 실패했습니다.'

    Write-Step -Message '10. 실제 PDF·Claude·생성 제한 E2E'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/e2e/test_real_pdf_rag_e2e.py',
            'tests/e2e/test_rag_answer_limits_real_pdf_e2e.py',
            '-vv',
            '-s',
            '-ra'
        ) `
        -FailureMessage '실제 PDF·Claude·생성 제한 E2E에 실패했습니다.'

    Write-Step -Message '11. 실제 비PDF 다중 형식 E2E'
    Invoke-NativeCommand `
        -FilePath 'uv' `
        -ArgumentList @(
            'run',
            'pytest',
            'tests/e2e/test_real_non_pdf_multiformat_rag_e2e.py',
            '-vv',
            '-s',
            '-ra'
        ) `
        -FailureMessage '실제 비PDF 다중 형식 E2E에 실패했습니다.'

    Write-Host ''
    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray
    Write-Host (
        'Ruff, Mypy, 일반 전체 Pytest, Office COM, CUDA EasyOCR, ' +
        'CUDA TEI, Local DB, Qdrant 및 Claude E2E가 모두 통과했습니다.'
    ) -ForegroundColor Green
    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray
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
