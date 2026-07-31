# 이전 RAG 내부 배치안 정리

이 문서는 초안으로 제안됐던 `RAG/tools/performance` 구조를 독립 프로그램으로 전환할 때
중복 파일을 남기지 않기 위한 확인 목록입니다.

## 최종 유지 구조

```text
INT2-Jipsa-Team04/
├─ RAG/
└─ RAG-Performance/
```

성능 측정 코드, 합성 Fixture, 단위 테스트와 결과 파일은 모두 `RAG-Performance/` 아래에
존재해야 합니다. `RAG/`에는 기존 서비스 코드와 단계별 구조화 로그만 유지합니다.

## 이전 초안을 로컬에 복사한 경우 제거할 경로

다음 경로는 최종 구조에서 사용하지 않습니다.

```text
RAG/tools/performance/
RAG/tests/unit/performance/
RAG/scripts/run-rag-resource-benchmark.ps1
RAG/artifacts/performance/
```

해당 경로가 존재하지 않으면 별도 작업은 필요하지 않습니다.

## 유지해야 하는 RAG 파일

성능 측정기는 현재 RAG의 다음 기능을 외부에서 사용하므로 관련 파일을 삭제하거나
벤치마크 전용으로 수정하지 않습니다.

```text
RAG/src/jipsa_rag/api/v1/endpoints/file_processing.py
RAG/src/jipsa_rag/core/logging.py
RAG/src/jipsa_rag/main.py
RAG/infra/qdrant/compose.yaml
RAG/.env.local
```

- 파일 처리 단계별 JSON 로그는 인제스트 단계 시간 수집에 사용합니다.
- FastAPI, EasyOCR, TEI, Local RAG DB와 Qdrant는 실제 측정 대상입니다.
- `.env.local`은 읽기만 하며 성능 프로그램이 파일 내용을 변경하지 않습니다.
- 벤치마크 전용 의존성을 RAG의 `pyproject.toml`이나 `uv.lock`에 추가하지 않습니다.
