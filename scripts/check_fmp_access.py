from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.fmp_access_diagnostics import run_fmp_access_diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only FMP access diagnostics.")
    parser.add_argument("--symbols", nargs="*", default=["AAPL"], help="Symbols to probe; first non-empty symbol is used.")
    args = parser.parse_args(argv)
    print(json.dumps(run_fmp_access_diagnostics(symbols=args.symbols), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
