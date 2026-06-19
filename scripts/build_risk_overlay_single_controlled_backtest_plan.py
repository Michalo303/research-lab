from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.risk_overlay_single_controlled_backtest_v1 import (
    build_single_controlled_backtest_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a review-only single controlled backtest plan from a controlled backtest request artifact."
    )
    parser.add_argument("--input", required=True, help="Path to a controlled backtest request JSON artifact.")
    parser.add_argument("--output", required=True, help="Path to the output single controlled backtest plan JSON artifact.")
    parser.add_argument(
        "--run-single-controlled-backtest",
        action="store_true",
        help="Explicitly request execution. This currently fails closed in v1.",
    )
    args = parser.parse_args(argv)

    try:
        artifact = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: unable to read input controlled backtest request JSON: {exc}\n")

    try:
        payload = build_single_controlled_backtest_plan(
            artifact,
            source_controlled_backtest_request_path=str(args.input),
            run_single_controlled_backtest=args.run_single_controlled_backtest,
        )
    except ValueError as exc:
        parser.exit(1, f"error: unable to build single controlled backtest plan: {exc}\n")

    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
