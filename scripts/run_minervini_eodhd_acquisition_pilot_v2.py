from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.research.minervini_eodhd_acquisition_pilot_v2 import (
    PROVIDER_REQUEST_LIMIT,
    run_minervini_eodhd_acquisition_pilot_v2,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded EOD-only Minervini acquisition pilot V2."
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--expected-provider-requests", type=int)
    args = parser.parse_args(argv)
    if not args.execute_live:
        print("status=DRY_RUN")
        print("version=minervini_eodhd_acquisition_pilot_v2")
        print(f"planned_provider_requests={PROVIDER_REQUEST_LIMIT}")
        print("writes_performed=False")
        return 0
    try:
        output_dir = _validated_output_dir(args.output_dir)
        if args.expected_provider_requests != PROVIDER_REQUEST_LIMIT:
            raise ValueError("expected provider requests must be exactly 24.")
        result = run_minervini_eodhd_acquisition_pilot_v2(
            output_dir=output_dir,
            expected_provider_requests=args.expected_provider_requests,
        )
    except (OSError, ValueError) as exc:
        print("status=FAIL")
        print(f"reason={exc}")
        return 1
    print(f"status={result['status']}")
    print(f"provider_requests_used={result['provider_requests_used']}")
    print(f"result_manifest_sha256={result['result_manifest_sha256']}")
    print(f"output_dir={result['output_dir']}")
    universe = result.get("universe")
    if isinstance(universe, dict):
        print(f"active_count={universe['active_count']}")
        print(f"delisted_count={universe['delisted_count']}")
        print(
            "deduplicated_code_count="
            f"{universe['deduplicated_code_count']}"
        )
    estimate = result.get("estimate")
    if isinstance(estimate, dict):
        print(f"total_http_requests={estimate['total_http_requests']}")
        print(
            "minimum_acquisition_days_at_100000_units="
            f"{estimate['minimum_acquisition_days_at_100000_units']}"
        )
        print(
            "raw_storage_bytes_upper_bound="
            f"{estimate['raw_storage_bytes_upper_bound']}"
        )
    return (
        0
        if result["status"]
        == "READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL"
        else 2
    )


def _validated_output_dir(value: Path | None) -> Path:
    if value is None or not value.is_absolute():
        raise ValueError("--output-dir must be an absolute local path.")
    if value.exists() and value.is_symlink():
        raise ValueError("--output-dir must not be a symlink.")
    resolved = value.resolve()
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("--output-dir must be a directory.")
        if any(resolved.iterdir()):
            raise ValueError("--output-dir must be empty.")
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
