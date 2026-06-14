from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.orchestration.input_adapter import build_orchestration_input


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only orchestration input from existing research artifacts.")
    parser.add_argument("--root", default=".", help="Project root containing registry/ and reports/. Defaults to the current directory.")
    parser.add_argument("--output", required=True, help="Path to output orchestration input JSON.")
    parser.add_argument("--max-experiments", type=int, default=50, help="Maximum recent experiment rows to read from registry/experiments.jsonl.")
    parser.add_argument("--max-gate-rows", type=int, default=100, help="Maximum rows to read from the latest weekly deployment gate CSV.")
    args = parser.parse_args(argv)

    payload = build_orchestration_input(Path(args.root), max_experiments=args.max_experiments, max_gate_rows=args.max_gate_rows)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
