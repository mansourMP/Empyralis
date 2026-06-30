from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Prompt 14 public bot load, failure, and recovery drills.")
    parser.add_argument(
        "--output",
        default="docs/operations/prompt14-public-bot-drill-report.md",
        help="Markdown report output path.",
    )
    args = parser.parse_args()

    os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
    os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")
    logging.disable(logging.CRITICAL)

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from server_modules.public_bot_drill_support import write_prompt14_report

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(os.devnull, "w", encoding="utf-8") as devnull:
        original_stdout = getattr(sys, "__stdout__", None)
        original_stderr = getattr(sys, "__stderr__", None)
        try:
            sys.__stdout__ = devnull
            sys.__stderr__ = devnull
            with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                report = write_prompt14_report(output_path=output_path)
        finally:
            if original_stdout is not None:
                sys.__stdout__ = original_stdout
            if original_stderr is not None:
                sys.__stderr__ = original_stderr

    hard_numbers = report.get("hard_numbers") if isinstance(report, dict) else {}
    summary = {
        "report_markdown": str(output_path),
        "report_json": str(output_path.with_suffix(".json")),
        "hard_numbers": hard_numbers,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
