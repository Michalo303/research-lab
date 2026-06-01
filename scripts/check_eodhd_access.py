#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.eodhd_access_diagnostics import run_eodhd_access_diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only EODHD daily/fundamentals access diagnostics.")
    parser.add_argument("--symbol", default="AAPL.US", help="Single EODHD symbol to probe.")
    parser.add_argument("--daily-start", default="2026-05-01", help="Start date for the tiny daily OHLCV probe.")
    args = parser.parse_args()
    diagnostics = run_eodhd_access_diagnostics(symbol=args.symbol, daily_start=args.daily_start)
    print(json.dumps(diagnostics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
