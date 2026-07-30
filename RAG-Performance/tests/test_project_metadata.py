"""독립 배포 이름과 실제 import package의 uv build 계약을 검증한다."""

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
