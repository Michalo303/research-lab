from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from research_lab.execution.local_ohlcv_file_input_adapter_v1 import (
    build_local_ohlcv_file_input_adapter,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "multi_asset_immutable_ohlcv_snapshot_contract_v1.py"
UNIVERSE_MODULE_PATH = ROOT / "research_lab" / "execution" / "point_in_time_universe_contract_v1.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows_a() -> list[dict[str, object]]:
    return [
        {"timestamp": "2024-01-02", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
        {"timestamp": "2024-01-03", "open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5, "volume": 1100},
        {"timestamp": "2024-01-05", "open": 102.0, "high": 103.0, "low": 101.5, "close": 102.5, "volume": 1200},
    ]


def _rows_b() -> list[dict[str, object]]:
    return [
        {"timestamp": "2024-01-03", "open": 200.0, "high": 202.0, "low": 199.0, "close": 201.5, "volume": 2100},
        {"timestamp": "2024-01-04", "open": 201.0, "high": 203.0, "low": 200.5, "close": 202.5, "volume": 2200},
        {"timestamp": "2024-01-05", "open": 202.0, "high": 204.0, "low": 201.5, "close": 203.5, "volume": 2300},
    ]


def _write_dataset(tmp_path: Path, dataset_id: str, symbol: str, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / f"{dataset_id}.json"
    payload = {"dataset_id": dataset_id, "symbol": symbol, "rows": rows}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _build_universe() -> dict[str, object]:
    module = _load_module(UNIVERSE_MODULE_PATH, "point_in_time_universe_contract_v1")
    return module.build_point_in_time_universe_contract(
        {
            "version": "point_in_time_universe_contract_request_v1",
            "universe_id": "liquid_us_listed_etf_research_universe_v1",
            "universe_version": "v1",
            "as_of_timestamp": "2024-01-05T00:00:00Z",
            "membership_policy": {
                "allow_unsafe_current_membership": False,
                "allow_not_point_in_time_safe": False,
                "unsafe_policy_label": "FAIL_CLOSED",
            },
            "base_currency": "USD",
            "instruments": [
                {
                    "instrument_id": "asset-a",
                    "provider": "EODHD",
                    "provider_symbol": "SPY.US",
                    "display_symbol": "SPY.US",
                    "instrument_type": "ETF",
                    "currency": "USD",
                    "market_venue_group": "US_EQUITY",
                    "calendar_id": "US_EQUITY_DAY",
                    "active_from": "2015-01-01T00:00:00Z",
                    "membership_from": "2015-01-01T00:00:00Z",
                    "point_in_time_membership_status": "EXPLICIT_STATIC_RESEARCH_UNIVERSE",
                    "lot_size": 1,
                    "price_precision": 4,
                    "corporate_action_policy_id": "raw_prices_only_v1",
                    "source_sha256": "a" * 64,
                    "provenance": {"source": "unit_test"},
                },
                {
                    "instrument_id": "asset-b",
                    "provider": "EODHD",
                    "provider_symbol": "QQQ.US",
                    "display_symbol": "QQQ.US",
                    "instrument_type": "ETF",
                    "currency": "USD",
                    "market_venue_group": "US_EQUITY",
                    "calendar_id": "US_EQUITY_DAY",
                    "active_from": "2015-01-01T00:00:00Z",
                    "membership_from": "2015-01-01T00:00:00Z",
                    "point_in_time_membership_status": "EXPLICIT_STATIC_RESEARCH_UNIVERSE",
                    "lot_size": 1,
                    "price_precision": 4,
                    "corporate_action_policy_id": "raw_prices_only_v1",
                    "source_sha256": "b" * 64,
                    "provenance": {"source": "unit_test"},
                },
            ],
            "provenance": {"source": "unit_test"},
        }
    )


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _asset_input(
    tmp_path: Path,
    *,
    instrument_id: str,
    provider_symbol: str,
    dataset_id: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    path = _write_dataset(tmp_path, dataset_id, provider_symbol, rows)
    adapter_result = build_local_ohlcv_file_input_adapter(
        {
            "version": "local_ohlcv_file_input_adapter_request_v1",
            "file_path": str(path.resolve()),
            "format": "json",
            "dataset_id": dataset_id,
            "symbol": provider_symbol,
            "max_bytes": 1_000_000,
            "max_rows": 10,
            "provenance": {"source": "unit_test"},
        }
    )
    return {
        "instrument_id": instrument_id,
        "provider_symbol": provider_symbol,
        "dataset_id": dataset_id,
        "adapter_result": adapter_result,
        "source_artifact_sha256": adapter_result["source_sha256"],
        "normalized_bars_sha256": adapter_result["normalized_rows_hash"],
        "row_count": adapter_result["row_count"],
        "first_timestamp": adapter_result["first_timestamp"],
        "last_timestamp": adapter_result["last_timestamp"],
        "adjustment_status": "RAW_AS_SUPPLIED",
        "provenance": {"source": "unit_test"},
    }


def _request(
    tmp_path: Path,
    *,
    alignment_policy: str = "INDEPENDENT_SERIES",
    output_dir: Path | None = None,
) -> dict[str, object]:
    assets = [
        _asset_input(tmp_path, instrument_id="asset-a", provider_symbol="SPY.US", dataset_id="asset-a-dataset", rows=_rows_a()),
        _asset_input(tmp_path, instrument_id="asset-b", provider_symbol="QQQ.US", dataset_id="asset-b-dataset", rows=_rows_b()),
    ]
    request: dict[str, object] = {
        "version": "multi_asset_immutable_ohlcv_snapshot_contract_request_v1",
        "snapshot_id": "multi-asset-snapshot-v1",
        "universe_result": _build_universe(),
        "asset_inputs": assets,
        "expected_asset_identities": [
            {"instrument_id": "asset-a", "provider_symbol": "SPY.US", "dataset_id": "asset-a-dataset"},
            {"instrument_id": "asset-b", "provider_symbol": "QQQ.US", "dataset_id": "asset-b-dataset"},
        ],
        "expected_source_hashes": {
            "asset-a": assets[0]["source_artifact_sha256"],
            "asset-b": assets[1]["source_artifact_sha256"],
        },
        "expected_normalized_row_hashes": {
            "asset-a": assets[0]["normalized_bars_sha256"],
            "asset-b": assets[1]["normalized_bars_sha256"],
        },
        "alignment_policy": alignment_policy,
        "missing_session_policy": "REPORT_ONLY",
        "created_at": "2026-07-13T20:00:00Z",
        "provenance": {"source": "unit_test"},
    }
    if output_dir is not None:
        request["output_dir"] = str(output_dir.resolve())
    return request


def _run(request: dict[str, object]) -> dict[str, object]:
    module = _load_module(MODULE_PATH, "multi_asset_immutable_ohlcv_snapshot_contract_v1")
    return module.build_multi_asset_immutable_ohlcv_snapshot_contract(copy.deepcopy(request))


def test_builds_deterministic_multi_asset_snapshot_for_independent_series(tmp_path):
    request = _request(tmp_path)

    first = _run(request)
    second = _run(request)

    assert first == second
    assert first["version"] == "multi_asset_immutable_ohlcv_snapshot_contract_result_v1"
    assert first["contract_version"] == "multi_asset_immutable_ohlcv_snapshot_contract_v1"
    assert first["production_runtime_supported"] is False
    assert [item["instrument_id"] for item in first["validated_asset_series"]] == ["asset-a", "asset-b"]
    assert first["alignment_summary"] == {
        "alignment_policy": "INDEPENDENT_SERIES",
        "aligned_session_count": 0,
        "union_session_count": 4,
        "intersection_session_count": 2,
    }
    assert first["missing_session_summary"] == {
        "asset-a": ["2024-01-04T00:00:00Z"],
        "asset-b": ["2024-01-02T00:00:00Z"],
    }


def test_union_and_intersection_alignment_are_reported_without_inventing_bars(tmp_path):
    union = _run(_request(tmp_path, alignment_policy="UNION_SESSIONS"))
    intersection = _run(_request(tmp_path, alignment_policy="INTERSECTION_SESSIONS"))

    assert union["alignment_summary"]["aligned_session_count"] == 4
    assert intersection["alignment_summary"]["aligned_session_count"] == 2
    assert [item["row_count"] for item in union["validated_asset_series"]] == [3, 3]
    assert [item["row_count"] for item in intersection["validated_asset_series"]] == [3, 3]


def test_persistence_writes_bounded_artifact_set_and_complete_last(tmp_path):
    output_dir = tmp_path / "snapshot"
    result = _run(_request(tmp_path, output_dir=output_dir))

    assert result["persisted_artifacts"]["written_files"] == [
        "snapshot_request.json",
        "universe.json",
        "asset_manifest.json",
        "multi_asset_metadata.json",
        "checksums.json",
        "COMPLETE",
    ]
    assert sorted(path.name for path in output_dir.iterdir()) == [
        "COMPLETE",
        "asset_manifest.json",
        "checksums.json",
        "multi_asset_metadata.json",
        "snapshot_request.json",
        "universe.json",
    ]


def test_duplicate_assets_and_identity_mismatch_fail(tmp_path):
    duplicate = _request(tmp_path)
    duplicate["asset_inputs"] = [duplicate["asset_inputs"][0], duplicate["asset_inputs"][0]]
    with pytest.raises(ValueError, match="duplicate instrument_id"):
        _run(duplicate)

    mismatch = _request(tmp_path)
    mismatch["expected_asset_identities"][0]["provider_symbol"] = "DIA.US"
    with pytest.raises(ValueError, match="expected asset identity mismatch"):
        _run(mismatch)


def test_source_hash_normalized_hash_and_row_count_mismatches_fail(tmp_path):
    wrong_source = _request(tmp_path)
    wrong_source["expected_source_hashes"]["asset-a"] = "1" * 64
    with pytest.raises(ValueError, match="source hash mismatch"):
        _run(wrong_source)

    wrong_normalized = _request(tmp_path)
    wrong_normalized["expected_normalized_row_hashes"]["asset-b"] = "2" * 64
    with pytest.raises(ValueError, match="normalized row hash mismatch"):
        _run(wrong_normalized)

    wrong_count = _request(tmp_path)
    wrong_count["asset_inputs"][0]["row_count"] = 99
    with pytest.raises(ValueError, match="row_count mismatch"):
        _run(wrong_count)


def test_output_collision_and_incomplete_staging_fail_safely(tmp_path, monkeypatch):
    request = _request(tmp_path, output_dir=tmp_path / "snapshot")
    request["output_dir"] = str((tmp_path / "snapshot").resolve())
    output_dir = Path(request["output_dir"])
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="output_dir must be empty or absent"):
        _run(request)

    module = _load_module(MODULE_PATH, "multi_asset_immutable_ohlcv_snapshot_contract_v1_failure")
    failing_request = _request(tmp_path, output_dir=tmp_path / "staged")
    original = module._write_verified_json

    def fail_on_checksums(path: Path, payload: object) -> str:
        if path.name == "checksums.json":
            raise OSError("simulated staging failure")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_verified_json", fail_on_checksums)
    with pytest.raises(OSError, match="simulated staging failure"):
        module.build_multi_asset_immutable_ohlcv_snapshot_contract(copy.deepcopy(failing_request))
    assert not (tmp_path / "staged" / "COMPLETE").exists()


def test_result_is_deterministic_and_does_not_mutate_inputs(tmp_path):
    request = _request(tmp_path)
    original = copy.deepcopy(request)

    first = _run(request)
    second = _run(request)

    assert request == original
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]


def test_module_does_not_import_network_clients():
    forbidden_roots = (
        "requests",
        "urllib",
        "http",
        "socket",
        "aiohttp",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"unexpected forbidden import: {import_name}"
