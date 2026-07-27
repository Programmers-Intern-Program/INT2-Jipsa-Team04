# Issue #123 고정 E2E 문서 Fixture

이 디렉터리는 Local RAG 문서 입력 경계와 전체 색인 파이프라인의 결정적 회귀 테스트에
사용합니다. 테스트 실행 중 문서를 생성하지 않고 저장소에 커밋된 실제 바이너리를 그대로
사용합니다.

- `manifest.json`: 파일 SHA-256, 크기, 원문 구조, 이미지 위치 및 실패 단계 계약
- `pipeline_expectations.json`: 실제 OCR, CUDA TEI, Local RAG DB와 Qdrant 검증 계약
- `answer_expectations.json`: lookup·synthesis·혼합 OCR 답변과 형식별 출처 위치 계약

## 디렉터리 구성

```text
valid/text/
  pdf_text_table.pdf
  docx_structure.docx
  pptx_structure.pptx
  xlsx_structure.xlsx
  txt_lines_utf8.txt

valid/images/
  pdf_with_image.pdf
  docx_with_image.docx
  pptx_with_image.pptx
  xlsx_with_image.xlsx
  pdf_partial_ocr.pdf

valid/scanned/
  scanned_document.pdf
  hybrid_image_only_page.pdf

invalid/
  corrupted.pdf
  empty.pdf
  encrypted.pdf
  docx_payload_named_pdf.pdf
```

## 고정 계약

- `valid/text`: PDF 페이지 텍스트와 표 셀, DOCX 문단·표, PPTX 슬라이드·도형·표,
  XLSX 시트·행·셀 범위, TXT 인코딩·줄·문자 범위를 검증합니다.
- `valid/images`: PDF, DOCX, PPTX, XLSX의 실제 삽입 이미지와 문서 내부 위치를 검증합니다.
- `pdf_partial_ocr.pdf`: 서로 다른 이미지 두 개를 포함하며, 두 번째 이미지 OCR만 강제로
  실패시켜 첫 번째 이미지의 실제 EasyOCR·TEI·DB·Qdrant 색인이 유지되는지 검증합니다.
- `valid/scanned`: 텍스트 레이어가 없는 스캔 PDF와 혼합 PDF의 이미지 전용 페이지를
  검증합니다.
- `invalid`: 빈 파일, 손상 PDF, 암호화 PDF, 확장자·실제 내용 불일치를 검증합니다.

## 테스트 계층

### 결정적 입력 계약

`tests/e2e/test_fixed_document_fixture_contract_e2e.py`는 외부 인프라 없이 다음 항목을
항상 검증합니다.

- 파일 바이트와 SHA-256
- MIME Type, Magic Byte와 OOXML 루트
- 형식별 텍스트·표·위치 메타데이터
- 삽입 이미지와 스캔 페이지 탐지
- 손상·빈·암호화·잘못된 확장자 문서 방어

### 실제 Local RAG 전체 파이프라인

`tests/e2e/test_fixed_document_full_pipeline_e2e.py`는
`JIPSA_RAG_RUN_E2E=1`에서 다음 실제 구성요소를 사용합니다.

- CUDA EasyOCR 인식 결과와 원본 이미지 위치
- OCR 일부 실패 시 나머지 이미지 부분 성공
- CUDA TEI 문서 및 질의 임베딩
- Local RAG DB 문서·청크·색인 실행 이력
- Qdrant Point, vector, payload, 활성 상태와 사용자·문서 범위

AWS Backend manifest·완료 callback과 Presigned GET URL의 HTTP 경계만 결정적인
`MockTransport`로 대체합니다. AWS 자격 증명이나 Backend DB는 사용하지 않습니다.

### 실제 Claude 답변과 출처 위치

같은 `test_fixed_document_full_pipeline_e2e.py`는 고정 문서 색인이 완료된 뒤 실제
`/api/v1/rag/answers`를 호출하여 다음 계약을 추가로 검증합니다.

- PDF, DOCX, PPTX, XLSX, TXT 형식별 단일 문서 lookup 답변
- 다섯 형식을 함께 선택한 문서별 부분 생성 및 최종 synthesis 답변
- 한 혼합 PDF의 텍스트 페이지와 이미지 전용 페이지 OCR 청크를 함께 사용한 답변
- 답변 본문의 `[SOURCE-N]`, `cited_source_ids`, `sources` 최초 인용 순서 일치
- PDF 페이지, DOCX 문단·표, PPTX 슬라이드·도형, XLSX 시트·셀 범위,
  TXT 줄·문자 범위의 `source_locator`
- PDF, DOCX, PPTX, XLSX OCR 출처의 이미지 순번, 이미지 ID, 종류, OCR 엔진과
  원본 문서 위치
- 최종 `sources.chunk_id`와 Local RAG DB 원본 청크의 연결 및 선택 문서 범위

각 답변 시나리오는 실제 Claude 비용을 중복 발생시키지 않도록 모듈 범위에서 한 번만
호출하고, 형식별 위치와 인용 일치 테스트가 같은 불변 응답을 공유합니다.

## 유지보수 규칙

1. 기존 Fixture를 Office나 PDF 편집기로 열어 다시 저장하지 않습니다.
2. 원문, 구조 또는 바이너리를 변경하면 `manifest.json`,
   `pipeline_expectations.json`과 `answer_expectations.json`의 SHA-256·토큰·
   위치 예상값을 함께 갱신합니다.
3. 위치 메타데이터 계약이 바뀌면 파서 버전과 기존 색인 호환성도 함께 검토합니다.
4. OCR 문구는 ASCII 대문자·숫자·하이픈으로 유지하여 GPU와 OCR 모델 버전 간 오차를
   줄입니다.
5. 실제 E2E는 전용 사용자·파일 ID 범위만 정리하며 반드시
   `JIPSA_RAG_APP_ENV=test`에서 실행합니다.
6. Presigned URL, 인증 토큰, OCR 원문 전체, 임시 파일 경로와 임베딩 벡터는 로그에
   남기지 않습니다.
