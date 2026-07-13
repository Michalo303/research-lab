from __future__ import annotations

import copy
import json
from pathlib import Path

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import _canonical_sha256
from research_lab.execution.macro_aware_pilot_request_builder_v1 import (
    prepare_controlled_synthetic_macro_pilot_request,
)
from research_lab.execution.macro_aware_pilot_runner_v1 import run_macro_aware_pilot
from research_lab.execution.macro_aware_pilot_verifier_replay_v1 import (
    verify_macro_aware_pilot_run,
)


def _snapshot_payload() -> dict[str, object]:
    rows = []
    prices = (
        (1, 100.0, 100.0),
        (2, 100.0, 102.0),
        (3, 102.0, 104.0),
        (6, 104.0, 103.0),
        (7, 103.0, 101.0),
        (8, 101.0, 99.0),
        (9, 99.0, 100.0),
        (10, 100.0, 101.0),
    )
    for index, (day, open_price, close_price) in enumerate(prices):
        rows.append(
            {
                "timestamp": f"2026-01-{day:02d}",
                "open": open_price,
                "high": max(open_price, close_price) + 1.0,
                "low": min(open_price, close_price) - 1.0,
                "close": close_price,
                "volume": 1_000_000.0 + index,
            }
        )
    return {
        "dataset_id": "eodhd-spy-us-daily-2015-2026-v1",
        "symbol": "SPY.US",
        "rows": rows,
    }


def test_prepared_request_uses_local_snapshot_and_runs_without_source_mutation(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_request_builder_v1 as builder_module
    import research_lab.execution.macro_aware_pilot_runner_v1 as runner_module

    snapshot_path = tmp_path / "normalized_ohlcv.json"
    snapshot_payload = _snapshot_payload()
    snapshot_path.write_text(json.dumps(snapshot_payload, indent=2) + "\n", encoding="utf-8")
    snapshot_before = snapshot_path.read_bytes()
    monkeypatch.setattr(builder_module, "PRIVATE_RUN_ROOT", tmp_path)
    monkeypatch.setattr(builder_module, "EXPECTED_SNAPSHOT_SHA256", _canonical_sha256(snapshot_payload))
    monkeypatch.setattr(builder_module, "EXPECTED_ROW_COUNT", 8)
    monkeypatch.setattr(builder_module, "EXPECTED_FIRST_TIMESTAMP", "2026-01-01T00:00:00Z")
    monkeypatch.setattr(builder_module, "EXPECTED_LAST_TIMESTAMP", "2026-01-10T00:00:00Z")
    monkeypatch.setattr(runner_module, "PRIVATE_RUN_ROOT", tmp_path)
    request_path = tmp_path / "pilot-request.json"

    summary = prepare_controlled_synthetic_macro_pilot_request(
        market_snapshot_path=snapshot_path,
        request_output_path=request_path,
        run_id="synthetic-macro-pilot-builder-test",
        created_at="2026-01-11T00:00:00Z",
    )

    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert summary["request_status"] == "PREPARED"
    assert summary["market_snapshot_sha256"] == _canonical_sha256(snapshot_payload)
    assert request["run_label"] == "SYNTHETIC_MACRO_INTEGRATION_PILOT"
    assert request["expected_market_dataset_identity"] == snapshot_payload["dataset_id"]
    assert request["expected_market_symbol"] == snapshot_payload["symbol"]
    assert request["expected_market_bars_sha256"] != request[
        "expected_market_source_artifact_sha256"
    ]
    assert {
        item["provider"] for item in request["acceptance_request"]["macro_series_requests"]
    } == {"SYNTHETIC"}
    assert all(
        item["provenance"]["synthetic_macro_label"] == "SYNTHETIC_MACRO_INTEGRATION_PILOT"
        for item in request["acceptance_request"]["macro_series_requests"]
    )
    assert snapshot_path.read_bytes() == snapshot_before

    run_dir = tmp_path / "run"
    source_request = copy.deepcopy(request)
    run_macro_aware_pilot(request, output_dir=run_dir)
    assert verify_macro_aware_pilot_run(run_dir)["verification_status"] == "VERIFIED"
    assert request == source_request
    assert snapshot_path.read_bytes() == snapshot_before


def test_builder_rejects_snapshot_identity_hash_and_existing_output(tmp_path, monkeypatch):
    import research_lab.execution.macro_aware_pilot_request_builder_v1 as builder_module

    snapshot_path = tmp_path / "normalized_ohlcv.json"
    snapshot_payload = _snapshot_payload()
    snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")
    monkeypatch.setattr(builder_module, "PRIVATE_RUN_ROOT", tmp_path)
    monkeypatch.setattr(builder_module, "EXPECTED_ROW_COUNT", 8)
    monkeypatch.setattr(builder_module, "EXPECTED_FIRST_TIMESTAMP", "2026-01-01T00:00:00Z")
    monkeypatch.setattr(builder_module, "EXPECTED_LAST_TIMESTAMP", "2026-01-10T00:00:00Z")
    request_path = tmp_path / "pilot-request.json"

    monkeypatch.setattr(builder_module, "EXPECTED_SNAPSHOT_SHA256", "0" * 64)
    try:
        prepare_controlled_synthetic_macro_pilot_request(
            market_snapshot_path=snapshot_path,
            request_output_path=request_path,
            run_id="run",
            created_at="2026-01-11T00:00:00Z",
        )
    except ValueError as exc:
        assert "snapshot hash" in str(exc)
    else:
        raise AssertionError("snapshot hash mismatch must fail")

    monkeypatch.setattr(builder_module, "EXPECTED_SNAPSHOT_SHA256", _canonical_sha256(snapshot_payload))
    request_path.write_text("preserve", encoding="utf-8")
    try:
        prepare_controlled_synthetic_macro_pilot_request(
            market_snapshot_path=snapshot_path,
            request_output_path=request_path,
            run_id="run",
            created_at="2026-01-11T00:00:00Z",
        )
    except ValueError as exc:
        assert "fresh" in str(exc)
    else:
        raise AssertionError("existing request output must fail")
    assert request_path.read_text(encoding="utf-8") == "preserve"
