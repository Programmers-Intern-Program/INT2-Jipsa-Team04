"""이미지 추출, Microsoft Office 렌더링 및 CUDA OCR 설정 계약을 검증한다."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from jipsa_rag.core.document_processing import DocumentProcessingSettings


def test_default_ocr_policy_requires_cuda_and_disables_model_download() -> None:
    """코드 기본값은 CUDA 필수와 명시적 모델 준비 정책을 유지한다."""

    settings = DocumentProcessingSettings(_env_file=None)

    assert settings.ocr_enabled
    assert settings.ocr_gpu
    assert settings.ocr_gpu_required
    assert settings.ocr_device == "cuda:0"
    assert settings.ocr_languages == ("ko", "en")
    assert not settings.ocr_model_download_enabled


def test_default_office_policy_uses_serial_microsoft_com_rendering() -> None:
    """Office 시각 요소는 Windows COM 공급자로 한 문서씩 직렬 처리한다."""

    settings = DocumentProcessingSettings(_env_file=None)

    assert settings.office_rendering_enabled
    assert settings.office_rendering_provider == "microsoft_office_com"
    assert settings.office_com_require_interactive_session
    assert settings.office_render_max_concurrency == 1


def test_ocr_and_office_environment_variables_are_loaded_with_rag_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """JIPSA_RAG_ 접두사의 OCR 및 Office 설정을 타입 변환하여 읽는다."""

    model_directory = tmp_path / "easyocr-models"
    monkeypatch.setenv("JIPSA_RAG_OCR_LANGUAGES_CSV", "ko, en,ko")
    monkeypatch.setenv("JIPSA_RAG_OCR_MODEL_STORAGE_DIRECTORY", str(model_directory))
    monkeypatch.setenv("JIPSA_RAG_OCR_MODEL_DOWNLOAD_ENABLED", "true")
    monkeypatch.setenv("JIPSA_RAG_OCR_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("JIPSA_RAG_OCR_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("JIPSA_RAG_OCR_DOCUMENT_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv(
        "JIPSA_RAG_OFFICE_RENDERING_PROVIDER",
        "microsoft_office_com",
    )
    monkeypatch.setenv("JIPSA_RAG_OFFICE_RENDER_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("JIPSA_RAG_OFFICE_RENDER_DPI", "180")
    monkeypatch.setenv("JIPSA_RAG_IMAGE_HASH_DEDUP_ENABLED", "false")

    settings = DocumentProcessingSettings(_env_file=None)

    assert settings.ocr_languages == ("ko", "en")
    assert settings.ocr_model_storage_directory == model_directory
    assert settings.ocr_model_download_enabled
    assert settings.ocr_max_concurrency == 1
    assert settings.ocr_timeout_seconds == 30.0
    assert settings.ocr_document_timeout_seconds == 300.0
    assert settings.office_rendering_provider == "microsoft_office_com"
    assert settings.office_render_max_concurrency == 1
    assert settings.office_render_dpi == 180
    assert not settings.image_hash_dedup_enabled


def test_gpu_required_policy_rejects_disabled_gpu() -> None:
    """CUDA 필수 설정과 GPU 비활성화가 동시에 입력되는 오류를 거부한다."""

    with pytest.raises(ValidationError, match="ocr_gpu"):
        DocumentProcessingSettings(
            ocr_gpu=False,
            ocr_gpu_required=True,
            _env_file=None,
        )


def test_single_image_timeout_must_not_exceed_document_timeout() -> None:
    """문서 전체 제한보다 긴 단일 이미지 timeout을 허용하지 않는다."""

    with pytest.raises(ValidationError, match="단일 OCR 제한 시간"):
        DocumentProcessingSettings(
            ocr_timeout_seconds=301.0,
            ocr_document_timeout_seconds=300.0,
            _env_file=None,
        )


def test_image_byte_limits_preserve_single_to_total_relationship() -> None:
    """단일 이미지 상한은 문서 전체 이미지 상한을 초과할 수 없다."""

    with pytest.raises(ValidationError, match="단일 이미지 한계"):
        DocumentProcessingSettings(
            image_max_bytes=2 * 1024 * 1024,
            image_max_total_bytes=1024 * 1024,
            _env_file=None,
        )
