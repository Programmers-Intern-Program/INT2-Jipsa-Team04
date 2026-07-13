# Jipsa RAG Service

Jipsa 서비스에서 업로드된 파일을 처리하고 RAG 색인 파이프라인을
수행하기 위한 FastAPI 기반 서비스입니다.

## 환경별 설정

| 환경        | 설정 파일 또는 공급 방식              |
| ----------- | ------------------------------------- |
| local       | `.env.local`                          |
| development | `.env.development`                    |
| test        | `.env.test`                           |
| production  | 서버 환경 변수 또는 AWS Secret 저장소 |

실제 환경 설정 파일은 Git에 커밋하지 않습니다.
환경 변수 목록과 예시는 `.env.example`에서 관리합니다.

## 로컬 설정

```powershell
Copy-Item .env.example .env.local
uv sync
uv run fastapi dev src/jipsa_rag/main.py
```
