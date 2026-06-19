from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.risk_overlay_single_backtest_preflight_v1 import (
    build_single_backtest_preflight,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only single backtest preflight artifact from a single controlled backtest plan."
    )
    parser.add_argument("--input", required=True, help="Path to a single controlled backtest plan JSON artifact.")
    parser.add_argument("--output", required=True, help="Path to the output single backtest preflight JSON artifact.")
    args = parser.parse_args(argv)

    try:
        artifact = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: unable to read input single controlled backtest plan JSON: {exc}\n")

    try:
        payload = build_single_backtest_preflight(
            artifact,
            source_single_controlled_backtest_plan_path=str(args.input),
        )
    except ValueError as exc:
        parser.exit(1, f"error: unable to build single backtest preflight: {exc}\n")

    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
