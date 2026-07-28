# Jipsa Local RAG 문서 인덱스

> **문서 상태:** Active · Local RAG 문서 허브  
> **주 독자:** 모든 Local RAG 기여자와 연동 담당자  
> **최종 검토:** 2026-07-28  
> **목표:** 독자가 자신의 역할과 작업 목적에 맞는 단일 진입점을 빠르게 찾도록 안내

이 디렉터리는 현재 Local RAG 구현의 아키텍처, 지원 형식, API, 운영, 테스트, 보안과
문서 품질 계약을 관리합니다.

## 1. 무엇을 읽어야 하는가

| 독자·상황 | 먼저 읽을 문서 | 다음 문서 |
|---|---|---|
| 처음 프로젝트를 보는 개발자 | [시각적 Local RAG README](../README.html) | [Markdown README](../README.md) |
| AWS Backend 연동 개발자 | [종합 API 명세서](api/comprehensive-api-specification.md) | [책임 경계](architecture/responsibility-boundary.md) |
| 문서 파서·OCR 개발자 | [지원 형식과 OCR](features/document-support-and-ocr.md) | [실제 E2E](testing/test-guide.md) |
| 검색·답변 개발자 | [종합 API 명세서](api/comprehensive-api-specification.md) | [답변 API 계약](api/rag-answer-api-contract.md) |
| 색인·DB·Qdrant 개발자 | [재인제스트와 보상](operations/ingest-recovery-policy.md) | [관측성과 문제 해결](operations/observability-and-troubleshooting.md) |
| 로컬 환경 구축 담당자 | [Local Runtime](operations/local-runtime.md) | [PowerShell E2E](testing/powershell-e2e.md) |
| 보안 리뷰어 | [환경 변수와 민감정보](security/environment-and-secrets.md) | [책임 경계](architecture/responsibility-boundary.md) |
| PR 문서 리뷰어 | [문서 품질 표준](governance/documentation-quality-standard.md) | [품질 검토 보고서](governance/documentation-review-report.md) |

## 2. Source of truth 우선순위

문서와 구현이 충돌하면 다음 순서로 확인합니다.

1. Pydantic 요청·응답 스키마
2. FastAPI 엔드포인트와 서비스 코드
3. 색인·검색 저장소 구현
4. 실행·테스트 PowerShell 스크립트
5. `.env.example`과 Docker Compose
6. 이 문서 집합

문서가 틀렸다는 이유로 구현 계약을 추측해 수정하지 않습니다. 구현을 확인한 뒤 같은
변경 단위에서 문서와 회귀 테스트를 갱신합니다.

## 3. 아키텍처

- [AWS Backend와 Local RAG 책임 경계](architecture/responsibility-boundary.md)
- [시각적 Local RAG README](../README.html)
- [Markdown Local RAG README](../README.md)
- [용어집](glossary.md)

## 4. 기능

- [지원 문서 형식과 이미지 OCR 범위](features/document-support-and-ocr.md)
- [재인제스트·부분 실패·보상 처리 정책](operations/ingest-recovery-policy.md)

## 5. API

- [**종합 API 명세서 — 전체 endpoint·schema·오류·callback**](api/comprehensive-api-specification.md)
- [API 거버넌스·버전·호환성](api/api-governance-and-compatibility.md)
- [관련 청크 검색 API](chunk-search-api.md)
- [AWS Backend ↔ Local RAG 답변 API 계약](api/rag-answer-api-contract.md)
- [답변·인용·Source Locator 상세 계약](api/rag-answer-contract.md)

## 6. 운영과 문제 해결

- [CUDA·TEI·Qdrant·Local DB 실행 절차](operations/local-runtime.md)
- [관측성·진단·문제 해결](operations/observability-and-troubleshooting.md)
- [재인제스트·보상 처리](operations/ingest-recovery-policy.md)

## 7. 테스트

- [Ruff·Mypy·Pytest·실제 E2E 실행 절차](testing/test-guide.md)
- [PowerShell 실제 E2E 상세 가이드](testing/powershell-e2e.md)
- [문서 회귀 테스트](../tests/regression/test_rag_documentation_contract.py)

## 8. 보안

- [환경 변수와 민감정보 관리](security/environment-and-secrets.md)
- [환경 변수 예시](../.env.example)
- [RAG 전용 Git 제외 규칙](../.gitignore)

## 9. 문서 거버넌스

- [세계적 수준 문서 품질 표준](governance/documentation-quality-standard.md)
- [최종 문서 품질 검토 보고서](governance/documentation-review-report.md)
- [README.html 디자인·접근성 품질 보고서](governance/readme-html-quality-report.md)
- [종합 API 명세서 세계적 수준 품질 평가](governance/comprehensive-api-specification-quality-report.md)

## 10. 문서 유형별 필수 구성

| 문서 유형 | 반드시 포함할 항목 |
|---|---|
| 개요 | 목적, 구성요소, 책임 경계, 읽는 순서, 관련 문서 |
| API 계약 | 방향, 인증, 요청·응답, 제약, 오류, 호환성, 보안, 검증 |
| Runbook | 전제조건, 실행 순서, 준비 상태, 종료, 실패 증상, 복구, 검증 |
| 정책 | 목표, 불변조건, 상태 전이, 실패 매트릭스, 예외, 테스트 기준 |
| 보안 | 자산, 신뢰 경계, 금지 데이터, 저장·로그 정책, 회전·사고 대응 |
| 품질 보고서 | 기준, 객관적 검사, 수동 검토, 잔여 위험, 미실행 항목 |

## 11. 표준 용어

간략 표:

| 용어 | 표준 의미 |
|---|---|
| AWS Backend | 사용자, 파일, S3와 권한을 관리하는 Spring Boot 서비스 |
| Local RAG | Backend와 분리된 로컬 GPU 기반 FastAPI 서비스 |
| Local RAG DB | `Jipsa_Local_RAG` 문서·청크·색인 실행 이력 저장소 |
| `File_IDX` | AWS Backend DB 파일 식별자 |
| `reference_file_idxs` | 현재 검색·답변 요청에서 Backend가 확정한 선택 문서 목록 |
| Source Locator | 형식별 원본 위치와 OCR 이미지 위치를 표현하는 공통 객체 |
| lookup | 선택 문서 전체 단일 검색·단일 답변 전략 |
| synthesis | 문서별 검색·부분 답변·최종 종합 전략 |

전체 정의와 금지 표현은 [용어집](glossary.md)을 참조합니다.

## 12. 유지 규칙

- 제품·서비스 명칭은 용어집의 대소문자를 사용합니다.
- `RAG Server`, `AI Server`보다 책임이 명확한 `Local RAG`를 사용합니다.
- 사용자 선택 범위는 `reference_file_idxs`로 표기합니다.
- 신규 위치 기준은 `source_locator`이며 기존 위치 필드는 legacy 호환으로 표현합니다.
- `SOURCE-N`, `cited_source_ids`, `sources`의 최초 등장 순서 계약을 함께 설명합니다.
- 외부 링크를 제외한 Markdown 링크는 `RAG` 디렉터리 내부의 실제 상대 경로를 사용합니다.
- Local RAG 문서는 상위 프로젝트 README, Backend 또는 Frontend 문서의 내용에 의존하지 않습니다.
- 과거 PDF 전용, OCR 미지원 또는 전체 문서 자동 검색 설명을 다시 추가하지 않습니다.
- 실제로 실행하지 않은 품질 게이트나 E2E를 통과한 것으로 기록하지 않습니다.
