"""worker 내부 EasyOCR Runtime의 CUDA 필수 정책과 초기화 인자를 검증한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jipsa_rag.core.document_processing import DocumentProcessingSettings
from jipsa_rag.infrastructure.ocr.easyocr import EasyOcrRuntime
from jipsa_rag.infrastructure.ocr.exceptions import (
    OcrGpuUnavailableError,
    OcrModelUnavailableError,
)


class _FakeCuda:
    def __init__(self, available: bool) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available


class _FakeTorch:
    def __init__(self, available: bool) -> None:
        self.cuda = _FakeCuda(available)


class _FakeEasyOcr:
    def __init__(self) -> None:
        self.reader_arguments: tuple[list[str], dict[str, Any]] | None = None

    def Reader(self, languages: list[str], **kwargs: Any) -> object:
        """EasyOCR의 대문자 Reader 팩터리 이름을 그대로 모사한다."""

        self.reader_arguments = (languages, kwargs)
        return object()


def test_reader_initialization_rejects_missing_cuda_when_gpu_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU 필수 설정에서는 torch CUDA 미탐지를 CPU 폴백으로 숨기지 않는다."""

    runtime = EasyOcrRuntime(
        DocumentProcessingSettings(
            ocr_gpu=True,
            ocr_gpu_required=True,
            _env_file=None,
        )
    )
    fake_easyocr = _FakeEasyOcr()

    monkeypatch.setattr(
        runtime,
        "_import_dependency",
        lambda module_name: fake_easyocr if module_name == "easyocr" else _FakeTorch(False),
    )

    with pytest.raises(OcrGpuUnavailableError, match="CUDA OCR is required"):
        runtime._get_or_create_reader()


def test_reader_initialization_uses_configured_cuda_device_and_download_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CUDA 장치, 언어, 모델 경로 및 다운로드 설정을 Reader에 전달한다."""

    model_directory = tmp_path / "easyocr-models"
    settings = DocumentProcessingSettings(
        ocr_languages_csv="ko,en",
        ocr_gpu=True,
        ocr_gpu_required=True,
        ocr_device="cuda:0",
        ocr_model_storage_directory=model_directory,
        ocr_model_download_enabled=True,
        _env_file=None,
    )
    runtime = EasyOcrRuntime(settings)
    fake_easyocr = _FakeEasyOcr()

    monkeypatch.setattr(
        runtime,
        "_import_dependency",
        lambda module_name: fake_easyocr if module_name == "easyocr" else _FakeTorch(True),
    )

    reader = runtime._get_or_create_reader()

    assert reader is not None
    assert model_directory.is_dir()
    assert fake_easyocr.reader_arguments is not None
    languages, arguments = fake_easyocr.reader_arguments
    assert languages == ["ko", "en"]
    assert arguments["gpu"] == "cuda:0"
    assert Path(arguments["model_storage_directory"]) == model_directory
    assert arguments["download_enabled"] is True
    assert arguments["verbose"] is False


def test_reader_initialization_reuses_same_reader_inside_one_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """같은 worker Runtime은 Reader 팩터리를 한 번만 호출하고 객체를 재사용한다."""

    runtime = EasyOcrRuntime(
        DocumentProcessingSettings(
            ocr_gpu=True,
            ocr_gpu_required=True,
            _env_file=None,
        )
    )
    fake_easyocr = _FakeEasyOcr()
    reader_call_count = 0
    original_reader_factory = fake_easyocr.Reader

    def build_reader(languages: list[str], **kwargs: Any) -> object:
        nonlocal reader_call_count
        reader_call_count += 1
        return original_reader_factory(languages, **kwargs)

    monkeypatch.setattr(fake_easyocr, "Reader", build_reader)
    monkeypatch.setattr(
        runtime,
        "_import_dependency",
        lambda module_name: fake_easyocr if module_name == "easyocr" else _FakeTorch(True),
    )

    first_reader = runtime._get_or_create_reader()
    second_reader = runtime._get_or_create_reader()

    assert first_reader is second_reader
    assert reader_call_count == 1


def test_reader_initialization_rejects_missing_offline_model_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """다운로드 금지 모드에서는 존재하지 않는 모델 디렉터리를 즉시 거부한다."""

    model_directory = tmp_path / "missing-easyocr-models"
    runtime = EasyOcrRuntime(
        DocumentProcessingSettings(
            ocr_gpu=True,
            ocr_gpu_required=True,
            ocr_model_storage_directory=model_directory,
            ocr_model_download_enabled=False,
            _env_file=None,
        )
    )
    fake_easyocr = _FakeEasyOcr()
    monkeypatch.setattr(
        runtime,
        "_import_dependency",
        lambda module_name: fake_easyocr if module_name == "easyocr" else _FakeTorch(True),
    )

    with pytest.raises(OcrModelUnavailableError, match="offline mode"):
        runtime._get_or_create_reader()

    assert fake_easyocr.reader_arguments is None
