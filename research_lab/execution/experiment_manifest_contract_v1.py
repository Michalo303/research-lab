from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


REQUEST_VERSION = "experiment_manifest_contract_request_v1"
MANIFEST_VERSION = "experiment_manifest_contract_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_experiment_manifest_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    input_sha256 = _canonical_sha256(validated)
    result: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "experiment_id": validated["experiment_id"],
        "strategy_identity": validated["strategy_identity"],
        "immutable_input_hashes": validated["immutable_input_hashes"],
        "dataset_identity": validated["dataset_identity"],
        "evaluation_period_identity": validated["evaluation_period_identity"],
        "parameter_schema": validated["parameter_schema"],
        "baseline_parameter_set": validated["baseline_parameter_set"],
        "permitted_variants": validated["permitted_variants"],
        "required_evaluators": validated["required_evaluators"],
        "robustness_policy": validated["robustness_policy"],
        "complexity_budget": validated["complexity_budget"],
        "iteration_budget": validated["iteration_budget"],
        "revision_budget": validated["revision_budget"],
        "retry_budget": validated["retry_budget"],
        "knowledge_note_ids": validated["knowledge_note_ids"],
        "required_human_gates": validated["required_human_gates"],
        "execution_authority_granted": False,
        "persistence_performed": False,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "promotion_performed": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "production_runtime_supported": False,
        "input_sha256": input_sha256,
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "experiment_id",
            "strategy_identity",
            "immutable_input_hashes",
            "dataset_identity",
            "evaluation_period_identity",
            "parameter_schema",
            "baseline_parameter_set",
            "permitted_variants",
            "required_evaluators",
            "robustness_policy",
            "complexity_budget",
            "iteration_budget",
            "revision_budget",
            "retry_budget",
            "knowledge_note_ids",
            "required_human_gates",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")

    strategy_identity = _validate_strategy_identity(payload.get("strategy_identity"))
    dataset_identity = _validate_dataset_identity(payload.get("dataset_identity"))
    evaluation_period_identity = _validate_evaluation_period_identity(payload.get("evaluation_period_identity"))
    parameter_schema = _validate_parameter_schema(payload.get("parameter_schema"))
    baseline_parameter_set = _validate_parameter_values(
        payload.get("baseline_parameter_set"),
        schema=parameter_schema,
        name="baseline_parameter_set",
    )
    permitted_variants = _validate_permitted_variants(payload.get("permitted_variants"), schema=parameter_schema)
    robustness_policy = _required_mapping(payload.get("robustness_policy"), name="robustness_policy")
    complexity_budget = _validate_complexity_budget(payload.get("complexity_budget"))
    immutable_input_hashes = _validate_immutable_input_hashes(
        payload.get("immutable_input_hashes"),
        strategy_identity=strategy_identity,
        dataset_identity=dataset_identity,
        evaluation_period_identity=evaluation_period_identity,
        parameter_schema=parameter_schema,
        baseline_parameter_set=baseline_parameter_set,
        robustness_policy=robustness_policy,
        complexity_budget=complexity_budget,
        permitted_variants=permitted_variants,
    )

    return {
        "version": version,
        "experiment_id": _required_text(payload, "experiment_id"),
        "strategy_identity": strategy_identity,
        "immutable_input_hashes": immutable_input_hashes,
        "dataset_identity": dataset_identity,
        "evaluation_period_identity": evaluation_period_identity,
        "parameter_schema": parameter_schema,
        "baseline_parameter_set": baseline_parameter_set,
        "permitted_variants": permitted_variants,
        "required_evaluators": _required_unique_text_list(payload.get("required_evaluators"), name="required_evaluators"),
        "robustness_policy": robustness_policy,
        "complexity_budget": complexity_budget,
        "iteration_budget": _required_positive_int(payload, "iteration_budget"),
        "revision_budget": _required_non_negative_int(payload, "revision_budget"),
        "retry_budget": _required_non_negative_int(payload, "retry_budget"),
        "knowledge_note_ids": _required_unique_text_list(payload.get("knowledge_note_ids"), name="knowledge_note_ids"),
        "required_human_gates": _required_unique_text_list(payload.get("required_human_gates"), name="required_human_gates"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_strategy_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="strategy_identity")
    _reject_unknown_fields(payload, allowed={"strategy_id", "strategy_builder", "strategy_version"}, name="strategy_identity")
    strategy_builder = _required_text(payload, "strategy_builder")
    if strategy_builder != "swing_trend_filtered_pullback":
        raise ValueError("strategy_identity.strategy_builder must be swing_trend_filtered_pullback.")
    return {
        "strategy_id": _required_text(payload, "strategy_id"),
        "strategy_builder": strategy_builder,
        "strategy_version": _required_text(payload, "strategy_version"),
    }


def _validate_dataset_identity(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="dataset_identity")
    _reject_unknown_fields(payload, allowed={"dataset_id", "data_source", "symbol", "bar_count"}, name="dataset_identity")
    return {
        "dataset_id": _required_text(payload, "dataset_id"),
        "data_source": _required_text(payload, "data_source"),
        "symbol": _required_text(payload, "symbol"),
        "bar_count": _required_positive_int(payload, "bar_count"),
    }


def _validate_evaluation_period_identity(value: Any) -> dict[str, str]:
    payload = _required_mapping(value, name="evaluation_period_identity")
    _reject_unknown_fields(
        payload,
        allowed={"window_id", "train_start", "train_end", "test_start", "test_end"},
        name="evaluation_period_identity",
    )
    return {
        "window_id": _required_text(payload, "window_id"),
        "train_start": _required_text(payload, "train_start"),
        "train_end": _required_text(payload, "train_end"),
        "test_start": _required_text(payload, "test_start"),
        "test_end": _required_text(payload, "test_end"),
    }


def _validate_parameter_schema(value: Any) -> dict[str, list[dict[str, Any]]]:
    payload = _required_mapping(value, name="parameter_schema")
    _reject_unknown_fields(payload, allowed={"parameters"}, name="parameter_schema")
    items = _required_non_empty_list(payload.get("parameters"), name="parameter_schema.parameters")
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in items:
        entry = _required_mapping(item, name="parameter_schema_entry")
        _reject_unknown_fields(
            entry,
            allowed={"name", "type", "minimum", "maximum", "allowed_values"},
            name="parameter_schema_entry",
        )
        name = _required_text(entry, "name")
        if name in seen_names:
            raise ValueError("parameter_schema names must be unique.")
        seen_names.add(name)
        parameter_type = _required_text(entry, "type")
        if parameter_type not in {"int", "float", "bool", "str"}:
            raise ValueError(f"parameter_schema_entry.type for {name} is invalid.")
        normalized_entry: dict[str, Any] = {"name": name, "type": parameter_type}
        if "minimum" in entry:
            normalized_entry["minimum"] = _required_finite_number(entry, "minimum")
        if "maximum" in entry:
            normalized_entry["maximum"] = _required_finite_number(entry, "maximum")
        if "allowed_values" in entry:
            normalized_entry["allowed_values"] = _required_non_empty_list(entry.get("allowed_values"), name=f"{name}.allowed_values")
        if "minimum" in normalized_entry and "maximum" in normalized_entry and normalized_entry["minimum"] > normalized_entry["maximum"]:
            raise ValueError(f"parameter_schema_entry range for {name} is invalid.")
        normalized.append(normalized_entry)
    return {"parameters": normalized}


def _validate_parameter_values(value: Any, *, schema: dict[str, list[dict[str, Any]]], name: str) -> dict[str, Any]:
    payload = _required_mapping(value, name=name)
    allowed_names = {item["name"] for item in schema["parameters"]}
    _reject_unknown_fields(payload, allowed=allowed_names, name=name)
    normalized: dict[str, Any] = {}
    for parameter in schema["parameters"]:
        parameter_name = parameter["name"]
        if parameter_name not in payload:
            raise ValueError(f"{name}.{parameter_name} is required.")
        normalized[parameter_name] = _validate_parameter_value(payload[parameter_name], parameter=parameter, context=name)
    return normalized


def _validate_permitted_variants(value: Any, *, schema: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    items = _required_non_empty_list(value, name="permitted_variants")
    normalized: list[dict[str, Any]] = []
    seen_variant_ids: set[str] = set()
    allowed_names = {item["name"] for item in schema["parameters"]}
    for item in items:
        payload = _required_mapping(item, name="permitted_variant")
        _reject_unknown_fields(payload, allowed={"variant_id", "parameter_overrides"}, name="permitted_variant")
        variant_id = _required_text(payload, "variant_id")
        if variant_id in seen_variant_ids:
            raise ValueError("permitted_variants.variant_id values must be unique.")
        seen_variant_ids.add(variant_id)
        overrides = _required_mapping(payload.get("parameter_overrides"), name="parameter_overrides")
        _reject_unknown_fields(overrides, allowed=allowed_names, name=f"{variant_id}.parameter_overrides")
        normalized_overrides: dict[str, Any] = {}
        for parameter_name, raw in overrides.items():
            parameter = next(item for item in schema["parameters"] if item["name"] == parameter_name)
            normalized_overrides[parameter_name] = _validate_parameter_value(
                raw,
                parameter=parameter,
                context=f"{variant_id}.parameter_overrides",
            )
        normalized.append({"variant_id": variant_id, "parameter_overrides": normalized_overrides})
    return normalized


def _validate_parameter_value(value: Any, *, parameter: dict[str, Any], context: str) -> Any:
    name = parameter["name"]
    parameter_type = parameter["type"]
    if parameter_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{context}.{name} must be an int.")
    elif parameter_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{context}.{name} must be numeric.")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{context}.{name} must be finite.")
    elif parameter_type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{context}.{name} must be a boolean.")
    else:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{context}.{name} must be non-empty text.")
        value = value.strip()

    minimum = parameter.get("minimum")
    maximum = parameter.get("maximum")
    if minimum is not None and value < minimum:
        raise ValueError(f"{context}.{name} is below the permitted minimum.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{context}.{name} exceeds the permitted maximum.")
    allowed_values = parameter.get("allowed_values")
    if allowed_values is not None and value not in allowed_values:
        raise ValueError(f"{context}.{name} must be one of the allowed values.")
    return value


def _validate_complexity_budget(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="complexity_budget")
    _reject_unknown_fields(payload, allowed={"max_parameter_count", "max_complexity_score"}, name="complexity_budget")
    return {
        "max_parameter_count": _required_positive_int(payload, "max_parameter_count"),
        "max_complexity_score": _required_finite_number(payload, "max_complexity_score"),
    }


def _validate_immutable_input_hashes(
    value: Any,
    *,
    strategy_identity: dict[str, Any],
    dataset_identity: dict[str, Any],
    evaluation_period_identity: dict[str, Any],
    parameter_schema: dict[str, Any],
    baseline_parameter_set: dict[str, Any],
    robustness_policy: dict[str, Any],
    complexity_budget: dict[str, Any],
    permitted_variants: list[dict[str, Any]],
) -> dict[str, str]:
    payload = _required_mapping(value, name="immutable_input_hashes")
    expected_payloads = {
        "strategy_identity": strategy_identity,
        "dataset_identity": dataset_identity,
        "evaluation_period_identity": evaluation_period_identity,
        "parameter_schema": parameter_schema,
        "baseline_parameter_set": baseline_parameter_set,
        "robustness_policy": robustness_policy,
        "complexity_budget": complexity_budget,
        "permitted_variants": permitted_variants,
    }
    _reject_unknown_fields(payload, allowed=set(expected_payloads), name="immutable_input_hashes")
    normalized: dict[str, str] = {}
    for field, target in expected_payloads.items():
        raw_hash = payload.get(field)
        if not isinstance(raw_hash, str) or not _SHA256_RE.fullmatch(raw_hash):
            raise ValueError(f"immutable_input_hashes.{field} must be a lowercase sha256 hex digest.")
        expected_hash = _canonical_sha256(target)
        if raw_hash != expected_hash:
            raise ValueError(f"immutable_input_hashes.{field} does not match the supplied {field}.")
        normalized[field] = raw_hash
    return normalized


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


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_non_empty_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_unique_text_list(value: Any, *, name: str) -> list[str]:
    items = _required_non_empty_list(value, name=name)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} entries must be non-empty text.")
        normalized_item = item.strip()
        if normalized_item in seen:
            raise ValueError(f"{name} entries must be unique.")
        seen.add(normalized_item)
        normalized.append(normalized_item)
    return sorted(normalized)


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _required_non_negative_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _required_finite_number(payload: dict[str, Any], field: str) -> int | float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return value


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | None | bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
