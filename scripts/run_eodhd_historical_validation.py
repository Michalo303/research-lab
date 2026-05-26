#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from research_lab.data_eodhd import (
    EODHDCoverageRow,
    coverage_row,
    fetch_eodhd_eod,
    fetch_eodhd_eod_diagnostic,
    get_eodhd_api_key,
    write_coverage_report,
    write_vendor_report,
)

DEFAULT_SYMBOLS = ["SPY.US", "QQQ.US", "IWM.US", "TLT.US", "GLD.US"]
DEBUG_SYMBOLS = ["SPY.US", "SPY", "AAPL.US", "MSFT.US"]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    api_key = get_eodhd_api_key()

    print("EODHD diagnostic probe (sanitized):")
    for symbol in DEBUG_SYMBOLS:
        d = fetch_eodhd_eod_diagnostic(symbol, api_key=api_key, start="1990-01-01")
        print(
            f"{d.ticker} | url={d.request_url} | status={d.http_status} | content_type={d.content_type} "
            f"| body_len={d.body_length} | parsed_rows={d.parsed_row_count} | error={d.error_reason} "
            f"| preview={d.body_preview[:120]}"
        )

    rows = []
    for symbol in DEFAULT_SYMBOLS:
        try:
            df = fetch_eodhd_eod(symbol, api_key=api_key, start="1990-01-01")
            rows.append(coverage_row(symbol, df, min_years_ok=30.0))
        except Exception as exc:
            rows.append(EODHDCoverageRow(symbol, "", "", 0, 0.0, 0, 0, "FAIL"))
            print(f"coverage_fail {symbol}: {str(exc)[:240]}")

    coverage_path = root / "registry" / "eodhd_coverage_report.csv"
    vendor_path = root / "registry" / "eodhd_vendor_report.json"
    write_coverage_report(rows, coverage_path)
    write_vendor_report(rows, vendor_path)
    print(f"Wrote: {coverage_path}")
    print(f"Wrote: {vendor_path}")


if __name__ == "__main__":
    main()
