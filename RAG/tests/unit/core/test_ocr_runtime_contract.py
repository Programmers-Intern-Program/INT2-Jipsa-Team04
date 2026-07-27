"""OCR·Microsoft Office 의존성, 환경 변수 및 실행 문서 계약을 검증한다."""

import tomllib
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def test_pyproject_declares_cuda_129_ocr_and_office_dependencies() -> None:
    """CUDA OCR과 Windows Office COM 패키지를 재현 가능한 버전으로 선언한다."""

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    dependencies = set(pyproject["project"]["dependencies"])
    assert "easyocr>=1.7.2,<2.0" in dependencies
    assert "torch==2.8.0" in dependencies
    assert "torchvision==0.23.0" in dependencies
    assert "pywin32==312; sys_platform == 'win32'" in dependencies

    development_dependencies = set(pyproject["dependency-groups"]["dev"])
    assert "types-openpyxl==3.1.5.20260518" in development_dependencies
    assert "types-pywin32==312.0.0.20260609; sys_platform == 'win32'" in development_dependencies

    uv_sources = pyproject["tool"]["uv"]["sources"]
    assert uv_sources["torch"] == {"index": "pytorch-cu129"}
    assert uv_sources["torchvision"] == {"index": "pytorch-cu129"}

    pytorch_index = next(
        index for index in pyproject["tool"]["uv"]["index"] if index["name"] == "pytorch-cu129"
    )
    assert pytorch_index["url"] == "https://download.pytorch.org/whl/cu129"
    assert pytorch_index["explicit"] is True


def test_env_example_documents_ocr_and_microsoft_office_settings() -> None:
    """실행에 필요한 OCR·이미지·Microsoft Office 환경 변수를 예시에 공개한다."""

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    required_keys = {
        "JIPSA_RAG_IMAGE_EXTRACTION_ENABLED",
        "JIPSA_RAG_IMAGE_MAX_COUNT_PER_DOCUMENT",
        "JIPSA_RAG_IMAGE_MAX_PIXELS",
        "JIPSA_RAG_OFFICE_RENDERING_ENABLED",
        "JIPSA_RAG_OFFICE_RENDERING_PROVIDER",
        "JIPSA_RAG_OFFICE_COM_REQUIRE_INTERACTIVE_SESSION",
        "JIPSA_RAG_OFFICE_RENDER_MAX_CONCURRENCY",
        "JIPSA_RAG_OFFICE_RENDER_TIMEOUT_SECONDS",
        "JIPSA_RAG_OCR_ENABLED",
        "JIPSA_RAG_OCR_LANGUAGES_CSV",
        "JIPSA_RAG_OCR_GPU_REQUIRED",
        "JIPSA_RAG_OCR_DEVICE",
        "JIPSA_RAG_OCR_MODEL_STORAGE_DIRECTORY",
        "JIPSA_RAG_OCR_MODEL_DOWNLOAD_ENABLED",
        "JIPSA_RAG_OCR_TIMEOUT_SECONDS",
        "JIPSA_RAG_OCR_DOCUMENT_TIMEOUT_SECONDS",
    }

    for key in required_keys:
        assert f"{key}=" in env_example

    assert "JIPSA_RAG_LIBREOFFICE_EXECUTABLE" not in env_example


def test_execution_guide_documents_quality_gate_cuda_and_office_checks() -> None:
    """실행 문서가 CUDA, Office COM 및 전체 품질 검사 절차를 포함한다."""

    guide = (PROJECT_ROOT / "docs" / "ocr-image-processing.md").read_text(encoding="utf-8")

    assert "torch.cuda.is_available()" in guide
    assert "PowerPoint.Application" in guide
    assert "Excel.Application" in guide
    assert "JIPSA_RAG_RUN_OFFICE_COM_INTEGRATION=1" in guide
    assert "uv run ruff format --check src tests" in guide
    assert "uv run ruff check src tests" in guide
    assert "uv run mypy src tests" in guide
    assert "uv run pytest" in guide
    assert "LibreOffice" not in guide


def test_gitignore_excludes_easyocr_runtime_model_cache() -> None:
    """최초 실행에서 받은 OCR 모델 파일이 Git 변경 목록에 포함되지 않게 한다."""

    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".cache/" in gitignore
