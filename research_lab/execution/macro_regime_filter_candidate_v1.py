from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any


REQUEST_VERSION = "macro_regime_filter_candidate_request_v1"
RESULT_VERSION = "macro_regime_filter_candidate_result_v1"
CANDIDATE_VERSION = "macro_regime_filter_candidate_v1"
FEATURE_SET_VERSION = "macro_feature_set_contract_result_v1"
FEATURE_SET_CONTRACT_VERSION = "macro_feature_set_contract_v1"
HMM_VERSION = "markov_hmm_regime_pilot_v1"
STATUS_SUCCESS = "SUCCESS"
STATUS_COMPLETED = "COMPLETED"
MODE_DETERMINISTIC = "deterministic_rules"
MODE_MARKOV = "markov_hmm_candidate"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
RULE_OPERATIONS = {
    "greater_than",
    "less_than",
    "between_inclusive",
    "categorical_equals",
}


def build_macro_regime_filter_candidate(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    observations, transition_count, unavailable_period_count = _build_observations(validated)
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "candidate_version": CANDIDATE_VERSION,
        "candidate_id": validated["candidate_id"],
        "mode": validated["mode"],
        "regime_observations": observations,
        "regime_label": observations[-1]["regime_label"] if observations else INSUFFICIENT_EVIDENCE,
        "transition_count": transition_count,
        "unavailable_period_count": unavailable_period_count,
        "macro_feature_set_hash": validated["macro_feature_set_hash"],
        "hmm_source_hash": validated["hmm_source_hash"],
        "combination_policy": validated["combination_policy"],
        "candidate_only": True,
        "automatic_strategy_application_performed": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
    }
    hashable_request = {
        "version": REQUEST_VERSION,
        "candidate_id": validated["candidate_id"],
        "mode": validated["mode"],
        "macro_feature_set_hash": validated["macro_feature_set_hash"],
        "state_policy": validated["state_policy_hashable"],
        "minimum_supporting_features": validated["minimum_supporting_features"],
        "minimum_available_features": validated["minimum_available_features"],
        "transition_policy": validated["transition_policy"],
        "confidence_policy": validated["confidence_policy"],
        "provenance": validated["provenance"],
        "validated_markov_hmm_result": validated["validated_markov_hmm_result"],
        "markov_mapping_policy": validated["markov_mapping_policy_hashable"],
    }
    result["input_sha256"] = _canonical_sha256(hashable_request)
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _build_observations(validated: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    observations: list[dict[str, Any]] = []
    previous_label: str | None = None
    transition_count = 0
    unavailable_period_count = 0
    hmm_rows = validated["hmm_rows"]
    for index, row in enumerate(validated["feature_rows"]):
        observation = _deterministic_observation(row, validated)
        hmm_row = hmm_rows[index] if hmm_rows is not None else None
        if hmm_row is not None:
            _apply_hmm_overlay(observation, hmm_row, validated)
        if observation["unavailable_feature_ids"]:
            unavailable_period_count += 1
        if (
            validated["transition_policy"]["count_label_changes"]
            and previous_label is not None
            and observation["regime_label"] != previous_label
        ):
            transition_count += 1
        previous_label = str(observation["regime_label"])
        observations.append(observation)
    return observations, transition_count, unavailable_period_count


def _deterministic_observation(row: dict[str, Any], validated: dict[str, Any]) -> dict[str, Any]:
    timestamp = row["timestamp"]
    matched_by_label: dict[str, list[dict[str, Any]]] = {}
    qualified_labels: list[str] = []
    all_supporting_labels: list[str] = []
    available_features: set[str] = set()
    unavailable_features: set[str] = set()
    for label in validated["classified_labels"]:
        policy = validated["label_policies"][label]
        matches: list[dict[str, Any]] = []
        for rule in policy["rules"]:
            feature_state = _feature_state(row, rule["feature_id"], timestamp, validated["max_feature_age_days"])
            if feature_state["available"]:
                available_features.add(rule["feature_id"])
                if _rule_matches(feature_state["value"], rule):
                    matches.append(rule)
            else:
                unavailable_features.add(rule["feature_id"])
        matched_by_label[label] = matches
        if matches:
            all_supporting_labels.append(label)
        score = sum(float(rule["weight"]) for rule in matches)
        support_count = len({str(rule["feature_id"]) for rule in matches})
        if score >= policy["minimum_score"] and support_count >= max(
            validated["minimum_supporting_features"],
            policy["minimum_supporting_rules"],
        ):
            qualified_labels.append(label)

    unique_support_labels = sorted(set(all_supporting_labels))
    conflicting_feature_ids = sorted(
        {
            str(rule["feature_id"])
            for label in unique_support_labels
            for rule in matched_by_label[label]
        }
    ) if len(unique_support_labels) > 1 else []

    regime_label = INSUFFICIENT_EVIDENCE
    deterministic_score = 0.0
    supporting_feature_ids: list[str] = []
    if len(available_features) < validated["minimum_available_features"]:
        pass
    elif len(qualified_labels) == 1 and not conflicting_feature_ids:
        selected_label = qualified_labels[0]
        regime_label = selected_label
        selected_matches = matched_by_label[selected_label]
        supporting_feature_ids = sorted({str(rule["feature_id"]) for rule in selected_matches})
        deterministic_score = round(sum(float(rule["weight"]) for rule in selected_matches), 10)
    elif len(qualified_labels) == 1:
        selected_label = qualified_labels[0]
        selected_matches = matched_by_label[selected_label]
        deterministic_score = round(sum(float(rule["weight"]) for rule in selected_matches), 10)

    return {
        "timestamp": timestamp,
        "feature_availability_timestamps_utc": row["feature_availability_timestamps_utc"],
        "regime_label": regime_label,
        "deterministic_score": deterministic_score,
        "supporting_feature_ids": supporting_feature_ids,
        "conflicting_feature_ids": conflicting_feature_ids,
        "unavailable_feature_ids": sorted(unavailable_features),
    }


def _apply_hmm_overlay(observation: dict[str, Any], hmm_row: dict[str, Any], validated: dict[str, Any]) -> None:
    mapping = validated["label_mapping"]
    mapped_label = mapping[str(hmm_row["regime_label"])]
    observation["hmm_source_label"] = hmm_row["regime_label"]
    observation["hmm_mapped_label"] = mapped_label
    mode = validated["combination_policy"]["mode"]
    if mode == "annotate":
        return
    if mode == "confirm":
        if observation["regime_label"] != mapped_label:
            observation["regime_label"] = INSUFFICIENT_EVIDENCE
            observation["supporting_feature_ids"] = []
        return
    if mode == "veto":
        if mapped_label in validated["combination_policy"]["veto_labels"]:
            observation["regime_label"] = validated["combination_policy"]["veto_result_label"]
            observation["supporting_feature_ids"] = []
        return
    if mode == "leave_unchanged":
        return
    raise ValueError(f"unknown combination mode: {mode}")


def _feature_state(
    row: dict[str, Any],
    feature_id: str,
    timestamp: str,
    max_feature_age_days: float,
) -> dict[str, Any]:
    missing_indicators = row["missing_indicators"]
    feature_values = row["feature_values"]
    availability = row["feature_availability_timestamps_utc"]
    if bool(missing_indicators.get(feature_id)):
        return {"available": False, "value": None}
    if feature_id not in feature_values:
        raise ValueError(f"feature_values missing declared feature_id: {feature_id}")
    if feature_id not in availability:
        raise ValueError(f"feature_availability_timestamps_utc missing declared feature_id: {feature_id}")
    value = feature_values[feature_id]
    availability_timestamp = availability[feature_id]
    if availability_timestamp is None:
        return {"available": False, "value": None}
    age_days = _age_in_days(availability_timestamp, timestamp)
    if age_days < 0:
        raise ValueError("feature availability timestamp cannot be after observation timestamp.")
    if age_days > max_feature_age_days:
        return {"available": False, "value": value}
    return {"available": True, "value": value}


def _rule_matches(value: Any, rule: dict[str, Any]) -> bool:
    operation = rule["operation"]
    if operation == "greater_than":
        return _numeric(value, name="feature value") > float(rule["threshold"])
    if operation == "less_than":
        return _numeric(value, name="feature value") < float(rule["threshold"])
    if operation == "between_inclusive":
        numeric_value = _numeric(value, name="feature value")
        return float(rule["lower"]) <= numeric_value <= float(rule["upper"])
    if operation == "categorical_equals":
        return str(value) == str(rule["value"])
    raise ValueError(f"unknown operation: {operation}")


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "candidate_id",
            "mode",
            "macro_feature_set",
            "state_policy",
            "validated_markov_hmm_result",
            "markov_mapping_policy",
            "minimum_supporting_features",
            "minimum_available_features",
            "transition_policy",
            "confidence_policy",
            "provenance",
        },
        name="request",
    )
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    mode = _required_text(payload, "mode")
    if mode not in {MODE_DETERMINISTIC, MODE_MARKOV}:
        raise ValueError("mode must be deterministic_rules or markov_hmm_candidate.")
    feature_set = _validate_macro_feature_set(payload.get("macro_feature_set"))
    state_policy = _validate_state_policy(payload.get("state_policy"))
    minimum_supporting_features = _required_positive_int(payload, "minimum_supporting_features")
    minimum_available_features = _required_positive_int(payload, "minimum_available_features")
    transition_policy = _validate_transition_policy(payload.get("transition_policy"))
    confidence_policy = _validate_confidence_policy(payload.get("confidence_policy"))
    provenance = _validate_provenance(payload.get("provenance"))

    hmm_result = None
    hmm_rows = None
    label_mapping = None
    combination_policy = {"mode": "leave_unchanged"}
    mapping_hashable = None
    hmm_source_hash = None
    if mode == MODE_MARKOV:
        hmm_result = _validate_markov_hmm_result(payload.get("validated_markov_hmm_result"))
        mapping = _validate_markov_mapping_policy(
            payload.get("markov_mapping_policy"),
            allowed_labels=state_policy["allowed_regime_labels"],
            hmm_labels={str(item["regime_label"]) for item in hmm_result["regime_labels"]},
        )
        hmm_rows = _validate_hmm_alignment(hmm_result["regime_labels"], feature_set["feature_rows"])
        label_mapping = mapping["label_mapping"]
        combination_policy = mapping["combination_policy"]
        mapping_hashable = mapping["hashable"]
        hmm_source_hash = _required_text(hmm_result, "output_payload_sha256")

    return {
        "candidate_id": _required_text(payload, "candidate_id"),
        "mode": mode,
        "feature_rows": feature_set["feature_rows"],
        "macro_feature_set_hash": feature_set["macro_feature_set_hash"],
        "state_policy_hashable": state_policy["hashable"],
        "classified_labels": state_policy["classified_labels"],
        "label_policies": state_policy["label_policies"],
        "minimum_supporting_features": minimum_supporting_features,
        "minimum_available_features": minimum_available_features,
        "transition_policy": transition_policy,
        "confidence_policy": confidence_policy,
        "max_feature_age_days": confidence_policy["max_feature_age_days"],
        "provenance": provenance,
        "validated_markov_hmm_result": hmm_result,
        "markov_mapping_policy_hashable": mapping_hashable,
        "hmm_rows": hmm_rows,
        "hmm_source_hash": hmm_source_hash,
        "label_mapping": label_mapping,
        "combination_policy": combination_policy,
    }


def _validate_macro_feature_set(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="macro_feature_set")
    if _required_text(payload, "version") != FEATURE_SET_VERSION:
        raise ValueError(f"macro_feature_set.version must be {FEATURE_SET_VERSION}.")
    if _required_text(payload, "contract_version") != FEATURE_SET_CONTRACT_VERSION:
        raise ValueError(f"macro_feature_set.contract_version must be {FEATURE_SET_CONTRACT_VERSION}.")
    if _required_text(payload, "status") != STATUS_SUCCESS:
        raise ValueError("macro_feature_set.status must be SUCCESS.")
    definitions = _required_list(payload.get("feature_definitions"), name="macro_feature_set.feature_definitions")
    feature_ids: list[str] = []
    seen_feature_ids: set[str] = set()
    for raw in definitions:
        item = _required_mapping(raw, name="feature_definition")
        feature_id = _required_text(item, "feature_id")
        if feature_id in seen_feature_ids:
            raise ValueError("duplicate feature IDs are not allowed.")
        seen_feature_ids.add(feature_id)
        feature_ids.append(feature_id)
    rows = _required_list(payload.get("feature_observations"), name="macro_feature_set.feature_observations")
    normalized_rows: list[dict[str, Any]] = []
    previous_timestamp: str | None = None
    for raw in rows:
        row = _required_mapping(raw, name="feature_observation")
        timestamp = _required_text(row, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("macro_feature_set.feature_observations must be strictly ordered.")
        previous_timestamp = timestamp
        feature_values = _required_mapping(row.get("feature_values"), name="feature_values")
        availability = _required_mapping(
            row.get("feature_availability_timestamps_utc"),
            name="feature_availability_timestamps_utc",
        )
        missing_indicators = _required_mapping(row.get("missing_indicators"), name="missing_indicators")
        for feature_id in feature_ids:
            if feature_id not in feature_values:
                raise ValueError(f"feature_values missing declared feature_id: {feature_id}")
            if feature_id not in availability:
                raise ValueError(f"feature_availability_timestamps_utc missing declared feature_id: {feature_id}")
            if feature_id not in missing_indicators:
                raise ValueError(f"missing_indicators missing declared feature_id: {feature_id}")
        normalized_rows.append(
            {
                "timestamp": timestamp,
                "feature_values": dict(feature_values),
                "feature_availability_timestamps_utc": dict(availability),
                "missing_indicators": {str(key): bool(value) for key, value in missing_indicators.items()},
            }
        )
    return {
        "feature_rows": normalized_rows,
        "feature_ids": feature_ids,
        "macro_feature_set_hash": _required_text(payload, "output_payload_sha256"),
    }


def _validate_state_policy(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="state_policy")
    _reject_unknown_fields(payload, allowed={"allowed_regime_labels", "label_policies"}, name="state_policy")
    allowed_regime_labels = [_required_text_value(item, name="allowed_regime_label") for item in _required_list(
        payload.get("allowed_regime_labels"),
        name="state_policy.allowed_regime_labels",
    )]
    if len(set(allowed_regime_labels)) != len(allowed_regime_labels):
        raise ValueError("duplicate allowed regime labels are not allowed.")
    if INSUFFICIENT_EVIDENCE not in allowed_regime_labels:
        raise ValueError("state_policy.allowed_regime_labels must include INSUFFICIENT_EVIDENCE.")
    label_policies = _required_mapping(payload.get("label_policies"), name="state_policy.label_policies")
    normalized_policies: dict[str, Any] = {}
    classified_labels: list[str] = []
    for label, raw_policy in label_policies.items():
        label_name = str(label)
        if label_name not in allowed_regime_labels:
            raise ValueError(f"unknown label: {label_name}")
        if label_name == INSUFFICIENT_EVIDENCE:
            raise ValueError("INSUFFICIENT_EVIDENCE cannot define explicit rules.")
        classified_labels.append(label_name)
        policy = _required_mapping(raw_policy, name=f"label_policy.{label_name}")
        _reject_unknown_fields(policy, allowed={"minimum_score", "minimum_supporting_rules", "rules"}, name=f"label_policy.{label_name}")
        rules = _required_list(policy.get("rules"), name=f"label_policy.{label_name}.rules")
        normalized_rules: list[dict[str, Any]] = []
        for raw_rule in rules:
            rule = _required_mapping(raw_rule, name=f"rule.{label_name}")
            normalized_rules.append(_validate_rule(rule, allowed_regime_labels=allowed_regime_labels))
        normalized_policies[label_name] = {
            "minimum_score": _required_non_negative_number(policy, "minimum_score"),
            "minimum_supporting_rules": _required_positive_int(policy, "minimum_supporting_rules"),
            "rules": normalized_rules,
        }
    return {
        "allowed_regime_labels": allowed_regime_labels,
        "classified_labels": classified_labels,
        "label_policies": normalized_policies,
        "hashable": {
            "allowed_regime_labels": allowed_regime_labels,
            "label_policies": normalized_policies,
        },
    }


def _validate_rule(rule: dict[str, Any], *, allowed_regime_labels: list[str]) -> dict[str, Any]:
    operation = _required_text(rule, "operation")
    if operation not in RULE_OPERATIONS:
        raise ValueError(f"unknown operation: {operation}")
    if "target_label" in rule:
        target_label = _required_text(rule, "target_label")
        if target_label not in allowed_regime_labels:
            raise ValueError(f"unknown label: {target_label}")
    normalized: dict[str, Any] = {
        "feature_id": _required_text(rule, "feature_id"),
        "operation": operation,
        "weight": _required_non_negative_number(rule, "weight"),
    }
    if operation in {"greater_than", "less_than"}:
        normalized["threshold"] = _required_finite_number(rule, "threshold")
    elif operation == "between_inclusive":
        lower = _required_finite_number(rule, "lower")
        upper = _required_finite_number(rule, "upper")
        if upper < lower:
            raise ValueError("between_inclusive upper must be greater than or equal to lower.")
        normalized["lower"] = lower
        normalized["upper"] = upper
    else:
        normalized["value"] = _json_scalar(rule.get("value"), name="value")
    return normalized


def _validate_markov_hmm_result(value: Any) -> dict[str, Any]:
    if value is None:
        raise ValueError("validated_markov_hmm_result is required for markov_hmm_candidate mode.")
    payload = _required_mapping(value, name="validated_markov_hmm_result")
    if _required_text(payload, "regime_pilot_version") != HMM_VERSION:
        raise ValueError(f"validated_markov_hmm_result.regime_pilot_version must be {HMM_VERSION}.")
    if _required_text(payload, "final_status") != STATUS_COMPLETED:
        raise ValueError("validated_markov_hmm_result.final_status must be COMPLETED.")
    if _required_non_negative_number(payload, "provider_calls_used") != 0:
        raise ValueError("validated_markov_hmm_result.provider_calls_used must be 0.")
    if _required_bool(payload, "registry_write_performed"):
        raise ValueError("validated_markov_hmm_result.registry_write_performed must be false.")
    if _required_non_negative_number(payload, "broker_actions_used") != 0:
        raise ValueError("validated_markov_hmm_result.broker_actions_used must be 0.")
    if _required_bool(payload, "deployment_gate_run"):
        raise ValueError("validated_markov_hmm_result.deployment_gate_run must be false.")
    if _required_bool(payload, "promotion_performed"):
        raise ValueError("validated_markov_hmm_result.promotion_performed must be false.")
    if _required_bool(payload, "production_runtime_supported"):
        raise ValueError("validated_markov_hmm_result.production_runtime_supported must be false.")
    if _canonical_sha256(_without_field(payload, "output_payload_sha256")) != _required_text(payload, "output_payload_sha256"):
        raise ValueError("validated_markov_hmm_result output hash mismatch.")
    regime_labels = _required_list(payload.get("regime_labels"), name="validated_markov_hmm_result.regime_labels")
    previous_timestamp: str | None = None
    normalized_labels: list[dict[str, Any]] = []
    for raw in regime_labels:
        item = _required_mapping(raw, name="validated_markov_hmm_result.regime_label")
        timestamp = _required_text(item, "timestamp")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("validated_markov_hmm_result.regime_labels must be strictly ordered.")
        previous_timestamp = timestamp
        normalized_labels.append({"timestamp": timestamp, "regime_label": _required_text(item, "regime_label")})
    return {
        **payload,
        "regime_labels": normalized_labels,
    }


def _validate_markov_mapping_policy(value: Any, *, allowed_labels: list[str], hmm_labels: set[str]) -> dict[str, Any]:
    payload = _required_mapping(value, name="markov_mapping_policy")
    _reject_unknown_fields(payload, allowed={"label_mapping", "combination_policy"}, name="markov_mapping_policy")
    mapping = _required_mapping(payload.get("label_mapping"), name="markov_mapping_policy.label_mapping")
    normalized_mapping: dict[str, str] = {}
    for hmm_label in sorted(hmm_labels):
        if hmm_label not in mapping:
            raise ValueError(f"markov_mapping_policy.label_mapping missing HMM label: {hmm_label}")
        mapped_label = _required_text(mapping, hmm_label)
        if mapped_label not in allowed_labels:
            raise ValueError(f"unknown label: {mapped_label}")
        normalized_mapping[hmm_label] = mapped_label
    combination_policy = _validate_combination_policy(payload.get("combination_policy"), allowed_labels=allowed_labels)
    hashable = {
        "label_mapping": normalized_mapping,
        "combination_policy": combination_policy,
    }
    return {
        "label_mapping": normalized_mapping,
        "combination_policy": combination_policy,
        "hashable": hashable,
    }


def _validate_combination_policy(value: Any, *, allowed_labels: list[str]) -> dict[str, Any]:
    payload = _required_mapping(value, name="combination_policy")
    mode = _required_text(payload, "mode")
    if mode not in {"annotate", "confirm", "veto", "leave_unchanged"}:
        raise ValueError(f"unknown combination mode: {mode}")
    normalized: dict[str, Any] = {"mode": mode}
    if mode == "veto":
        veto_labels = [_required_text_value(item, name="veto_label") for item in _required_list(payload.get("veto_labels"), name="veto_labels")]
        for label in veto_labels:
            if label not in allowed_labels:
                raise ValueError(f"unknown label: {label}")
        veto_result_label = _required_text(payload, "veto_result_label")
        if veto_result_label not in allowed_labels:
            raise ValueError(f"unknown label: {veto_result_label}")
        normalized["veto_labels"] = veto_labels
        normalized["veto_result_label"] = veto_result_label
    return normalized


def _validate_hmm_alignment(hmm_rows: list[dict[str, Any]], feature_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(hmm_rows) != len(feature_rows):
        raise ValueError("validated_markov_hmm_result timestamps must align exactly with macro_feature_set timestamps.")
    for hmm_row, feature_row in zip(hmm_rows, feature_rows, strict=True):
        if _required_text(hmm_row, "timestamp") != feature_row["timestamp"]:
            raise ValueError("validated_markov_hmm_result timestamps must align exactly with macro_feature_set timestamps.")
    return [dict(item) for item in hmm_rows]


def _validate_transition_policy(value: Any) -> dict[str, bool]:
    payload = _required_mapping(value, name="transition_policy")
    _reject_unknown_fields(payload, allowed={"count_label_changes"}, name="transition_policy")
    return {"count_label_changes": _required_bool(payload, "count_label_changes")}


def _validate_confidence_policy(value: Any) -> dict[str, float]:
    payload = _required_mapping(value, name="confidence_policy")
    _reject_unknown_fields(payload, allowed={"max_feature_age_days"}, name="confidence_policy")
    return {"max_feature_age_days": _required_non_negative_number(payload, "max_feature_age_days")}


def _age_in_days(available_at: str, observed_at: str) -> float:
    available = _parse_timestamp(available_at)
    observed = _parse_timestamp(observed_at)
    return (observed - available).total_seconds() / 86400.0


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _without_field(payload: dict[str, Any], field: str) -> dict[str, Any]:
    copy_payload = dict(payload)
    copy_payload.pop(field, None)
    return copy_payload


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw_value in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw_value, name=f"provenance.{key_name}")
    return normalized


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(unknown)}")


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
    return _required_text_value(value, name=field)


def _required_text_value(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text.")
    return value.strip()


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _required_non_negative_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{field} must be a non-negative finite number.")
    return float(value)


def _required_finite_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    return _numeric(value, name=field)


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean.")
    return value


def _numeric(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number.")
    return float(value)


def _json_scalar(value: Any, *, name: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be JSON scalar compatible.")
        return value
    raise ValueError(f"{name} must be JSON scalar compatible.")
