from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.risk_overlay_execution_adapter_v1 import (
    build_risk_overlay_execution_spec,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a derived review-only execution spec from a risk overlay review artifact or queue row."
    )
    parser.add_argument("--input", required=True, help="Path to a risk overlay review artifact or queue row JSON.")
    parser.add_argument("--output", required=True, help="Path to the output derived execution spec JSON.")
    args = parser.parse_args(argv)

    try:
        artifact = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: unable to read input artifact JSON: {exc}\n")

    try:
        payload = build_risk_overlay_execution_spec(artifact, source_artifact_path=str(args.input))
    except ValueError as exc:
        parser.exit(1, f"error: unable to build derived execution spec: {exc}\n")

    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
