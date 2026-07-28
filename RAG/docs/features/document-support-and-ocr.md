# 지원 문서 형식과 이미지 OCR 범위

> **문서 상태:** Stable · 지원 형식 변경 시 필수 갱신  
> **주 독자:** 문서 파서·OCR 개발자, QA, 제품·연동 담당자  
> **최종 검토:** 2026-07-28  
> **검증 기준:** 고정 다중 형식 Fixture와 실제 CUDA EasyOCR E2E


## 1. 지원 범위

| 형식 | 텍스트 추출 | 구조 보존 | 이미지 추출·렌더링 | OCR | 대표 Source Locator |
|---|---:|---:|---:|---:|---|
| PDF | 지원 | 페이지·표 | 임베디드 이미지, 스캔 페이지 렌더링 | 지원 | `pdf_page` |
| DOCX | 지원 | 섹션·블록·문단·표 | 인라인·플로팅 이미지 | 지원 | `docx_block` |
| PPTX | 지원 | 슬라이드·도형·표·노트 | 이미지, 차트, SmartArt | 지원 | `pptx_shape` |
| XLSX | 지원 | 시트·행·셀·표·병합 범위 | 삽입 이미지, 차트 | 지원 | `xlsx_cell_range` |
| TXT | 지원 | 줄·문자 범위 | 해당 없음 | 해당 없음 | `txt_line` |

지원 여부는 파일 확장자만으로 결정하지 않습니다. MIME Type, Magic Byte와 OOXML 내부
구조가 선언된 형식과 일치해야 합니다.

## 2. 형식별 처리

### PDF

- 페이지 텍스트와 표를 추출합니다.
- 임베디드 이미지를 추출합니다.
- 텍스트가 적고 이미지가 페이지 대부분을 차지하면 스캔 페이지 후보로 탐지합니다.
- 스캔 PDF와 이미지 전용 페이지를 설정된 DPI의 PNG로 렌더링합니다.
- 기존 top-level `page`와 `source_locator.page`는 같은 1-based 값을 사용합니다.

### DOCX

- 섹션, 문단, 제목, 목록과 표의 순서를 보존합니다.
- 문서 XML 관계를 사용해 인라인 이미지와 플로팅 이미지를 원본 블록 문맥에 연결합니다.
- OCR 청크는 `section_index`, `block_index`, `paragraph_index` 또는 `table_index`를
  가능한 범위에서 상속합니다.

### PPTX

- 슬라이드, 도형 텍스트, 그룹 도형 경로, 표와 발표자 노트를 추출합니다.
- 일반 이미지 관계를 추출합니다.
- 차트와 SmartArt는 Microsoft PowerPoint COM으로 PNG 렌더링합니다.
- `slide_no`는 1-based이며 도형 위치는 `shape_path`와 EMU 좌표를 사용합니다.

### XLSX

- 시트, 행, 셀, 병합 범위, 표와 저장된 수식 결과를 추출합니다.
- 삽입 이미지는 앵커 셀과 연결합니다.
- 차트는 Microsoft Excel COM으로 PNG 렌더링합니다.
- `sheet_number`는 사용자 표시용 1-based 값이며 `sheet_name`과 셀 범위를 함께 반환합니다.

### TXT

- 인코딩을 탐지하고 바이너리 파일을 거부합니다.
- 줄 범위와 문자 범위를 보존합니다.
- `char_end`는 exclusive입니다.
- 이미지 컨테이너가 아니므로 OCR 경로를 사용하지 않습니다.

## 3. Office 렌더링 제약

PPTX 차트·SmartArt와 XLSX 차트 렌더링은 다음 조건에서만 수행합니다.

- Windows 로컬 또는 RDP 대화형 로그인 세션
- Microsoft PowerPoint와 Excel 설치
- `JIPSA_RAG_OFFICE_RENDERING_ENABLED=true`
- `JIPSA_RAG_OFFICE_RENDERING_PROVIDER=microsoft_office_com`
- 동시성 `1`

Windows 서비스, SYSTEM 계정과 비대화형 세션에서는 Office COM 렌더링을 건너뜁니다.
LibreOffice와 `soffice.exe`를 대체 렌더러로 사용하지 않습니다.

## 4. 이미지 후보 검증

이미지는 OCR 전에 다음 방어 절차를 통과해야 합니다.

- 문서당 이미지 개수 제한
- 단일 이미지와 문서 전체 바이트 제한
- 디코딩 후 최대 픽셀 수 제한
- 최소 바이트, 너비, 높이와 면적 제한
- 최대 종횡비 제한
- 안전한 이미지 디코딩
- SHA-256 기반 중복 제거

작은 아이콘, 로고, 배지, 구분선과 장식 이미지는 검색 품질과 GPU 비용을 보호하기 위해
OCR 후보에서 제외할 수 있습니다.

주요 환경 변수는 [환경 변수 예시](../../.env.example)의 다음 구역에 정의됩니다.

- `Document Image Extraction`
- `Decorative Image Filter`
- `Scanned PDF Detection and Rendering`
- `Microsoft Office 2024 COM Rendering`
- `CUDA EasyOCR`

## 5. CUDA EasyOCR

기본 OCR 계약:

| 항목 | 기본값 |
|---|---|
| 언어 | `ko,en` |
| GPU | 사용 |
| GPU 필수 | `true` |
| 장치 | `cuda:0` |
| 모델 저장소 | `.cache/easyocr` |
| 최소 신뢰도 | `0.35` |
| 이미지별 시간 제한 | `45초` |
| 문서 전체 시간 제한 | `600초` |

PyTorch와 torchvision은 `pyproject.toml`의 CUDA 12.9 전용 인덱스에서 설치합니다.
GPU 필수 설정에서 CUDA를 사용할 수 없으면 CPU로 조용히 폴백하지 않고 실행을 실패시킵니다.

## 6. OCR 정규화와 문맥

OCR 결과는 검색 청크로 만들기 전에 다음을 수행합니다.

- 비정상 제어 문자 제거
- 공백과 줄바꿈 정규화
- 최소 신뢰도 미만 줄 제외
- 이미지별 최대 문자 수 적용
- 주변 문단, 슬라이드 또는 시트 문맥 연결
- 원본 문서 위치 메타데이터 상속

OCR 텍스트와 일반 파서 텍스트는 같은 임베딩·Qdrant 검색 후보가 됩니다.
답변과 출처 계약도 동일합니다.

## 7. 중복 방지

동일 이미지 바이트의 SHA-256 Hash를 기준으로 다음 중복을 방지합니다.

- 같은 문서 안의 반복 이미지 추출
- 관계가 여러 번 참조하는 동일 이미지
- 동일 이미지의 반복 OCR
- 같은 OCR 결과를 가진 중복 청크 생성

원본 문서의 서로 다른 위치에 같은 이미지가 반복되는 경우 검색 데이터 중복은 줄이되,
대표 원본 위치와 이미지 식별자는 결정적으로 유지합니다.

## 8. 부분 실패

이미지 하나의 디코딩, 렌더링 또는 OCR이 실패해도 다른 이미지와 일반 텍스트 처리는
계속할 수 있습니다.

- 성공한 OCR 결과는 유지합니다.
- 실패한 이미지 개수와 안전한 오류 종류를 집계합니다.
- 이미지 원문 바이트와 OCR 원문을 오류 로그에 기록하지 않습니다.
- 문서 전체에 검색 가능한 일반 텍스트 또는 성공 OCR 청크가 남으면 부분 성공이 가능합니다.
- 문서 전체에 검색 가능한 청크가 없으면 인제스트 요청은 실패합니다.

## 9. Source Locator

공통 필드:

| 필드 | 의미 |
|---|---|
| `file_type` | `pdf`, `docx`, `pptx`, `xlsx`, `txt` |
| `kind` | 형식별 대표 위치 종류 |
| `content_origin` | `text` 또는 `ocr` |
| `unit_type` | 문단, 표, 도형, 셀 범위, 줄, OCR 이미지 |
| `structure_path` | 사람이 확인 가능한 결정적 구조 경로 |

OCR 추가 필드:

- `image_ordinal`
- `image_index`
- `image_id`
- `image_kind`
- `ocr_engine`
- `ocr_mean_confidence`

형식별 전체 필드는
[답변·인용·Source Locator 상세 계약](../api/rag-answer-contract.md)을 참조합니다.

## 10. 지원 범위 변경 체크리스트

- [ ] 파서 Factory와 형식 검증이 같은 확장자·MIME 계약을 사용함
- [ ] 일반 텍스트와 OCR 양쪽에 Source Locator가 생성됨
- [ ] 문서별 이미지·바이트·픽셀 제한이 적용됨
- [ ] CUDA GPU 필수 설정에서 CPU 폴백이 차단됨
- [ ] Office COM 미지원 세션의 처리 정책이 명확함
- [ ] 검색·답변 API 문서와 OpenAPI가 함께 갱신됨
- [ ] 고정 Fixture와 실제 E2E가 변경된 형식을 검증함
