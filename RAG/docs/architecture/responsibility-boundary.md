# AWS Backend와 Local RAG 책임 경계

> **문서 상태:** Stable · 변경 시 Backend와 Local RAG 양쪽 리뷰 필수  
> **주 독자:** 시스템 설계자, Backend·Local RAG 개발자, 보안 리뷰어  
> **최종 검토:** 2026-07-28  
> **핵심 결정:** 사용자·파일 권한은 AWS Backend, 파싱·OCR·검색·답변은 Local RAG


## 1. 목적

이 문서는 AWS Backend와 Local RAG 사이의 책임, 데이터 소유권, 신뢰 경계와 호출 방향을
고정합니다. 두 서비스는 같은 저장소에 있지만 실행 환경과 운영 책임이 다릅니다.

- AWS Backend: AWS 환경
- Local RAG: CUDA 12.9 GPU가 있는 별도 로컬 환경

## 2. 경계 요약

| 영역 | AWS Backend | Local RAG |
|---|---|---|
| 사용자 인증·인가 | 최종 책임 | 수행하지 않음 |
| 파일 소유권·접근 권한 | 최종 책임 | 전달받은 범위만 사용 |
| S3 원본 관리 | 업로드·삭제·상태·Presigned URL | Presigned GET URL 다운로드만 수행 |
| AWS 자격 증명 | IAM Role 또는 Backend 실행 환경에서 관리 | 보관·사용 금지 |
| 파일 manifest | 원본 제공자 | 최신 manifest 소비자 |
| 파싱·OCR·청킹 | 수행하지 않음 | 최종 책임 |
| 임베딩·VectorDB | 수행하지 않음 | TEI·Qdrant 운영 |
| RAG 문서·청크 상태 | 콜백 결과 동기화 | Local RAG DB의 원본 상태 관리 |
| 검색 범위 | `reference_file_idxs` 확정 | 범위 축소만 가능, 확대 금지 |
| 답변·출처 | 사용자 응답 중계 | lookup·synthesis·인용 검증 |
| 사용자 UI | Frontend와 함께 제공 | 직접 제공하지 않음 |

## 3. 호출 방향

```text
AWS Backend
    │
    ├─ X-Internal-Token: RAG_INGEST_TOKEN
    │
    ├─ POST /ingest
    ├─ POST /api/v1/chunks/search
    └─ POST /api/v1/rag/answers
            │
            ▼
Local RAG
            │
            ├─ X-Internal-Token: INTERNAL_TOKEN
            ├─ GET /internal/files/{fileIdx}/manifest
            └─ POST /internal/files/{fileIdx}/ingest-complete
                    │
                    ▼
AWS Backend
```

두 토큰은 이름과 호출 방향이 다릅니다.

| 환경 변수 | 보관 위치 | 사용 방향 |
|---|---|---|
| `RAG_INGEST_TOKEN` | Backend와 Local RAG | AWS Backend → Local RAG |
| `INTERNAL_TOKEN` | Backend와 Local RAG | Local RAG → AWS Backend |

같은 값으로 설정할 수 있더라도 역할을 구분하며, 운영에서는 별도 비밀값을 권장합니다.

## 4. 인제스트 경계

### Backend가 제공하는 정보

- `File_IDX`
- 사용자 식별자
- 최신 파일명·폴더·상태
- 원본 파일 크기·Hash·MIME Type
- S3 Object Key
- 짧은 만료 시간을 가진 Presigned GET URL

### Local RAG 처리

1. 요청의 `file_idx`로 최신 manifest를 다시 조회합니다.
2. Presigned GET URL의 허용 호스트, 크기와 시간 제한을 검증합니다.
3. 파일 바이트, MIME Type, Magic Byte와 OOXML 구조를 검증합니다.
4. 파싱, 이미지 추출, OCR, 청킹과 임베딩을 수행합니다.
5. Local RAG DB와 Qdrant를 안전하게 전환합니다.
6. 같은 `File_IDX` lock 안에서 최신 활성 청크 스냅샷을 읽습니다.
7. ingest-complete 콜백으로 성공·실패를 알립니다.

Local RAG는 S3 Object Key를 검증할 수 있지만 AWS SDK로 S3에 직접 접근하지 않습니다.

## 5. 검색·답변 경계

Backend는 사용자 인증·인가와 파일 접근 권한을 확인한 다음 질문 시점의 선택 문서 목록을
`reference_file_idxs`로 고정합니다.

Local RAG는 다음 필터를 모두 만족하는 Qdrant point만 검색합니다.

```text
users_idx == request.user_idx
AND is_active == true
AND file_idx IN request.reference_file_idxs
```

방어 계층:

1. Qdrant `must` 필터
2. Qdrant 응답 payload 재검증
3. 검색 서비스 범위·점수·정렬 검증
4. 답변 컨텍스트 범위 검증
5. 최종 `sources[].file_idx` 범위 검증

`reference_file_idxs`가 없거나 빈 배열이면 전체 문서 검색으로 확장하지 않습니다.

## 6. 데이터 소유권

| 데이터 | 원본 소유자 | 복제·파생 저장 |
|---|---|---|
| 사용자 계정·권한 | AWS Backend | Local RAG 저장 금지 |
| 원본 파일 | AWS Backend/S3 | 처리 중 임시 다운로드만 허용 |
| 파일 manifest | AWS Backend | 요청 범위에서만 사용 |
| 파싱 텍스트·OCR 텍스트 | Local RAG 파생 데이터 | Local RAG DB 청크 |
| 임베딩 벡터 | Local RAG 파생 데이터 | Qdrant |
| 파일 처리 상태 | Backend 사용자 상태 | Local RAG 실행 이력과 콜백으로 동기화 |
| 질문과 답변 | 요청 처리 데이터 | 민감 원문 로그 금지 |

## 7. 네트워크 경계

- Local RAG FastAPI만 Backend가 접근할 수 있는 주소로 노출합니다.
- TEI `18081`, Qdrant REST `6333`, Qdrant gRPC `6334`는 루프백에 바인딩합니다.
- Local RAG DB는 외부 인터넷에 노출하지 않습니다.
- 외부 네트워크 구간은 HTTPS, 방화벽과 IP allowlist를 적용합니다.
- `X-Internal-Token`은 URL query parameter가 아니라 요청 헤더로만 전달합니다.

## 8. 장애 책임

| 장애 | 우선 확인 |
|---|---|
| 사용자 권한·선택 파일 오류 | AWS Backend |
| manifest·Presigned URL 오류 | AWS Backend와 서비스 간 네트워크 |
| 파일 형식·파싱·OCR 오류 | Local RAG |
| CUDA·TEI·Qdrant·Local DB 오류 | Local RAG 운영 환경 |
| ingest-complete 반영 오류 | Backend 내부 API와 서비스 간 네트워크 |
| 잘못된 SOURCE-N·출처 순서 | Local RAG 답변 계약 |
| 사용자 화면 출처 표시 오류 | Frontend와 Backend 응답 매핑 |

## 9. 금지 사항

- Local RAG 코드나 `.env.*`에 AWS Access Key를 추가하지 않습니다.
- Local RAG가 Backend DB에 직접 연결해 사용자·파일 상태를 수정하지 않습니다.
- Backend가 Qdrant를 직접 조회해 Local RAG의 범위 검증을 우회하지 않습니다.
- 브라우저가 Local RAG 내부 API를 직접 호출하게 하지 않습니다.
- 선택 문서가 없을 때 사용자 전체 문서를 자동 검색하지 않습니다.
