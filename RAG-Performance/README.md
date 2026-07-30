# Jipsa RAG Performance

> **목적:** Jipsa Local RAG의 현재 자원 사용량과 처리 가능한 범위를 측정합니다.  
> **범위:** 측정·기록만 수행하며 성능 최적화, 운영 제한값 변경, 모델 교체는 수행하지 않습니다.  
> **실행 환경:** Windows PowerShell 5.1, Python 3.12, CUDA 12.9, Docker Desktop, Local RAG DB  
> **측정 대상:** 같은 저장소의 `RAG/` 프로젝트  
> **전체 테스트 명령:** [`TEST_COMMANDS.md`](TEST_COMMANDS.md)

## 문서 바로가기

| 문서 | 목적 |
|---|---|
| [`README.md`](README.md) | 구조, 측정 범위, 안전장치와 결과 해석 |
| [`TEST_COMMANDS.md`](TEST_COMMANDS.md) | 품질 검사, 단위 테스트와 실제 측정 명령 |
| [`MIGRATION.md`](MIGRATION.md) | 이전 RAG 내부 초안 경로 정리 기준 |
| [`configs/benchmark-plan.json`](configs/benchmark-plan.json) | Fixture·동시성·포화 분석 계획 |

## 1. 분리 구조

```text
INT2-Jipsa-Team04/
├─ RAG/
│  ├─ src/
│  ├─ scripts/
│  ├─ infra/
│  └─ .env.local
└─ RAG-Performance/
   ├─ configs/
   ├─ scripts/
   ├─ src/
   ├─ tests/
   ├─ .python-version
   ├─ pyproject.toml
   └─ uv.lock  # 최초 uv sync에서 생성
```

`RAG-Performance`는 독립된 `pyproject.toml`, 가상환경과 테스트를 사용합니다. 최초 `uv sync`가 현재 플랫폼용 `uv.lock`을 생성하고 이후 실행은 `--frozen`으로 고정합니다.
RAG 운영 패키지에 성능 측정용 `psutil`, Fixture 생성 라이브러리, 보고서 코드를 추가하지
않습니다.

실행 시 프로세스도 분리됩니다.

```text
RAG-Performance 부하 생성기·자원 수집기
  │
  ├─ HTTP 부하 ───────────────▶ 별도 Uvicorn RAG Target Process
  │                                ├─ FastAPI
  │                                ├─ 문서 Parser
  │                                ├─ CUDA EasyOCR Worker
  │                                └─ Local RAG DB Client
  │
  ├─ nvidia-smi ──────────────▶ GPU·VRAM·온도·전력
  ├─ docker stats ────────────▶ jipsa-embedding·jipsa-qdrant
  └─ psutil ──────────────────▶ Host·RAG Process Tree·Disk·Network
```

## 2. 구현된 10개 측정 항목

1. 실행 환경과 Git Branch·Commit SHA 자동 기록
2. CPU, RAM, GPU, VRAM, Disk I/O, Network I/O 측정
3. 다운로드, 파싱·OCR, 청킹, 임베딩, DB·Qdrant 색인 단계별 시간·자원 측정
4. PDF, DOCX, PPTX, XLSX, TXT 형식별 측정
5. 일반 텍스트와 OCR-only 문서 비교
6. 텍스트 단위, 실제 파일 크기, 이미지 수와 생성 청크 수 단계 증가
7. `POST /api/v1/chunks/search` 단일·동시 요청 측정
8. `POST /api/v1/rag/answers`의 `lookup`·`synthesis` 분리 측정
9. 인프라·RAG·OCR·검색·답변의 Cold Start와 Warm Run 분리
10. 동시성 증가에 따른 처리량, 오류율, p50·p95·p99와 최초 포화 후보 기록

## 3. 측정 경계

### 포함

- Uvicorn TCP/HTTP 경로
- FastAPI Middleware, 인증과 요청 검증
- `HttpFileDownloader` URL·MIME·Magic Byte·OOXML 검증
- 임시 파일 쓰기와 정리
- PDF, DOCX, PPTX, XLSX, TXT 운영 Parser
- 문서 이미지 추출과 CUDA EasyOCR
- 구조화 청킹
- CUDA TEI 문서·질의 임베딩
- Local RAG DB 저장
- Qdrant 저장·검색
- 활성화한 경우 Claude lookup·synthesis

### 제외

- AWS Backend 사용자 인증·권한 처리
- S3 실제 업로드와 Presigned GET 네트워크 지연
- 성능 개선이나 운영 설정 자동 변경

합성 문서는 `https://files.performance.invalid/...` 형식의 URL을 사용합니다. 대상 프로세스가
RAG의 실제 `HttpFileDownloader`에 `httpx2.MockTransport`만 주입하므로 외부 네트워크를
사용하지 않으면서 다운로드 검증과 임시 파일 I/O는 유지합니다.

## 4. 안전장치

- 대상 프로세스는 `JIPSA_RAG_APP_ENV=test`에서만 실행합니다.
- Issue #159 전용 `test_user_idx`와 `file_idx_start`를 사용합니다.
- 정리는 정확한 사용자와 File_IDX 목록만 대상으로 합니다.
- 범위 삭제나 운영 사용자 전체 삭제를 수행하지 않습니다.
- 관리 API는 실행마다 생성한 무작위 `X-Benchmark-Token`으로 보호합니다.
- 내부 토큰, Anthropic Key, DB DSN, Presigned URL Query, 질문·청크 원문은 결과 파일에
  저장하지 않습니다.
- 기본 실행은 이전 실패 실행의 전용 데이터 정리 후 시작하고 종료 시 다시 정리합니다.

## 5. 사전 조건

다음 항목이 준비되어 있어야 합니다.

- Windows PowerShell 5.1 이상
- `uv`
- Git
- Docker Desktop과 Docker Compose
- NVIDIA Driver와 `nvidia-smi`
- CUDA 12.9를 사용하는 RAG 환경
- 실행 가능한 Local RAG DB
- `RAG/.env.local`
- 답변 측정 시 Anthropic API Key
- PowerPoint·Excel 이미지·차트 OCR Fixture를 확장할 경우 Microsoft Office 대화형 세션

일반 Local RAG 서버는 종료한 상태를 권장합니다. 기본 실행은 정확한 Cold Start 측정을
위해 `jipsa-qdrant`와 `jipsa-embedding`을 정지 후 다시 시작합니다.

## 6. 기본 실행

저장소 Root가 `D:\Programming\INT2-Jipsa-Team04`인 경우:

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-benchmark.ps1'
```

스크립트는 다음을 순서대로 실행합니다.

```text
최초 uv sync / 이후 uv sync --frozen
→ Ruff format 검사
→ Ruff lint 검사
→ Mypy strict 검사
→ Pytest
→ Fixture 생성
→ Qdrant·TEI 시작
→ 별도 RAG Target 시작
→ 전체 성능 측정
→ 결과 생성
→ 전용 데이터와 인프라 정리
```

## 7. 실행 옵션

### Claude 제외

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-benchmark.ps1' `
    -DisableAnswers
```

인제스트와 검색만 측정하므로 Claude 비용이 발생하지 않습니다.

### 기존 인프라 유지

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-benchmark.ps1' `
    -PreserveRunningInfrastructure `
    -KeepInfrastructureRunning
```

이 경우 실행 중인 Qdrant와 TEI를 재시작하지 않으므로 인프라 Cold Start는 측정하지
않습니다. RAG Target의 Cold Start와 최초 OCR·검색·답변은 계속 분리됩니다.

### 대상 RAG 경로 지정

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-benchmark.ps1' `
    -RagRoot 'D:\Programming\INT2-Jipsa-Team04\RAG'
```

### 실패 분석용 데이터 유지

```powershell
powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File '.\scripts\run-benchmark.ps1' `
    -KeepTestData `
    -KeepInfrastructureRunning
```

분석 후 전용 데이터는 수동으로 정리해야 합니다. 일반 실행에서는 사용하지 않는 것이
안전합니다.

## 8. 측정 계획 수정

기본 계획은 `configs/benchmark-plan.json`입니다.

```json
{
  "ingest": {
    "concurrency_levels": [1, 2, 4],
    "requests_per_level": 4
  },
  "search": {
    "top_k": 5,
    "load": {
      "concurrency_levels": [1, 2, 4, 8, 16],
      "requests_per_level": 16
    }
  }
}
```

`concurrency_levels`는 중복 없는 오름차순 양의 정수여야 합니다.
`requests_per_level`은 최대 동시성 이상이어야 합니다. 처음 실행은 기본 계획으로 환경과
안전성을 확인한 뒤 동시성·파일 크기를 점진적으로 높이는 것이 좋습니다.

### 기본 Fixture Matrix

| Group | 형식 | Profile | 목적 |
|---|---|---|---|
| `format-coverage` | PDF, DOCX, PPTX, XLSX, TXT | `small_text` | 형식별 비교 |
| `ocr-comparison` | PDF, DOCX, PPTX, XLSX | `small_ocr` | 일반 텍스트 대비 OCR |
| `scale-text` | PDF | `medium_text`, `large_text` | 텍스트·청크 증가 |
| `scale-ocr` | PDF | `medium_ocr`, `large_ocr` | 이미지 수 증가 |

합성 문서에는 실행별 File_IDX와 고유 Token이 포함되어 압축으로 파일 크기 차이가 지나치게
사라지는 것을 줄이고, 검색 API가 해당 문서를 찾을 수 있게 합니다.

## 9. Cold Start와 Warm Run 정의

| 구분 | 측정 내용 |
|---|---|
| 인프라 Cold | Qdrant·TEI Container 정지 후 시작과 준비 완료 |
| RAG Cold | 별도 Uvicorn Process 시작과 FastAPI lifespan 완료 |
| Text Ingest Cold | 최초 일반 문서 다운로드·파싱·청킹·TEI 문서 임베딩·색인 |
| OCR Ingest Cold | 최초 EasyOCR Worker·CUDA Model 지연 로딩 포함 |
| Search Cold | 최초 질의 임베딩과 Qdrant 검색 |
| Answer Cold | 최초 Claude lookup 또는 synthesis |
| Warm | 동일 계층 초기화 후 별도 File_IDX 또는 반복 요청 |

Cold와 Warm 문서는 같은 원본 Byte를 사용하지만 서로 다른 File_IDX를 할당하여 재인제스트
최적화나 활성 문서 상태가 결과를 섞지 않게 합니다.

## 10. 자원 측정 정의

### Host

- 전체 CPU 사용률
- 전체 RAM 사용량
- 누적 Disk Read·Write Byte
- 누적 Network Receive·Send Byte

### RAG Process Tree

대상 FastAPI PID와 모든 자식 Process를 합산합니다.

- CPU 사용률 합계
- RSS와 Private Memory 합계
- Process 수
- Thread 수
- Windows Handle 수
- Process Disk Read·Write Byte
- 대상 PID가 사용한 GPU Memory

### GPU

`nvidia-smi` 장치 전체 기준입니다.

- GPU 사용률 최대값
- 전체 사용 VRAM
- 전체 VRAM
- 최고 온도
- 전력 사용량

장치 전체 VRAM에는 EasyOCR과 TEI가 함께 포함될 수 있으므로 `target_gpu_memory_used_bytes_sum`
및 Docker TEI 값과 함께 해석합니다.

### Docker

- `jipsa-embedding`: CPU, RAM, Network I/O, Block I/O
- `jipsa-qdrant`: CPU, RAM, Network I/O, Block I/O

## 11. 포화 후보 판정

포화 판정은 서비스 제한값을 변경하지 않습니다. 다음 중 먼저 발생한 동시성 단계를
보고서에 후보로 표시합니다.

- 오류율이 `max_error_rate` 초과
- 이전 단계 대비 처리량 증가율이 `throughput_gain_floor_percent` 이하이면서
  p95 증가율이 `p95_growth_trigger_percent` 이상

기본값:

```text
최대 허용 오류율: 1%
처리량 증가 하한: 5%
p95 증가 Trigger: 20%
```

이 값은 운영 기준이 아니라 자동 보고용 분석 규칙입니다.

## 12. 결과 파일

```text
RAG-Performance/artifacts/<run-id>/
├─ environment.json
├─ benchmark_plan.resolved.json
├─ all_owned_fixtures.json
├─ fixtures/
├─ target.log
├─ request_records.csv
├─ level_summaries.csv
├─ resource_samples.jsonl
├─ resource_samples.csv
├─ resource_summaries.csv
├─ ingest_stage_events.csv
├─ ingest_stage_resource_summary.csv
├─ host_io_deltas.csv
├─ saturation_candidates.json
├─ report.json
└─ report.md
```

`report.md`는 사람이 검토하는 요약이고, CSV·JSON은 후속 비교와 그래프 생성에 사용합니다.
측정 도구는 기준선 비교나 성능 개선 판정을 자동 수행하지 않습니다.

## 13. 독립 프로그램 단위 테스트

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

uv sync
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

## 14. 전체 측정 전 RAG 품질 검증

성능 측정 프로그램은 RAG Source를 수정하지 않지만, 측정 대상 Commit이 정상인지 먼저
확인해야 합니다.

```powershell
Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG'

uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

실제 GPU·Qdrant·DB·Claude E2E까지 확인한 Commit에서 성능 측정을 수행하는 것이 좋습니다.

## 15. 해석 주의사항

- 샘플보다 짧은 단계는 해당 구간 자원 샘플이 0개일 수 있습니다.
- Host I/O에는 같은 PC에서 실행된 다른 프로그램의 사용량도 포함될 수 있습니다.
- RAG Process Tree 값은 대상 서비스 자체 비용을 보기 위한 값입니다.
- Claude 지연은 외부 API 상태와 네트워크 영향을 받습니다.
- Office COM을 사용하는 별도 Fixture는 대화형 Windows Session 상태의 영향을 받습니다.
- 측정 중 다른 GPU 작업, Docker 작업, 대용량 복사와 Windows Update를 피해야 합니다.
- 포화 직전의 단일 성공 결과보다 반복 측정과 p95·p99를 우선 해석해야 합니다.
