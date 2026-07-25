#Requires -Version 5.1

<#
.SYNOPSIS
    Claude SOURCE-N 인용 검증 기능과 RAG 답변 API 계약을 검사합니다.

.DESCRIPTION
    다음 검증을 순서대로 수행합니다.

    1. uv와 필수 변경 파일 존재 여부 확인
    2. API 계약 문서 핵심 내용 확인
    3. Ruff 포맷 적용 및 검증
    4. Ruff 린트 검사
    5. Mypy 전체 정적 타입 검사
    6. RAG 답변 인용 관련 집중 테스트
    7. 전체 Pytest 회귀 테스트
    8. uv.lock 동기화 검증
    9. Git whitespace 오류 검사

    PowerShell 명령은 $LASTEXITCODE를 생성하지 않을 수 있습니다.
    따라서 각 검증 단계 시작 전에 $LASTEXITCODE를 0으로 초기화하고,
    PowerShell 실행 결과와 네이티브 프로그램 종료 코드를 함께 검사합니다.

.NOTES
    실행 위치와 관계없이 이 파일이 있는 RAG 디렉터리를 작업 경로로 사용합니다.

    테스트 환경:
        JIPSA_RAG_APP_ENV=test
        PYTHONUTF8=1
        PYTHONIOENCODING=utf-8
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# 작업 경로 설정
# ============================================================

# 이 스크립트가 저장된 디렉터리를 RAG 프로젝트 루트로 사용합니다.
#
# 사용자가 다른 디렉터리에서 스크립트를 실행하더라도 pyproject.toml,
# src 및 tests 경로를 올바르게 찾도록 현재 위치를 스크립트 위치로 변경합니다.
$ProjectRoot = $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    throw 'PowerShell 스크립트의 프로젝트 경로를 확인할 수 없습니다.'
}

Set-Location -LiteralPath $ProjectRoot

# ============================================================
# 테스트 환경 설정
# ============================================================

# local 또는 development 환경의 실제 설정을 읽지 않고
# 테스트 전용 설정을 사용하도록 애플리케이션 환경을 고정합니다.
$env:JIPSA_RAG_APP_ENV = 'test'

# Python 소스, 테스트 데이터 및 터미널 입출력을 UTF-8로 통일합니다.
#
# Windows PowerShell 5.1은 기본 인코딩이 환경에 따라 달라질 수 있으므로
# Python과 PowerShell 양쪽에 UTF-8 설정을 명시합니다.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$Utf8Encoding = [System.Text.UTF8Encoding]::new($false)

$OutputEncoding = $Utf8Encoding
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding

# ============================================================
# 이번 작업에서 수정하거나 추가한 파일
# ============================================================

# Ruff 포맷과 린트 검사를 적용할 Python 파일입니다.
#
# API 계약 문서는 Python 검사 대상이 아니므로 별도 변수로 관리합니다.
$ChangedPythonFiles = @(
    'src/jipsa_rag/services/rag_answer.py',
    'src/jipsa_rag/api/v1/endpoints/rag_answer.py',
    'src/jipsa_rag/infrastructure/document/parser_factory.py',
    'tests/unit/services/test_rag_answer_citations.py',
    'tests/unit/services/test_rag_answer_citation_security.py',
    'tests/unit/api/v1/endpoints/test_rag_answer.py',
    'tests/unit/api/v1/endpoints/test_rag_answer_citation_contract.py',
    'tests/unit/infrastructure/document/test_parser_factory.py'
)

$ContractPath = 'docs/api/rag-answer-api-contract.md'

# 인용 검증과 직접 관련된 집중 테스트 파일입니다.
#
# 전체 테스트보다 먼저 실행하여 실패가 발생했을 때 이번 변경 범위에서
# 원인을 빠르게 찾을 수 있도록 구성합니다.
$FocusedTestFiles = @(
    'tests/unit/infrastructure/document/test_parser_factory.py',
    'tests/unit/services/test_rag_answer.py',
    'tests/unit/services/test_rag_answer_citations.py',
    'tests/unit/services/test_rag_answer_citation_security.py',
    'tests/unit/services/test_prompt_builder.py',
    'tests/unit/schemas/test_rag_answer.py',
    'tests/unit/api/v1/endpoints/test_rag_answer.py',
    'tests/unit/api/v1/endpoints/test_rag_answer_citation_contract.py'
)

# ============================================================
# 공통 출력 함수
# ============================================================

function Write-ValidationHeader {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    Write-Host ''
    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray

    Write-Host "[$Name]" -ForegroundColor Cyan

    Write-Host (
        '============================================================'
    ) -ForegroundColor DarkGray
}

# ============================================================
# 단계별 검증 실행 함수
# ============================================================

function Invoke-ValidationStep {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [scriptblock] $Command
    )

    Write-ValidationHeader -Name $Name

    # Test-Path, Get-Content, Write-Host 같은 PowerShell 명령은
    # $LASTEXITCODE 값을 새로 만들지 않습니다.
    #
    # Set-StrictMode가 활성화된 상태에서 아직 생성되지 않은
    # $LASTEXITCODE를 읽으면 VariableIsUndefined 오류가 발생합니다.
    #
    # 또한 이전 단계에서 실행한 uv 또는 git의 종료 코드가 남아 있으면
    # 현재 PowerShell 전용 단계가 잘못 실패할 수 있으므로 단계 시작 전에
    # 전역 자동 변수를 0으로 초기화합니다.
    $global:LASTEXITCODE = 0

    try {
        & $Command

        # $?는 바로 앞에서 실행한 PowerShell 문 또는 네이티브 명령의
        # 성공 여부를 나타냅니다.
        #
        # 다른 명령을 실행하면 값이 변경될 수 있으므로 스크립트 블록이
        # 끝난 직후 즉시 지역 변수에 저장합니다.
        $PowerShellSucceeded = $?

        # uv, git, python 같은 네이티브 프로그램의 종료 코드를 저장합니다.
        #
        # PowerShell 명령만 실행한 단계라면 단계 시작 시 설정한 0이
        # 그대로 유지됩니다.
        $NativeExitCode = $global:LASTEXITCODE

        # PowerShell 실행 실패와 네이티브 프로그램의 비정상 종료를
        # 모두 검증 실패로 처리합니다.
        if (
            -not $PowerShellSucceeded -or
            $NativeExitCode -ne 0
        ) {
            throw (
                "$Name 실패. " +
                "PowerShell 성공 여부: $PowerShellSucceeded, " +
                "네이티브 종료 코드: $NativeExitCode"
            )
        }
    }
    catch {
        Write-Host "$Name 실패" -ForegroundColor Red

        # 원래 발생한 예외를 유지하여 실패한 경로와 메시지를
        # PowerShell 호출자에게 그대로 전달합니다.
        throw
    }

    Write-Host "$Name 성공" -ForegroundColor Green
}

# ============================================================
# 1. 실행 도구 확인
# ============================================================

Invoke-ValidationStep `
    -Name '실행 도구 확인' `
    -Command {
        # Get-Command는 PowerShell 명령이므로 $LASTEXITCODE를 만들지 않습니다.
        # 수정된 Invoke-ValidationStep이 이러한 단계도 정상 처리하는지 함께
        # 확인할 수 있습니다.
        $UvCommand = Get-Command 'uv' -ErrorAction Stop

        Write-Host "uv 실행 파일: $($UvCommand.Source)"
    }

# ============================================================
# 2. 프로젝트 구조 확인
# ============================================================

Invoke-ValidationStep `
    -Name '프로젝트 구조 확인' `
    -Command {
        $RequiredProjectFiles = @(
            'pyproject.toml',
            'uv.lock'
        )

        foreach ($RequiredFile in $RequiredProjectFiles) {
            if (
                -not (
                    Test-Path `
                        -LiteralPath $RequiredFile `
                        -PathType Leaf
                )
            ) {
                throw "프로젝트 필수 파일을 찾을 수 없습니다: $RequiredFile"
            }
        }

        Write-Host 'pyproject.toml과 uv.lock을 확인했습니다.'
    }

# ============================================================
# 3. 변경 파일 존재 여부 확인
# ============================================================

Invoke-ValidationStep `
    -Name '변경 파일 존재 여부 확인' `
    -Command {
        foreach ($File in $ChangedPythonFiles) {
            if (
                -not (
                    Test-Path `
                        -LiteralPath $File `
                        -PathType Leaf
                )
            ) {
                throw "필수 Python 파일을 찾을 수 없습니다: $File"
            }
        }

        foreach ($File in $FocusedTestFiles) {
            if (
                -not (
                    Test-Path `
                        -LiteralPath $File `
                        -PathType Leaf
                )
            ) {
                throw "집중 테스트 파일을 찾을 수 없습니다: $File"
            }
        }

        if (
            -not (
                Test-Path `
                    -LiteralPath $ContractPath `
                    -PathType Leaf
            )
        ) {
            throw "API 계약 문서를 찾을 수 없습니다: $ContractPath"
        }

        Write-Host '변경 대상 파일과 집중 테스트 파일을 모두 확인했습니다.'
    }

# ============================================================
# 4. API 계약 문서 핵심 내용 확인
# ============================================================

Invoke-ValidationStep `
    -Name 'RAG 답변 API 계약 문서 확인' `
    -Command {
        # Windows PowerShell 5.1의 Get-Content 인코딩 동작에 의존하지 않도록
        # .NET UTF-8 판독기를 사용하여 계약 문서를 한 번에 읽습니다.
        #
        # UTF-8 BOM이 있는 파일과 없는 파일을 모두 처리할 수 있으며,
        # Markdown 문서의 한글이 현재 시스템 ANSI 코드 페이지로 잘못
        # 해석되는 문제를 방지합니다.
        $ContractAbsolutePath = (
            Resolve-Path `
                -LiteralPath $ContractPath
        ).Path

        $ContractContent = [System.IO.File]::ReadAllText(
            $ContractAbsolutePath,
            [System.Text.Encoding]::UTF8
        )

        # Markdown 표는 열 너비 정렬을 위해 공백 개수가 달라질 수 있습니다.
        #
        # 예를 들어 다음 두 표기는 의미가 같지만 단순 Contains 비교에서는
        # 서로 다른 문자열로 처리됩니다.
        #
        # | 계약 버전 | `1.1.0` |
        # | 계약 버전      | `1.1.0`      |
        #
        # 따라서 계약 버전 행은 공백을 허용하는 정규식으로 검증합니다.
        $VersionPattern = (
            '(?m)^\|\s*계약\s*버전\s*\|\s*`1\.1\.0`\s*\|'
        )

        if (-not [regex]::IsMatch($ContractContent, $VersionPattern)) {
            # 실패 원인을 빠르게 확인할 수 있도록 현재 문서에 기록된
            # 계약 버전을 안전하게 추출합니다.
            #
            # 계약 문서 내용 전체는 오류 메시지나 로그에 출력하지 않습니다.
            $DetectedVersionMatch = [regex]::Match(
                $ContractContent,
                '(?m)^\|\s*계약\s*버전\s*\|\s*`(?<version>[^`]+)`\s*\|'
            )

            if ($DetectedVersionMatch.Success) {
                $DetectedVersion = $DetectedVersionMatch.Groups[
                    'version'
                ].Value

                throw (
                    'API 계약 문서 버전이 올바르지 않습니다. ' +
                    '기대 버전: 1.1.0, ' +
                    "현재 버전: $DetectedVersion"
                )
            }

            throw (
                'API 계약 문서에서 계약 버전 표 행을 찾을 수 없습니다. ' +
                '다음 형식의 행이 필요합니다: ' +
                '| 계약 버전 | `1.1.0` |'
            )
        }

        # 버전 이외의 핵심 계약은 문장 배치나 Markdown 표의 공백과
        # 무관하게 확인할 수 있는 고유한 문자열로 검사합니다.
        #
        # 각 항목은 이번 변경 사항을 문서가 실제로 설명하고 있는지
        # 확인하기 위한 최소 계약 표식입니다.
        $RequiredContractMarkers = @(
            '[SOURCE-N]',
            'INVALID_GENERATION_RESPONSE',
            'insufficient_evidence',
            '실제로 인용된 출처',
            '최초 인용 순서',
            'Claude 응답 원문'
        )

        foreach ($Marker in $RequiredContractMarkers) {
            if (-not $ContractContent.Contains($Marker)) {
                throw (
                    'API 계약 문서에서 필수 내용을 찾을 수 없습니다: ' +
                    $Marker
                )
            }
        }

        Write-Host (
            'API 계약 문서 버전 1.1.0과 인용 및 근거 부족 계약을 ' +
            '확인했습니다.'
        )
    }

# ============================================================
# 5. Ruff 포맷 적용
# ============================================================

Invoke-ValidationStep `
    -Name 'Ruff 포맷 적용' `
    -Command {
        uv run ruff format @ChangedPythonFiles
    }

# ============================================================
# 6. Ruff 포맷 검증
# ============================================================

Invoke-ValidationStep `
    -Name 'Ruff 포맷 검증' `
    -Command {
        uv run ruff format --check @ChangedPythonFiles
    }

# ============================================================
# 7. Ruff 린트 검사
# ============================================================

Invoke-ValidationStep `
    -Name 'Ruff 린트 검사' `
    -Command {
        uv run ruff check @ChangedPythonFiles
    }

# ============================================================
# 8. Mypy 전체 정적 타입 검사
# ============================================================

Invoke-ValidationStep `
    -Name 'Mypy 전체 타입 검사' `
    -Command {
        # 인용 검증 코드가 다른 서비스 및 API 계층의 타입 계약에 미치는
        # 영향을 확인하기 위해 변경 파일만이 아니라 src와 tests 전체를
        # 검사합니다.
        uv run mypy src tests
    }

# ============================================================
# 9. RAG 답변 인용 관련 집중 테스트
# ============================================================

Invoke-ValidationStep `
    -Name 'RAG 답변 인용 관련 집중 테스트' `
    -Command {
        # 실제 Claude, TEI 및 Qdrant를 호출하지 않는 단위 테스트입니다.
        #
        # 다음 계약을 우선 검증합니다.
        # - 존재하지 않는 SOURCE-N 인용 거부
        # - 인용 없는 정상 답변 거부
        # - 실제 인용 출처만 응답에 포함
        # - 중복 인용 제거 및 최초 순서 유지
        # - 고정 근거 부족 문구의 insufficient_evidence 변환
        # - Claude 답변 및 프롬프트 비노출
        # - API 계층의 INVALID_GENERATION_RESPONSE 변환
        uv run pytest @FocusedTestFiles -vv
    }

# ============================================================
# 10. 전체 Pytest 회귀 테스트
# ============================================================

Invoke-ValidationStep `
    -Name '전체 Pytest 회귀 테스트' `
    -Command {
        # 기존 문서 처리, 임베딩, Qdrant 검색, 생성 클라이언트 및 API 기능에
        # 회귀가 발생하지 않았는지 전체 테스트 스위트로 확인합니다.
        uv run pytest
    }

# ============================================================
# 11. uv.lock 동기화 검증
# ============================================================

Invoke-ValidationStep `
    -Name 'uv.lock 동기화 검증' `
    -Command {
        # 이번 변경은 새로운 패키지를 요구하지 않지만 pyproject.toml과
        # uv.lock 사이에 의도하지 않은 차이가 없는지 확인합니다.
        uv lock --check
    }

# ============================================================
# 12. Git whitespace 검사
# ============================================================

Invoke-ValidationStep `
    -Name 'Git whitespace 검사' `
    -Command {
        # trailing whitespace, 잘못된 공백 및 패치 적용을 방해할 수 있는
        # 기본적인 diff 형식 오류를 검사합니다.
        git diff --check
    }

# ============================================================
# 최종 결과
# ============================================================

Write-Host ''
Write-Host (
    '============================================================'
) -ForegroundColor DarkGray

Write-Host '모든 검증이 성공적으로 완료되었습니다.' `
    -ForegroundColor Green

Write-Host (
    '============================================================'
) -ForegroundColor DarkGray