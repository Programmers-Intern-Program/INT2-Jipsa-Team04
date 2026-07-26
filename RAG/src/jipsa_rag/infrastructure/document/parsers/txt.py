"""TXT 문서 파서는 현재 PDF 전용 정책에 따라 제공하지 않는다.

이 모듈에는 의도적으로 ``TxtDocumentParser`` 클래스를 정의하지 않는다.
TXT 지원을 다시 추가할 때는 다음 항목을 하나의 변경 단위로 함께 구현해야 한다.

- TXT 파서와 인코딩·빈 문서 예외 처리
- ``SupportedFileType`` 요청 계약
- ``DocumentParserFactory`` 기본 등록
- Local RAG DB 및 Qdrant source metadata 계약
- 단위·통합·E2E 회귀 테스트
"""
