from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.risk_overlay_candidate import build_risk_overlay_candidate_draft


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic risk-overlay candidate draft from extracted book notes."
    )
    parser.add_argument("--notes", required=True, help="Path to extracted book notes JSONL.")
    parser.add_argument("--output", required=True, help="Path to the candidate draft JSON output.")
    args = parser.parse_args(argv)

    try:
        notes = _read_jsonl(Path(args.notes))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(1, f"error: unable to read notes JSONL: {exc}\n")

    try:
        draft = build_risk_overlay_candidate_draft(notes)
    except ValueError as exc:
        parser.exit(1, f"error: unable to build draft: {exc}\n")

    output_path = Path(args.output)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(draft, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except OSError as exc:
        parser.exit(1, f"error: unable to write output JSON: {exc}\n")
    return 0


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("each JSONL row must be a JSON object")
        rows.append(item)
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
