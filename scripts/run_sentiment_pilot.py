from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.sentiment import build_snapshots, load_file_items, run_apify_scaffold, write_outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run research-only sentiment pilot layer.")
    parser.add_argument("--provider", choices=["file", "apify"], default="file")
    parser.add_argument("--input", default="tests/fixtures/sentiment_sample.jsonl")
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--max-cost-usd", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    root = Path(args.root)
    if args.provider == "apify":
        result = run_apify_scaffold(max_items=args.max_items, max_cost_usd=args.max_cost_usd)
        print(result)
        items = result.get("items", [])
    else:
        items = load_file_items(root / args.input)[: args.max_items]

    snapshots = build_snapshots(items)
    print(f"snapshots built: {len(snapshots)}")
    if args.write:
        iso_year, iso_week, _ = date.today().isocalendar()
        report_stem = f"{iso_year}-W{iso_week:02d}"
        output = write_outputs(root, snapshots, report_stem=report_stem)
        print(output)
