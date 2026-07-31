from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import research_lab.research.real_qlib_eodhd_edge_discovery_pilot_v1 as pilot_module
from research_lab.research.global_experiment_ledger_v1 import (
    build_global_experiment_ledger_policy_v1,
    build_global_experiment_ledger_v1,
)
from research_lab.research.price_volume_factor_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    build_price_volume_factor_catalog_metadata_v1,
)
from research_lab.research.real_qlib_eodhd_edge_discovery_pilot_v1 import (
    run_real_qlib_eodhd_edge_discovery_pilot_v1,
)


FACTOR_IDS = tuple(FACTOR_DEFINITIONS_V1)


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _policy(*, global_trials: int = 40, family_trials: int = 20, hypotheses: int = 40) -> dict[str, object]:
    return build_global_experiment_ledger_policy_v1(
        {
            "version": "global_experiment_ledger_policy_request_v1",
            "policy_id": "QLIB-PV-POLICY-V1",
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
            "ledger_id": "QLIB-PV-LEDGER-V1",
            "policy": _policy(
                global_trials=global_trials,
                family_trials=family_trials,
                hypotheses=hypotheses,
            ),
            "trials": [],
            "m32a_contract_version": "research_objective_promotion_gate_v1",
            "m32a_policy_sha256": "f" * 64,
            "provenance": {"source": "unit_test"},
        }
    )


def _write_request(tmp_path: Path, ledger: dict[str, object] | None = None) -> tuple[dict[str, object], dict[str, object]]:
    previous = _ledger() if ledger is None else ledger
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps(previous, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    request = {
        "version": "real_qlib_eodhd_edge_discovery_request_v1",
        "pilot_id": "QLIB-PV-001",
        "dataset_manifest_path": str(manifest_path.resolve()),
        "expected_dataset_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "previous_ledger_path": str(ledger_path.resolve()),
        "expected_previous_ledger_sha256": previous["canonical_ledger_sha256"],
        "output_dir": str((tmp_path / "output").resolve()),
        "discovery_interval": {"start": "2006-01-01", "end": "2018-12-31"},
        "development_interval": {"start": "2019-01-01", "end": "2022-12-31"},
        "sealed_oos_interval": {
            "dataset_version": "SEALED-PV-V1",
            "start": "2023-01-01",
            "end": "2026-06-30",
        },
        "universe": {
            "minimum_price": 5.0,
            "minimum_history_sessions": 252,
            "minimum_median_dollar_volume": 10_000_000.0,
            "maximum_instruments": 1500,
        },
        "costs": {
            "base_bps_one_way": 15.0,
            "stress_bps_one_way": 30.0,
            "severe_bps_one_way": 50.0,
        },
        "provenance": {"source": "operator_approved_local_snapshot"},
    }
    return request, previous


def _source_frame() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.to_datetime(["2010-01-04", "2010-01-05", "2020-01-06", "2020-01-07"]), ["AAA", "BBB"]],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame(
        {
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 1_000_000.0,
            "raw_close": 10.0,
            "dollar_volume": 10_000_000.0,
            "eligible": True,
        },
        index=index,
    )


def _factor_frame() -> pd.DataFrame:
    source = _source_frame()
    result = pd.DataFrame(index=source.index)
    for index, factor_id in enumerate(FACTOR_IDS):
        result[factor_id] = float(index + 1)
    result["forward_return_5d"] = 0.01
    result["eligible"] = source["eligible"]
    return result


def _dataset_metadata(manifest_sha256: str) -> dict[str, object]:
    result: dict[str, object] = {
        "version": "eodhd_qlib_dataset_metadata_v1",
        "dataset_id": "EODHD-UNIT",
        "dataset_manifest_sha256": manifest_sha256,
        "provider_calls_used": 0,
        "sealed_oos_rows_read": 0,
    }
    result["canonical_metadata_sha256"] = _sha(result)
    return result


def _screen(*, continuing: int = 1) -> dict[str, object]:
    catalog = build_price_volume_factor_catalog_metadata_v1()
    factors = {
        factor_id: {
            "factor_id": factor_id,
            "decision": "FACTOR_CONTINUE" if index < continuing else "FACTOR_STOP",
            "failure_taxonomy": [] if index < continuing else ["INSUFFICIENT_EDGE_MAGNITUDE"],
            "median_rank_ic": 0.02 if index < continuing else 0.0,
            "stress_net_spread": 0.001 if index < continuing else -0.001,
        }
        for index, factor_id in enumerate(FACTOR_IDS)
    }
    result: dict[str, object] = {
        "version": "qlib_factor_screen_v1",
        "status": "COMPLETED",
        "factor_catalog_sha256": catalog["canonical_catalog_sha256"],
        "factor_count": 8,
        "ordered_factor_ids": list(FACTOR_IDS),
        "weekly_observation_count": 104,
        "maximum_observation_date": "2022-12-30",
        "costs": {
            "base_bps_one_way": 15.0,
            "stress_bps_one_way": 30.0,
            "severe_bps_one_way": 50.0,
        },
        "factors": factors,
        "sector_concentration_evaluated": False,
        "promotion_authorized": False,
        "sealed_oos_opened": False,
    }
    result["canonical_screen_sha256"] = _sha(result)
    return result


class FakeRuntime:
    is_real_qlib = True
    qlib_version = "0.9.7"

    def __init__(self, *, alter_prepared_value: bool = False) -> None:
        self.alter_prepared_value = alter_prepared_value

    def prepare_segments(
        self,
        frame: pd.DataFrame,
        *,
        feature_columns: tuple[str, ...],
        label_column: str,
        segments: dict[str, tuple[str, str]],
    ) -> dict[str, pd.DataFrame]:
        dates = frame.index.get_level_values("datetime")
        result = {
            name: frame.loc[
                (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end)),
                [*feature_columns, label_column],
            ].copy()
            for name, (start, end) in segments.items()
        }
        if self.alter_prepared_value:
            result["development"].iloc[0, 0] += 1.0
        return result


class UnavailableRuntime:
    is_real_qlib = False
    qlib_version = "UNAVAILABLE"


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, request: dict[str, object]) -> None:
    monkeypatch.setattr(
        pilot_module,
        "load_eodhd_qlib_development_frame_v1",
        lambda loader_request: (_source_frame(), _dataset_metadata(request["expected_dataset_manifest_sha256"])),
    )
    monkeypatch.setattr(pilot_module, "compute_price_volume_factor_frame_v1", lambda source: _factor_frame())
    monkeypatch.setattr(
        pilot_module,
        "run_qlib_factor_screen_v1",
        lambda development, *, factor_metadata, costs: _screen(),
    )


def test_pilot_registers_eight_hypotheses_and_trials_without_mutating_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, previous = _write_request(tmp_path)
    original = copy.deepcopy(previous)
    _patch_pipeline(monkeypatch, request)

    result = run_real_qlib_eodhd_edge_discovery_pilot_v1(request, runtime=FakeRuntime())

    assert result["status"] == "EDGE_CANDIDATE_FOUND"
    assert result["new_hypothesis_count"] == 8
    assert result["new_trial_count"] == 8
    assert [trial["experiment_id"] for trial in result["new_trials"]] == [
        f"QLIB-PV-001-{factor_id}" for factor_id in FACTOR_IDS
    ]
    assert len(result["updated_ledger"]["hypotheses"]) == 8
    assert previous == original


def test_each_trial_binds_dataset_catalog_intervals_costs_and_factor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _ = _write_request(tmp_path)
    _patch_pipeline(monkeypatch, request)

    result = run_real_qlib_eodhd_edge_discovery_pilot_v1(request, runtime=FakeRuntime())

    for factor_id, trial in zip(FACTOR_IDS, result["new_trials"]):
        assert trial["parent_hypothesis_id"] == f"H-QLIB-PV-001-{factor_id}"
        assert trial["data_snapshot_hashes"] == [request["expected_dataset_manifest_sha256"]]
        assert trial["train_interval"] == request["discovery_interval"]
        assert trial["validation_interval"] == request["development_interval"]
        assert trial["sealed_oos_interval"] == request["sealed_oos_interval"]
        assert trial["parameter_configuration"]["factor_id"] == factor_id
        assert trial["transaction_cost_model"] == "ONE_WAY_BPS_15_30_50"


@pytest.mark.parametrize("capacity", ["hash", "global", "family", "hypothesis"])
def test_rejects_wrong_ledger_hash_or_insufficient_budget_before_dataset_load(
    capacity: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(
        global_trials=7 if capacity == "global" else 40,
        family_trials=7 if capacity == "family" else 20,
        hypotheses=7 if capacity == "hypothesis" else 40,
    )
    request, _ = _write_request(tmp_path, ledger)
    if capacity == "hash":
        request["expected_previous_ledger_sha256"] = "0" * 64
    monkeypatch.setattr(
        pilot_module,
        "load_eodhd_qlib_development_frame_v1",
        lambda loader_request: pytest.fail("dataset load must not occur before ledger preflight"),
    )

    with pytest.raises(ValueError, match="LEDGER_BINDING_FAILED"):
        run_real_qlib_eodhd_edge_discovery_pilot_v1(request, runtime=FakeRuntime())


def test_rejects_any_previous_sealed_oos_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger()
    ledger["sealed_oos_consumption_records"] = [{"trial_id": "OLD"}]
    ledger.pop("canonical_ledger_sha256")
    ledger["canonical_ledger_sha256"] = _sha(ledger)
    request, _ = _write_request(tmp_path, ledger)

    with pytest.raises(ValueError, match="SEALED_OOS_CONTAMINATION"):
        run_real_qlib_eodhd_edge_discovery_pilot_v1(request, runtime=FakeRuntime())


def test_unavailable_runtime_returns_fail_closed_without_writes(tmp_path: Path) -> None:
    request, previous = _write_request(tmp_path)

    result = run_real_qlib_eodhd_edge_discovery_pilot_v1(request, runtime=UnavailableRuntime())

    assert result["status"] == "QLIB_RUNTIME_UNAVAILABLE"
    assert result["new_hypothesis_count"] == 0
    assert result["new_trial_count"] == 0
    assert result["updated_ledger"] == previous
    assert not Path(request["output_dir"]).exists()


def test_preparation_parity_failure_prevents_ledger_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, previous = _write_request(tmp_path)
    _patch_pipeline(monkeypatch, request)

    result = run_real_qlib_eodhd_edge_discovery_pilot_v1(
        request,
        runtime=FakeRuntime(alter_prepared_value=True),
    )

    assert result["status"] == "QLIB_PREPARATION_PARITY_FAILED"
    assert result["new_hypothesis_count"] == 0
    assert result["new_trial_count"] == 0
    assert result["updated_ledger"] == previous


def test_success_has_zero_forbidden_action_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _ = _write_request(tmp_path)
    _patch_pipeline(monkeypatch, request)

    result = run_real_qlib_eodhd_edge_discovery_pilot_v1(request, runtime=FakeRuntime())

    assert result["provider_calls_used"] == 0
    assert result["broker_calls_used"] == 0
    assert result["registry_write_performed"] is False
    assert result["deployment_performed"] is False
    assert result["knihomol_used"] is False
    assert result["rd_agent_used"] is False
    assert result["sealed_oos_opened"] is False
