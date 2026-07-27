# Issue #123 고정 E2E 문서 Fixture

이 디렉터리는 Local RAG 문서 입력 경계의 결정적 회귀 테스트에 사용합니다.
테스트 실행 중 문서를 생성하지 않고 저장소에 커밋된 실제 바이너리를 그대로 사용합니다.
각 파일의 SHA-256, 크기, 원문 토큰, 원본 위치 및 예상 실패 단계는 `manifest.json`에 고정합니다.

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

- `valid/text`: PDF 페이지, DOCX 문단·표, PPTX 슬라이드·도형·표,
  XLSX 시트·행·셀 범위, TXT 줄·문자 범위를 검증합니다.
- `valid/images`: PDF, DOCX, PPTX, XLSX의 실제 삽입 이미지와 문서 내부 위치를 검증합니다.
- `valid/scanned`: 텍스트 레이어가 없는 스캔 PDF와 혼합 PDF의 이미지 전용 페이지를 검증합니다.
- `invalid`: 빈 파일, 손상 PDF, 암호화 PDF, 확장자·실제 내용 불일치를 검증합니다.

## 유지보수 규칙

1. 기존 Fixture를 Office나 PDF 편집기로 열어 다시 저장하지 않습니다.
2. 원문, 구조 또는 바이너리를 변경하면 `manifest.json`의 SHA-256과 예상값을 함께 갱신합니다.
3. 위치 메타데이터 계약이 바뀌면 파서 버전 및 기존 색인 호환성도 함께 검토합니다.
4. OCR 문구는 ASCII 대문자·숫자·하이픈으로 유지하여 GPU 및 OCR 모델 버전 간 오차를 줄입니다.
5. 실제 Claude, CUDA TEI, Local RAG DB와 Qdrant를 사용하는 비용성 E2E는 기존 실행 스크립트로
   별도 수행하며, 이 Fixture 계약 테스트는 일반 Pytest에서 항상 실행 가능해야 합니다.
