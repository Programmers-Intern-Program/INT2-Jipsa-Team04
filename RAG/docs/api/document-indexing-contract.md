# 다중 형식 문서 인제스트 및 색인 계약

## 1. 대상 API

### `POST /api/v1/files/process`

애플리케이션 서버가 전달한 Presigned GET URL에서 문서를 내려받고 파싱, 청킹,
임베딩, Local RAG DB 저장과 Qdrant 색인을 완료합니다.

지원 `file_type` 값:

```text
pdf
docx
pptx
txt
xlsx
```

요청 스키마의 `file_name` 최종 확장자와 `file_type`은 일치해야 합니다.
다운로드 응답 MIME Type, Magic Byte와 OOXML 내부 루트도 같은 형식이어야 합니다.

## 2. 하위 호환성

- 기존 PDF 파서의 `page_number`를 유지합니다.
- 기존 Content Hash와 UUIDv5 Chunk ID 생성 입력은 변경하지 않습니다.
- API 응답의 `page_count` 필드명은 유지합니다.
  - PDF: 페이지 unit 수
  - DOCX/PPTX/TXT/XLSX: 파서가 생성한 원본 구조 unit 수
- 기본 Index Version은 `2`입니다.

## 3. 공통 청크 위치 필드

모든 청크는 다음 필드를 가집니다.

```json
{
  "chunk_id": "UUIDv5",
  "chunk_index": 0,
  "content_hash": "SHA-256 hex",
  "start_offset": 0,
  "end_offset": 123,
  "source_metadata": {
    "source_unit_index": 0,
    "unit_start_offset": 0,
    "unit_end_offset": 123,
    "chunking_strategy": "STRUCTURED_DOCUMENT",
    "chunking_strategy_version": "1.0.0",
    "location_kind": "format-specific",
    "structure_path": "format-specific"
  }
}
```

offset의 end 값은 exclusive입니다.

## 4. 형식별 source metadata

### PDF

```json
{
  "page_number": 1,
  "location_kind": "pdf_page",
  "structure_path": "page:1"
}
```

### DOCX

```json
{
  "section_index": 1,
  "block_index": 3,
  "paragraph_index": 2,
  "unit_type": "paragraph",
  "section_title": "최근 제목",
  "location_kind": "docx_block",
  "structure_path": "section:1/block:3"
}
```

표 unit은 `table_index`, `row_count`, `column_count`를 사용합니다.

### PPTX

```json
{
  "slide_number": 2,
  "shape_index": 3,
  "shape_id": 7,
  "shape_path": "3.1",
  "shape_left_emu": 914400,
  "shape_top_emu": 457200,
  "shape_width_emu": 3657600,
  "shape_height_emu": 914400,
  "coordinate_space": "group",
  "location_kind": "pptx_shape",
  "structure_path": "slide:2/shape:3.1"
}
```

발표자 노트는 `unit_type=speaker_notes`이며 도형 좌표가 없습니다.

### XLSX

```json
{
  "sheet_number": 1,
  "sheet_name": "Summary",
  "row_number": 5,
  "start_cell": "B5",
  "end_cell": "F5",
  "cell_range": "B5:F5",
  "cell_coordinates": ["B5", "C5", "F5"],
  "merged_cell_ranges": ["B5:C5"],
  "location_kind": "xlsx_cell_range",
  "structure_path": "sheet:Summary/range:B5:F5"
}
```

### TXT

```json
{
  "line_number": 10,
  "source_char_start": 120,
  "source_char_end": 147,
  "text_char_start": 120,
  "text_char_end": 145,
  "has_line_break": true,
  "location_kind": "txt_line",
  "structure_path": "line:10"
}
```

긴 한 줄이 여러 청크로 분할되면 `chunk_source_char_start`와
`chunk_source_char_end`가 실제 청크 문자 범위를 나타냅니다.

## 5. Local RAG DB 계약

`RAG_Document`의 식별 기준:

```text
File_IDX + File_Hash + Parser_Version + Embedding_Model + Index_Version
```

`RAG_Chunk.Source_Metadata` JSON에는 전체 source metadata를 저장합니다. 페이지,
슬라이드, 시트와 섹션 제목은 기존 전용 컬럼에도 투영합니다.

파서 버전이 달라 기존 INDEXED 문서가 존재하면 새 실행의 `Run_Type`은
`REINDEX`입니다. 새 Qdrant 색인이 성공하기 전에는 기존 문서와 Point를 제거하지
않습니다.

## 6. Qdrant payload 계약

전체 위치 객체:

```json
{
  "source_metadata": {}
}
```

필터·운영 확인용 top-level 투영:

```text
parser_type
parser_version
unit_type
location_kind
page
slide_no
sheet_name
sheet_number
cell_range
line_number
shape_path
section_title
chunking_strategy
chunking_strategy_version
```

사용자·참조 파일·활성 상태 필터는 기존과 동일하게 강제합니다. 형식별 위치
필드는 payload에 저장하되 기존 Collection의 payload index 집합은 변경하지 않습니다.
위치 기반 필터 인덱스가 필요해질 때는 Collection 마이그레이션과 함께 추가합니다.

## 7. 오류 계약

- 빈 문서 또는 공백만 있는 문서: `DOCUMENT_TEXT_NOT_FOUND`
- 암호화 Office 문서/ZIP 엔트리: `ENCRYPTED_DOCUMENT`
- 손상 문서, OOXML 형식 불일치, 압축 안전 정책 위반: `INVALID_DOCUMENT`
- 특정 원본 위치 추출 실패: `DOCUMENT_TEXT_EXTRACTION_FAILED`
- 청크 없음 또는 청킹 설정 오류: 청킹 오류 코드
- Local RAG DB 또는 Qdrant 저장 실패: 기존 저장 오류 코드

오류 응답과 로그에는 Presigned URL, 임시 파일 경로, 원문, 청크 내용과 임베딩
벡터를 포함하지 않습니다.
