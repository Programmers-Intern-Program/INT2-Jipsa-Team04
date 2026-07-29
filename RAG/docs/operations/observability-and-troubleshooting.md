# Local RAG 관측성과 문제 해결

> **문서 상태:** Active · 진단·장애 대응 Runbook  
> **주 독자:** Local RAG 개발자, QA, 운영·장애 대응 담당자  
> **최종 검토:** 2026-07-29  
> **범위:** FastAPI, CUDA EasyOCR, CUDA TEI, Qdrant, Local RAG DB, Backend callback

## 1. 진단 원칙

1. 먼저 **어느 경계가 실패했는지** 식별합니다.
2. Request ID로 inbound 요청, 내부 단계와 outbound 연동 결과를 연결합니다.
3. 정상, 지연, 부분 실패와 실제 실패를 로그 레벨로 구분합니다.
4. 질문·청크·OCR·프롬프트·벡터·토큰·Presigned URL 원문을 로그로 남기지 않습니다.
5. 이전 정상 색인이 유지되는지 확인한 뒤 신규 실패 상태를 조사합니다.
6. 보상 처리 실패가 최초 오류를 덮지 않았는지 확인합니다.
7. 실제로 수행하지 않은 복구를 완료한 것으로 기록하지 않습니다.

## 2. 출력 형식

### Console

사람이 PowerShell에서 즉시 읽는 한 줄 형식입니다.

```text
2026-07-29 15:46:41.030+09:00 INFO     [jipsa-rag/local] [file-processing] file_download_completed req=5a506ea3 | File download completed. | file=952301 type=pdf size=2.09KiB duration=1.70ms
```

특징:

- 로컬 시간 또는 UTC 선택
- UTC Offset 표시
- Logger 별칭
- 기본 8자리 Request ID
- 사람이 읽기 쉬운 `KiB`, `MiB`, `ms`, `s`
- TTY에서만 제한적 색상
- `NO_COLOR` 지원
- ANSI·개행·제어 문자 제거

### JSON

로그 수집기와 자동 분석 도구용 구조화 형식입니다.

```json
{
  "timestamp": "2026-07-29T06:46:41.438Z",
  "level": "INFO",
  "logger": "jipsa_rag.core.middleware",
  "message": "HTTP request completed.",
  "request_id": "5a506ea3-9583-41a0-a0da-5f9bb5a9c620",
  "log_schema_version": 1,
  "service": "Jipsa RAG Service",
  "environment": "local",
  "event": "http_request_completed",
  "method": "POST",
  "path": "/ingest",
  "status_code": 200,
  "duration_ms": 419.853
}
```

특징:

- UTC RFC 3339 timestamp
- 전체 Request ID
- `log_schema_version=1`
- 정규화된 primitive·list·tuple·mapping 값
- 예외 발생 시 정제된 stack trace
- Console 표시 정책과 독립적인 수집 계약

## 3. 설정

```dotenv
JIPSA_RAG_LOG_LEVEL=INFO
JIPSA_RAG_LOG_FORMAT=console
JIPSA_RAG_LOG_CONSOLE_TIMEZONE=local
JIPSA_RAG_LOG_COLOR=auto
JIPSA_RAG_LOG_REQUEST_ID_LENGTH=8
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=WARNING
JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS=5000
```

| 변수 | 허용값·범위 | 의미 |
|---|---|---|
| `JIPSA_RAG_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | 애플리케이션 로그 최소 레벨 |
| `JIPSA_RAG_LOG_FORMAT` | `console`, `json` | 출력 형식 |
| `JIPSA_RAG_LOG_CONSOLE_TIMEZONE` | `local`, `utc` | Console timestamp |
| `JIPSA_RAG_LOG_COLOR` | `auto`, `always`, `never` | Console ANSI 색상 |
| `JIPSA_RAG_LOG_REQUEST_ID_LENGTH` | `8`~`36` | Console Request ID 길이 |
| `JIPSA_RAG_LOG_THIRD_PARTY_LEVEL` | 표준 로그 레벨 | httpx·Qdrant 등 외부 Logger 레벨 |
| `JIPSA_RAG_SLOW_STAGE_THRESHOLD_MS` | `0` 초과 | 정상 단계를 WARNING으로 올릴 지연 기준 |

`WARN`은 `WARNING`, `FATAL`은 `CRITICAL`로 정규화됩니다.

## 4. 로그 레벨 계약

| 레벨 | 의미 | 대표 사례 |
|---|---|---|
| DEBUG | 상세 진단 | 저수준 분기, 선택적 외부 클라이언트 진단 |
| INFO | 정상 완료 | 다운로드, 청킹, 임베딩, 색인, HTTP 2xx |
| WARNING | 부분 실패·지연 | OCR 일부 실패, 느린 정상 단계 |
| ERROR | 요청 실패 | HTTP 5xx, AppException, callback 실패 |
| CRITICAL | 프로세스 지속 불가 | 시작 실패, 필수 자원 초기화 실패 |

`is_slow_stage=true`와 `success=true`가 함께 있으면 실패가 아니라 지연 경고입니다.

## 5. 요청 전체 흐름

### 인제스트

```text
application_startup_initiated
application_startup_completed
  ↓
ingest_manifest_fetch_completed
  ↓
file_download_completed
  ↓
document_parsing_ocr_completed
  ↓
document_chunking_completed
  ↓
document_embedding_completed
  ↓
file_indexing_completed
  ↓
file_processing_completed
  ↓
ingest_success_callback_completed
  ↓
http_request_completed
```

실패 시:

```text
ingest_failure_callback_completed
application_exception
http_request_completed(status_code=5xx)
```

### RAG 답변

```text
rag_answer_search_started
rag_answer_search_completed
rag_answer_generation_completed
http_request_completed
```

근거 부족:

```text
rag_answer_search_completed(result_count=0)
rag_answer_insufficient_evidence
http_request_completed(status_code=200)
```

부분 실패를 허용하는 synthesis:

```text
rag_answer_search_failed
rag_synthesis_document_search_failed
http_request_completed(status_code=200)
```

## 6. 단계별 핵심 필드

| 단계 | 필드 |
|---|---|
| HTTP | method, path, status_code, duration_ms |
| Manifest | users_idx, file_idx, file_type, duration_ms |
| Download | file_idx, file_type, size_bytes, duration_ms |
| Parsing·OCR | parser_type, parser_version, structure_unit_count, text_unit_count |
| Chunking | chunk_count, structure_unit_count, text_unit_count |
| Embedding | chunk_count, embedding_dim, batch_count |
| Indexing | rag_document_idx, rag_index_run_idx, chunk_count |
| Callback | callback_type, success, file_idx |
| Answer | reference_file_count, result_count, answer_status, source_count |

## 7. 대표 구조화 이벤트와 우선 확인 항목

아래 이벤트는 운영 중 보상 처리, 실행 소유권과 Backend callback 경계를 진단할 때
우선적으로 확인합니다. 이 목록은 전체 이벤트 목록이 아니며 실제 코드의 `event` 값이
최종 Source of truth입니다.

| 이벤트 | 의미 | 우선 확인 |
|---|---|---|
| `file_index_run_ownership_lost` | 오래된 실행이 최신 실행 소유권을 잃음 | 최신 SUCCESS RUN과 활성 point |
| `file_index_upsert_cleanup_failed` | 신규 staging point 정리 실패 | 신규 Chunk ID와 `is_active` |
| `file_index_previous_reactivation_failed` | 이전 정상 point 재활성화 실패 | 검색 공백과 이전 정상 point |
| `file_index_new_deactivation_failed` | 실패한 신규 point 비활성화 실패 | 신규 point 검색 노출 여부 |
| `file_index_new_point_delete_failed` | 신규 point 보상 삭제 실패 | orphan point와 재정리 범위 |
| `ingest_failure_callback_failed` | 처리 실패를 Backend에 알리지 못함 | Backend 파일 상태와 네트워크 |
| `ingest_success_callback_completed` | 최신 활성 청크 성공 callback 완료 | callback 응답과 chunk count |
| `application_exception` | 공개 오류로 변환된 요청 예외 | error code, status code, Request ID |

보상 관련 이벤트가 발생하면 최초 오류와 보상 오류를 분리해서 확인합니다. 보상 실패가
최초 원인을 덮어쓰지 않아야 하며, Local RAG DB 상태만 보고 Qdrant 복구까지 완료된
것으로 판단하지 않습니다.

## 8. 로그에 허용되는 정보

- Request ID
- `users_idx`, `file_idx`
- `rag_document_idx`, `rag_index_run_idx`
- 문서 형식
- 청크 수와 구조 단위 수
- Parser Type·Version
- Embedding 차원과 Batch 수
- 공개 오류 코드
- 예외 클래스 이름
- 처리 단계와 경과 시간
- HTTP method, 안전한 path, status code
- 보상 처리 성공·실패 상태

## 9. 로그에 금지되는 정보

- 사용자 질문
- 청크·OCR 원문
- 이미지 바이트
- Claude 전체 프롬프트·응답
- 임베딩 벡터
- HTTP 요청·응답 본문
- `INTERNAL_TOKEN`, `RAG_INGEST_TOKEN`
- `ANTHROPIC_API_KEY`
- Presigned URL과 AWS 서명 query
- DB DSN·비밀번호
- 원본 파일의 로컬 절대 경로
- 임시 파일 경로

금지된 필드는 값만 `[MASKED]`로 남기지 않고 필드 자체를 제거할 수 있습니다.
토큰·URL·DSN처럼 진단에 필드 존재가 필요한 경우에는 안전하게 마스킹합니다.

## 10. 로그 주입 방어

- ANSI Escape Sequence 제거
- 개행과 제어 문자 이스케이프
- 과도하게 긴 message와 extra 값 제한
- 순환 참조 또는 직렬화 불가 객체의 안전한 문자열화
- `color_message` 같은 Uvicorn 중복 필드 제거
- 비TTY 출력에 ANSI 미사용

외부 입력을 그대로 message 또는 event로 사용하지 않습니다.

## 11. 서드파티 노이즈 관리

기본값:

```dotenv
JIPSA_RAG_LOG_THIRD_PARTY_LEVEL=WARNING
```

대상 예:

- `httpx`
- `httpcore`
- Qdrant Python SDK
- Uvicorn access logger
- 외부 라이브러리의 반복 정상 통신

외부 라이브러리 INFO를 숨겨도 Local RAG의 업무 이벤트와 실패 로그는 유지됩니다.
진단이 필요한 동안에만 `INFO` 또는 `DEBUG`로 낮춥니다.

## 12. 빠른 상태 확인

### Docker와 GPU

```powershell
docker version
docker compose version
nvidia-smi
docker compose --file .\infra\qdrant\compose.yaml ps
```

### Qdrant

```text
GET http://127.0.0.1:6333/readyz
```

확인:

- 컨테이너 실행
- Collection 존재
- 벡터 차원 `1024`
- 거리 함수 Cosine
- 예상 payload
- 검색 대상 point의 `is_active=true`

### TEI

- GPU 할당
- CUDA 초기화
- CPU 폴백 없음
- 모델 ID 일치
- 요청 수와 벡터 수 일치
- 각 벡터 차원 `1024`
- NaN·Infinity 없음

### Python CUDA

```powershell
uv run python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

### Local RAG DB

```powershell
uv run pytest tests/integration/test_database_connection.py
```

## 13. 증상별 진단

### PowerShell 스크립트가 차단됨

```powershell
Set-ExecutionPolicy `
    -Scope Process `
    -ExecutionPolicy Bypass `
    -Force
```

같은 PowerShell 창에서 `& .\scripts\...ps1`을 다시 실행합니다.

### FastAPI가 시작되지 않음

1. `.env.local` 선택과 필수값
2. `uv sync --frozen`
3. DB startup check
4. 포트 `8077`
5. Settings 검증 오류의 비밀값 노출 여부

### EasyOCR가 CPU를 사용함

1. `JIPSA_RAG_OCR_GPU=true`
2. `JIPSA_RAG_OCR_GPU_REQUIRED=true`
3. `JIPSA_RAG_OCR_DEVICE=cuda:0`
4. CUDA 12.9 PyTorch wheel
5. `torch.cuda.is_available()`
6. NVIDIA Driver와 장치 가시성

### TEI가 준비되지 않음

1. `jipsa-embedding` 컨테이너
2. GPU reservation
3. 이미지와 GPU 아키텍처
4. 모델 Cache
5. CUDA Entrypoint
6. 모델 ID와 출력 차원
7. 포트 `18081`

### Qdrant 검색이 비어 있음

1. 최신 Local RAG 문서가 `INDEXED`인지 확인
2. Qdrant point 존재
3. `users_idx` 일치
4. `file_idx`가 `reference_file_idxs`에 포함
5. `is_active=true`
6. Embedding Model과 Index Version
7. `score_threshold`

검색 범위를 완화해 다른 사용자나 선택하지 않은 파일을 포함시키지 않습니다.

### 인제스트는 성공했지만 Backend 상태가 갱신되지 않음

1. 최신 SUCCESS 문서와 청크
2. 성공 callback 이벤트
3. Backend ingest-complete 응답
4. `INTERNAL_TOKEN`
5. Request ID와 `file_idx`
6. callback 오류 후 상태 정책

### 답변에 잘못된 출처가 포함됨

1. 응답 `status`가 `answered`인지 확인합니다.
2. 본문 `[SOURCE-N]`의 최초 등장 순서를 추출합니다.
3. `cited_source_ids` 순서와 비교합니다.
4. `sources[].source_id` 순서와 비교합니다.
5. 각 `sources[].file_idx`가 `reference_file_idxs`에 포함되는지 확인합니다.
6. 각 Chunk ID가 Local RAG DB와 Qdrant에서 `is_active=true`인지 확인합니다.
7. `source_locator`와 하위 호환 위치 필드가 같은 원본 위치를 가리키는지 확인합니다.
8. UI가 순서를 재정렬하거나 누락 출처를 임의 추가하지 않았는지 확인합니다.

출처 불일치를 UI에서 임의 교정하지 않습니다. 사용자·선택 문서 범위 위반 또는 인용
무결성 위반은 Local RAG 계약 오류로 처리하고 Request ID를 기준으로 검색, 답변 생성과
응답 조립 로그를 연결합니다.

### 재인제스트 후 검색 결과가 사라짐

1. 이전 정상 point가 비활성화되었는지 확인합니다.
2. 신규 point가 활성화되었는지 확인합니다.
3. Local SUCCESS 확정 전 실패가 있었는지 확인합니다.
4. `file_index_previous_reactivation_failed`를 확인합니다.
5. `file_index_run_ownership_lost` 발생 여부를 확인합니다.
6. 최신 SUCCESS 문서와 Qdrant 활성 point가 일치하는지 확인합니다.
7. 오래된 실행이 최신 실행의 point를 변경하지 않았는지 확인합니다.

이 증상은 검색 공백을 만들 수 있으므로 우선순위를 높입니다. 수동 삭제 전에 재인제스트
보상 정책을 확인하고 신규 실패 point만 범위를 좁혀 정리합니다.

### WARNING이 많음

- `is_slow_stage=true`: 지연 임계값 초과
- `ocr_image_failed`: OCR 부분 실패
- `success=true`: 파이프라인은 정상 완료
- 테스트 시나리오의 의도된 실패인지 `PASSED` 결과 확인

WARNING 분석 순서:

1. `event`와 `request_id`를 확인합니다.
2. `success`, `status_code`, `error_code`를 함께 확인합니다.
3. `duration_ms`와 `slow_threshold_ms`를 비교합니다.
4. 같은 Request ID의 최종 `http_request_completed`를 확인합니다.
5. 실제 E2E 시나리오가 부분 실패 또는 보상 검증인지 확인합니다.

### ERROR인데 테스트가 PASSED

보상·장애 변환·HTTP 5xx 관측성 테스트는 의도적으로 예외를 발생시킵니다. 다음을 모두
확인합니다.

- 테스트가 `PASSED`
- 후속 데이터 정리 성공
- 최종 스크립트 종료 코드 `0`
- 예상 error code와 status code
- 비밀정보 비노출

## 14. 요청 경계별 장애 판정표

| 관찰 결과 | 가능성이 높은 경계 | 다음 확인 |
|---|---|---|
| Manifest HTTP 4xx | Backend 인증·권한·요청 계약 | token 방향, file ownership, request schema |
| Presigned 다운로드 403·404 | URL 만료·S3 객체·Backend 발급 | URL 발급 시각, 객체 존재, Backend 로그 |
| 다운로드 성공 후 파서 실패 | Local RAG 형식 검증·파서 | MIME, Magic Byte, OOXML 내부 구조 |
| OCR만 일부 실패 | 이미지 제한·CUDA OCR | image ordinal, 크기, timeout, worker 상태 |
| TEI timeout | CUDA TEI·모델·batch | container log, GPU, 모델 차원, batch size |
| Qdrant 5xx | VectorDB readiness·collection | collection, dimension, distance, payload |
| Local 성공 후 callback 실패 | Backend callback 경계 | callback URL, token, Backend 상태 |
| HTTP 5xx와 `application_exception` | Local RAG 공개 오류 변환 | error code, 최초 예외, 보상 결과 |

경계 판단은 단일 로그 한 줄이 아니라 같은 Request ID의 시작, 단계 완료, 실패와 최종 HTTP
로그를 연결해서 수행합니다. Presigned URL, 질문, OCR 텍스트와 응답 본문은 분석을 위해서도
로그에 추가하지 않습니다.

## 15. 현재 보장하지 않는 관측성

다음 기능은 현재 구현 자료에서 확정되지 않았으므로 존재한다고 가정하지 않습니다.

- Prometheus metrics endpoint
- OpenTelemetry trace exporter
- Grafana dashboard
- 중앙 로그 저장소와 보존 기간
- 자동 Pager·알림 임계값

도입 시 환경 변수, 민감정보 정책, Runbook과 회귀 테스트를 함께 추가합니다.

## 16. 장애 기록 템플릿

```text
[발생 시각]
- 로컬 시간:
- Request ID:

[영향]
- 인제스트 / 검색 / 답변:
- 영향 받은 user_idx·file_idx:
- 이전 정상 검색 가능 여부:

[관찰]
- event:
- level:
- 공개 오류 코드:
- status_code:
- duration_ms:
- 최신 rag_index_run_idx:
- Qdrant is_active:
- callback 상태:

[조치]
- 수행한 진단:
- 수행한 보상:
- 미수행 항목:

[검증]
- 일반 품질 게이트:
- 실제 E2E:
- 잔여 위험:
```

민감 원문과 비밀값을 이 템플릿에 넣지 않습니다.

## 17. 관련 문서

- [Windows Local RAG 실행](local-runtime.md)
- [재인제스트와 보상 처리](ingest-recovery-policy.md)
- [PowerShell 실제 E2E](../testing/powershell-e2e.md)
- [환경 변수와 비밀정보](../security/environment-and-secrets.md)
- [종합 API 명세](../api/comprehensive-api-specification.md)
