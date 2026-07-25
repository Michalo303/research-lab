from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.research.minervini_eodhd_capability_v1 import (
    PROVIDER_CALL_LIMIT,
    run_minervini_eodhd_capability_v1,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded read-only Minervini EODHD capability diagnostic."
    )
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_live:
        print("status=DRY_RUN")
        print(f"planned_provider_calls={PROVIDER_CALL_LIMIT}")
        return 0
    result = run_minervini_eodhd_capability_v1()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
