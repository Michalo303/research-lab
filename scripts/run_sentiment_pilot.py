from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.sentiment import run_apify_scaffold, run_sentiment_pilot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the research-only sentiment / attention pilot.")
    parser.add_argument("--provider", choices=["file", "apify"], default="file")
    parser.add_argument("--input", dest="input_path", default=None)
    parser.add_argument("--tickers", default="")
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--max-cost-usd", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    tickers = [ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()] or None
    dry_run = not args.write or args.dry_run and not args.write
    if args.provider == "apify":
        print(run_apify_scaffold(max_items=args.max_items, max_cost_usd=args.max_cost_usd))
    result = run_sentiment_pilot(
        root=Path(args.root),
        provider=args.provider,
        input_path=args.input_path,
        tickers=tickers,
        max_items=args.max_items,
        write=args.write,
        dry_run=dry_run,
    )
    print(f"provider_status: {result['provider_status']}")
    if result.get("provider_reason"):
        print(f"provider_reason: {result['provider_reason']}")
    print(f"snapshots: {len(result['snapshots'])}")
    print(f"candidates: {len(result['candidates'])}")
    if args.write:
        print(f"sentiment_snapshot: {result['snapshot_path']}")
        print(f"sentiment_candidates: {result['candidates_path']}")
    else:
        print("dry_run: true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
