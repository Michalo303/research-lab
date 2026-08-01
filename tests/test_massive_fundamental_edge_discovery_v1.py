from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import research_lab.research.massive_fundamental_edge_discovery_v1 as pilot_module
from research_lab.research.global_experiment_ledger_v1 import (
    build_global_experiment_ledger_policy_v1,
    build_global_experiment_ledger_v1,
)
from research_lab.research.massive_fundamental_catalog_v1 import FACTOR_DEFINITIONS_V1
from research_lab.research.massive_fundamental_catalog_v1 import build_massive_fundamental_catalog_metadata_v1
from research_lab.research.massive_fundamental_edge_discovery_v1 import (
    build_massive_fundamental_edge_discovery_plan_v1,
    run_massive_fundamental_edge_discovery_v1,
)


def _policy(*, global_trials: int = 40, family_trials: int = 20, hypotheses: int = 40) -> dict[str, object]:
    return build_global_experiment_ledger_policy_v1(
        {
            "version": "global_experiment_ledger_policy_request_v1",
            "policy_id": "FUNDAMENTAL-POLICY-V1",
            "m32a_contract_version": "research_objective_promotion_gate_v1",
            "m32a_policy_sha256": "f" * 64,
            "max_total_hypotheses": hypotheses,
            "max_global_trials": global_trials,
            "max_trials_per_family": family_trials,
            "max_trials_per_hypothesis": 1,
            "max_sealed_oos_consumptions": 1,
            "max_parameter_configurations": 1,
            "max_entry_variants": 1,
            "max_exit_variants": 1,
            "max_universe_variants": 1,
            "max_regime_filter_variants": 1,
            "novelty_policy": {"rejected_duplicates_consume_trial_allocation": True},
            "sealed_oos_policy": {"one_clean_consumption_per_frozen_lineage": True},
            "exact_duplicate_consumes_hypothesis_allocation": True,
            "exact_duplicate_consumes_trial_allocation": True,
            "near_duplicate_consumes_hypothesis_allocation": True,
            "near_duplicate_consumes_trial_allocation": True,
            "provenance": {"source": "unit_test"},
        }
    )


def _ledger(*, global_trials: int = 40, family_trials: int = 20, hypotheses: int = 40) -> dict[str, object]:
    return build_global_experiment_ledger_v1(
        {
            "version": "global_experiment_ledger_request_v1",
            "ledger_id": "FUNDAMENTAL-LEDGER-V1",
            "policy": _policy(global_trials=global_trials, family_trials=family_trials, hypotheses=hypotheses),
            "trials": [],
            "m32a_contract_version": "research_objective_promotion_gate_v1",
            "m32a_policy_sha256": "f" * 64,
            "provenance": {"source": "unit_test"},
        }
    )


def _write_request(tmp_path: Path, ledger: dict[str, object] | None = None) -> tuple[dict[str, object], dict[str, object]]:
    previous = _ledger() if ledger is None else ledger
    eodhd_root = tmp_path / "eodhd"
    fundamental_root = tmp_path / "fundamental"
    eodhd_root.mkdir()
    fundamental_root.mkdir()
    eodhd_manifest = eodhd_root / "dataset_manifest.json"
    fundamental_manifest = fundamental_root / "fundamental_manifest.json"
    spy = eodhd_root / "raw" / "session-proxy" / "spy.json.gz"
    spy.parent.mkdir(parents=True)
    eodhd_manifest.write_text("{}\n", encoding="utf-8")
    fundamental_manifest.write_text("{}\n", encoding="utf-8")
    spy.write_bytes(b"spy")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(previous, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    sec_audit_root = tmp_path / "sec-audit"
    sec_audit_records = []
    for index in range(30):
        raw_path = sec_audit_root / "raw" / f"CIK{index + 1:010d}.json.gz"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(f"sec-{index}".encode())
        sec_audit_records.append(
            {
                "status": "PASS",
                "cik": f"{index + 1:010d}",
                "accession": f"{index + 1:010d}-20-{index + 1:06d}",
                "matched_field_count": 3,
                "raw_response_path": raw_path.relative_to(sec_audit_root).as_posix(),
                "raw_response_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            }
        )
    sec_audit = {
        "version": "massive_sec_filing_audit_v1",
        "status": "PASS",
        "audit_id": "SEC-AUDIT-001",
        "fundamental_manifest_sha256": hashlib.sha256(fundamental_manifest.read_bytes()).hexdigest(),
        "fundamental_canonical_manifest_sha256": "c" * 64,
        "sample_size": 30,
        "minimum_matched_fields_per_record": 3,
        "passed_record_count": 30,
        "failed_record_count": 0,
        "records": sec_audit_records,
        "provider_http_requests_used": 30,
        "sealed_oos_opened": False,
    }
    sec_audit["canonical_audit_sha256"] = hashlib.sha256(
        json.dumps(sec_audit, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sec_audit_path = sec_audit_root / "audit.json"
    sec_audit_path.write_text(json.dumps(sec_audit, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    request = {
        "version": "massive_fundamental_edge_discovery_request_v1",
        "pilot_id": "MASSIVE-FUND-001",
        "eodhd_bundle_root": str(eodhd_root.resolve()),
        "eodhd_manifest_path": str(eodhd_manifest.resolve()),
        "expected_eodhd_manifest_sha256": hashlib.sha256(eodhd_manifest.read_bytes()).hexdigest(),
        "expected_spy_raw_sha256": hashlib.sha256(spy.read_bytes()).hexdigest(),
        "fundamental_bundle_root": str(fundamental_root.resolve()),
        "expected_fundamental_manifest_sha256": hashlib.sha256(fundamental_manifest.read_bytes()).hexdigest(),
        "expected_fundamental_canonical_manifest_sha256": "c" * 64,
        "sec_audit_path": str(sec_audit_path.resolve()),
        "expected_sec_audit_sha256": hashlib.sha256(sec_audit_path.read_bytes()).hexdigest(),
        "expected_sec_audit_canonical_sha256": sec_audit["canonical_audit_sha256"],
        "previous_ledger_path": str(ledger_path.resolve()),
        "expected_previous_ledger_sha256": previous["canonical_ledger_sha256"],
        "output_dir": str((tmp_path / "output").resolve()),
        "discovery_interval": {"start": "2006-01-01", "end": "2018-12-31"},
        "development_interval": {"start": "2019-01-01", "end": "2022-12-31"},
        "sealed_oos_interval": {"dataset_version": "SEALED-US-EQUITY-V1", "start": "2023-01-01", "end": "2026-06-30"},
        "universe": {
            "minimum_price": 5.0,
            "minimum_history_sessions": 252,
            "minimum_median_dollar_volume": 10_000_000.0,
            "maximum_instruments": 1500,
        },
        "costs": {"base_bps_one_way": 15.0, "stress_bps_one_way": 30.0, "severe_bps_one_way": 50.0},
        "provenance": {"source": "operator_approved_local_snapshot"},
    }
    return request, previous


def _price_frame() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2018-12-28", "2019-01-04", "2019-01-07"]), ["AAA", "BBB"]],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame(
        {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1_000_000.0,
         "raw_close": 10.0, "dollar_volume": 10_000_000.0, "eligible": True}, index=index,
    )


def _panel() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2019-01-04"]), ["AAA", "BBB"]], names=["datetime", "instrument"]
    )
    frame = pd.DataFrame(index=index)
    for position, factor_id in enumerate(FACTOR_DEFINITIONS_V1):
        frame[factor_id] = float(position + 1)
    frame["MOM_12_1"] = 1.0
    frame["issuer_cik"] = ["0000000001", "0000000002"]
    frame["open"] = 10.0
    frame["close"] = 10.0
    frame["raw_close"] = 10.0
    frame["eligible"] = True
    return frame[[*FACTOR_DEFINITIONS_V1, "MOM_12_1", "issuer_cik", "open", "close", "raw_close", "eligible"]]


def _screen(*, continuing: int = 1) -> dict[str, object]:
    factors = {}
    for index, factor_id in enumerate(FACTOR_DEFINITIONS_V1):
        factors[factor_id] = {
            "factor_id": factor_id,
            "decision": "FACTOR_CONTINUE" if index < continuing else "FACTOR_STOP",
            "failure_taxonomy": [] if index < continuing else ["NET_CAGR_BELOW_TARGET"],
            "base_net_cagr": 0.15 if index < continuing else -0.01,
            "stress_net_cagr": 0.12 if index < continuing else -0.02,
            "base_max_drawdown": -0.12 if index < continuing else -0.30,
        }
    result = {
        "version": "fundamental_portfolio_screen_v1",
        "status": "COMPLETED",
        "factor_catalog_sha256": build_massive_fundamental_catalog_metadata_v1()["canonical_catalog_sha256"],
        "factor_count": 10,
        "ordered_factor_ids": list(FACTOR_DEFINITIONS_V1),
        "factors": factors,
        "continuing_factor_ids": list(FACTOR_DEFINITIONS_V1)[:continuing],
        "stopped_factor_ids": list(FACTOR_DEFINITIONS_V1)[continuing:],
        "costs": {"base_bps_one_way": 15.0, "stress_bps_one_way": 30.0, "severe_bps_one_way": 50.0},
        "portfolio_size": 15,
        "promotion_authorized": False,
        "sealed_oos_opened": False,
    }
    result["canonical_screen_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, request: dict[str, object], *, coverage: float = 700.0) -> None:
    monkeypatch.setattr(
        pilot_module,
        "load_eodhd_qlib_development_frame_v1",
        lambda _: (_price_frame(), {"dataset_manifest_sha256": request["expected_eodhd_manifest_sha256"], "provider_calls_used": 0, "sealed_oos_rows_read": 0}),
    )
    monkeypatch.setattr(
        pilot_module,
        "load_massive_fundamental_histories_v1",
        lambda root, expected: ({"AAA": {}}, {"fundamental_manifest_sha256": expected, "fundamental_canonical_manifest_sha256": request["expected_fundamental_canonical_manifest_sha256"], "provider_calls_used": 0, "sealed_oos_rows_read": 0}),
    )
    monkeypatch.setattr(
        pilot_module,
        "build_point_in_time_fundamental_factor_panel_v1",
        lambda *args, **kwargs: (
            _panel(),
            {
                "coverage_by_factor": {factor_id: {"minimum": int(coverage), "median": coverage, "maximum": int(coverage)} for factor_id in FACTOR_DEFINITIONS_V1},
                "provider_calls_used": 0,
                "sealed_oos_rows_read": 0,
            },
        ),
    )
    monkeypatch.setattr(pilot_module, "_load_spy_prices", lambda *args, **kwargs: pd.DataFrame({"open": [10.0], "close": [10.0]}, index=pd.to_datetime(["2019-01-07"])))
    monkeypatch.setattr(pilot_module, "run_fundamental_portfolio_screen_v1", lambda *args, **kwargs: _screen())


def test_pilot_accounts_exactly_ten_trials_and_preserves_previous_ledger(tmp_path: Path, monkeypatch) -> None:
    request, previous = _write_request(tmp_path)
    original = copy.deepcopy(previous)
    _patch_pipeline(monkeypatch, request)

    result = run_massive_fundamental_edge_discovery_v1(request)

    assert result["status"] == "FUNDAMENTAL_EDGE_CANDIDATE_FOUND"
    assert result["new_hypothesis_count"] == 10
    assert result["new_trial_count"] == 10
    assert result["attempted_factor_ids"] == list(FACTOR_DEFINITIONS_V1)
    assert result["accounted_factor_ids"] == list(FACTOR_DEFINITIONS_V1)
    assert result["accounting_complete"] is True
    assert len(result["updated_ledger"]["trials"]) == 10
    assert result["updated_ledger"]["sealed_oos_consumptions"] == 0
    assert result["provider_calls_used"] == 0
    assert result["sealed_oos_opened"] is False
    assert previous == original


def test_plan_preflights_ten_attempts_without_loading_economic_data(tmp_path: Path, monkeypatch) -> None:
    request, _ = _write_request(tmp_path)
    monkeypatch.setattr(
        pilot_module,
        "load_eodhd_qlib_development_frame_v1",
        lambda _: pytest.fail("economic data read during plan"),
    )

    plan = build_massive_fundamental_edge_discovery_plan_v1(request)

    assert plan["planned_factor_ids"] == list(FACTOR_DEFINITIONS_V1)
    assert plan["planned_trial_count"] == 10
    assert plan["sec_audit_status"] == "PASS"
    assert plan["provider_calls_used"] == 0
    assert plan["sealed_oos_opened"] is False


def test_insufficient_coverage_stops_before_screen_and_trials(tmp_path: Path, monkeypatch) -> None:
    request, previous = _write_request(tmp_path)
    _patch_pipeline(monkeypatch, request, coverage=499.0)
    monkeypatch.setattr(
        pilot_module,
        "run_fundamental_portfolio_screen_v1",
        lambda *args, **kwargs: pytest.fail("economic trials must not run"),
    )

    result = run_massive_fundamental_edge_discovery_v1(request)

    assert result["status"] == "FUNDAMENTAL_COVERAGE_INSUFFICIENT"
    assert result["new_trial_count"] == 0
    assert result["updated_ledger"] == previous


def test_insufficient_ledger_budget_stops_before_loading_any_data(tmp_path: Path, monkeypatch) -> None:
    request, _ = _write_request(tmp_path, ledger=_ledger(global_trials=9, family_trials=9, hypotheses=9))
    monkeypatch.setattr(
        pilot_module,
        "load_eodhd_qlib_development_frame_v1",
        lambda _: pytest.fail("data read before ledger preflight"),
    )

    with pytest.raises(ValueError, match="LEDGER_BINDING_FAILED"):
        run_massive_fundamental_edge_discovery_v1(request)


def test_request_hash_drift_and_existing_output_fail_closed(tmp_path: Path) -> None:
    request, _ = _write_request(tmp_path)
    request["expected_eodhd_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest hash"):
        run_massive_fundamental_edge_discovery_v1(request)

    second_root = tmp_path / "second"
    second_root.mkdir()
    request, _ = _write_request(second_root)
    Path(str(request["output_dir"])).mkdir()
    with pytest.raises(ValueError, match="output_dir"):
        run_massive_fundamental_edge_discovery_v1(request)


def test_failed_or_tampered_sec_audit_stops_before_economic_data(tmp_path: Path, monkeypatch) -> None:
    request, _ = _write_request(tmp_path)
    audit_path = Path(str(request["sec_audit_path"]))
    audit = json.loads(audit_path.read_text())
    audit["status"] = "FAIL"
    audit_path.write_text(json.dumps(audit, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    request["expected_sec_audit_sha256"] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        pilot_module,
        "load_eodhd_qlib_development_frame_v1",
        lambda _: pytest.fail("economic data read before SEC audit gate"),
    )

    with pytest.raises(ValueError, match="SEC_AUDIT_BINDING_FAILED"):
        run_massive_fundamental_edge_discovery_v1(request)


def test_tampered_sec_raw_response_stops_before_economic_data(tmp_path: Path, monkeypatch) -> None:
    request, _ = _write_request(tmp_path)
    audit = json.loads(Path(str(request["sec_audit_path"])).read_text())
    raw_path = Path(str(request["sec_audit_path"])).parent / audit["records"][0]["raw_response_path"]
    raw_path.write_bytes(b"tampered")
    monkeypatch.setattr(
        pilot_module,
        "load_eodhd_qlib_development_frame_v1",
        lambda _: pytest.fail("economic data read before SEC raw audit verification"),
    )

    with pytest.raises(ValueError, match="SEC_AUDIT_BINDING_FAILED"):
        run_massive_fundamental_edge_discovery_v1(request)
