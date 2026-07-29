"""RAG 로그 개선 전후의 출력량과 로깅 오버헤드 보고서를 생성한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jipsa_rag.diagnostics.logging_performance import run_logging_comparison


def _parse_arguments() -> argparse.Namespace:
    """반복 횟수와 결과 저장 위치를 명령행 인자로 읽는다."""

    parser = argparse.ArgumentParser(
        description=("Compare legacy and improved RAG logging overhead and output volume.")
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1000,
        help="Number of workload executions per timing round.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=7,
        help="Number of timing rounds used to calculate the median.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("reports/logging"),
        help="Directory for Markdown and JSON reports.",
    )
    return parser.parse_args()


def main() -> int:
    """비교를 실행하고 동일 결과를 Markdown과 JSON으로 저장한다."""

    arguments = _parse_arguments()
    report = run_logging_comparison(
        iterations=arguments.iterations,
        rounds=arguments.rounds,
    )

    output_directory = arguments.output_directory.resolve()
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    markdown_path = output_directory / "logging-performance-comparison.md"
    json_path = output_directory / "logging-performance-comparison.json"

    markdown_path.write_text(
        report.to_markdown(),
        encoding="utf-8",
    )
    json_path.write_text(
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Markdown report: {markdown_path}")
    print(f"JSON report: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
