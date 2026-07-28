"""격리된 OCR worker 안에서 사용하는 CUDA EasyOCR 동기 런타임을 제공한다.

이 모듈의 ``EasyOcrRuntime``은 FastAPI 부모 프로세스에서 직접 추론하지 않는다.
각 OCR worker process가 자신만의 Runtime을 하나 만들고, 첫 추론에서 EasyOCR Reader와
CUDA 모델을 지연 초기화한 뒤 해당 worker가 살아 있는 동안 계속 재사용한다.

프로세스 생성, timeout, 취소 및 worker 교체는 ``process_manager.py``의
``EasyOcrEngine``이 담당한다. 이 분리를 통해 Python thread에서는 안전하게 중단할 수
없는 ``Reader.readtext()``를 부모 요청 취소와 분리하고, timeout 시 worker process를
종료하여 실제 CUDA 작업까지 회수할 수 있다.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.ocr.exceptions import (
    OcrDependencyUnavailableError,
    OcrGpuUnavailableError,
    OcrImageDecodeError,
    OcrModelUnavailableError,
    OcrRecognitionError,
)
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult, OcrTextLine

_EASYOCR_ENGINE_NAME: Final[str] = "EASYOCR_CUDA"


class EasyOcrRuntime:
    """단일 OCR worker process 안에서 EasyOCR Reader를 한 번 생성해 재사용한다.

    Reader 초기화는 모델 파일 로딩과 CUDA 메모리 할당을 포함한다. 동일 worker 안에서
    여러 이미지가 연속으로 처리되더라도 ``_reader``를 재사용하므로 요청마다 모델을
    다시 올리지 않는다. 부모 프로세스가 설정한 worker 개수가 애플리케이션 전체 모델
    복제 수의 상한이 된다.

    ``threading.Lock``은 한 worker 내부에서 동시에 들어온 초기화 시도를 방어한다.
    현재 worker는 요청을 직렬 처리하지만, 이 잠금은 향후 내부 구조 변경 시에도 Reader
    중복 생성을 막는 마지막 방어선으로 유지한다.
    """

    def __init__(self, settings: DocumentProcessingSettings) -> None:
        self._settings = settings
        self._reader: Any | None = None
        self._reader_lock = threading.Lock()

    @property
    def engine_name(self) -> str:
        """저장 메타데이터에 사용할 안정적인 EasyOCR 엔진 이름을 반환한다."""

        return _EASYOCR_ENGINE_NAME

    def recognize_content(self, content: bytes) -> OcrRecognitionResult:
        """이미지 바이트를 디코딩하고 EasyOCR 결과를 불변 내부 모델로 변환한다.

        원본 이미지 경로나 OCR 원문은 로그에 남기지 않는다. 이 메서드에서 발생하는
        예외는 부모 프로세스에 예외 객체 자체로 전달하지 않고, worker IPC 경계에서
        안전한 예외 종류 문자열로 변환된다.
        """

        reader = self._get_or_create_reader()
        decoded_image = self._decode_image(content)

        try:
            raw_result = reader.readtext(
                decoded_image,
                detail=1,
                paragraph=False,
                batch_size=1,
                workers=0,
            )
        except Exception as error:
            raise OcrRecognitionError("EasyOCR recognition failed.") from error

        lines: list[OcrTextLine] = []
        for order, item in enumerate(raw_result):
            if not isinstance(item, list | tuple) or len(item) < 3:
                continue
            text = item[1]
            confidence = item[2]
            if not isinstance(text, str):
                continue
            try:
                normalized_confidence = float(confidence)
            except (TypeError, ValueError):
                normalized_confidence = 0.0
            lines.append(
                OcrTextLine(
                    text=text,
                    confidence=max(0.0, min(normalized_confidence, 1.0)),
                    order=order,
                )
            )

        return OcrRecognitionResult(
            lines=tuple(lines),
            engine_name=self.engine_name,
            languages=self._settings.ocr_languages,
            device=self._settings.ocr_device if self._settings.ocr_gpu else "cpu",
        )

    def _get_or_create_reader(self) -> Any:
        """현재 worker process 전용 EasyOCR Reader를 지연 생성한다."""

        if self._reader is not None:
            return self._reader

        with self._reader_lock:
            if self._reader is not None:
                return self._reader

            easyocr = self._import_dependency("easyocr")
            torch = self._import_dependency("torch")

            cuda_available = bool(torch.cuda.is_available())
            if self._settings.ocr_gpu_required and not cuda_available:
                raise OcrGpuUnavailableError(
                    "CUDA OCR is required, but torch.cuda.is_available() is false."
                )

            gpu_argument: bool | str
            if self._settings.ocr_gpu and cuda_available:
                gpu_argument = self._settings.ocr_device
            else:
                gpu_argument = False

            model_storage_directory = self._prepare_model_storage_directory()
            try:
                self._reader = easyocr.Reader(
                    list(self._settings.ocr_languages),
                    gpu=gpu_argument,
                    model_storage_directory=(
                        str(model_storage_directory)
                        if model_storage_directory is not None
                        else None
                    ),
                    download_enabled=self._settings.ocr_model_download_enabled,
                    verbose=False,
                )
            except FileNotFoundError as error:
                raise OcrModelUnavailableError(
                    "EasyOCR model files are unavailable in offline mode."
                ) from error
            except Exception as error:
                message = str(error).lower()
                if "download" in message or "model" in message:
                    raise OcrModelUnavailableError(
                        "EasyOCR model initialization failed."
                    ) from error
                raise OcrRecognitionError("EasyOCR Reader initialization failed.") from error

            return self._reader

    def _prepare_model_storage_directory(self) -> Path | None:
        """EasyOCR 모델 디렉터리를 다운로드 정책에 맞게 준비한다.

        로컬 최초 실행에서 자동 다운로드가 활성화되어 있으면 디렉터리를 안전하게
        생성한다. 오프라인 모드에서는 존재하지 않는 경로를 Reader에 넘기지 않고
        명확한 모델 오류로 변환한다. 모델 경로는 로그에 기록하지 않는다.
        """

        directory = self._settings.ocr_model_storage_directory
        if directory is None:
            return None
        if directory.is_dir():
            return directory
        if not self._settings.ocr_model_download_enabled:
            raise OcrModelUnavailableError(
                "EasyOCR model directory is unavailable in offline mode."
            )
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OcrModelUnavailableError(
                "EasyOCR model directory could not be created."
            ) from error
        return directory

    @staticmethod
    def _decode_image(content: bytes) -> Any:
        """OpenCV로 이미지 바이트를 EasyOCR 입력 배열로 디코딩한다."""

        try:
            cv2 = importlib.import_module("cv2")
            numpy = importlib.import_module("numpy")
        except ImportError as error:
            raise OcrDependencyUnavailableError(
                "OpenCV and NumPy are required for OCR image decoding."
            ) from error

        array = numpy.frombuffer(content, dtype=numpy.uint8)
        decoded = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if decoded is None:
            raise OcrImageDecodeError("The extracted image could not be decoded.")
        return decoded

    @staticmethod
    def _import_dependency(module_name: str) -> Any:
        """선택 OCR 의존성을 실제 추론 시점에 가져온다."""

        try:
            return importlib.import_module(module_name)
        except ImportError as error:
            raise OcrDependencyUnavailableError(
                f"The OCR dependency '{module_name}' is unavailable."
            ) from error


if TYPE_CHECKING:
    # 기존 직접 import 경로의 정적 타입 호환성을 유지한다. 런타임 import는 아래
    # ``__getattr__``에서 지연 수행하여 process_manager -> easyocr 순환 import를 막는다.
    from jipsa_rag.infrastructure.ocr.process_manager import EasyOcrEngine as EasyOcrEngine


def __getattr__(name: str) -> object:
    """기존 ``ocr.easyocr.EasyOcrEngine`` import를 지연 호환한다.

    public process 엔진은 ``process_manager.py``로 이동했지만, 이전 테스트나 내부 코드가
    기존 모듈 경로를 사용해도 즉시 깨지지 않도록 모듈 attribute를 지연 반환한다.
    """

    if name == "EasyOcrEngine":
        from jipsa_rag.infrastructure.ocr.process_manager import EasyOcrEngine

        return EasyOcrEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
