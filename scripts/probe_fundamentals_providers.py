from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.fundamentals_eodhd import classify_eodhd_payload
from research_lab.fundamentals_massive import classify_massive_payload


DEFAULT_SYMBOLS = ("AAPL", "MSFT", "NVDA")


def credential_presence(env: dict[str, str] | None = None) -> dict[str, dict[str, bool]]:
    env = env or os.environ
    return {
        "eodhd": {"api_key_present": bool(env.get("EODHD_API_KEY", "").strip())},
        "massive": {"api_key_present": bool(env.get("MASSIVE_API_KEY", "").strip())},
    }


def run_diagnostics(
    symbols: list[str],
    probe: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    limited_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()][:3]
    diagnostics: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read_only_fundamentals_provider_probe",
        "credentials": credential_presence(env),
        "network_probe_requested": bool(probe),
        "symbols": limited_symbols,
        "providers": {},
    }
    if not probe:
        diagnostics["providers"] = {
            "eodhd": {"status": "not_probed"},
            "massive": {"status": "not_probed"},
        }
        return diagnostics

    eodhd_key = env.get("EODHD_API_KEY", "").strip()
    massive_key = env.get("MASSIVE_API_KEY", "").strip()
    diagnostics["providers"]["eodhd"] = _probe_eodhd(limited_symbols, eodhd_key) if eodhd_key else {"status": "missing_api_key"}
    diagnostics["providers"]["massive"] = _probe_massive(limited_symbols, massive_key, env) if massive_key else {"status": "missing_api_key"}
    return diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only timestamp-safety diagnostics for fundamentals providers.")
    parser.add_argument("--probe", action="store_true", help="Perform tiny provider probes when credentials are present.")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS), help="Symbols to probe; capped at three.")
    args = parser.parse_args()
    print(json.dumps(run_diagnostics(args.symbols, probe=args.probe), indent=2, sort_keys=True))


def _probe_eodhd(symbols: list[str], api_key: str) -> dict[str, Any]:
    rows = []
    for symbol in symbols:
        provider_symbol = symbol if "." in symbol else f"{symbol}.US"
        query = urllib.parse.urlencode({"api_token": api_key, "fmt": "json"})
        url = f"https://eodhd.com/api/fundamentals/{urllib.parse.quote(provider_symbol)}?{query}"
        rows.append(classify_eodhd_payload(_download_json(url), request_url=url))
    return {"status": "probed", "symbols": rows}


def _probe_massive(symbols: list[str], api_key: str, env: dict[str, str]) -> dict[str, Any]:
    rows = []
    base_url = env.get("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/")
    for symbol in symbols:
        query = urllib.parse.urlencode({"ticker": symbol, "limit": 1, "apiKey": api_key})
        url = f"{base_url}/vX/reference/financials?{query}"
        rows.append(classify_massive_payload(_download_json(url), request_url=url))
    return {"status": "probed", "symbols": rows}


def _download_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "research-lab/0.1 research-only"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": True, "message": raw[:300], "status": exc.code}
    except Exception as exc:
        return {"error": True, "message": str(exc)[:300]}


if __name__ == "__main__":
    main()
