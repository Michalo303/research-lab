from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from research_lab.research.edge_discovery_scorecard_v1 import build_edge_discovery_scorecard_v1
from research_lab.research.eodhd_qlib_dataset_v1 import load_eodhd_qlib_development_frame_v1
from research_lab.research.global_experiment_ledger_v1 import apply_global_experiment_ledger_operation_v1
from research_lab.research.price_volume_factor_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    build_price_volume_factor_catalog_metadata_v1,
    compute_price_volume_factor_frame_v1,
)
from research_lab.research.qlib_factor_screen_v1 import run_qlib_factor_screen_v1
from research_lab.research.real_qlib_runtime_v1 import (
    QlibPreparationParityError,
    build_real_qlib_preparation_parity_v1,
    build_real_qlib_runtime_metadata_v1,
    prepare_real_qlib_segments_v1,
)


REQUEST_VERSION = "real_qlib_eodhd_edge_discovery_request_v1"
PILOT_VERSION = "real_qlib_eodhd_edge_discovery_pilot_v1"
QLIB_RUNTIME_UNAVAILABLE = "QLIB_RUNTIME_UNAVAILABLE"
QLIB_PREPARATION_PARITY_FAILED = "QLIB_PREPARATION_PARITY_FAILED"
LEDGER_BINDING_FAILED = "LEDGER_BINDING_FAILED"
SEALED_OOS_CONTAMINATION = "SEALED_OOS_CONTAMINATION"
STRATEGY_FAMILY_ID = "PRICE_VOLUME_FACTOR"

_REQUEST_FIELDS = {
    "version",
    "pilot_id",
    "dataset_manifest_path",
    "expected_dataset_manifest_sha256",
    "previous_ledger_path",
    "expected_previous_ledger_sha256",
    "output_dir",
    "discovery_interval",
    "development_interval",
    "sealed_oos_interval",
    "universe",
    "costs",
    "provenance",
}
_EXPECTED_UNIVERSE = {
    "minimum_price": 5.0,
    "minimum_history_sessions": 252,
    "minimum_median_dollar_volume": 10_000_000.0,
    "maximum_instruments": 1500,
}
_EXPECTED_COSTS = {
    "base_bps_one_way": 15.0,
    "stress_bps_one_way": 30.0,
    "severe_bps_one_way": 50.0,
}
_SAFETY = {
    "provider_calls_used": 0,
    "broker_calls_used": 0,
    "registry_write_performed": False,
    "deployment_performed": False,
    "knihomol_used": False,
    "rd_agent_used": False,
    "sealed_oos_opened": False,
}


def run_real_qlib_eodhd_edge_discovery_pilot_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Run one development-only Qlib price/volume pilot without writing files."""

    validated = _validate_request(request)
    previous_ledger = _read_and_preflight_ledger(validated)
    runtime_metadata = build_real_qlib_runtime_metadata_v1()
    if runtime_metadata["is_real_qlib"] is not True:
        return _fail_closed_result(
            status=QLIB_RUNTIME_UNAVAILABLE,
            pilot_id=validated["pilot_id"],
            previous_ledger=previous_ledger,
            runtime_metadata=runtime_metadata,
        )

    loader_request = {
        "version": "eodhd_qlib_development_frame_request_v1",
        "manifest_path": validated["dataset_manifest_path"],
        "expected_manifest_sha256": validated["expected_dataset_manifest_sha256"],
        "discovery_interval": copy.deepcopy(validated["discovery_interval"]),
        "development_interval": copy.deepcopy(validated["development_interval"]),
        "sealed_oos_interval": copy.deepcopy(validated["sealed_oos_interval"]),
        "universe": copy.deepcopy(validated["universe"]),
        "provenance": copy.deepcopy(validated["provenance"]),
    }
    source_frame, dataset_metadata = load_eodhd_qlib_development_frame_v1(loader_request)
    if (
        dataset_metadata.get("dataset_manifest_sha256") != validated["expected_dataset_manifest_sha256"]
        or dataset_metadata.get("provider_calls_used") != 0
        or dataset_metadata.get("sealed_oos_rows_read") != 0
    ):
        raise ValueError("DATASET_VALIDATION_FAILED")
    factor_frame = compute_price_volume_factor_frame_v1(source_frame)
    feature_columns = tuple(FACTOR_DEFINITIONS_V1)
    segments = {
        "discovery": (
            validated["discovery_interval"]["start"],
            validated["discovery_interval"]["end"],
        ),
        "development": (
            validated["development_interval"]["start"],
            validated["development_interval"]["end"],
        ),
    }
    prepared = prepare_real_qlib_segments_v1(
        factor_frame,
        feature_columns=feature_columns,
        label_column="forward_return_5d",
        segments=segments,
    )
    try:
        parity = build_real_qlib_preparation_parity_v1(
            factor_frame,
            prepared,
            feature_columns=feature_columns,
            label_column="forward_return_5d",
            segments=segments,
        )
    except QlibPreparationParityError:
        return _fail_closed_result(
            status=QLIB_PREPARATION_PARITY_FAILED,
            pilot_id=validated["pilot_id"],
            previous_ledger=previous_ledger,
            runtime_metadata=runtime_metadata,
        )
    development = prepared["development"].copy()
    eligibility = factor_frame.loc[development.index, "eligible"]
    if not eligibility.index.equals(development.index) or eligibility.isna().any():
        return _fail_closed_result(
            status=QLIB_PREPARATION_PARITY_FAILED,
            pilot_id=validated["pilot_id"],
            previous_ledger=previous_ledger,
            runtime_metadata=runtime_metadata,
        )
    development["eligible"] = eligibility.astype(bool)
    catalog_metadata = build_price_volume_factor_catalog_metadata_v1()
    factor_screen = run_qlib_factor_screen_v1(
        development,
        factor_metadata=catalog_metadata,
        costs=validated["costs"],
    )

    updated_ledger = copy.deepcopy(previous_ledger)
    hypothesis_ids: list[str] = []
    experiment_ids: list[str] = []
    accounted_factor_ids: list[str] = []
    for factor_id in feature_columns:
        hypothesis_id = f"H-{validated['pilot_id']}-{factor_id}"
        experiment_id = f"{validated['pilot_id']}-{factor_id}"
        semantic_fingerprint = _canonical_sha256(
            {
                "version": PILOT_VERSION,
                "factor_id": factor_id,
                "definition": FACTOR_DEFINITIONS_V1[factor_id],
                "universe": validated["universe"],
                "execution": "weekly_next_session_open_to_fifth_future_close",
            }
        )
        hypothesis = _build_hypothesis(
            hypothesis_id=hypothesis_id,
            factor_id=factor_id,
            semantic_fingerprint=semantic_fingerprint,
            dataset_sha256=validated["expected_dataset_manifest_sha256"],
            catalog_sha256=catalog_metadata["canonical_catalog_sha256"],
            screen_sha256=factor_screen["canonical_screen_sha256"],
            pilot_id=validated["pilot_id"],
        )
        try:
            updated_ledger = _apply_operation(
                updated_ledger,
                operation={
                    "operation_id": f"OP-{validated['pilot_id']}-REGISTER-{factor_id}",
                    "kind": "REGISTER_HYPOTHESIS",
                    "hypothesis": hypothesis,
                },
            )
        except ValueError:
            return _accounting_failure_result(
                validated=validated,
                previous_ledger=previous_ledger,
                updated_ledger=updated_ledger,
                runtime_metadata=runtime_metadata,
                dataset_metadata=dataset_metadata,
                parity=parity,
                factor_screen=factor_screen,
                hypothesis_ids=hypothesis_ids,
                experiment_ids=experiment_ids,
                attempted_factor_ids=list(feature_columns),
                accounted_factor_ids=accounted_factor_ids,
            )
        hypothesis_ids.append(hypothesis_id)
        factor_result = factor_screen["factors"][factor_id]
        trial_status = (
            "WALK_FORWARD_COMPLETE"
            if factor_result["decision"] == "FACTOR_CONTINUE"
            else "STRATEGY_GATE_FAIL"
        )
        trial = _build_trial(
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            factor_id=factor_id,
            semantic_fingerprint=semantic_fingerprint,
            factor_result=factor_result,
            trial_status=trial_status,
            request=validated,
            catalog_metadata=catalog_metadata,
            factor_screen=factor_screen,
        )
        try:
            updated_ledger = _apply_operation(
                updated_ledger,
                operation={
                    "operation_id": f"OP-{validated['pilot_id']}-APPEND-{factor_id}",
                    "kind": "APPEND_TRIAL",
                    "trial": trial,
                },
            )
        except ValueError:
            return _accounting_failure_result(
                validated=validated,
                previous_ledger=previous_ledger,
                updated_ledger=updated_ledger,
                runtime_metadata=runtime_metadata,
                dataset_metadata=dataset_metadata,
                parity=parity,
                factor_screen=factor_screen,
                hypothesis_ids=hypothesis_ids,
                experiment_ids=experiment_ids,
                attempted_factor_ids=list(feature_columns),
                accounted_factor_ids=accounted_factor_ids,
            )
        experiment_ids.append(experiment_id)
        accounted_factor_ids.append(factor_id)

    trial_lookup = {trial["experiment_id"]: trial for trial in updated_ledger["trials"]}
    new_trials = [copy.deepcopy(trial_lookup[experiment_id]) for experiment_id in experiment_ids]
    hypothesis_lookup = {
        hypothesis["hypothesis_id"]: hypothesis for hypothesis in updated_ledger.get("hypotheses", [])
    }
    new_hypotheses = [copy.deepcopy(hypothesis_lookup[hypothesis_id]) for hypothesis_id in hypothesis_ids]
    prior_trial_count = len(previous_ledger.get("trials", []))
    ledger_summary = {
        "prior_trial_count": prior_trial_count,
        "updated_trial_count": len(updated_ledger["trials"]),
        "new_trial_count": len(new_trials),
        "new_hypothesis_count": len(new_hypotheses),
        "new_experiment_ids": experiment_ids,
        "new_hypothesis_ids": hypothesis_ids,
        "new_trial_statuses_by_factor": {
            factor_id: trial_lookup[f"{validated['pilot_id']}-{factor_id}"]["trial_status"]
            for factor_id in feature_columns
        },
        "dataset_manifest_sha256": validated["expected_dataset_manifest_sha256"],
        "updated_ledger_sha256": updated_ledger["canonical_ledger_sha256"],
        "sealed_oos_consumptions": 0,
    }
    scorecard = build_edge_discovery_scorecard_v1(
        factor_screen,
        qlib_runtime_metadata=runtime_metadata,
        preparation_parity=parity,
        ledger_summary=ledger_summary,
    )
    result: dict[str, object] = {
        "version": PILOT_VERSION,
        "pilot_id": validated["pilot_id"],
        "status": scorecard["status"],
        "dataset_metadata": copy.deepcopy(dataset_metadata),
        "qlib_runtime": runtime_metadata,
        "reference_parity": parity,
        "factor_screen": factor_screen,
        "economic_scorecard": scorecard,
        "updated_ledger": updated_ledger,
        "new_hypotheses": new_hypotheses,
        "new_trials": new_trials,
        "new_hypothesis_count": 8,
        "new_trial_count": 8,
        "attempted_factor_ids": list(feature_columns),
        "accounted_factor_ids": accounted_factor_ids,
        "accounting_complete": True,
        **copy.deepcopy(_SAFETY),
    }
    result["canonical_result_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _REQUEST_FIELDS:
        raise ValueError("request fields are invalid.")
    if raw.get("version") != REQUEST_VERSION:
        raise ValueError("request version is invalid.")
    pilot_id = _text(raw.get("pilot_id"), "pilot_id")
    paths: dict[str, str] = {}
    for field in ("dataset_manifest_path", "previous_ledger_path", "output_dir"):
        path = Path(_text(raw.get(field), field))
        if not path.is_absolute():
            raise ValueError(f"{field} must be absolute.")
        paths[field] = str(path)
    manifest_path = Path(paths["dataset_manifest_path"])
    ledger_path = Path(paths["previous_ledger_path"])
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("dataset_manifest_path is invalid.")
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ValueError(LEDGER_BINDING_FAILED)
    output_path = Path(paths["output_dir"])
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("output_dir must not exist.")
    repository_root = Path(__file__).resolve().parents[2]
    try:
        output_path.resolve().relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise ValueError("output_dir must be outside the repository.")
    manifest_sha256 = _required_sha256(
        raw.get("expected_dataset_manifest_sha256"), "expected_dataset_manifest_sha256"
    )
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_sha256:
        raise ValueError("dataset manifest hash mismatch.")
    ledger_sha256 = _required_sha256(
        raw.get("expected_previous_ledger_sha256"), "expected_previous_ledger_sha256"
    )
    discovery = _interval(raw.get("discovery_interval"), "discovery_interval")
    development = _interval(raw.get("development_interval"), "development_interval")
    sealed = _sealed_interval(raw.get("sealed_oos_interval"))
    if pd.Timestamp(discovery["end"]) >= pd.Timestamp(development["start"]):
        raise ValueError("intervals overlap or are out of order.")
    if pd.Timestamp(development["end"]) >= pd.Timestamp(sealed["start"]):
        raise ValueError("intervals overlap or are out of order.")
    if raw.get("universe") != _EXPECTED_UNIVERSE:
        raise ValueError("universe must equal the frozen V1 policy.")
    if raw.get("costs") != _EXPECTED_COSTS:
        raise ValueError("costs must equal the frozen V1 policy.")
    if raw.get("provenance") != {"source": "operator_approved_local_snapshot"}:
        raise ValueError("provenance is invalid.")
    return {
        "version": REQUEST_VERSION,
        "pilot_id": pilot_id,
        **paths,
        "expected_dataset_manifest_sha256": manifest_sha256,
        "expected_previous_ledger_sha256": ledger_sha256,
        "discovery_interval": discovery,
        "development_interval": development,
        "sealed_oos_interval": sealed,
        "universe": copy.deepcopy(_EXPECTED_UNIVERSE),
        "costs": copy.deepcopy(_EXPECTED_COSTS),
        "provenance": {"source": "operator_approved_local_snapshot"},
    }


def _read_and_preflight_ledger(request: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(Path(request["previous_ledger_path"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(LEDGER_BINDING_FAILED) from exc
    if not isinstance(raw, dict):
        raise ValueError(LEDGER_BINDING_FAILED)
    declared = raw.get("canonical_ledger_sha256")
    expected = request["expected_previous_ledger_sha256"]
    if declared != expected or _canonical_sha256(
        {key: value for key, value in raw.items() if key != "canonical_ledger_sha256"}
    ) != expected:
        raise ValueError(LEDGER_BINDING_FAILED)
    policy = raw.get("policy")
    trials = raw.get("trials")
    hypotheses = raw.get("hypotheses", [])
    if not isinstance(policy, dict) or not isinstance(trials, list) or not isinstance(hypotheses, list):
        raise ValueError(LEDGER_BINDING_FAILED)
    if _sealed_oos_contaminates_request(raw, request):
        raise ValueError(SEALED_OOS_CONTAMINATION)
    family_count = sum(trial.get("strategy_family_id") == STRATEGY_FAMILY_ID for trial in trials)
    if (
        policy.get("max_global_trials", 0) - len(trials) < 8
        or policy.get("max_trials_per_family", 0) - family_count < 8
        or policy.get("max_total_hypotheses", 0) - len(hypotheses) < 8
    ):
        raise ValueError(LEDGER_BINDING_FAILED)
    return copy.deepcopy(raw)


def _sealed_oos_contaminates_request(
    ledger: dict[str, Any],
    request: dict[str, Any],
) -> bool:
    records = ledger.get("sealed_oos_consumption_records", [])
    trials = ledger.get("trials", [])
    if not isinstance(records, list) or not isinstance(trials, list):
        return True
    trial_lookup: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        if not isinstance(trial, dict) or not isinstance(trial.get("experiment_id"), str):
            return True
        trial_lookup.setdefault(trial["experiment_id"], []).append(trial)
    sealed = request["sealed_oos_interval"]
    recorded_trial_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("trial_id"), str):
            return True
        matches = trial_lookup.get(record["trial_id"], [])
        if len(matches) != 1:
            return True
        trial = matches[0]
        recorded_trial_ids.add(record["trial_id"])
        if trial.get("strategy_family_id") == STRATEGY_FAMILY_ID:
            return True
        if record.get("dataset_version") == sealed["dataset_version"]:
            return True
        if (
            record.get("interval_start") == sealed["start"]
            and record.get("interval_end") == sealed["end"]
        ):
            return True
    consumed_statuses = {"SEALED_OOS_CONSUMED", "SEALED_OOS_CONTAMINATED"}
    for trial in trials:
        consumed = (
            trial.get("sealed_oos_consumption_state") == "CONSUMED"
            or trial.get("trial_status") in consumed_statuses
        )
        if not consumed:
            continue
        if trial.get("strategy_family_id") == STRATEGY_FAMILY_ID:
            return True
        if trial["experiment_id"] not in recorded_trial_ids:
            return True
    return False


def _build_hypothesis(
    *,
    hypothesis_id: str,
    factor_id: str,
    semantic_fingerprint: str,
    dataset_sha256: str,
    catalog_sha256: str,
    screen_sha256: str,
    pilot_id: str,
) -> dict[str, object]:
    return {
        "hypothesis_id": hypothesis_id,
        "strategy_family_id": STRATEGY_FAMILY_ID,
        "parent_hypothesis_ids": [],
        "parent_failure_ids": [],
        "evidence_hashes": [dataset_sha256, catalog_sha256, screen_sha256],
        "economic_mechanism_fingerprint": f"price volume factor {factor_id}",
        "semantic_strategy_fingerprint": semantic_fingerprint,
        "market_scope": "US_COMMON_STOCKS",
        "instrument_classes": ["COMMON_STOCK"],
        "timeframe": "1D_WEEKLY_REBALANCE",
        "novelty_basis": f"frozen transparent factor definition {factor_id}",
        "expected_failure_modes": [
            "COST_FRAGILITY",
            "RANK_IC_INSTABILITY",
            "YEAR_CONCENTRATION",
            "INSTRUMENT_CONCENTRATION",
        ],
        "provenance": {"source": PILOT_VERSION, "pilot_id": pilot_id, "factor_id": factor_id},
    }


def _build_trial(
    *,
    experiment_id: str,
    hypothesis_id: str,
    factor_id: str,
    semantic_fingerprint: str,
    factor_result: dict[str, object],
    trial_status: str,
    request: dict[str, Any],
    catalog_metadata: dict[str, object],
    factor_screen: dict[str, object],
) -> dict[str, object]:
    configuration = {
        "factor_id": factor_id,
        "definition": copy.deepcopy(FACTOR_DEFINITIONS_V1[factor_id]),
        "costs": copy.deepcopy(request["costs"]),
        "weekly_rebalance": True,
        "label": "next_session_open_to_fifth_future_close",
    }
    material_fingerprint = _canonical_sha256(
        {
            "semantic_strategy_fingerprint": semantic_fingerprint,
            "parameter_configuration": configuration,
            "dataset_manifest_sha256": request["expected_dataset_manifest_sha256"],
            "intervals": {
                "discovery": request["discovery_interval"],
                "development": request["development_interval"],
                "sealed": request["sealed_oos_interval"],
            },
        }
    )
    return {
        "experiment_id": experiment_id,
        "strategy_family_id": STRATEGY_FAMILY_ID,
        "strategy_fingerprint": semantic_fingerprint,
        "parent_hypothesis_id": hypothesis_id,
        "parent_trial_ids": [],
        "parent_failure_ids": [],
        "evidence_hashes": [
            request["expected_dataset_manifest_sha256"],
            catalog_metadata["canonical_catalog_sha256"],
            factor_screen["canonical_screen_sha256"],
        ],
        "economic_mechanism_fingerprint": f"price volume factor {factor_id}",
        "universe_variant": "US_COMMON_STOCK_PIT_LIQUID_1500",
        "screener_variant": "FROZEN_EIGHT_FACTOR_V1",
        "ranking_variant": factor_id,
        "entry_variant": "WEEKLY_SIGNAL_NEXT_SESSION_OPEN",
        "exit_variant": "FIFTH_FUTURE_CLOSE_LABEL_ONLY",
        "sizing_variant": "TOP_QUINTILE_EQUAL_WEIGHT_DIAGNOSTIC",
        "regime_filter_variant": "NONE",
        "parameter_configuration": configuration,
        "parameter_space_sha256": _canonical_sha256(configuration),
        "data_snapshot_hashes": [request["expected_dataset_manifest_sha256"]],
        "train_interval": copy.deepcopy(request["discovery_interval"]),
        "validation_interval": copy.deepcopy(request["development_interval"]),
        "walk_forward_intervals": [copy.deepcopy(request["development_interval"])],
        "sealed_oos_interval": copy.deepcopy(request["sealed_oos_interval"]),
        "sealed_oos_consumption_state": "UNCONSUMED",
        "transaction_cost_model": "ONE_WAY_BPS_15_30_50",
        "slippage_model": "INCLUDED_IN_COST_SCENARIOS",
        "trial_status": trial_status,
        "metrics": copy.deepcopy(factor_result),
        "failure_taxonomy": list(factor_result["failure_taxonomy"]),
        "novelty_justification": {
            "kind": "FROZEN_FACTOR_MECHANISM",
            "failure_mechanism_evidence": [],
        },
        "canonical_trial_fingerprint": material_fingerprint,
        "provenance": {"source": PILOT_VERSION, "pilot_id": request["pilot_id"], "factor_id": factor_id},
    }


def _apply_operation(previous: dict[str, object], *, operation: dict[str, object]) -> dict[str, object]:
    try:
        return apply_global_experiment_ledger_operation_v1(
            {
                "version": "global_experiment_ledger_operation_request_v1",
                "previous_ledger": previous,
                "previous_ledger_sha256": previous["canonical_ledger_sha256"],
                "operation": operation,
                "provenance": {"source": PILOT_VERSION},
            }
        )
    except ValueError as exc:
        raise ValueError(LEDGER_BINDING_FAILED) from exc


def _accounting_failure_result(
    *,
    validated: dict[str, Any],
    previous_ledger: dict[str, object],
    updated_ledger: dict[str, object],
    runtime_metadata: dict[str, object],
    dataset_metadata: dict[str, object],
    parity: dict[str, object],
    factor_screen: dict[str, object],
    hypothesis_ids: list[str],
    experiment_ids: list[str],
    attempted_factor_ids: list[str],
    accounted_factor_ids: list[str],
) -> dict[str, object]:
    """Preserve observed attempts when ledger accounting fails partway through."""

    hypothesis_lookup = {
        item["hypothesis_id"]: item for item in updated_ledger.get("hypotheses", [])
    }
    trial_lookup = {item["experiment_id"]: item for item in updated_ledger.get("trials", [])}
    new_hypotheses = [
        copy.deepcopy(hypothesis_lookup[item]) for item in hypothesis_ids if item in hypothesis_lookup
    ]
    new_trials = [copy.deepcopy(trial_lookup[item]) for item in experiment_ids if item in trial_lookup]
    factor_metrics: dict[str, object] = {}
    for factor_id in attempted_factor_ids:
        metrics = copy.deepcopy(factor_screen["factors"][factor_id])
        metrics["decision"] = "FACTOR_STOP"
        metrics["failure_taxonomy"] = sorted(
            set(metrics.get("failure_taxonomy", [])) | {LEDGER_BINDING_FAILED}
        )
        metrics["ledger_trial_status"] = (
            trial_lookup[f"{validated['pilot_id']}-{factor_id}"]["trial_status"]
            if factor_id in accounted_factor_ids
            else "UNACCOUNTED_DUE_TO_LEDGER_FAILURE"
        )
        factor_metrics[factor_id] = metrics
    economic_scorecard: dict[str, object] = {
        "version": "edge_discovery_scorecard_v1",
        "status": LEDGER_BINDING_FAILED,
        "next_authorized_milestone": "MANUAL_ACCOUNTING_REVIEW_REQUIRED",
        "factor_screen_sha256": factor_screen["canonical_screen_sha256"],
        "dataset_manifest_sha256": validated["expected_dataset_manifest_sha256"],
        "updated_ledger_sha256": updated_ledger["canonical_ledger_sha256"],
        "factor_metrics": factor_metrics,
        "continuing_factor_ids": [],
        "stopped_factor_ids": list(attempted_factor_ids),
        "attempted_factor_ids": list(attempted_factor_ids),
        "accounted_factor_ids": list(accounted_factor_ids),
        "accounting_complete": False,
        "promotion_authorized": False,
        "production_runtime_supported": False,
        "sealed_oos_opened": False,
    }
    economic_scorecard["canonical_scorecard_sha256"] = _canonical_sha256(economic_scorecard)
    result: dict[str, object] = {
        "version": PILOT_VERSION,
        "pilot_id": validated["pilot_id"],
        "status": LEDGER_BINDING_FAILED,
        "dataset_metadata": copy.deepcopy(dataset_metadata),
        "qlib_runtime": copy.deepcopy(runtime_metadata),
        "reference_parity": copy.deepcopy(parity),
        "factor_screen": copy.deepcopy(factor_screen),
        "economic_scorecard": economic_scorecard,
        "updated_ledger": copy.deepcopy(updated_ledger),
        "previous_ledger_sha256": previous_ledger["canonical_ledger_sha256"],
        "new_hypotheses": new_hypotheses,
        "new_trials": new_trials,
        "new_hypothesis_count": len(new_hypotheses),
        "new_trial_count": len(new_trials),
        "attempted_factor_ids": list(attempted_factor_ids),
        "accounted_factor_ids": list(accounted_factor_ids),
        "accounting_complete": False,
        **copy.deepcopy(_SAFETY),
    }
    result["canonical_result_sha256"] = _canonical_sha256(result)
    return result


def _fail_closed_result(
    *,
    status: str,
    pilot_id: str,
    previous_ledger: dict[str, object],
    runtime_metadata: dict[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "version": PILOT_VERSION,
        "pilot_id": pilot_id,
        "status": status,
        "qlib_runtime": copy.deepcopy(runtime_metadata),
        "updated_ledger": copy.deepcopy(previous_ledger),
        "new_hypotheses": [],
        "new_trials": [],
        "new_hypothesis_count": 0,
        "new_trial_count": 0,
        **copy.deepcopy(_SAFETY),
    }
    result["canonical_result_sha256"] = _canonical_sha256(result)
    return result


def _interval(raw: Any, name: str) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"start", "end"}:
        raise ValueError(f"{name} is invalid.")
    start = _date(raw.get("start"), f"{name}.start")
    end = _date(raw.get("end"), f"{name}.end")
    if pd.Timestamp(start) > pd.Timestamp(end):
        raise ValueError(f"{name} is out of order.")
    return {"start": start, "end": end}


def _sealed_interval(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != {"dataset_version", "start", "end"}:
        raise ValueError("sealed_oos_interval is invalid.")
    interval = _interval({"start": raw["start"], "end": raw["end"]}, "sealed_oos_interval")
    return {"dataset_version": _text(raw.get("dataset_version"), "dataset_version"), **interval}


def _date(raw: Any, name: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{name} is invalid.")
    try:
        value = pd.Timestamp(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is invalid.") from exc
    if value.tz is not None or value.date().isoformat() != raw:
        raise ValueError(f"{name} is invalid.")
    return raw


def _text(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be nonempty text.")
    return raw.strip()


def _required_sha256(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ValueError(f"{name} must be lowercase SHA-256.")
    return raw


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
