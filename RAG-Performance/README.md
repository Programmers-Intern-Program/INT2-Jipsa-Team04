# Jipsa External RAG Performance

> **목적:** `RAG-Performance`에서 실제 외부 Jipsa RAG Search API의 단기·장기·파괴적 한계를 측정합니다.  
> **실행 방식:** 외부 HTTP Black-box 부하 테스트  
> **대상 API:** `POST /api/v1/chunks/search`  
> **환경 로드:** 같은 저장소의 `RAG/.env.local` 자동 사용  
> **데이터 선정:** 실행 중인 Qdrant → Local RAG DB → 최신 Qdrant Snapshot 자동 Fallback  
> **지원 환경:** Windows PowerShell 5.1+, Python 3.12, `uv`  
> **시각적 문서:** [`README.html`](README.html)  
> **명령 모음:** [`TEST_COMMANDS.md`](TEST_COMMANDS.md)

---

## 0. 핵심 요약

사용자가 더 이상 외부 RAG 주소, `RAG_INGEST_TOKEN`, `Users_IDX`, `File_IDX`를 직접 입력하지
않아도 됩니다. 실행 스크립트가 기존 `RAG/.env.local`에서 다음 값을 자동으로 읽습니다.

| 용도 | RAG 환경 변수 |
|---|---|
| 외부 RAG Origin | `JIPSA_RAG_EXTERNAL_BASE_URL` |
| API Prefix | `JIPSA_RAG_API_V1_PREFIX` |
| Search 인증 Token | `RAG_INGEST_TOKEN` |
| Qdrant URL | `JIPSA_RAG_QDRANT_URL` |
| Qdrant Collection | `JIPSA_RAG_QDRANT_COLLECTION` |
| Qdrant API Key | `JIPSA_RAG_QDRANT_API_KEY` |
| Local RAG DB | `JIPSA_RAG_DATABASE_*` |

현재 제공된 RAG 환경에서는 다음 공개 설정이 자동 파생됩니다.

```text
외부 RAG Origin : http://INT2-jipsa.iptime.org:9802
API Prefix       : /api/v1
Search Path      : /api/v1/chunks/search
Qdrant           : http://127.0.0.1:6333
Collection       : rag_chunk_vector_qwen3_embedding_0_6b_1024
Embedding        : Qwen/Qwen3-Embedding-0.6B / 1024 dimensions
```

Token, DB Password와 API Key의 실제 값은 README, Console, JSON, CSV, HTML과 실행 명령에
기록하지 않습니다.

---

## 1. 문서 바로가기

| 문서·파일 | 역할 |
|---|---|
| [`README.md`](README.md) | 구조, 자동 환경 로드, 데이터 선정, 실행, 결과 해석 |
| [`README.html`](README.html) | 검색·테마·인쇄·표·그래프가 포함된 시각적 총괄 문서 |
| [`TEST_COMMANDS.md`](TEST_COMMANDS.md) | 품질 검사와 Profile별 실제 명령 |
| [`scripts/run-staged-stress-test.ps1`](scripts/run-staged-stress-test.ps1) | 자동 환경·데이터 선정 포함 PowerShell 진입점 |
| [`configs/stress-plan-quick.json`](configs/stress-plan-quick.json) | 단기 Smoke·기본 한계 |
| [`configs/stress-plan-standard.json`](configs/stress-plan-standard.json) | 표준 부하·피크 복구 |
| [`configs/stress-plan-endurance.json`](configs/stress-plan-endurance.json) | 장시간 누수·지연 Drift |
| [`configs/stress-plan-destructive.json`](configs/stress-plan-destructive.json) | 계획 범위 내 최초 실패 탐색 |
| [`src/jipsa_rag_benchmark/rag_environment.py`](src/jipsa_rag_benchmark/rag_environment.py) | RAG `.env.local` 안전 로더 |
| [`src/jipsa_rag_benchmark/test_data_discovery.py`](src/jipsa_rag_benchmark/test_data_discovery.py) | Qdrant·DB·Snapshot 자동 데이터 선정 |

---

## 2. 외부 전용 측정 구조

```text
부하 생성 PC
└─ RAG-Performance
   ├─ RAG/.env.local 자동 로드
   ├─ 기존 데이터 읽기 전용 선정
   ├─ Burst / Interval / Batch / Ramp / Chaos / Soak
   └─ 외부 HTTP 요청
          │
          ▼
http://INT2-jipsa.iptime.org:9802
          │
          ▼
외부 Jipsa RAG
├─ 내부 Token 검증
├─ Query Embedding
├─ Qdrant Search
├─ Local RAG DB 조회
└─ Search Response
```

Control Plane에서 Local Qdrant 또는 DB를 조회할 수 있지만, 실제 부하는 항상
`JIPSA_RAG_EXTERNAL_BASE_URL`에만 전송됩니다. Local `8077` Listener가 실행 중이어도 외부
테스트를 차단하지 않습니다.

---

## 3. 측정 범위

### 포함

- DNS·TCP·HTTP 연결
- TLS 사용 시 Handshake와 인증서 검증
- 외부 Network
- Reverse Proxy·Gateway·Port Forwarding
- 내부 Token 인증
- Request Validation
- Query Embedding
- Qdrant Vector Search
- Local RAG DB 원문 조회
- 응답 직렬화와 Download
- 상태 코드와 오류 유형
- Throughput, 오류율, SLA 성공률
- 평균·최소·최대·p50·p90·p95·p99
- Ramp의 정상 최대 동시성·최초 실패 동시성
- 실행 전·후 Health·Readiness

### 제외

- 외부 서버 CPU·RAM·GPU·VRAM 직접 측정
- 외부 Qdrant·TEI Container 중단
- 외부 Process Kill
- 운영 DB Row 또는 Collection 삭제
- 실제 Host RAM·VRAM 강제 고갈
- RAG 운영 제한값 변경
- 문서 업로드·인제스트·OCR 성능
- Claude Answer 비용

---

## 4. 환경 변수 자동 로드

기본 경로:

```text
INT2-Jipsa-Team04/
├─ RAG/
│  └─ .env.local
└─ RAG-Performance/
   └─ scripts/run-staged-stress-test.ps1
```

Quick 실행만으로 다음이 자동 수행됩니다.

```text
RAG/.env.local 읽기
→ JIPSA_RAG_EXTERNAL_BASE_URL 적용
→ RAG_INGEST_TOKEN을 현재 Process에만 설정
→ Qdrant·DB 연결 설정 적용
→ 실제 User/File 자동 선정
→ 외부 Search 단일 사전 검증
→ 단계형 Stress Campaign
```

Token은 다음 Process 환경 변수에 자동 주입됩니다.

```text
JIPSA_RAG_PERFORMANCE_INTERNAL_TOKEN
```

사용자가 값을 입력할 필요가 없으며, Script 종료 후 별도 파일에 남지 않습니다.

---

## 5. 기존 데이터 자동 선정

기본 `DataSource=auto` 우선순위:

```text
1. 실행 중인 Qdrant Collection
2. Local RAG DB
3. 최신 Qdrant *.snapshot
```

### 5.1 Qdrant

`JIPSA_RAG_QDRANT_URL`과 `JIPSA_RAG_QDRANT_COLLECTION`을 사용해 활성 Point를 Scroll합니다.
Vector는 읽지 않고 다음 Payload만 읽습니다.

```text
users_idx
file_idx
is_active
content
```

`is_active=true`인 Point만 후보로 사용합니다.

### 5.2 Local RAG DB

Qdrant에 접근할 수 없고 `mariadb` 또는 `mysql` Client가 있으면 다음 조건의 청크를 읽습니다.

```text
RAG_Document.Deleted_At IS NULL
AND RAG_Document.Index_Status = 'INDEXED'
AND RAG_Document.Chunk_Count > 0
```

DB 조회는 `SELECT`만 수행하며 `MYSQL_PWD`는 자식 Process 환경에만 전달합니다.

### 5.3 Snapshot

Qdrant·DB를 사용할 수 없으면 다음 위치에서 최신 Snapshot을 탐색합니다.

```text
RAG-Performance/snapshots/**/*.snapshot
INT2-Jipsa-Team04/**/*.snapshot
RAG/**/*.snapshot
```

Collection 이름이 파일명에 포함된 최신 파일을 우선합니다. Snapshot은 운영 Qdrant에 복원하지
않습니다.

```text
최신 Snapshot 발견
→ 현재 jipsa-qdrant와 동일한 Docker Image 확인
→ 임시 Qdrant Container 시작
→ 임시 Collection에 Snapshot Upload
→ 활성 Payload 읽기
→ 무작위 User/File 선정
→ 임시 Container 강제 제거
```

Qdrant Collection Snapshot은 Point와 Payload를 포함하는 복원용 Archive이므로 직접 Binary를
추측해 읽지 않고 Qdrant의 공식 복원 경로를 사용합니다.

### 5.4 무작위 선정 규칙

- 요청한 파일 수 이상을 가진 User를 후보로 선정
- 후보 User 중 하나를 Seed 기반 무작위 선택
- 해당 User의 활성 File IDX를 무작위 선택
- 선택 File의 실제 Chunk Content 일부를 검색 Query로 사용
- Query와 Content 원문은 결과 파일에 저장하지 않음
- Seed는 결과에 기록하여 같은 범위를 재현 가능
- 외부 Search API에서 `result_count > 0`인 File만 최종 유지

---

## 6. 모든 Profile의 공통 1~5단계

| 단계 | Mode | 동작 | 핵심 관측 |
|---:|---|---|---|
| 1 | Burst | 다수 요청 즉시 동시 제출 | 순간 Connection·Queue 수용력 |
| 2 | Interval | 짧은 간격으로 지속 제출 | Queue 누적·연속 처리 |
| 3 | Batch | 일정 간격의 그룹 Wave | Wave 충격과 회복 |
| 4 | Ramp | 동시성을 단계적으로 증가 | 정상 최대·최초 실패 |
| 5 | Chaos | 기준 TPS + 주기적 Spike | 실제 피크와 복구 |

Profile별 Soak가 이후 실행됩니다. 모든 기본 Profile은 `max_requests=0`을 사용하므로 요청 수로 조기 종료하지 않고 `duration_seconds` 전체를 수행합니다. 고정 동시성과 Client 메모리 Guard는 유지되며, 별도 사용자 Plan에서 양수 `max_requests`를 지정한 경우에만 요청 수 안전 상한이 활성화됩니다.

```text
Quick       1~5단계 + 2분 Soak
Standard    1~5단계 + 20분 Soak
Endurance   1~5단계 + 5시간 Soak
Destructive 1~5단계 + 동시성 128의 15분 Soak
```

---

## 7. Profile 비교

| Profile | Burst | Ramp 상한 | Chaos | Soak | 목적 |
|---|---:|---:|---|---|---|
| Quick | 40 / C8 | C32 | 1분, 5 TPS, Spike 40 | 실제 2분 / 요청 수 조기 종료 없음 | 연결·기본 한계 확인 |
| Standard | 200 / C32 | C128 | 5분, 20 TPS, Spike 200 | 실제 20분 / 요청 수 조기 종료 없음 | 표준 처리량·피크 복구 |
| Endurance | 500 / C64 | C128 | 30분, 20 TPS, Spike 200 | 실제 5시간 / 요청 수 조기 종료 없음 | 장시간 오류·지연 Drift |
| Destructive | 1,000 / C256 | C256 | 10분, 50 TPS, Spike 400 | 실제 15분 / C128 / 요청 수 조기 종료 없음 | 최초 실패 경계 탐색 |

---

## 8. 파괴적 외부 테스트의 의미

Destructive는 트래픽 관점의 파괴적 테스트입니다.

### 수행

- 동시성 256 Burst
- 대량 Interval·Batch
- 동시성 32 → 256 Ramp
- 기본 50 TPS + 400 Request Spike
- 높은 동시성 Soak
- 오류율·p95·Memory Guard 기반 조기 중단

### 수행하지 않음

- 외부 Host 종료
- Docker Stop·Restart
- Qdrant Snapshot을 운영 Collection에 복원
- DB Delete·Update
- 운영 환경 설정 변경
- 실제 OOM 유발

---

## 9. 실행 전 준비

- 외부 RAG가 `JIPSA_RAG_EXTERNAL_BASE_URL`에서 접근 가능
- `RAG/.env.local` 존재
- `RAG_INGEST_TOKEN`이 외부 RAG와 일치
- Qdrant, DB 또는 Snapshot 중 하나에서 실제 활성 데이터 확인 가능
- HTTP Test Endpoint 사용 승인
- Destructive 실행 승인
- 외부 RAG와 Snapshot/Qdrant/DB가 같은 데이터 시점임

Snapshot을 사용할 경우 파일을 다음 폴더에 두면 자동 탐색됩니다.

```text
RAG-Performance/snapshots/
```

`.snapshot` 파일은 Git에 커밋하지 않습니다.

---

## 10. 품질 검사

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location 'D:\Programming\INT2-Jipsa-Team04\RAG-Performance'

uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
uv run python -m compileall -q src tests
```

---

## 11. Quick 실행

환경·Token·User/File을 직접 입력하지 않습니다.

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -SkipQualityGate
```

현재 RAG 외부 주소가 HTTP이므로 Script가 Test 환경에서 `AllowInsecureHttp`를 자동 적용하고
경고를 출력합니다.

---

## 12. Capacity Ladder·Standard·Endurance 실행

Quick C32까지 실패가 없으면 다음 명령이 같은 Seed와 같은 자동 선정 데이터로 Standard
C128까지 자동 승격합니다. Standard에서 최초 실패가 발견되면 즉시 종료합니다.

```powershell
& .\scripts\run-capacity-ladder.ps1 `
    -SkipQualityGate
```

이미 Quick 결과를 확보했다면 Standard부터 시작할 수 있습니다.

```powershell
& .\scripts\run-capacity-ladder.ps1 `
    -StartProfile standard `
    -SkipQualityGate
```

Standard C128까지 실패가 없을 때 C256 Destructive까지 이어가려면 명시적 승인이 필요합니다.

```powershell
& .\scripts\run-capacity-ladder.ps1 `
    -AllowDestructive `
    -ConfirmTargetHost 'int2-jipsa.iptime.org' `
    -SkipQualityGate
```

단일 Standard 또는 Endurance 실행도 계속 지원합니다.

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile standard `
    -SkipQualityGate
```

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile endurance `
    -SkipQualityGate
```

---

## 13. Destructive 실행

현재 외부 Host 확인:

```text
INT2-jipsa.iptime.org
```

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile destructive `
    -AllowDestructive `
    -ConfirmTargetHost 'int2-jipsa.iptime.org' `
    -SkipQualityGate
```

Production으로 표시된 환경에는 `-AllowProductionTarget`도 필요합니다.

---

## 14. 데이터 Source 강제 실행

실행 중인 Qdrant만 사용:

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource qdrant `
    -SkipQualityGate
```

DB만 사용:

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource database `
    -SkipQualityGate
```

Snapshot만 사용:

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource snapshot `
    -SkipQualityGate
```

특정 Snapshot 재현:

```powershell
& .\scripts\run-staged-stress-test.ps1 `
    -TestProfile quick `
    -DataSource snapshot `
    -SnapshotPath '.\snapshots\rag_chunk_vector.snapshot' `
    -RandomSeed 159 `
    -SkipQualityGate
```

---

## 15. 결과 파일과 해석

```text
artifacts/external-stress/<RUN_ID>/
├─ report.md
├─ report.html
└─ external-stress/
   ├─ target_config.public.json
   ├─ requests.json
   ├─ requests.csv
   ├─ stage_summaries.json
   ├─ stage_summaries.csv
   ├─ capacity_boundaries.json
   ├─ capacity_boundaries.csv
   ├─ health_checks.json
   ├─ progress.log
   ├─ report.md
   └─ report.html
```

자동 선정 정보는 `target_config.public.json`에 다음 형태로 기록됩니다.

```text
selection_source
selection_seed
selection_detail
candidate_user_count
candidate_file_count
candidate_chunk_count
test_user_idx
reference_file_idxs
query_count
```

질문·청크 원문·Token·DB Password는 기록하지 않습니다.

### 정상 최대와 최초 실패

```text
정상 최대 동시성: 오류율과 p95 기준을 모두 만족한 마지막 단계
최초 실패 동시성: 오류율·SLA·p95 기준을 처음 초과한 단계
```

Ramp 상한까지 실패가 없으면 실제 한계는 계획 상한보다 높을 수 있습니다. `run-capacity-ladder.ps1`은 이 상한 검열을 읽어 Quick C32에서 Standard C128로 자동 승격하며, Destructive C256은 명시적으로 승인한 경우에만 실행합니다.

Soak 결과는 `elapsed_seconds`가 계획 시간에 도달했는지 함께 확인해야 합니다. 기본 Profile은 요청 수 상한을 비활성화했으므로 정상 종료는 설정 시간 경과가 기준입니다. 사용자 정의 Plan에서 양수 상한을 지정하고 상한이 먼저 소진되면 Stage 상태는 `stopped`가 됩니다.

---

<!-- STRESS-VERIFICATION:START -->
## 16. 마지막 검증 기록

> 이 구간은 외부 단계형 Stress Campaign 종료 시 자동 갱신됩니다. 
> 성공뿐 아니라 실패·중단 결과도 마지막 실행 상태로 기록합니다.

**상태: `FAIL` · Profile: `quick` · Run ID: `20260730T221907Z-36da3bd7`**

| 항목 | 마지막 실행 값 |
|---|---|
| 완료 시각 | `2026-07-30T22:19:07.360+00:00` |
| 실행 방식 | `external_http_black_box` |
| 외부 Target | `http://int2-jipsa.iptime.org:9802` |
| Target 환경 | `test` |
| 데이터 Source | `qdrant` |
| 선정 Seed | `8179069822024929128` |
| 선정 User IDX | `8` |
| 선정 File 수 | `2` |
| 파괴적 Profile | `False` |
| 품질 게이트 | `생략` |
| 사전 Health | `False` |
| 사후 Health | `False` |
| Local RAG 접근 | `False` |
| 실행 오류 | `UnicodeEncodeError` |
| Stage | 전체 `0` · 통과 `0` · 저하 `0` · 실패 `0` · 중단 `0` |
| 요청 | 전체 `0` · 성공 `0` · 실패 `0` |

### 처리 한계 관측

| 작업 | 확인된 정상 최대 동시성 | 최초 실패 동시성 | 해석 |
|---|---:|---:|---|
| - | - | - | Ramp 근거 없음 |

### 마지막 결과 바로가기

- [캠페인 Markdown 보고서](artifacts/external-stress/20260730T221907Z-36da3bd7/report.md)
- [캠페인 HTML 보고서](artifacts/external-stress/20260730T221907Z-36da3bd7/report.html)
- [상세 Stress Markdown](artifacts/external-stress/20260730T221907Z-36da3bd7/external-stress/report.md)
- [상세 Stress HTML](artifacts/external-stress/20260730T221907Z-36da3bd7/external-stress/report.html)

README 갱신으로 작업 트리에 변경이 생기는 것은 의도된 동작입니다.
<!-- STRESS-VERIFICATION:END -->

---

## 17. README 자동 갱신

캠페인 종료 시 다음 정보가 자동 반영됩니다.

- PASS·DEGRADED·FAIL
- Profile과 Run ID
- 외부 Target Origin
- 데이터 선정 Source와 Seed
- 자동 선정 User/File 수
- 사전·사후 Health
- Stage별 상태 수
- 전체·성공·실패 요청 수
- 정상 최대·최초 실패 동시성
- 결과 Markdown·HTML 링크

자동 갱신 제외:

```powershell
-SkipReadmeUpdate
```

---

## 18. 안전장치

- RAG `.env.local`은 읽기 전용
- Token·Password·API Key 출력 금지
- Query·Chunk 원문 보고서 저장 금지
- Qdrant Vector 미조회
- 활성 Payload만 후보 사용
- DB는 `SELECT`만 수행
- Snapshot은 임시 Container·임시 Collection에만 복원
- 임시 Container는 성공·실패·중단과 관계없이 제거
- 자동 선정 후 외부 Search 결과 1건 이상 검증
- Loopback Target 기본 차단
- Production Destructive 이중 승인
- Load Generator Memory Guard
- 오류 연속 발생 시 조기 중단

---

## 19. 문제 해결

### `No active Qdrant payload`

- `jipsa-qdrant` 상태 확인
- Collection 이름 확인
- `is_active=true` Point 존재 확인
- `DataSource=auto`로 DB·Snapshot Fallback 허용

### `mariadb/mysql client unavailable`

DB Fallback만 사용할 때 Client가 필요합니다. Qdrant가 실행 중이면 설치하지 않아도 됩니다.

### `No Qdrant snapshot was found`

Snapshot을 다음 위치에 둡니다.

```text
RAG-Performance/snapshots/*.snapshot
```

### `Snapshot restore failed`

Qdrant Snapshot은 생성한 Cluster와 같은 Minor Version 계열에서 복원해야 합니다. Script는
실행 중인 `jipsa-qdrant` Container의 Image를 우선 재사용합니다.

### `Automatically selected values returned no results`

Control Plane의 Qdrant·DB·Snapshot과 외부 RAG가 서로 다른 데이터 시점일 가능성이 큽니다.
외부 RAG가 사용하는 최신 Snapshot 또는 DB/Qdrant를 사용해야 합니다.

### `401 Unauthorized`

`RAG/.env.local`의 `RAG_INGEST_TOKEN`과 외부 RAG의 Token이 일치하는지 확인합니다.
`INTERNAL_TOKEN`과 혼용하지 않습니다.

---

## 20. 권장 실행 순서

```text
1. 품질 게이트
2. Quick / DataSource auto
3. Quick 결과와 자동 선정 Source 확인
4. Standard
5. Endurance
6. 승인 후 Destructive
7. README 마지막 검증 기록 확인
8. 결과와 Commit SHA 함께 보관
```

---

## 21. 병합 전 체크리스트

- [ ] Ruff Format 통과
- [ ] Ruff Lint 통과
- [ ] Mypy Strict 통과
- [ ] Pytest 통과
- [ ] Compileall 통과
- [ ] `RAG/.env.local` Git 추적 제외
- [ ] `snapshots/*.snapshot` Git 추적 제외
- [ ] Token·Password가 결과 파일에 없음
- [ ] Quick의 자동 데이터 선정 성공
- [ ] 외부 Search `result_count > 0` 확인
- [ ] README.md·README.html 자동 갱신 확인
- [ ] Destructive 승인 대상 Host 확인

---

## 22. 설계 원칙

1. 성능 측정 코드는 `RAG/` 운영 코드와 분리합니다.
2. 외부 Endpoint의 실제 사용자 경로를 측정합니다.
3. 사용자가 Secret과 식별자를 반복 입력하지 않게 합니다.
4. 운영 데이터는 읽기 전용으로만 탐색합니다.
5. Snapshot은 격리된 임시 Qdrant에서만 해석합니다.
6. 무작위 선정 Seed를 기록해 재현성을 보장합니다.
7. 검색 결과가 있는 데이터만 부하에 사용합니다.
8. 측정 결과로 운영 제한값을 자동 변경하지 않습니다.
9. 실행하지 않은 검증을 통과로 기록하지 않습니다.
10. 모든 결과는 Commit SHA·환경·Profile과 함께 해석합니다.
