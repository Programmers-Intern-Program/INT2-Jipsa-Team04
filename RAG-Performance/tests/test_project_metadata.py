"""독립 배포 이름, 실제 import package와 실행 Entry Point 계약을 검증한다."""

import tomllib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_uv_build_backend_uses_actual_import_package_name() -> None:
    with (_PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)

    project = pyproject["project"]
    build_backend = pyproject["tool"]["uv"]["build-backend"]

    assert project["name"] == "jipsa-rag-performance"
    assert build_backend["module-name"] == "jipsa_rag_benchmark"
    assert (_PROJECT_ROOT / "src/jipsa_rag_benchmark/__init__.py").is_file()


def test_baseline_reliability_and_stress_entry_points_are_registered() -> None:
    with (_PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)

    scripts = pyproject["project"]["scripts"]

    assert scripts["jipsa-rag-benchmark"] == "jipsa_rag_benchmark.runner:main"
    assert scripts["jipsa-rag-reliability"] == ("jipsa_rag_benchmark.reliability_runner:main")
    assert scripts["jipsa-rag-stress"] == "jipsa_rag_benchmark.stress_runner:main"
