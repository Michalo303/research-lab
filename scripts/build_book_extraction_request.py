from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.book_request import build_book_extraction_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic book extraction request from an orchestration decision."
    )
    parser.add_argument("--decision", required=True, help="Path to an orchestration_decision_v1 JSON file.")
    parser.add_argument("--output", required=True, help="Path to the output book_extraction_request_v1 JSON file.")
    args = parser.parse_args(argv)

    try:
        decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"error: unable to read decision JSON: {exc}\n")

    request = build_book_extraction_request(decision)
    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(request, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
