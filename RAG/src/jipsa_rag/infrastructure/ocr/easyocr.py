"""CUDA EasyOCR 기반 OCR 엔진 구현체를 제공한다."""

from __future__ import annotations

import asyncio
import importlib
import threading
from pathlib import Path
from typing import Any, Final

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.document.images.models import ExtractedDocumentImage
from jipsa_rag.infrastructure.ocr.exceptions import (
    OcrDependencyUnavailableError,
    OcrGpuUnavailableError,
    OcrImageDecodeError,
    OcrModelUnavailableError,
    OcrRecognitionError,
)
from jipsa_rag.infrastructure.ocr.models import OcrRecognitionResult, OcrTextLine

_EASYOCR_ENGINE_NAME: Final[str] = "EASYOCR_CUDA"


class EasyOcrEngine:
    """EasyOCR Reader를 최초 추론 시 한 번만 생성하여 재사용한다.

    Reader 초기화는 모델 로딩과 CUDA 메모리 할당을 포함하므로 요청마다 생성하지
    않는다. ``threading.Lock``은 동시에 들어온 첫 요청들이 Reader를 중복 생성하는
    상황을 막는다. 실제 ``readtext`` 호출은 OCR 보강 서비스의 asyncio Semaphore로
    제한하며, 동기 추론 자체는 ``asyncio.to_thread``에서 실행한다.
    """

    def __init__(self, settings: DocumentProcessingSettings) -> None:
        self._settings = settings
        self._reader: Any | None = None
        self._reader_lock = threading.Lock()

    @property
    def engine_name(self) -> str:
        return _EASYOCR_ENGINE_NAME

    async def recognize(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        return await asyncio.to_thread(self._recognize_sync, image)

    def _recognize_sync(
        self,
        image: ExtractedDocumentImage,
    ) -> OcrRecognitionResult:
        reader = self._get_or_create_reader()
        decoded_image = self._decode_image(image.content)

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
        try:
            return importlib.import_module(module_name)
        except ImportError as error:
            raise OcrDependencyUnavailableError(
                f"The OCR dependency '{module_name}' is unavailable."
            ) from error
