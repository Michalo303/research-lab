from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SCORECARD_VERSION = "edge_discovery_scorecard_v1"
EDGE_CANDIDATE_FOUND = "EDGE_CANDIDATE_FOUND"
NO_PRICE_VOLUME_EDGE = "NO_PRICE_VOLUME_EDGE"

_LEDGER_FIELDS = {
    "prior_trial_count",
    "updated_trial_count",
    "new_trial_count",
    "new_hypothesis_count",
    "new_experiment_ids",
    "new_hypothesis_ids",
    "new_trial_statuses_by_factor",
    "dataset_manifest_sha256",
    "updated_ledger_sha256",
    "sealed_oos_consumptions",
}


def build_edge_discovery_scorecard_v1(
    factor_screen: dict[str, object],
    *,
    qlib_runtime_metadata: dict[str, object],
    preparation_parity: dict[str, object],
    ledger_summary: dict[str, object],
) -> dict[str, object]:
    """Build the only authoritative economic output of the pilot."""

    screen = _validate_screen(factor_screen)
    runtime = _validate_runtime(qlib_runtime_metadata)
    parity = _validate_parity(preparation_parity)
    ledger = _validate_ledger_summary(ledger_summary)
    factor_ids = tuple(screen["ordered_factor_ids"])
    ordered_factors = {factor_id: copy.deepcopy(screen["factors"][factor_id]) for factor_id in factor_ids}
    ledger_statuses = ledger["new_trial_statuses_by_factor"]
    if set(ledger_statuses) != set(factor_ids):
        raise ValueError("ledger trial statuses do not cover every factor.")
    for factor_id in factor_ids:
        ledger_status = ledger_statuses[factor_id]
        ordered_factors[factor_id]["ledger_trial_status"] = ledger_status
        if ledger_status in {"REJECTED_DUPLICATE", "REJECTED_NEAR_DUPLICATE"}:
            ordered_factors[factor_id]["decision"] = "FACTOR_STOP"
            ordered_factors[factor_id]["failure_taxonomy"] = sorted(
                set(ordered_factors[factor_id]["failure_taxonomy"]) | {ledger_status}
            )
    continuing = [
        factor_id for factor_id in factor_ids if ordered_factors[factor_id]["decision"] == "FACTOR_CONTINUE"
    ]
    stopped = [factor_id for factor_id in factor_ids if factor_id not in continuing]
    status = EDGE_CANDIDATE_FOUND if continuing else NO_PRICE_VOLUME_EDGE
    next_milestone = (
        "PORTFOLIO_CONSTRUCTION_DESIGN_V1"
        if continuing
        else "SHARADAR_FUNDAMENTAL_EDGE_DISCOVERY_V1"
    )

    result: dict[str, object] = {
        "version": SCORECARD_VERSION,
        "status": status,
        "next_authorized_milestone": next_milestone,
        "qlib_runtime_version": runtime["version"],
        "qlib_version": runtime["qlib_version"],
        "qlib_runtime_sha256": runtime["runtime_sha256"],
        "preparation_parity_sha256": parity["canonical_parity_sha256"],
        "factor_screen_sha256": screen["canonical_screen_sha256"],
        "dataset_manifest_sha256": ledger["dataset_manifest_sha256"],
        "factor_catalog_sha256": screen["factor_catalog_sha256"],
        "updated_ledger_sha256": ledger["updated_ledger_sha256"],
        "costs": copy.deepcopy(screen["costs"]),
        "weekly_observation_count": screen["weekly_observation_count"],
        "factor_metrics": ordered_factors,
        "continuing_factor_ids": continuing,
        "stopped_factor_ids": stopped,
        "prior_trial_count": ledger["prior_trial_count"],
        "updated_trial_count": ledger["updated_trial_count"],
        "new_trial_count": ledger["new_trial_count"],
        "new_hypothesis_count": ledger["new_hypothesis_count"],
        "new_experiment_ids": list(ledger["new_experiment_ids"]),
        "new_hypothesis_ids": list(ledger["new_hypothesis_ids"]),
        "sector_concentration_evaluated": bool(screen["sector_concentration_evaluated"]),
        "promotion_authorized": False,
        "production_runtime_supported": False,
        "sealed_oos_opened": False,
        "broker_action_authorized": False,
        "registry_write_authorized": False,
        "deployment_authorized": False,
    }
    result["canonical_scorecard_sha256"] = _canonical_sha256(result)
    return result


def _validate_screen(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("factor_screen must be a mapping.")
    declared = _required_sha256(raw.get("canonical_screen_sha256"), "canonical_screen_sha256")
    if _canonical_sha256({key: value for key, value in raw.items() if key != "canonical_screen_sha256"}) != declared:
        raise ValueError("factor screen hash mismatch.")
    if raw.get("version") != "qlib_factor_screen_v1" or raw.get("status") != "COMPLETED":
        raise ValueError("factor screen contract is invalid.")
    ordered = raw.get("ordered_factor_ids")
    factors = raw.get("factors")
    if (
        raw.get("factor_count") != 8
        or not isinstance(ordered, list)
        or len(ordered) != 8
        or len(set(ordered)) != 8
        or not isinstance(factors, dict)
        or set(factors) != set(ordered)
    ):
        raise ValueError("factor screen must account for exactly eight factors.")
    for factor_id in ordered:
        factor = factors[factor_id]
        if not isinstance(factor, dict) or factor.get("factor_id") != factor_id:
            raise ValueError("factor result identity is invalid.")
        if factor.get("decision") not in {"FACTOR_CONTINUE", "FACTOR_STOP"}:
            raise ValueError("factor decision is invalid.")
    if raw.get("promotion_authorized") is not False or raw.get("sealed_oos_opened") is not False:
        raise ValueError("factor screen attempts an unauthorized action.")
    if not isinstance(raw.get("weekly_observation_count"), int) or raw["weekly_observation_count"] < 52:
        raise ValueError("factor screen has insufficient observations.")
    _required_sha256(raw.get("factor_catalog_sha256"), "factor_catalog_sha256")
    costs = raw.get("costs")
    if not isinstance(costs, dict) or set(costs) != {
        "base_bps_one_way",
        "stress_bps_one_way",
        "severe_bps_one_way",
    }:
        raise ValueError("factor screen costs are invalid.")
    return copy.deepcopy(raw)


def _validate_runtime(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("qlib_runtime_metadata must be a mapping.")
    declared = _required_sha256(raw.get("runtime_sha256"), "runtime_sha256")
    if _canonical_sha256({key: value for key, value in raw.items() if key != "runtime_sha256"}) != declared:
        raise ValueError("Qlib runtime hash mismatch.")
    if (
        raw.get("version") != "real_qlib_runtime_v1"
        or raw.get("status") != "AVAILABLE"
        or raw.get("is_real_qlib") is not True
        or raw.get("qlib_version") != "0.9.7"
    ):
        raise ValueError("genuine pinned Qlib runtime is required.")
    return copy.deepcopy(raw)


def _validate_parity(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("preparation_parity must be a mapping.")
    declared = _required_sha256(raw.get("canonical_parity_sha256"), "canonical_parity_sha256")
    if _canonical_sha256({key: value for key, value in raw.items() if key != "canonical_parity_sha256"}) != declared:
        raise ValueError("preparation parity hash mismatch.")
    if raw.get("version") != "real_qlib_preparation_parity_v1" or raw.get("status") != "PASS":
        raise ValueError("Qlib preparation parity must pass.")
    if raw.get("source_frame_sha256") != raw.get("prepared_frame_sha256"):
        raise ValueError("Qlib preparation parity hashes differ.")
    return copy.deepcopy(raw)


def _validate_ledger_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _LEDGER_FIELDS:
        raise ValueError("ledger_summary fields are invalid.")
    for field in ("prior_trial_count", "updated_trial_count", "new_trial_count", "new_hypothesis_count"):
        value = raw.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} is invalid.")
    if raw["new_trial_count"] != 8 or raw["new_hypothesis_count"] != 8:
        raise ValueError("exactly eight new hypotheses and trials are required.")
    if raw["updated_trial_count"] != raw["prior_trial_count"] + raw["new_trial_count"]:
        raise ValueError("trial counts do not reconcile.")
    experiment_ids = raw.get("new_experiment_ids")
    hypothesis_ids = raw.get("new_hypothesis_ids")
    trial_statuses = raw.get("new_trial_statuses_by_factor")
    if (
        not isinstance(experiment_ids, list)
        or len(experiment_ids) != 8
        or len(set(experiment_ids)) != 8
        or not all(isinstance(value, str) and value for value in experiment_ids)
        or not isinstance(hypothesis_ids, list)
        or len(hypothesis_ids) != 8
        or len(set(hypothesis_ids)) != 8
        or not all(isinstance(value, str) and value for value in hypothesis_ids)
        or not isinstance(trial_statuses, dict)
        or len(trial_statuses) != 8
        or not all(
            isinstance(key, str)
            and key
            and value
            in {
                "WALK_FORWARD_COMPLETE",
                "STRATEGY_GATE_FAIL",
                "REJECTED_DUPLICATE",
                "REJECTED_NEAR_DUPLICATE",
            }
            for key, value in trial_statuses.items()
        )
    ):
        raise ValueError("new attempt identities are invalid.")
    if raw.get("sealed_oos_consumptions") != 0:
        raise ValueError("sealed OOS consumption is prohibited.")
    _required_sha256(raw.get("dataset_manifest_sha256"), "dataset_manifest_sha256")
    _required_sha256(raw.get("updated_ledger_sha256"), "updated_ledger_sha256")
    return copy.deepcopy(raw)


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
