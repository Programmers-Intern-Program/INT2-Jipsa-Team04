# Local RAG 관측성과 문제 해결

> **문서 상태:** Active · 진단·장애 대응 Runbook  
> **주 독자:** Local RAG 개발자, QA, 운영·장애 대응 담당자  
> **최종 검토:** 2026-07-28  
> **범위:** FastAPI, CUDA EasyOCR, CUDA TEI, Qdrant, Local RAG DB, Backend callback

## 1. 진단 원칙

1. 먼저 **어느 경계가 실패했는지** 식별합니다.
2. Request ID와 안전한 식별자를 사용해 요청 흐름을 연결합니다.
3. 질문·청크·OCR·프롬프트·토큰·Presigned URL 원문을 로그로 남기지 않습니다.
4. 이전 정상 색인이 유지되는지 확인한 뒤 신규 실패 상태를 조사합니다.
5. 보상 처리 실패가 최초 오류를 덮지 않았는지 확인합니다.
6. 실제로 수행하지 않은 복구를 완료한 것으로 기록하지 않습니다.

## 2. 관찰 지점

| 계층 | 준비 상태·확인 방법 | 핵심 식별자 |
|---|---|---|
| FastAPI | 프로세스, OpenAPI `/docs`, 요청 상태 | Request ID, `user_idx`, `file_idx` |
| Backend manifest | HTTP 상태와 공개 오류 코드 | Request ID, `file_idx` |
| 파일 다운로드 | 호스트 검증, 크기, timeout 단계 | `file_idx`, 오류 종류 |
| 파서·OCR | 형식, 성공 청크 수, 실패 이미지 수 | `file_idx`, parser type |
| EasyOCR | CUDA 장치, 모델 Cache, timeout | 이미지 순번, 오류 클래스 |
| TEI | `/embed`, 벡터 수·차원 | 모델 ID, 차원 |
| Local RAG DB | `SELECT 1`, 최신 RUN 상태 | `rag_document_idx`, `rag_index_run_idx` |
| Qdrant | `/readyz`, Collection, payload | `chunk_id`, `file_idx`, `is_active` |
| Claude | 공개 오류 코드, 호출 예산 | Request ID, 모델 ID |
| Backend callback | 성공·실패 호출 결과 | Request ID, `file_idx` |

## 3. 로그에 허용되는 정보

- Request ID
- `users_idx`, `file_idx`
- `rag_document_idx`, `rag_index_run_idx`
- `chunk_count`, 이미지 성공·실패 개수
- Parser Version, Embedding Model, Index Version
- 공개 오류 코드
- 예외 클래스 이름
- 처리 단계와 경과 시간
- 보상 처리 성공·실패 상태

## 4. 로그에 금지되는 정보

- 사용자 질문
- 청크·OCR 원문
- 이미지 바이트
- Claude 전체 프롬프트·응답
- 임베딩 벡터
- `INTERNAL_TOKEN`, `RAG_INGEST_TOKEN`
- `ANTHROPIC_API_KEY`
- Presigned URL과 AWS 서명 query
- DB DSN·비밀번호

자세한 정책:
[환경 변수와 민감정보 관리](../security/environment-and-secrets.md)

## 5. 대표 구조화 이벤트

| 이벤트 | 의미 | 우선 확인 |
|---|---|---|
| `file_index_run_ownership_lost` | 오래된 실행이 최신 실행 소유권을 잃음 | 최신 SUCCESS RUN과 활성 point |
| `file_index_upsert_cleanup_failed` | 신규 staging point 정리 실패 | 신규 Chunk ID와 `is_active` |
| `file_index_previous_reactivation_failed` | 이전 정상 point 재활성화 실패 | 검색 공백 여부 |
| `file_index_new_deactivation_failed` | 실패한 신규 point 비활성화 실패 | 신규 point 노출 여부 |
| `file_index_new_point_delete_failed` | 신규 point 보상 삭제 실패 | orphan point |
| `ingest_failure_callback_failed` | 처리 실패를 Backend에 통지하지 못함 | Backend 파일 상태와 네트워크 |

이 표는 전체 이벤트 목록이 아닙니다. 코드의 `event` 필드가 Source of truth이며 새 이벤트를
추가하면 이 Runbook과 테스트를 함께 검토합니다.

## 6. 빠른 상태 확인

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

확인 항목:

- 컨테이너 실행
- Collection 존재
- 벡터 차원 `1024`
- 거리 함수 Cosine
- 예상 payload 필드
- 검색 대상 point의 `is_active=true`

### TEI

시작 스크립트의 실제 `/embed` 검사를 우선 사용합니다.

확인 항목:

- GPU 할당
- CUDA 초기화 성공
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

실제 환경을 읽는 테스트인지 확인하고 운영 DB를 사용하지 않습니다.

## 7. 증상별 진단

### FastAPI가 시작되지 않음

1. `.env.local` 선택과 필수값을 확인합니다.
2. `uv sync --frozen` 성공 여부를 확인합니다.
3. DB startup check 실패 여부를 확인합니다.
4. 포트 `8077` 충돌을 확인합니다.
5. Settings 검증 오류에서 비밀값 원문이 출력되지 않았는지 확인합니다.

### EasyOCR가 CPU를 사용함

1. `JIPSA_RAG_OCR_GPU=true`
2. `JIPSA_RAG_OCR_GPU_REQUIRED=true`
3. `JIPSA_RAG_OCR_DEVICE=cuda:0`
4. CUDA 12.9 PyTorch wheel
5. `torch.cuda.is_available()`
6. NVIDIA Driver와 장치 가시성

GPU 필수 환경에서 CPU 폴백을 정상 처리로 간주하지 않습니다.

### TEI가 준비되지 않음

1. `jipsa-embedding` 컨테이너 상태
2. GPU reservation
3. 이미지와 GPU 아키텍처의 일치
4. Hugging Face 모델 Cache와 다운로드 상태
5. CUDA Entrypoint 로그
6. 모델 ID와 출력 차원
7. 포트 `18081` 충돌

### Qdrant 검색이 비어 있음

1. 해당 `File_IDX`의 최신 Local RAG 문서가 `INDEXED`인지 확인합니다.
2. Qdrant point 존재 여부를 확인합니다.
3. `users_idx`가 요청 사용자와 같은지 확인합니다.
4. `file_idx`가 `reference_file_idxs`에 포함되는지 확인합니다.
5. `is_active=true`인지 확인합니다.
6. Embedding Model과 Index Version을 확인합니다.
7. `score_threshold`가 지나치게 높지 않은지 확인합니다.

범위를 완화해 다른 사용자·선택하지 않은 파일을 포함시키지 않습니다.

### 인제스트는 성공했지만 Backend 상태가 갱신되지 않음

1. Local RAG의 최신 SUCCESS 문서와 청크를 확인합니다.
2. 성공 callback 시작 여부를 확인합니다.
3. Backend `/internal/files/{fileIdx}/ingest-complete` 응답을 확인합니다.
4. `INTERNAL_TOKEN` 방향과 값 일치를 확인합니다.
5. Request ID로 Backend 로그를 연결합니다.
6. 성공 callback 오류 뒤 실패 callback을 보내지 않는 정책인지 확인합니다.

### 재인제스트 후 검색 결과가 사라짐

이 증상은 핵심 불변식 위반 가능성이 있으므로 우선순위를 높입니다.

1. 이전 정상 point가 비활성화됐는지 확인합니다.
2. 신규 point가 활성화됐는지 확인합니다.
3. Local SUCCESS 확정 전 실패가 있었는지 확인합니다.
4. `file_index_previous_reactivation_failed`를 확인합니다.
5. 실행 소유권 상실 여부를 확인합니다.
6. 최신 SUCCESS 문서와 Qdrant 활성 point가 일치하는지 확인합니다.

### 답변에 잘못된 출처가 포함됨

1. 응답이 `answered`인지 확인합니다.
2. 본문 `[SOURCE-N]` 최초 등장 순서를 추출합니다.
3. `cited_source_ids`와 비교합니다.
4. `sources[].source_id`와 비교합니다.
5. 각 `sources[].file_idx`가 `reference_file_idxs` 안인지 확인합니다.
6. 각 Chunk ID가 활성 Local RAG 청크인지 확인합니다.
7. locator와 legacy 위치가 일치하는지 확인합니다.

불일치를 UI에서 임의 교정하지 않고 Local RAG 계약 오류로 처리합니다.

## 8. 인제스트 상태 점검 순서

```text
Backend File 상태
  → RAG_Index_Run 최신 실행
  → RAG_Document 최신 SUCCESS·활성 상태
  → RAG_Chunk 개수와 결정적 Chunk ID
  → Qdrant point 개수·활성 상태
  → Backend ingest-complete 반영
```

수동 삭제 전에
[재인제스트와 보상 처리 정책](ingest-recovery-policy.md)을 확인합니다.

## 9. 복구 원칙

- 이전 정상 색인을 우선 보존합니다.
- 신규 실패 point만 범위를 좁혀 정리합니다.
- 소유권을 잃은 오래된 RUN이 최신 point를 변경하지 않게 합니다.
- Qdrant 보상 실패 시 Local RAG DB 상태만 보고 복구 완료로 판단하지 않습니다.
- callback 재전송은 Backend가 기존 성공을 반영했는지 확인한 뒤 결정합니다.
- 테스트·운영 데이터를 구분하고 운영 범위에서 E2E 정리 코드를 실행하지 않습니다.

## 10. 현재 문서로 보장하지 않는 관측성

다음 기능은 현재 제공된 구현 자료에서 확정되지 않았으므로 존재한다고 가정하지 않습니다.

- Prometheus metrics endpoint
- OpenTelemetry trace exporter
- Grafana dashboard
- 중앙 로그 저장소와 보존 기간
- 자동 Pager·알림 임계값

도입 시 환경 변수, 민감정보 정책, Runbook과 회귀 테스트를 함께 추가합니다.

## 11. 장애 기록 템플릿

```text
[발생 시각]
- 로컬 시간:
- Request ID:

[영향]
- 인제스트 / 검색 / 답변:
- 영향 받은 user_idx·file_idx:
- 이전 정상 검색 가능 여부:

[관찰]
- 공개 오류 코드:
- 오류 클래스:
- 최신 rag_index_run_idx:
- Qdrant is_active 상태:
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
