from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.risk_overlay_hypothesis_queue import (
    build_risk_overlay_hypothesis_queue_entry,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a conservative hypothesis queue entry review artifact from a risk overlay candidate draft."
    )
    parser.add_argument("--draft", required=True, help="Path to candidate_experiment_draft_v1 JSON.")
    parser.add_argument("--output", required=True, help="Path to the output hypothesis queue entry candidate JSON.")
    args = parser.parse_args(argv)

    try:
        draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: unable to read draft JSON: {exc}\n")

    try:
        payload = build_risk_overlay_hypothesis_queue_entry(draft, source_draft=str(args.draft))
    except ValueError as exc:
        parser.exit(1, f"error: unable to build queue entry candidate: {exc}\n")

    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
