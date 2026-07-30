"""dotenv 파싱과 자식 환경 우선순위가 비밀값을 변형하지 않는지 검증한다."""

from pathlib import Path

from jipsa_rag_benchmark.dotenv_loader import build_child_environment, read_dotenv


def test_read_dotenv_preserves_equals_and_strips_matching_quotes(tmp_path: Path) -> None:
    path = tmp_path / ".env.local"
    path.write_text(
        "# comment\nTOKEN=abc==\nQUOTED=\"hello world\"\nexport VALUE='x=y'\n",
        encoding="utf-8",
    )

    values = read_dotenv(path)

    assert values == {"TOKEN": "abc==", "QUOTED": "hello world", "VALUE": "x=y"}


def test_build_child_environment_applies_explicit_overrides() -> None:
    # pytest의 monkeypatch Fixture 타입에 의존하지 않고 os.environ 오염을 피하기 위해
    # 실제 변수와 충돌 가능성이 없는 키를 사용한다.
    values = build_child_environment(
        {"JIPSA_TEST_DOTENV_ONLY": "from-file"},
        overrides={
            "JIPSA_TEST_DOTENV_ONLY": "override",
            "JIPSA_TEST_REMOVED": None,
        },
    )

    assert values["JIPSA_TEST_DOTENV_ONLY"] == "override"
    assert "JIPSA_TEST_REMOVED" not in values
