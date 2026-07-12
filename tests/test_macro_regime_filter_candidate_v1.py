from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from research_lab.execution.macro_regime_filter_candidate_v1 import (
    build_macro_regime_filter_candidate,
)


MODULE_PATH = Path("research_lab/execution/macro_regime_filter_candidate_v1.py")


def _feature_set() -> dict[str, object]:
    return {
        "version": "macro_feature_set_contract_result_v1",
        "contract_version": "macro_feature_set_contract_v1",
        "status": "SUCCESS",
        "feature_observations": [
            {
                "timestamp": "2024-03-01T14:30:00Z",
                "feature_values": {"growth_z": 1.2, "inflation_state": "LOW", "unrate_diff": -0.2},
                "feature_availability_timestamps_utc": {
                    "growth_z": "2024-03-01T13:30:00Z",
                    "inflation_state": "2024-03-01T13:30:00Z",
                    "unrate_diff": "2024-03-01T13:30:00Z",
                },
                "missing_indicators": {"growth_z": False, "inflation_state": False, "unrate_diff": False},
            },
            {
                "timestamp": "2024-03-04T14:30:00Z",
                "feature_values": {"growth_z": 0.0, "inflation_state": "MID", "unrate_diff": 0.0},
                "feature_availability_timestamps_utc": {
                    "growth_z": "2024-03-04T13:30:00Z",
                    "inflation_state": "2024-03-04T13:30:00Z",
                    "unrate_diff": "2024-03-04T13:30:00Z",
                },
                "missing_indicators": {"growth_z": False, "inflation_state": False, "unrate_diff": False},
            },
            {
                "timestamp": "2024-03-05T14:30:00Z",
                "feature_values": {"growth_z": -1.3, "inflation_state": "HIGH", "unrate_diff": 0.3},
                "feature_availability_timestamps_utc": {
                    "growth_z": "2024-03-05T13:30:00Z",
                    "inflation_state": "2024-03-05T13:30:00Z",
                    "unrate_diff": "2024-03-05T13:30:00Z",
                },
                "missing_indicators": {"growth_z": False, "inflation_state": False, "unrate_diff": False},
            },
        ],
        "feature_definitions": [
            {"feature_id": "growth_z"},
            {"feature_id": "inflation_state"},
            {"feature_id": "unrate_diff"},
        ],
        "feature_availability_timestamps": {},
        "source_lineage": ["FRED:GROWTH", "FRED:INFLATION", "FRED:UNRATE"],
        "warm_up_periods": {"growth_z": 0, "inflation_state": 0, "unrate_diff": 0},
        "missing_indicators": {},
        "deterministic_feature_hash": "1" * 64,
        "production_runtime_supported": False,
        "provenance": {"source": "unit_test"},
        "input_sha256": "2" * 64,
        "output_payload_sha256": "3" * 64,
    }


def _state_policy() -> dict[str, object]:
    return {
        "allowed_regime_labels": [
            "RISK_SUPPORTIVE",
            "NEUTRAL",
            "RISK_RESTRICTIVE",
            "INSUFFICIENT_EVIDENCE",
        ],
        "label_policies": {
            "RISK_SUPPORTIVE": {
                "minimum_score": 1.0,
                "minimum_supporting_rules": 1,
                "rules": [
                    {"feature_id": "growth_z", "operation": "greater_than", "threshold": 0.5, "weight": 1.0},
                    {"feature_id": "inflation_state", "operation": "categorical_equals", "value": "LOW", "weight": 0.6},
                ],
            },
            "NEUTRAL": {
                "minimum_score": 1.0,
                "minimum_supporting_rules": 1,
                "rules": [
                    {"feature_id": "growth_z", "operation": "between_inclusive", "lower": -0.25, "upper": 0.25, "weight": 1.0},
                    {"feature_id": "inflation_state", "operation": "categorical_equals", "value": "MID", "weight": 0.5},
                ],
            },
            "RISK_RESTRICTIVE": {
                "minimum_score": 1.0,
                "minimum_supporting_rules": 1,
                "rules": [
                    {"feature_id": "growth_z", "operation": "less_than", "threshold": -0.5, "weight": 1.0},
                    {"feature_id": "inflation_state", "operation": "categorical_equals", "value": "HIGH", "weight": 0.6},
                    {"feature_id": "unrate_diff", "operation": "greater_than", "threshold": 0.1, "weight": 0.4},
                ],
            },
        },
    }


def _hmm_result(label_sequence: list[str] | None = None, *, final_status: str = "COMPLETED", prod_supported: bool = False) -> dict[str, object]:
    if label_sequence is None:
        label_sequence = ["bull", "sideways", "bear"]
    result = {
        "regime_pilot_version": "markov_hmm_regime_pilot_v1",
        "regime_model_type": "deterministic_markov_stub",
        "input_hash": "4" * 64,
        "regime_labels": [
            {"timestamp": "2024-03-01T14:30:00Z", "regime_label": label_sequence[0], "window_return_mean": 0.01},
            {"timestamp": "2024-03-04T14:30:00Z", "regime_label": label_sequence[1], "window_return_mean": 0.0},
            {"timestamp": "2024-03-05T14:30:00Z", "regime_label": label_sequence[2], "window_return_mean": -0.01},
        ],
        "regime_summary": "bear:1;bull:1;sideways:1",
        "drawdown_timing_hint": "hint",
        "exposure_timing_hint": "hint",
        "final_status": final_status,
        "failure_reason": None if final_status == "COMPLETED" else "bad",
        "source_review_candidate_id": None,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "production_runtime_supported": prod_supported,
    }
    import hashlib, json
    result["output_payload_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return result


def _request(mode: str = "deterministic_rules") -> dict[str, object]:
    request: dict[str, object] = {
        "version": "macro_regime_filter_candidate_request_v1",
        "candidate_id": "macro-regime-v1",
        "mode": mode,
        "macro_feature_set": _feature_set(),
        "state_policy": _state_policy(),
        "minimum_supporting_features": 1,
        "minimum_available_features": 2,
        "transition_policy": {"count_label_changes": True},
        "confidence_policy": {"max_feature_age_days": 1},
        "provenance": {"source": "unit_test"},
    }
    if mode == "markov_hmm_candidate":
        request["validated_markov_hmm_result"] = _hmm_result()
        request["markov_mapping_policy"] = {
            "label_mapping": {
                "bull": "RISK_SUPPORTIVE",
                "sideways": "NEUTRAL",
                "bear": "RISK_RESTRICTIVE",
            },
            "combination_policy": {"mode": "annotate"},
        }
    return request


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_macro_regime_filter_candidate(copy.deepcopy(request))


def test_deterministic_supportive_neutral_restrictive_and_transition_counts():
    result = _run(_request())
    labels = [item["regime_label"] for item in result["regime_observations"]]
    assert labels == ["RISK_SUPPORTIVE", "NEUTRAL", "RISK_RESTRICTIVE"]
    assert result["transition_count"] == 2
    assert result["candidate_only"] is True
    assert result["automatic_strategy_application_performed"] is False


def test_insufficient_missing_stale_and_conflicting_evidence_are_explicit():
    request = _request()
    request["minimum_available_features"] = 3
    request["macro_feature_set"]["feature_observations"][0]["missing_indicators"]["unrate_diff"] = True
    request["macro_feature_set"]["feature_observations"][0]["feature_values"]["unrate_diff"] = None
    result = _run(request)
    assert result["regime_observations"][0]["regime_label"] == "INSUFFICIENT_EVIDENCE"

    stale_request = _request()
    stale_request["confidence_policy"]["max_feature_age_days"] = 0
    stale_request["macro_feature_set"]["feature_observations"][2]["feature_availability_timestamps_utc"]["growth_z"] = "2024-03-03T13:30:00Z"
    stale_result = _run(stale_request)
    assert "growth_z" in stale_result["regime_observations"][2]["unavailable_feature_ids"]

    conflicting = _request()
    conflicting["macro_feature_set"]["feature_observations"][1]["feature_values"]["growth_z"] = 0.7
    conflicting["macro_feature_set"]["feature_observations"][1]["feature_values"]["inflation_state"] = "HIGH"
    conflict_result = _run(conflicting)
    assert "growth_z" in conflict_result["regime_observations"][1]["conflicting_feature_ids"]


def test_weighted_threshold_boundaries_and_duplicate_timestamp_fail_closed():
    result = _run(_request())
    assert result["regime_observations"][0]["deterministic_score"] > result["regime_observations"][1]["deterministic_score"]

    duplicate = _request()
    duplicate["macro_feature_set"]["feature_observations"][1]["timestamp"] = duplicate["macro_feature_set"]["feature_observations"][0]["timestamp"]
    with pytest.raises(ValueError, match="strictly ordered"):
        _run(duplicate)

    bad_op = _request()
    bad_op["state_policy"]["label_policies"]["RISK_SUPPORTIVE"]["rules"][0]["operation"] = "unknown"
    with pytest.raises(ValueError, match="unknown operation"):
        _run(bad_op)

    bad_label = _request()
    bad_label["state_policy"]["label_policies"]["RISK_SUPPORTIVE"]["rules"][0]["target_label"] = "BROKEN"
    with pytest.raises(ValueError, match="unknown label"):
        _run(bad_label)


def test_markov_hmm_annotate_confirm_veto_and_validation_guards(monkeypatch):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("macro features must not be converted into fake OHLC bars")

    monkeypatch.setattr(
        "research_lab.execution.markov_hmm_regime_pilot_v1.run_markov_hmm_regime_pilot",
        unexpected_call,
    )

    annotate = _run(_request("markov_hmm_candidate"))
    assert annotate["regime_observations"][0]["hmm_mapped_label"] == "RISK_SUPPORTIVE"

    confirm = _request("markov_hmm_candidate")
    confirm["markov_mapping_policy"]["combination_policy"] = {"mode": "confirm"}
    confirm_result = _run(confirm)
    assert confirm_result["regime_observations"][1]["regime_label"] == "NEUTRAL"

    veto = _request("markov_hmm_candidate")
    veto["markov_mapping_policy"]["combination_policy"] = {
        "mode": "veto",
        "veto_labels": ["RISK_RESTRICTIVE"],
        "veto_result_label": "INSUFFICIENT_EVIDENCE",
    }
    veto_result = _run(veto)
    assert veto_result["regime_observations"][2]["regime_label"] == "INSUFFICIENT_EVIDENCE"

    missing_hmm = _request("markov_hmm_candidate")
    missing_hmm.pop("validated_markov_hmm_result")
    with pytest.raises(ValueError, match="validated_markov_hmm_result is required"):
        _run(missing_hmm)

    failed_hmm = _request("markov_hmm_candidate")
    failed_hmm["validated_markov_hmm_result"] = _hmm_result(final_status="FAILED_VALIDATION")
    with pytest.raises(ValueError, match="final_status must be COMPLETED"):
        _run(failed_hmm)

    prod_hmm = _request("markov_hmm_candidate")
    prod_hmm["validated_markov_hmm_result"] = _hmm_result(prod_supported=True)
    with pytest.raises(ValueError, match="production_runtime_supported must be false"):
        _run(prod_hmm)

    bad_hash = _request("markov_hmm_candidate")
    bad_hash["validated_markov_hmm_result"]["output_payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="output hash mismatch"):
        _run(bad_hash)


def test_module_does_not_import_network_or_filesystem_modules():
    forbidden_roots = (
        "requests",
        "urllib",
        "http",
        "socket",
        "pathlib",
        "subprocess",
        "shutil",
        "os",
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
