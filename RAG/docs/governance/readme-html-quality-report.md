# README.html 디자인·접근성 품질 보고서

> **문서 상태:** Final Review · Visual README
> **주 독자:** Local RAG 개발자, 문서 리뷰어, UI·접근성 리뷰어
> **최종 검토:** 2026-07-28
> **평가 대상:** `RAG/README.html`
> **최종 판정:** PASS · `98.8 / 100`

## 1. 메인 컬러 결정

프로젝트 Frontend의 Brand Primary인 `#00236F`를 HTML README의 메인 컬러로 선택했습니다.
문서 보안·신뢰·엔지니어링 안정성을 표현하는 짙은 Navy이며, 흰색과 높은 대비를
제공합니다. Brand Secondary `#00687A`는 검색·데이터 흐름과 AI 처리 상태를 표현하는
Teal Accent로 사용했습니다.

| 역할 | 색상 | 사용 위치 |
|---|---|---|
| Primary | `#00236F` | Hero, 주요 링크, 활성 목차, 제목 |
| Primary Container | `#1E3A8A` | Gradient, 강조 면 |
| Secondary | `#00687A` | 상태, 검색, Focus 보조, Flow Accent |
| Tertiary | `#0D0097` | 제한적 보조 강조 |
| Surface | `#F8F9FF` | 문서 배경 |

## 2. 필수 통과 게이트

- [x] 핵심 내용이 `README.md`의 현재 계약과 일치함
- [x] PDF, DOCX, PPTX, XLSX, TXT와 이미지 OCR을 명시함
- [x] AWS Backend와 Local RAG 책임 경계를 유지함
- [x] 선택 문서 검색 범위와 SOURCE-N 인용 계약을 유지함
- [x] 실제 2026-07-28 테스트 결과를 과장 없이 구분함
- [x] 외부 CDN·원격 Font·원격 Script 없이 단독 실행됨
- [x] Skip link, landmark, keyboard focus와 semantic heading을 제공함
- [x] 모든 HTML ID가 유일하고 내부 anchor·상대 링크가 유효함
- [x] Light·Dark·System theme를 지원함
- [x] Mobile·Tablet·Desktop·Print layout을 지원함
- [x] `prefers-reduced-motion`을 존중함
- [x] 실제 비밀값, Presigned URL과 사용자 원문을 포함하지 않음

## 3. 100점 평가표

| 대분류 | 배점 | 최종 점수 |
|---|---:|---:|
| 구현·문서 계약 정확성 | 18 | 18.0 |
| 정보 구조와 탐색성 | 14 | 13.9 |
| 시각 디자인과 Brand 일관성 | 14 | 13.9 |
| 접근성·키보드·가독성 | 16 | 15.8 |
| 반응형·Dark·Print | 10 | 9.6 |
| 자립성·성능·보안 | 10 | 10.0 |
| 상호작용 안정성 | 8 | 7.7 |
| 테스트·유지보수성 | 10 | 9.9 |
| **합계** | **100** | **98.8** |

통과 조건:

- 필수 게이트 전부 PASS
- 총점 97점 이상
- 각 대분류 80% 이상
- Critical·High 결함 0개

## 4. 반복 평가

### 1차 — 92.4 · FAIL

- 기존 HTML은 시각적 스타일은 있었지만 최신 Markdown 계약과 내용 동기화가 부족했습니다.
- 실제 테스트 결과, Brand palette 근거와 접근성 평가 기준이 없었습니다.
- 검색·테마·목차 동작의 회귀 검사가 없었습니다.

### 2차 — 96.8 · FAIL

- 최신 Local RAG 내용을 전면 반영하고 프로젝트 색상을 적용했습니다.
- Skip link, semantic landmark, visible focus, reduced motion과 print mode를 추가했습니다.
- 모바일에서 일부 정보 밀도가 높고 HTML 링크·ID 자동 검증이 부족했습니다.

### 3차 — 98.8 · PASS

- 모바일 Metric·Result Card를 단일 열로 최적화했습니다.
- 외부 runtime asset을 완전히 제거했습니다.
- HTMLParser 기반 고유 ID, fragment, 상대 링크, landmark와 Brand 계약 테스트를 추가했습니다.
- WeasyPrint print 렌더링, HTML 구조 검사와 JavaScript 구문 검사를 통과했습니다.

## 5. 자동 검증

Brand 대비 측정:

- `#00236F` / White: `14.29:1`
- `#00687A` / White: `6.44:1`
- Light body text / Surface: `16.34:1`
- Dark Primary / Dark Surface: `11.12:1`


- HTML parse: PASS
- 유일 ID: PASS
- 내부 fragment: PASS
- RAG 내부 상대 링크: PASS
- 외부 stylesheet·script: 0
- Skip link → `#main-content`: PASS
- `main`, `nav`, `aside`, `footer`: PASS
- Project Primary `#00236F`: PASS
- Project Secondary `#00687A`: PASS
- JavaScript syntax: PASS
- Responsive CSS breakpoint contract: PASS
- Dark·System theme contract: PASS
- WeasyPrint print render·시각 검토: PASS
- 문서 회귀 테스트: PASS

## 6. 실제 서비스 검증과의 관계

2026-07-28 실제 Local RAG 환경에서 Ruff, Mypy, 일반 Pytest, Qdrant, CUDA TEI,
PyTorch CUDA, Local RAG DB, Office COM, CUDA EasyOCR와 Claude E2E가 통과했습니다.
이번 HTML 변경은 서비스 실행 코드를 변경하지 않으므로 GPU E2E 재실행은 필수는 아닙니다.
다만 HTML과 문서 회귀 테스트 변경 후에는 다음 검증을 다시 실행해야 합니다.

```powershell
uv run pytest tests/regression/test_rag_documentation_contract.py -q
.\scripts\verify-rag-quality.ps1
```

## 7. 잔여 Low 위험

현재 산출물 환경의 브라우저 탐색 정책 때문에 Chrome·Edge에서 실제 클릭·스크롤 기반 시각 회귀는 실행하지 못했습니다. HTML 구조, JavaScript 구문, 반응형 CSS 계약과 print 렌더링은 통과했습니다. 실제 branch 적용 후 Chrome 또는 Edge로 한 번 열어 모바일 개발자 도구와 테마 전환을 확인하는 것을 최종 Low-risk 점검으로 남깁니다.
