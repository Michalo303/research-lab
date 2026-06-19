from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.risk_overlay_controlled_backtest_v1 import (
    build_controlled_backtest_request,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a review-only controlled backtest request from a derived risk overlay execution spec."
    )
    parser.add_argument("--input", required=True, help="Path to a derived risk overlay execution spec JSON artifact.")
    parser.add_argument("--output", required=True, help="Path to the output controlled backtest request JSON artifact.")
    args = parser.parse_args(argv)

    try:
        artifact = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: unable to read input execution spec JSON: {exc}\n")

    try:
        payload = build_controlled_backtest_request(artifact, source_execution_spec_path=str(args.input))
    except ValueError as exc:
        parser.exit(1, f"error: unable to build controlled backtest request: {exc}\n")

    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
