# 문서 이미지 추출, Microsoft Office 렌더링 및 CUDA OCR 실행 가이드

이 문서는 Local RAG의 PDF, DOCX, PPTX, XLSX 이미지 추출과 Microsoft Office
2024 COM 렌더링, EasyOCR 실행에 필요한 의존성, 환경 변수, 모델 준비, 실행 확인
및 품질 검사를 설명합니다. RAG는 애플리케이션 서버 및 AWS 실행 환경과 분리되어
Windows 로컬 사용자 세션에서 동작합니다.

## 1. 처리 범위

지원하는 이미지 처리 경로는 다음과 같습니다.

- PDF 임베디드 이미지와 스캔·이미지 전용 페이지 렌더링
- DOCX 인라인 및 플로팅 이미지
- PPTX 일반 이미지, 차트 렌더링 이미지 및 SmartArt 렌더링 이미지
- XLSX 삽입 이미지 및 차트 렌더링 이미지
- 이미지 SHA-256 기반 중복 추출 및 중복 OCR 방지
- OCR 텍스트 정규화, 구조 문맥 연결 및 검색 가능한 문서 청크 변환
- 이미지 또는 OCR 부분 실패 시 기존 텍스트 인제스트 유지

사진이나 다이어그램의 의미를 해석하지는 않습니다. 이미지 내부의 문자만 OCR로
인식하여 검색 근거로 사용합니다.

## 2. 요구 환경

- Windows 10 또는 Windows 11 로컬 로그인 세션
- Python 3.12
- `uv`
- Microsoft PowerPoint 2024
- Microsoft Excel 2024
- NVIDIA GPU와 CUDA 12.9 호환 드라이버
- EasyOCR 한국어·영어 모델 파일

Word는 DOCX의 OOXML 이미지 파트를 직접 읽기 때문에 필요하지 않습니다.
PowerPoint와 Excel은 최초 1회 직접 실행하여 라이선스 동의, 정품 인증 및 첫 실행
안내 창을 완료한 뒤 정상 종료해야 합니다. RAG를 실행하는 것과 같은 Windows 사용자
계정에서 이 절차를 수행합니다.

PyTorch와 torchvision은 `pyproject.toml`의 전용 `pytorch-cu129` 인덱스에서
각각 `2.8.0`, `0.23.0`으로 설치됩니다. CPU 전용 PyTorch가 우연히 선택되지
않도록 해당 인덱스는 `explicit = true`로 제한합니다.

## 3. Python 의존성 설치

RAG 프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
uv sync
```

이 명령은 Windows에서 `pywin32`도 함께 설치하고 변경된 의존성으로 `uv.lock`을
갱신합니다. 갱신된 lock 파일을 커밋한 이후에는 다음 명령으로 동일 환경을 재현합니다.

```powershell
uv sync --frozen
```

주요 이미지·OCR·Office 의존성은 다음과 같습니다.

- `pymupdf`: PDF 이미지 추출과 스캔 페이지 렌더링
- `pillow`: 이미지 크기와 형식 검증
- `numpy`, `opencv-python-headless`: OCR 입력 이미지 디코딩
- `easyocr`: 한국어·영어 문자 인식
- `torch==2.8.0`, `torchvision==0.23.0`: CUDA 12.9 OCR 런타임
- `pywin32`: 설치된 PowerPoint와 Excel의 COM 자동화
- `types-pywin32`, `types-openpyxl`: Mypy strict 검사용 타입 정보

별도의 렌더링 프로그램은 설치하지 않습니다.

## 4. Microsoft Office 2024 준비

PPTX 차트·SmartArt는 PowerPoint의 `Shape.Export`, XLSX 차트는 Excel의
`Chart.Export`를 이용해 대상 객체만 PNG로 직접 출력합니다. 전체 문서를 PDF로
변환하지 않으므로 중간 PDF와 전체 페이지 이미지 생성을 피합니다.

PowerShell에서 PowerPoint와 Excel COM 등록 상태를 확인합니다.

```powershell
$powerPoint = New-Object -ComObject PowerPoint.Application
$powerPoint.Quit()

$excel = New-Object -ComObject Excel.Application
$excel.Quit()
```

두 명령 모두 오류 없이 종료되어야 합니다. Office 앱 창이 열려 있거나 저장 확인
대화상자가 남아 있으면 모두 닫은 뒤 다시 검사합니다.

환경 변수는 다음과 같이 설정합니다.

```dotenv
JIPSA_RAG_OFFICE_RENDERING_ENABLED=true
JIPSA_RAG_OFFICE_RENDERING_PROVIDER=microsoft_office_com
JIPSA_RAG_OFFICE_COM_REQUIRE_INTERACTIVE_SESSION=true
JIPSA_RAG_OFFICE_RENDER_MAX_CONCURRENCY=1
JIPSA_RAG_OFFICE_RENDER_TIMEOUT_SECONDS=120.0
JIPSA_RAG_OFFICE_RENDER_DPI=160
```

Office COM은 한 프로세스에서 직렬 처리합니다. 문서 하나를 열 때 PowerPoint 또는
Excel을 한 번만 실행하고, 해당 문서의 모든 차트·SmartArt를 내보낸 후 문서와 앱을
종료합니다. 이미지마다 Office를 다시 실행하지 않습니다.

Windows 서비스, SYSTEM 계정, Docker 또는 Linux에서는 Office COM 렌더링을
실행하지 않습니다. Office 오류는 부분 실패로 처리하여 기존 텍스트 인제스트를
유지합니다.

## 5. EasyOCR 모델 준비

`.env.local`과 `.env.development`는 최초 로컬 실행에서 모델을 자동으로 받을 수
있도록 다음 값을 사용합니다.

```dotenv
JIPSA_RAG_OCR_MODEL_STORAGE_DIRECTORY=.cache/easyocr
JIPSA_RAG_OCR_MODEL_DOWNLOAD_ENABLED=true
```

첫 OCR 실행 시 EasyOCR가 필요한 한국어·영어 모델을 `.cache/easyocr`에 저장합니다.
이후 실행에서는 같은 파일을 재사용하므로 매번 다운로드하지 않습니다. 모델 파일은
용량이 크고 실행 환경에 종속되므로 Git 저장소에 커밋하지 않습니다.

네트워크가 차단된 환경에서는 모델을 미리 준비한 뒤 다음처럼 다운로드를 금지합니다.

```dotenv
JIPSA_RAG_OCR_MODEL_STORAGE_DIRECTORY=D:/Models/easyocr
JIPSA_RAG_OCR_MODEL_DOWNLOAD_ENABLED=false
```

다운로드 금지 모드에서 지정한 디렉터리가 없으면 OCR 엔진은 명확한 모델 오류를
반환하고, 기존 텍스트 인제스트는 계속 유지합니다.

## 6. CUDA 확인

RAG 가상환경에서 PyTorch CUDA 인식 상태를 확인합니다.

```powershell
uv run python -c "import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

정상적인 CUDA 12.9 환경에서는 `torch.version.cuda`가 `12.9`이고
`torch.cuda.is_available()`이 `True`여야 합니다.

기본 설정은 다음과 같습니다.

```dotenv
JIPSA_RAG_OCR_GPU=true
JIPSA_RAG_OCR_GPU_REQUIRED=true
JIPSA_RAG_OCR_DEVICE=cuda:0
```

`OCR_GPU_REQUIRED=true` 상태에서 CUDA를 사용할 수 없으면 OCR 엔진 초기화가
명시적으로 실패합니다. CPU로 조용히 전환하여 운영 성능 문제를 숨기지 않습니다.

## 7. 자원 제한

이미지 처리에는 다음 제한을 적용합니다.

1. 문서당 이미지 개수
2. 단일 이미지와 문서 전체 이미지 바이트
3. 이미지 디코딩 후 최대 픽셀 수
4. Office 문서 렌더링 시간
5. 단일 이미지 및 문서 전체 OCR 처리 시간

```dotenv
JIPSA_RAG_IMAGE_MAX_COUNT_PER_DOCUMENT=300
JIPSA_RAG_IMAGE_MAX_BYTES=26214400
JIPSA_RAG_IMAGE_MAX_TOTAL_BYTES=268435456
JIPSA_RAG_IMAGE_MAX_PIXELS=40000000
JIPSA_RAG_OFFICE_RENDER_MAX_CONCURRENCY=1
JIPSA_RAG_OFFICE_RENDER_TIMEOUT_SECONDS=120.0
JIPSA_RAG_OCR_MAX_CONCURRENCY=2
JIPSA_RAG_OCR_TIMEOUT_SECONDS=45.0
JIPSA_RAG_OCR_DOCUMENT_TIMEOUT_SECONDS=600.0
```

GPU VRAM이 부족하면 먼저 `JIPSA_RAG_OCR_MAX_CONCURRENCY`를 `1`로 낮춥니다.
Office COM 동시성은 항상 `1`로 유지합니다.

## 8. 로컬 RAG 실행

환경 변수와 OCR 모델 정책을 `.env.local`에 설정한 뒤 실행합니다.

```powershell
uv run uvicorn jipsa_rag.main:app --host 0.0.0.0 --port 8077
```

EasyOCR Reader는 첫 OCR 대상 이미지가 처리될 때 한 번 생성하고 이후 요청에서
재사용합니다. PowerPoint와 Excel은 렌더링 대상 문서가 있을 때만 실행합니다.

## 9. 코드 품질 및 일반 테스트

RAG 프로젝트 루트에서 다음 순서로 검사합니다.

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

기본 전체 Pytest에서는 실제 Microsoft Office와 외부 서비스가 필요한 테스트가
명시적으로 skip될 수 있습니다. skip은 성공으로 위장하지 않고 결과에 표시됩니다.

## 10. 실제 Microsoft Office COM 통합 테스트

PowerPoint와 Excel이 설치되고 초기 설정이 끝난 Windows 로컬 세션에서만 다음
통합 테스트를 활성화합니다.

```powershell
$env:JIPSA_RAG_RUN_OFFICE_COM_INTEGRATION=1
uv run pytest -ra tests/integration/test_document_image_extractors.py
```

테스트는 `PowerPoint.Application`과 `Excel.Application`을 실제로 실행하여
PPTX 차트와 XLSX 차트를 PNG로 내보냅니다. opt-in 상태에서 Office COM을 사용할 수
없으면 skip이 아니라 실패로 처리합니다.

## 11. 로그와 보안

이미지 추출, Office 렌더링 및 OCR 오류 로그에는 다음 값을 기록하지 않습니다.

- 이미지 원본 바이트 또는 Base64
- OCR로 인식한 원문
- 다운로드 원본 파일 경로
- 임시 이미지와 렌더링 파일 경로
- EasyOCR 모델 경로와 파일 내용
- Office COM 예외의 원문 메시지

운영 진단에는 문서 형식, 이미지 종류, 오류 클래스, timeout 및 처리 개수처럼
원문을 복원할 수 없는 메타데이터만 사용합니다.
