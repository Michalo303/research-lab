from __future__ import annotations

import json
import shutil
from urllib.parse import parse_qs, urlparse

from research_lab.research.minervini_eodhd_acquisition_pilot_v2 import (
    run_minervini_eodhd_acquisition_pilot_v2,
)
from research_lab.research.minervini_immutable_pilot_artifacts_v1 import (
    replay_minervini_pilot_artifacts_v1,
)


def _universe(prefix: str, anchor: str) -> list[dict[str, object]]:
    codes = [anchor, *(f"{prefix}{number:02d}" for number in range(9))]
    return [
        {
            "Code": code,
            "Name": f"{code} Company",
            "Country": "USA",
            "Exchange": "NASDAQ",
            "Currency": "USD",
            "Type": "Common Stock",
            "Isin": f"US{number:010d}",
        }
        for number, code in enumerate(codes)
    ]


def _raw(url: str) -> bytes:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.path == "/api/exchange-symbol-list/US":
        return json.dumps(
            _universe("DEL", "ATVI")
            if query.get("delisted") == ["1"]
            else _universe("ACT", "AAPL")
        ).encode("utf-8")
    if parsed.path.startswith("/api/eod/"):
        return json.dumps(
            [
                {
                    "date": "2025-01-02",
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 101.0,
                    "adjusted_close": 100.5,
                    "volume": 1_000_000,
                },
                {
                    "date": "2025-01-03",
                    "open": 101.0,
                    "high": 103.0,
                    "low": 100.0,
                    "close": 102.0,
                    "adjusted_close": 101.5,
                    "volume": 1_100_000,
                },
            ]
        ).encode("utf-8")
    if parsed.path.startswith("/api/splits/"):
        return b"[]"
    raise AssertionError(f"unexpected endpoint: {parsed.path}")


def test_v2_acquisition_pilot_replays_and_detects_raw_mutation(tmp_path):
    calls: list[str] = []

    def getter(url: str):
        calls.append(url)
        return _raw(url), {"http_status": 200}

    root = tmp_path / "pilot"
    result = run_minervini_eodhd_acquisition_pilot_v2(
        api_key="secret-value",
        output_dir=root,
        expected_provider_requests=24,
        http_get=getter,
        now_utc=lambda: "2026-07-26T10:00:00Z",
    )
    replay = replay_minervini_pilot_artifacts_v1(root)

    assert result["status"] == (
        "READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL"
    )
    assert result["provider_requests_used"] == 24
    assert len(calls) == 24
    assert replay["status"] == "VERIFIED"
    assert replay["result_manifest_sha256"] == result["result_manifest_sha256"]
    assert result["wide_acquisition_authorized"] is False
    assert result["broker_actions_used"] == 0
    assert result["registry_write_performed"] is False
    assert result["promotion_performed"] is False
    assert result["deployment_performed"] is False

    mutated = tmp_path / "mutated"
    shutil.copytree(root, mutated)
    target = mutated / "eod-SPY.US.json"
    target.write_bytes(target.read_bytes() + b" ")

    assert replay_minervini_pilot_artifacts_v1(mutated)["status"] == (
        "FAILED_RAW_HASH_MISMATCH"
    )
