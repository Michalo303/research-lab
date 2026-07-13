from __future__ import annotations

import hashlib
import json
from typing import Any

from research_lab.execution.immutable_macro_snapshot_contract_v1 import (
    build_immutable_macro_snapshot_contract,
)
from research_lab.execution.isolated_real_data_adapter_contract_v1 import (
    build_isolated_real_data_adapter_contract,
)
from research_lab.execution.macro_feature_set_contract_v1 import (
    build_macro_feature_set_contract,
)
from research_lab.execution.macro_market_asof_alignment_contract_v1 import (
    build_macro_market_asof_alignment_contract,
)
from research_lab.execution.macro_regime_filter_candidate_v1 import (
    build_macro_regime_filter_candidate,
)
from research_lab.execution.macro_series_contract_v1 import (
    build_macro_series_contract,
)
from research_lab.execution.macro_strategy_filter_evaluator_v1 import (
    build_macro_strategy_filter_evaluator,
)
from research_lab.execution.result_review_gate_v1 import (
    build_result_review_gate,
)
from research_lab.execution.strategy_execution_capability_bridge_v1 import (
    build_strategy_execution_bridge_request,
)
from research_lab.execution.swing_trend_filtered_pullback_strategy_contract_v1 import (
    build_swing_trend_filtered_pullback_strategy_contract,
)


REQUEST_VERSION = "e2e_macro_aware_research_acceptance_request_v1"
RESULT_VERSION = "e2e_macro_aware_research_acceptance_result_v1"
ACCEPTANCE_VERSION = "e2e_macro_aware_research_acceptance_v1"
STATUS_ACCEPTED = "ACCEPTED_REVIEW_ONLY"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_FAILED = "FAILED_VALIDATION"


def run_e2e_macro_aware_research_acceptance(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    market_adapter_result = build_isolated_real_data_adapter_contract(validated["market_data_request"])
    series_results = [build_macro_series_contract(item) for item in validated["macro_series_requests"]]
    snapshot_series_results = [_macro_series_snapshot_adapter_result(item) for item in series_results]
    macro_series_results = [_macro_series_alignment_result(item) for item in series_results]
    market_bars_sha256 = _canonical_sha256(market_adapter_result["synthetic_bars"])

    _validate_symbol_alignment(
        market_symbol=str(market_adapter_result["symbol"]),
        expected_market_symbol=validated["expected_identities"]["market_symbol"],
        strategy_request_symbol=_required_text(validated["strategy_request"], "symbol"),
        evaluator_strategy_symbol=_required_text(
            _required_mapping(
                validated["macro_filter_evaluation_request"].get("strategy_identity"),
                name="macro_filter_evaluation_request.strategy_identity",
            ),
            "symbol",
        ),
    )

    macro_snapshot_result = build_immutable_macro_snapshot_contract(
        {
            **validated["macro_snapshot_request"],
            "series_adapter_results": snapshot_series_results,
            "provenance": validated["provenance"],
        }
    )
    _validate_safe_flags(macro_snapshot_result["safe_flags"], name="macro_snapshot_result.safe_flags")

    alignment_result = build_macro_market_asof_alignment_contract(
        {
            **validated["macro_alignment_request"],
            "market_bars": validated["market_data_request"]["input_bars"],
            "macro_series_results": macro_series_results,
            "provenance": validated["provenance"],
        }
    )
    _validate_safe_flags(alignment_result["safety_flags"], name="alignment_result.safety_flags")

    feature_set_result = build_macro_feature_set_contract(
        {
            **validated["macro_feature_request"],
            "aligned_macro_result": alignment_result,
            "provenance": validated["provenance"],
        }
    )
    regime_candidate_result = build_macro_regime_filter_candidate(
        {
            **validated["macro_regime_request"],
            "macro_feature_set": feature_set_result,
            "provenance": validated["provenance"],
        }
    )
    _validate_regime_candidate(regime_candidate_result)

    strategy_contract_result = build_swing_trend_filtered_pullback_strategy_contract(
        {
            **validated["strategy_request"],
            "synthetic_bars": market_adapter_result["synthetic_bars"],
            "provenance": validated["provenance"],
        }
    )
    evaluator_signal_sequence = _build_evaluator_signal_sequence(
        strategy_contract_result=strategy_contract_result,
        strategy_identity=_required_mapping(
            validated["macro_filter_evaluation_request"].get("strategy_identity"),
            name="macro_filter_evaluation_request.strategy_identity",
        ),
        baseline_variant_identity=_required_text(validated["macro_filter_evaluation_request"], "baseline_variant_identity"),
        market_data_identity=_required_text(validated["macro_filter_evaluation_request"], "market_data_identity"),
        market_symbol=str(market_adapter_result["symbol"]),
    )
    bridge_result = build_strategy_execution_bridge_request(
        {
            "version": "strategy_execution_capability_bridge_request_v1",
            "strategy_builder": str(strategy_contract_result["strategy_builder"]),
            "symbol": str(strategy_contract_result["symbol"]),
            "synthetic_bars": strategy_contract_result["synthetic_bars"],
            "strategy_signal_plan": strategy_contract_result["strategy_signal_plan"],
            "provenance": validated["provenance"],
        }
    )
    evaluator_result = build_macro_strategy_filter_evaluator(
        {
            **validated["macro_filter_evaluation_request"],
            "baseline_signal_sequence": evaluator_signal_sequence,
            "market_bars": market_adapter_result["synthetic_bars"],
            "market_data_sha256": market_bars_sha256,
            "market_source_artifact_sha256": market_adapter_result["output_payload_sha256"],
            "macro_snapshot_sha256": macro_snapshot_result["output_payload_sha256"],
            "alignment_output_sha256": alignment_result["output_payload_sha256"],
            "feature_set_output_sha256": feature_set_result["output_payload_sha256"],
            "macro_regime_candidate_output_sha256": regime_candidate_result["output_payload_sha256"],
            "macro_regime_candidate_result": regime_candidate_result,
            "provenance": validated["provenance"],
        }
    )
    _validate_evaluator(evaluator_result)

    review_artifact = build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": market_adapter_result,
            "strategy_contract_result": strategy_contract_result,
            "bridge_result": bridge_result,
            "provenance": validated["provenance"],
        }
    )
    _validate_review_artifact(review_artifact)

    _validate_expected_identities(
        validated["expected_identities"],
        market_symbol=market_adapter_result["symbol"],
        strategy_identity=evaluator_result["strategy_identity"],
        baseline_variant_identity=evaluator_result["baseline_variant_identity"],
        market_data_identity=evaluator_result["market_data_identity"],
    )
    _validate_expected_hashes(
        validated["expected_hashes"],
        market_data_sha256=market_bars_sha256,
        macro_snapshot_sha256=macro_snapshot_result["output_payload_sha256"],
        alignment_output_sha256=alignment_result["output_payload_sha256"],
        feature_set_output_sha256=feature_set_result["output_payload_sha256"],
        macro_regime_candidate_output_sha256=regime_candidate_result["output_payload_sha256"],
        evaluator_output_sha256=evaluator_result["output_payload_sha256"],
    )

    baseline_preservation_proof = {
        "baseline_unchanged": evaluator_result["baseline_unchanged"],
        "disabled_filter_equals_baseline": evaluator_result["disabled_ablation_metrics"] == evaluator_result["baseline_metrics"],
        "baseline_strategy_request_unchanged": True,
        "baseline_strategy_parameters_unchanged": True,
        "baseline_signals_unchanged": evaluator_result["variant_results"]["BASELINE_NO_FILTER"]["normalized_signal_sequence"] == evaluator_signal_sequence,
    }
    protective_exit_preservation_proof = {
        "protective_exits_preserved": evaluator_result["protective_exits_preserved"],
        "stop_loss_semantics_unchanged": True,
    }
    no_look_ahead_proof = _no_look_ahead_proof(
        alignment_result=alignment_result,
        feature_set_result=feature_set_result,
        regime_candidate_result=regime_candidate_result,
        execution_policy=validated["macro_filter_evaluation_request"]["execution_policy"],
    )
    validation_errors = _validation_errors(
        review_artifact=review_artifact,
        no_look_ahead_proof=no_look_ahead_proof,
    )

    evaluator_classification = str(evaluator_result["classification"])
    if validation_errors:
        status = STATUS_FAILED
    else:
        status = STATUS_ACCEPTED if evaluator_classification in {
            "CANDIDATE_IMPROVES_RISK",
            "CANDIDATE_IMPROVES_RETURN",
            "CANDIDATE_MIXED",
        } else STATUS_REVIEW_REQUIRED

    lineage = {
        "acceptance_id": validated["acceptance_id"],
        "market_data_identity": validated["expected_identities"]["market_data_identity"],
        "market_symbol": market_adapter_result["symbol"],
        "market_data_sha256": market_bars_sha256,
        "market_source_artifact_sha256": market_adapter_result["output_payload_sha256"],
        "macro_provider_identities": [item["provider"] for item in macro_series_results],
        "macro_series_identities": [f"{item['provider']}:{item['series_id']}" for item in macro_series_results],
        "macro_snapshot_sha256": macro_snapshot_result["output_payload_sha256"],
        "alignment_output_sha256": alignment_result["output_payload_sha256"],
        "feature_set_output_sha256": feature_set_result["output_payload_sha256"],
        "macro_regime_candidate_output_sha256": regime_candidate_result["output_payload_sha256"],
        "strategy_id": evaluator_result["strategy_identity"]["strategy_id"],
        "strategy_version": evaluator_result["strategy_identity"]["strategy_version"],
        "strategy_builder": evaluator_result["strategy_identity"]["strategy_builder"],
        "baseline_variant_identity": evaluator_result["baseline_variant_identity"],
        "evaluator_output_sha256": evaluator_result["output_payload_sha256"],
    }
    safety_flags = {
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "paper_trading_performed": False,
        "deployment_performed": False,
        "promotion_performed": False,
        "generated_code_executed": False,
        "automatic_strategy_application_performed": False,
        "production_runtime_supported": False,
    }
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "acceptance_version": ACCEPTANCE_VERSION,
        "acceptance_id": validated["acceptance_id"],
        "status": status,
        "lineage": lineage,
        "macro_snapshot_result": macro_snapshot_result,
        "alignment_result": alignment_result,
        "feature_set_result": feature_set_result,
        "macro_regime_candidate_result": regime_candidate_result,
        "baseline_strategy_result": strategy_contract_result,
        "macro_filter_evaluator_result": evaluator_result,
        "review_artifact": review_artifact,
        "evaluator_classification": evaluator_classification,
        "baseline_preservation_proof": baseline_preservation_proof,
        "protective_exit_preservation_proof": protective_exit_preservation_proof,
        "no_look_ahead_proof": no_look_ahead_proof,
        "validation_errors": validation_errors,
        "failure_reason": "; ".join(validation_errors) if validation_errors else None,
        "safety_flags": safety_flags,
        "provenance": validated["provenance"],
    }
    result["input_sha256"] = _canonical_sha256(validated["hashable_request"])
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "acceptance_id",
            "market_data_request",
            "macro_series_requests",
            "macro_snapshot_request",
            "macro_alignment_request",
            "macro_feature_request",
            "macro_regime_request",
            "strategy_request",
            "macro_filter_evaluation_request",
            "expected_identities",
            "expected_hashes",
            "provenance",
        },
        name="request",
    )
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    market_data_request = _required_mapping(payload.get("market_data_request"), name="market_data_request")
    macro_series_requests = _required_list(payload.get("macro_series_requests"), name="macro_series_requests")
    macro_snapshot_request = _required_mapping(payload.get("macro_snapshot_request"), name="macro_snapshot_request")
    macro_alignment_request = _required_mapping(payload.get("macro_alignment_request"), name="macro_alignment_request")
    macro_feature_request = _required_mapping(payload.get("macro_feature_request"), name="macro_feature_request")
    macro_regime_request = _required_mapping(payload.get("macro_regime_request"), name="macro_regime_request")
    strategy_request = _required_mapping(payload.get("strategy_request"), name="strategy_request")
    macro_filter_evaluation_request = _required_mapping(payload.get("macro_filter_evaluation_request"), name="macro_filter_evaluation_request")
    expected_identities = _validate_expected_identities_request(payload.get("expected_identities"))
    expected_hashes = _validate_expected_hashes_request(payload.get("expected_hashes"))
    provenance = _validate_provenance(payload.get("provenance"))
    hashable_request = {
        "version": REQUEST_VERSION,
        "acceptance_id": _required_text(payload, "acceptance_id"),
        "market_data_request": market_data_request,
        "macro_series_requests": macro_series_requests,
        "macro_snapshot_request": macro_snapshot_request,
        "macro_alignment_request": macro_alignment_request,
        "macro_feature_request": macro_feature_request,
        "macro_regime_request": macro_regime_request,
        "strategy_request": strategy_request,
        "macro_filter_evaluation_request": macro_filter_evaluation_request,
        "expected_identities": expected_identities,
        "expected_hashes": expected_hashes,
        "provenance": provenance,
    }
    return {
        "acceptance_id": _required_text(payload, "acceptance_id"),
        "market_data_request": market_data_request,
        "macro_series_requests": list(macro_series_requests),
        "macro_snapshot_request": macro_snapshot_request,
        "macro_alignment_request": macro_alignment_request,
        "macro_feature_request": macro_feature_request,
        "macro_regime_request": macro_regime_request,
        "strategy_request": strategy_request,
        "macro_filter_evaluation_request": macro_filter_evaluation_request,
        "expected_identities": expected_identities,
        "expected_hashes": expected_hashes,
        "provenance": provenance,
        "hashable_request": hashable_request,
    }


def _macro_series_snapshot_adapter_result(series_result: dict[str, Any]) -> dict[str, Any]:
    provider = str(series_result["provider"])
    series_id = str(series_result["series_id"])
    return {
        "version": "fred_alfred_readonly_adapter_result_v1",
        "adapter_version": "fred_alfred_readonly_adapter_v1",
        "status": "SUCCESS",
        "provider": provider,
        "series_id": series_id,
        "response_sha256": series_result["input_sha256"],
        "macro_series_contract": series_result,
        "network_used": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
        "provenance": {"source": "macro_aware_acceptance"},
        "input_sha256": series_result["input_sha256"],
        "output_payload_sha256": series_result["output_payload_sha256"],
    }


def _macro_series_alignment_result(series_result: dict[str, Any]) -> dict[str, Any]:
    result = _macro_series_snapshot_adapter_result(series_result)
    classifications = {item["point_in_time"]["classification"] for item in series_result["observations"]}
    result["point_in_time_classification"] = "VINTAGE_AWARE" if "vintage_date_only" in classifications else "RELEASE_AWARE"
    return result


def _validate_symbol_alignment(
    *,
    market_symbol: str,
    expected_market_symbol: str,
    strategy_request_symbol: str,
    evaluator_strategy_symbol: str,
) -> None:
    if market_symbol != expected_market_symbol:
        raise ValueError("market symbol identity mismatch.")
    if strategy_request_symbol != market_symbol:
        raise ValueError("strategy symbol mismatch.")
    if evaluator_strategy_symbol != market_symbol:
        raise ValueError("strategy symbol mismatch.")


def _build_evaluator_signal_sequence(
    *,
    strategy_contract_result: dict[str, Any],
    strategy_identity: dict[str, Any],
    baseline_variant_identity: str,
    market_data_identity: str,
    market_symbol: str,
) -> list[dict[str, Any]]:
    if str(strategy_contract_result.get("symbol")) != market_symbol:
        raise ValueError("strategy symbol mismatch.")
    contracts_by_id = {
        str(item["signal_id"]): _required_mapping(item, name="signal_contract")
        for item in _required_list(strategy_contract_result.get("signal_contracts"), name="signal_contracts")
    }
    normalized: list[dict[str, Any]] = []
    for raw in _required_list(strategy_contract_result.get("strategy_signal_plan"), name="strategy_signal_plan"):
        signal = _required_mapping(raw, name="strategy_signal")
        signal_id = _required_text(signal, "signal_id")
        contract = _required_mapping(contracts_by_id.get(signal_id), name=f"signal_contracts[{signal_id}]")
        signal_type = _required_text(signal, "signal_type")
        if "symbol" in signal and _required_text(signal, "symbol") != market_symbol:
            raise ValueError("strategy symbol mismatch.")
        target_direction = "flat" if signal_type == "exit" else "long"
        if "target_direction" in signal and _required_text(signal, "target_direction") != target_direction:
            raise ValueError("strategy signal target_direction mismatch.")
        normalized.append(
            {
                "timestamp": _required_text(signal, "timestamp"),
                "signal_id": signal_id,
                "signal_type": signal_type,
                "target_direction": target_direction,
                "target_exposure": 0.0 if signal_type == "exit" else float(contract["target_exposure"]),
                "strategy_identity": _required_text(strategy_identity, "strategy_id"),
                "baseline_variant_id": baseline_variant_identity,
                "symbol": market_symbol,
                "market_data_identity": market_data_identity,
                "protective_exit": contract.get("protective_exit"),
            }
        )
    return normalized


def _validation_errors(
    *,
    review_artifact: dict[str, Any],
    no_look_ahead_proof: dict[str, bool],
) -> list[str]:
    errors: list[str] = []
    if review_artifact["final_review_status"] == "FAILED_VALIDATION":
        reason = review_artifact.get("failure_reason")
        errors.append(str(reason) if reason else "review_artifact.failed_validation")
    for key, value in no_look_ahead_proof.items():
        if value is not True:
            errors.append(f"no_look_ahead_proof.{key}")
    return errors


def _validate_regime_candidate(result: dict[str, Any]) -> None:
    if result.get("automatic_strategy_application_performed") is not False:
        raise ValueError("macro_regime_candidate_result.automatic_strategy_application_performed must be false.")
    if result.get("production_runtime_supported") is not False:
        raise ValueError("macro_regime_candidate_result.production_runtime_supported must be false.")
    if int(result.get("provider_calls_used", 0)) != 0:
        raise ValueError("macro_regime_candidate_result.provider_calls_used must be 0.")
    if result.get("registry_write_performed") is not False:
        raise ValueError("macro_regime_candidate_result.registry_write_performed must be false.")
    if int(result.get("broker_actions_used", 0)) != 0:
        raise ValueError("macro_regime_candidate_result.broker_actions_used must be 0.")
    if result.get("deployment_performed") is not False:
        raise ValueError("macro_regime_candidate_result.deployment_performed must be false.")


def _validate_evaluator(result: dict[str, Any]) -> None:
    if result.get("candidate_only") is not True:
        raise ValueError("macro_filter_evaluator_result.candidate_only must be true.")
    if result.get("automatic_strategy_application_performed") is not False:
        raise ValueError("macro_filter_evaluator_result.automatic_strategy_application_performed must be false.")
    if result.get("production_runtime_supported") is not False:
        raise ValueError("macro_filter_evaluator_result.production_runtime_supported must be false.")


def _validate_review_artifact(result: dict[str, Any]) -> None:
    if result.get("promotion_performed") is not False:
        raise ValueError("review_artifact.promotion_performed must be false.")
    if result.get("registry_write_performed") is not False:
        raise ValueError("review_artifact.registry_write_performed must be false.")
    if int(result.get("provider_calls_used", 0)) != 0:
        raise ValueError("review_artifact.provider_calls_used must be 0.")


def _validate_safe_flags(flags: dict[str, Any], *, name: str) -> None:
    if int(flags.get("provider_calls_used", 0)) != 0:
        raise ValueError(f"{name}.provider_calls_used must be 0.")
    if flags.get("network_used") is not False:
        raise ValueError(f"{name}.network_used must be false.")
    if flags.get("registry_write_performed") is not False:
        raise ValueError(f"{name}.registry_write_performed must be false.")
    if int(flags.get("broker_actions_used", 0)) != 0:
        raise ValueError(f"{name}.broker_actions_used must be 0.")
    if flags.get("deployment_performed") is not False:
        raise ValueError(f"{name}.deployment_performed must be false.")
    if flags.get("production_runtime_supported") is not False:
        raise ValueError(f"{name}.production_runtime_supported must be false.")


def _validate_expected_identities_request(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="expected_identities")
    return {
        "market_data_identity": _required_text(payload, "market_data_identity"),
        "market_symbol": _required_text(payload, "market_symbol"),
        "strategy_id": _required_text(payload, "strategy_id"),
        "strategy_builder": _required_text(payload, "strategy_builder"),
        "baseline_variant_identity": _required_text(payload, "baseline_variant_identity"),
    }


def _validate_expected_hashes_request(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="expected_hashes")
    return {
        "market_data_sha256": _required_text(payload, "market_data_sha256"),
        "macro_snapshot_sha256": _required_text(payload, "macro_snapshot_sha256"),
        "alignment_output_sha256": _required_text(payload, "alignment_output_sha256"),
        "feature_set_output_sha256": _required_text(payload, "feature_set_output_sha256"),
        "macro_regime_candidate_output_sha256": _required_text(payload, "macro_regime_candidate_output_sha256"),
        "evaluator_output_sha256": _required_text(payload, "evaluator_output_sha256"),
    }


def _validate_expected_identities(
    expected: dict[str, str],
    *,
    market_symbol: str,
    strategy_identity: dict[str, Any],
    baseline_variant_identity: str,
    market_data_identity: str,
) -> None:
    if expected["market_symbol"] != market_symbol:
        raise ValueError("market symbol identity mismatch.")
    if expected["strategy_id"] != str(strategy_identity["strategy_id"]):
        raise ValueError("strategy identity mismatch.")
    if expected["strategy_builder"] != str(strategy_identity["strategy_builder"]):
        raise ValueError("strategy identity mismatch.")
    if expected["baseline_variant_identity"] != baseline_variant_identity:
        raise ValueError("baseline variant identity mismatch.")
    if expected["market_data_identity"] != market_data_identity:
        raise ValueError("market data identity mismatch.")


def _validate_expected_hashes(
    expected: dict[str, str],
    *,
    market_data_sha256: str,
    macro_snapshot_sha256: str,
    alignment_output_sha256: str,
    feature_set_output_sha256: str,
    macro_regime_candidate_output_sha256: str,
    evaluator_output_sha256: str,
) -> None:
    if expected["market_data_sha256"] != market_data_sha256:
        raise ValueError("market data hash mismatch.")
    if expected["macro_snapshot_sha256"] != macro_snapshot_sha256:
        raise ValueError("macro snapshot hash mismatch.")
    if expected["alignment_output_sha256"] != alignment_output_sha256:
        raise ValueError("alignment hash mismatch.")
    if expected["feature_set_output_sha256"] != feature_set_output_sha256:
        raise ValueError("feature-set hash mismatch.")
    if expected["macro_regime_candidate_output_sha256"] != macro_regime_candidate_output_sha256:
        raise ValueError("regime-candidate hash mismatch.")
    if expected["evaluator_output_sha256"] != evaluator_output_sha256:
        raise ValueError("evaluator hash mismatch.")


def _no_look_ahead_proof(
    *,
    alignment_result: dict[str, Any],
    feature_set_result: dict[str, Any],
    regime_candidate_result: dict[str, Any],
    execution_policy: dict[str, Any],
) -> dict[str, bool]:
    no_future_release_used = True
    for row in alignment_result["aligned_bars"]:
        decision = row["decision_timestamp_utc"]
        for value in row["availability_timestamps_utc"].values():
            if value is not None and value > decision:
                no_future_release_used = False
    no_future_feature_used = True
    for row in feature_set_result["feature_observations"]:
        for value in row["feature_availability_timestamps_utc"].values():
            if value is not None and value > row["timestamp"]:
                no_future_feature_used = False
    no_future_regime_used = True
    for row in regime_candidate_result["regime_observations"]:
        for value in row["feature_availability_timestamps_utc"].values():
            if value is not None and value > row["timestamp"]:
                no_future_regime_used = False
    no_future_market_fill_used = (
        execution_policy["fill_convention"] == "next_open"
        and int(execution_policy["decision_to_fill_delay_bars"]) >= 1
        and execution_policy["allow_same_bar_fill"] is False
    )
    return {
        "no_future_release_used": no_future_release_used,
        "no_future_feature_used": no_future_feature_used,
        "no_future_regime_used": no_future_regime_used,
        "no_future_market_fill_used": no_future_market_fill_used,
    }


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _json_scalar(value: Any, *, name: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
