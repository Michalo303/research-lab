from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


REQUIRED_MARKERS = [
    "READ ONLY MODE",
    "Live Status",
    "Research Results",
    "Portfolio / Paper Readiness",
    "Sentiment / Attention",
    "Data / Edge Audit",
    "Improvement Ideas",
    "Alerts / Errors",
]


def check_dashboard_smoke(base_url: str, timeout: float = 5.0) -> None:
    html = _fetch_text(base_url.rstrip("/") + "/", timeout)
    for marker in REQUIRED_MARKERS:
        if marker not in html:
            raise AssertionError(f"missing marker: {marker}")

    payload = json.loads(_fetch_text(base_url.rstrip("/") + "/api/refresh", timeout))
    if payload.get("read_only_mode") is not True:
        raise AssertionError("refresh payload does not report read_only_mode=true")


def _fetch_text(url: str, timeout: float) -> str:
    with urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test the research-lab dashboard.")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        check_dashboard_smoke(args.url, timeout=args.timeout)
    except (AssertionError, HTTPError, URLError, json.JSONDecodeError) as exc:
        print(f"dashboard smoke test failed: {exc}", file=sys.stderr)
        return 1
    print("dashboard smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
