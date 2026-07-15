"""Deterministic, review-only aggregation of locked Fio and IBKR exposures."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


REQUEST_VERSION = "dual_broker_exposure_risk_request_v1"
RESULT_VERSION = "dual_broker_exposure_risk_result_v1"
CONTRACT_VERSION = "dual_broker_exposure_risk_v1"
_FIELDS = {"version", "risk_request_id", "as_of_timestamp", "base_currency", "fio_inventory_result", "ibkr_universe_result", "existing_ibkr_positions", "proposed_ibkr_intents", "research_execution_mapping_results", "point_in_time_fx_conversion_results", "valuation_evidence", "concentration_classifications", "risk_limits", "provenance"}
_INTENT = {"intent_id", "candidate_id", "quantity", "currency", "valuation_evidence", "issuer", "sector", "theme", "asset_class", "product_type", "research_source_policy", "research_execution_mapping_id", "provenance"}
_LIMITS = {"maximum_single_instrument_percentage", "maximum_issuer_percentage", "maximum_sector_percentage", "maximum_theme_percentage", "maximum_asset_class_percentage", "maximum_currency_percentage", "maximum_broker_percentage", "maximum_product_type_percentage", "maximum_combined_gross_exposure", "maximum_proposed_intent_percentage", "maximum_unvalued_exposure_count", "mapping_overlap_review_policy"}
_FX_FIELDS = {"version", "contract_version", "conversion_id", "base_currency", "conversion_status", "converted_values", "conversion_paths", "selected_observations", "direct_conversions", "inverse_conversions", "cross_conversions", "same_currency_conversions", "rate_ages_seconds", "stale_conversions", "missing_conversions", "blocking_findings", "review_findings", "warnings", "complete_lineage", "input_sha256", "provider_calls_used", "network_used", "filesystem_reads_performed", "filesystem_writes_performed", "registry_write_performed", "broker_actions_used", "paper_trading_performed", "deployment_performed", "production_runtime_supported", "provenance", "output_payload_sha256"}
_MAP_FIELDS = {"version", "contract_version", "mapping_id", "mapping_status", "mapping_type", "research_instrument_identity", "execution_instrument_identity", "material_differences", "tracking_requirements", "execution_route", "automation_allowed", "blocking_findings", "review_findings", "input_sha256", "provenance", "safety_flags", "output_payload_sha256"}
_MAP_TYPES = {"SAME_INSTRUMENT_SAME_LISTING", "SAME_SECURITY_DIFFERENT_LISTING", "ECONOMIC_PROXY", "RELATED_EXPOSURE_NOT_IDENTICAL", "BENCHMARK_ONLY", "NO_EXECUTION_MAPPING"}


def build_dual_broker_exposure_risk(request: dict[str, object]) -> dict[str, object]:
    """Compose supplied child evidence only; never query, identify, or instruct a broker."""
    value = _validate(request)
    fio = _child(value["fio_inventory_result"], "fio_manual_long_term_inventory_v1")
    universe = _child(value["ibkr_universe_result"], "ibkr_active_execution_universe_v1")
    fx, fx_lineage = _fx_children(value["point_in_time_fx_conversion_results"], value)
    mappings, mapping_lineage = _mapping_children(value["research_execution_mapping_results"])
    eligible = {item["candidate_id"]: item for item in universe.get("accepted_instruments", []) if item.get("eligibility_status") == "ELIGIBLE"}
    fio_exposures, unvalued = _fio_exposures(fio, value, fx)
    existing = [_position(item, "IBKR_EXISTING_POSITIONS", value, fx) for item in value["existing_ibkr_positions"]]
    intents = [_intent(item, eligible, value, fx, mappings) for item in value["proposed_ibkr_intents"]]
    if len({item["intent_id"] for item in intents}) != len(intents):
        raise ValueError("duplicate intent_id")
    if len(unvalued) > value["risk_limits"]["maximum_unvalued_exposure_count"]:
        raise ValueError("maximum unvalued exposure count exceeded")

    locked = sum((item["value"] for item in fio_exposures), Decimal("0"))
    existing_value = sum((item["value"] for item in existing), Decimal("0"))
    proposed_value = sum((item["value"] for item in intents), Decimal("0"))
    portfolio_value = locked + existing_value + proposed_value
    capacity = max(Decimal("0"), portfolio_value * value["risk_limits"]["maximum_combined_gross_exposure"] - locked - existing_value)
    decisions, overlap_findings, used = [], [], Decimal("0")
    for item in sorted(intents, key=lambda x: x["intent_id"]):
        overlap = _overlap(item, fio_exposures)
        overlap_findings.extend(overlap)
        requested, permitted = item["value"], max(Decimal("0"), min(item["value"], capacity - used))
        if item["mapping_review"] or overlap:
            decision = _decision(item, "REQUIRE_OVERLAP_REVIEW", requested, permitted, "mapping_overlap_review_policy", "MAPPING_OR_OVERLAP_REVIEW_REQUIRED")
        elif permitted == 0:
            decision = _decision(item, "BLOCK_REVIEW_ONLY", requested, Decimal("0"), "maximum_combined_gross_exposure", "FIO_LOCKED_EXPOSURE_REDUCES_CAPACITY")
        elif permitted < requested:
            decision = _decision(item, "RESIZE_REVIEW_ONLY", requested, permitted, "maximum_combined_gross_exposure", "FIO_LOCKED_EXPOSURE_REDUCES_CAPACITY")
            used += permitted
        else:
            decision = _decision(item, "ACCEPT_AS_PROPOSED", requested, requested, None, "WITHIN_REVIEW_ONLY_LIMITS")
            used += requested
        decisions.append(decision)
    concentrations = _concentrations(fio_exposures + existing + intents, portfolio_value)
    breaches = _breaches(concentrations, value["risk_limits"])
    status = "FAILED_VALIDATION" if breaches else "REVIEW_REQUIRED" if overlap_findings or unvalued or any(x["mapping_review"] for x in intents) else "ACCEPTED_REVIEW_ONLY"
    child_hashes = {"fio_inventory_result": fio["output_payload_sha256"], "ibkr_universe_result": universe["output_payload_sha256"]}
    child_hashes.update({f"fx:{key}": item["child_output_sha256"] for key, item in fx_lineage.items()})
    child_hashes.update({f"mapping:{key}": item["output_payload_sha256"] for key, item in mapping_lineage.items()})
    result: dict[str, Any] = {
        "version": RESULT_VERSION, "contract_version": CONTRACT_VERSION, "status": status,
        "risk_request_id": value["risk_request_id"], "as_of_timestamp": value["as_of_timestamp"], "base_currency": value["base_currency"],
        "portfolio_totals": {"total_valued_portfolio_amount": _d(portfolio_value), "unvalued_exposure_count": len(unvalued), "combined_gross_exposure": _d(portfolio_value), "combined_net_long_exposure": _d(portfolio_value), "remaining_risk_capacity": _d(max(Decimal("0"), capacity - used))},
        "account_exposures": {"fio_gross_exposure": _d(locked), "fio_locked_exposure": _d(locked), "ibkr_existing_gross_exposure": _d(existing_value), "ibkr_proposed_gross_exposure": _d(proposed_value), "ibkr_controllable_exposure": _d(existing_value + used)},
        "fio_locked_exposure": _d(locked), "ibkr_existing_exposure": _d(existing_value), "ibkr_proposed_exposure": _d(proposed_value), "unvalued_items": sorted(unvalued),
        "fx_conversion_lineage": [fx_lineage[key] for key in sorted(fx_lineage)], "mapping_lineage": [mapping_lineage[key] for key in sorted(mapping_lineage)],
        "exact_instrument_groups": _groups(fio_exposures + existing + intents, "instrument"), "same_security_groups": _groups(fio_exposures + existing + intents, "security"),
        "economic_proxy_groups": _groups(fio_exposures + existing + intents, "economic_proxy"), "related_exposure_groups": _groups(fio_exposures + existing + intents, "related_exposure"),
        "concentration_metrics": concentrations, "overlap_findings": sorted(set(overlap_findings)), "intent_decisions": decisions, "resize_calculations": [copy.deepcopy(x) for x in decisions if x["decision"] == "RESIZE_REVIEW_ONLY"],
        "binding_limits": sorted(breaches), "child_contract_lineage": {"fio_inventory_sha256": fio["output_payload_sha256"], "ibkr_universe_sha256": universe["output_payload_sha256"]}, "recomputed_child_hashes": child_hashes,
        "input_sha256": value["input_sha256"], "findings": sorted(set(breaches + overlap_findings)), "provenance": copy.deepcopy(value["provenance"]),
        "safety_flags": {"provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "Fio_actions_performed": False, "IBKR_actions_performed": False, "paper_trading_performed": False, "live_trading_performed": False, "deployment_performed": False, "registry_write_performed": False, "automatic_strategy_application_performed": False, "automatic_capital_allocation_performed": False, "production_runtime_supported": False},
    }
    result["output_payload_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _validate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _FIELDS or raw.get("version") != REQUEST_VERSION:
        raise ValueError("unknown, missing, or invalid request field")
    value = copy.deepcopy(raw)
    for key in ("risk_request_id", "base_currency"):
        if not isinstance(value[key], str) or not value[key].strip(): raise ValueError(f"invalid {key}")
    _timestamp(value["as_of_timestamp"], "as_of_timestamp")
    if not all(isinstance(value[key], list) for key in ("existing_ibkr_positions", "proposed_ibkr_intents", "research_execution_mapping_results", "point_in_time_fx_conversion_results")):
        raise ValueError("exposure collections must be lists")
    if not all(isinstance(value[key], dict) for key in ("valuation_evidence", "concentration_classifications", "provenance")):
        raise ValueError("evidence and provenance must be objects")
    limits = value["risk_limits"]
    if not isinstance(limits, dict) or set(limits) != _LIMITS: raise ValueError("missing required risk limits")
    for key in _LIMITS - {"maximum_unvalued_exposure_count", "mapping_overlap_review_policy"}: limits[key] = _pct(limits[key], key)
    if not isinstance(limits["maximum_unvalued_exposure_count"], int) or isinstance(limits["maximum_unvalued_exposure_count"], bool) or limits["maximum_unvalued_exposure_count"] < 0: raise ValueError("invalid maximum_unvalued_exposure_count")
    if limits["mapping_overlap_review_policy"] != "REQUIRE_REVIEW_WHEN_UNAVAILABLE": raise ValueError("unsafe mapping overlap review policy")
    value["input_sha256"] = _sha(value)
    return value


def _child(raw: Any, contract: str) -> dict[str, Any]:
    if not isinstance(raw, dict): raise ValueError("malformed child")
    child = copy.deepcopy(raw); declared = child.pop("output_payload_sha256", None)
    if not isinstance(declared, str) or _sha(child) != declared: raise ValueError("child hash mismatch")
    if child.get("contract_version") != contract: raise ValueError("malformed child contract")
    child["output_payload_sha256"] = declared
    return child


def _fx_children(raw: list[Any], request: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    converted, lineage = {}, {}
    as_of = _timestamp(request["as_of_timestamp"], "as_of_timestamp")
    for raw_child in raw:
        if not isinstance(raw_child, dict) or set(raw_child) != _FX_FIELDS: raise ValueError("malformed FX child fields")
        child = _child(raw_child, "point_in_time_fx_conversion_contract_v1")
        if child.get("conversion_status") != "SUCCESS" or child.get("stale_conversions") or child.get("missing_conversions") or child.get("blocking_findings"): raise ValueError("failed or stale FX child")
        if child.get("base_currency") != request["base_currency"] or not isinstance(child.get("converted_values"), list): raise ValueError("FX target currency mismatch")
        for item in child["converted_values"]:
            required = {"instrument_id", "source_currency", "target_currency", "source_value", "converted_value", "effective_rate", "path_type", "path_id", "decision_timestamp", "selected_observation_ids", "source_hashes", "rate_ages_seconds", "arithmetic_formula"}
            if not isinstance(item, dict) or set(item) != required: raise ValueError("malformed FX conversion")
            valuation_id = item["instrument_id"]
            if not isinstance(valuation_id, str) or valuation_id in converted: raise ValueError("duplicate or ambiguous FX child")
            if item["target_currency"] != request["base_currency"]: raise ValueError("FX target currency mismatch")
            evidence = _timestamp(item["decision_timestamp"], "FX evidence timestamp")
            if evidence > as_of or not isinstance(item["rate_ages_seconds"], int) or item["rate_ages_seconds"] < 0: raise ValueError("incompatible FX evidence timestamp or staleness")
            if item["path_type"] not in {"SAME_CURRENCY", "DIRECT", "INVERSE", "CROSS"}: raise ValueError("unsupported conversion path")
            if item["path_type"] == "SAME_CURRENCY" and (item["effective_rate"] != "1.000000" or item["selected_observation_ids"]): raise ValueError("invalid same-currency FX child")
            source, target, rate, amount = _money(item["source_value"]), _money(item["converted_value"]), _positive(item["effective_rate"], "FX rate"), _money(item["converted_value"])
            converted[valuation_id] = {"source_currency": item["source_currency"], "source_value": source, "value": amount}
            lineage[valuation_id] = {"exposure_id": valuation_id, "source_currency": item["source_currency"], "target_currency": item["target_currency"], "source_monetary_amount": _d(source), "conversion_rate": item["effective_rate"], "converted_base_currency_amount": _d(target), "conversion_method": item["path_type"], "selected_observation_identity": list(item["selected_observation_ids"]), "fx_child_input_sha256": child["input_sha256"], "fx_child_output_sha256": child["output_payload_sha256"], "child_output_sha256": child["output_payload_sha256"], "evidence_timestamp": item["decision_timestamp"], "staleness_evidence_seconds": item["rate_ages_seconds"], "provenance": copy.deepcopy(child["provenance"])}
    return converted, lineage


def _mapping_children(raw: list[Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    mappings, lineage = {}, {}
    for raw_child in raw:
        if not isinstance(raw_child, dict) or set(raw_child) != _MAP_FIELDS: raise ValueError("malformed M31B child fields")
        child = _child(raw_child, "research_execution_instrument_mapping_v1")
        mapping_id = child.get("mapping_id")
        if not isinstance(mapping_id, str) or not mapping_id or mapping_id in mappings or child.get("mapping_type") not in _MAP_TYPES: raise ValueError("duplicate or malformed M31B mapping")
        if child.get("mapping_status") not in {"PASS", "REVIEW_REQUIRED"} or not isinstance(child.get("research_instrument_identity"), dict): raise ValueError("invalid M31B mapping status")
        if child["mapping_type"] in {"BENCHMARK_ONLY", "NO_EXECUTION_MAPPING"} and child.get("execution_instrument_identity") is not None: raise ValueError("invalid non-execution mapping")
        mappings[mapping_id] = child
        lineage[mapping_id] = {"mapping_id": mapping_id, "mapping_type": child["mapping_type"], "mapping_status": child["mapping_status"], "research_instrument_identity": copy.deepcopy(child["research_instrument_identity"]), "execution_instrument_identity": copy.deepcopy(child["execution_instrument_identity"]), "tracking_requirements": copy.deepcopy(child["tracking_requirements"]), "material_differences": copy.deepcopy(child["material_differences"]), "input_sha256": child["input_sha256"], "output_payload_sha256": child["output_payload_sha256"], "provenance": copy.deepcopy(child["provenance"])}
    return mappings, lineage


def _converted(valuation_id: str, source_currency: str, source_value: Any, fx: dict[str, dict[str, Any]]) -> Decimal:
    item = fx.get(valuation_id)
    if item is None: raise ValueError("missing required FX result")
    if item["source_currency"] != source_currency or item["source_value"] != _money(source_value): raise ValueError("FX source currency or value mismatch")
    return item["value"]


def _fio_exposures(fio: dict[str, Any], request: dict[str, Any], fx: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    if fio.get("validation_status") not in {"PASS", "REVIEW_REQUIRED"} or not isinstance(fio.get("validated_positions"), list): raise ValueError("malformed M31C child")
    exposures, unvalued = [], []
    for position in fio["validated_positions"]:
        if position.get("automatic_liquidation_allowed") is not False or position.get("manual_action_only") is not True: raise ValueError("Fio automatic liquidation prohibited")
        if position.get("market_value") is None: unvalued.append(position["position_id"]); continue
        identity = position.get("identity_routing_result", {})
        key = identity.get("identity_key")
        if not isinstance(key, str): raise ValueError("malformed M31C child position")
        cls = _classification(request, position["position_id"])
        exposures.append(_exposure(position["position_id"], position["market_value"], position["currency"], "FIO", key, identity.get("instrument", {}), cls, fx))
    return exposures, unvalued


def _position(raw: Any, broker: str, request: dict[str, Any], fx: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("position_id"), str) or not isinstance(raw.get("currency"), str): raise ValueError("malformed existing IBKR position")
    return _exposure(raw["position_id"], raw.get("market_value"), raw["currency"], broker, raw.get("identity_key", raw["position_id"]), raw, _classification(request, raw["position_id"]), fx)


def _intent(raw: Any, eligible: dict[str, Any], request: dict[str, Any], fx: dict[str, dict[str, Any]], mappings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _INTENT: raise ValueError("intent has unknown or missing field")
    item = copy.deepcopy(raw)
    if item["candidate_id"] not in eligible or item["research_source_policy"] not in {"DERIVED_FROM_RESEARCH", "MANUALLY_SUPPLIED_STANDALONE"}: raise ValueError("ineligible instrument or invalid research source policy")
    if not isinstance(item["valuation_evidence"], dict) or set(item["valuation_evidence"]) != {"market_value", "timestamp"}: raise ValueError("missing valuation evidence")
    _timestamp(item["valuation_evidence"]["timestamp"], "valuation timestamp")
    for key in ("intent_id", "currency", "issuer", "sector", "theme", "asset_class", "product_type"):
        if not isinstance(item[key], str) or not item[key].strip(): raise ValueError(f"invalid intent {key}")
    _positive(item["quantity"], "quantity")
    candidate = eligible[item["candidate_id"]]
    mapping_review, mapping_type = False, None
    mapping_id = item["research_execution_mapping_id"]
    if item["research_source_policy"] == "DERIVED_FROM_RESEARCH":
        if not isinstance(mapping_id, str) or mapping_id not in mappings: raise ValueError("missing required M31B mapping")
        mapping = mappings[mapping_id]; mapping_type = mapping["mapping_type"]
        execution = mapping.get("execution_instrument_identity")
        if mapping_type in {"BENCHMARK_ONLY", "NO_EXECUTION_MAPPING"} or not isinstance(execution, dict): raise ValueError("non-executable M31B mapping")
        if not _same_listing(execution, candidate["identity_routing_result"]["instrument"]): raise ValueError("M31B execution identity does not match selected listing")
        mapping_review = mapping["mapping_status"] != "PASS"
    elif mapping_id is not None: raise ValueError("standalone intent must not provide a mapping")
    result = _exposure(item["intent_id"], item["valuation_evidence"]["market_value"], item["currency"], "IBKR", candidate["identity_key"], candidate["identity_routing_result"]["instrument"], {key: item[key] for key in ("issuer", "sector", "theme", "asset_class", "product_type")}, fx)
    result.update({"intent_id": item["intent_id"], "mapping_id": mapping_id, "mapping_type": mapping_type, "mapping_research_identity": copy.deepcopy(mappings[mapping_id]["research_instrument_identity"]) if mapping_id else None, "mapping_review": mapping_review, "economic_proxy": mapping_id if mapping_type == "ECONOMIC_PROXY" else result["economic_proxy"], "related_exposure": mapping_id if mapping_type == "RELATED_EXPOSURE_NOT_IDENTICAL" else result["related_exposure"]})
    return result


def _exposure(exposure_id: str, amount: Any, currency: str, broker: str, instrument: str, instrument_data: Any, classification: dict[str, str], fx: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(instrument, str) or not isinstance(instrument_data, dict): raise ValueError("malformed instrument identity")
    return {"id": exposure_id, "value": _converted(exposure_id, currency, amount, fx), "broker": broker, "currency": currency, "instrument": instrument, "instrument_data": copy.deepcopy(instrument_data), "security": instrument_data.get("isin", instrument), "economic_proxy": classification.get("economic_proxy_group", "NONE"), "related_exposure": classification.get("related_exposure_group", "NONE"), **classification}


def _classification(request: dict[str, Any], exposure_id: str) -> dict[str, str]:
    raw = request["concentration_classifications"].get(exposure_id)
    required = {"issuer", "sector", "theme", "asset_class", "product_type"}
    if not isinstance(raw, dict) or not required.issubset(raw) or set(raw) - (required | {"economic_proxy_group", "related_exposure_group"}): raise ValueError("missing explicit concentration classification")
    return {key: raw.get(key, "NONE") for key in required | {"economic_proxy_group", "related_exposure_group"}}


def _same_listing(mapping_identity: dict[str, Any], candidate_identity: dict[str, Any]) -> bool:
    return all(mapping_identity.get(key) == candidate_identity.get(key) for key in ("instrument_id", "isin", "selected_exchange", "exchange_ticker"))


def _overlap(intent: dict[str, Any], fio: list[dict[str, Any]]) -> list[str]:
    findings = []
    lineage = f":MAPPING:{intent['mapping_id']}" if intent.get("mapping_id") else ""
    for item in fio:
        research = intent.get("mapping_research_identity")
        mapping_type = intent.get("mapping_type")
        maps_exact = mapping_type == "SAME_INSTRUMENT_SAME_LISTING" and isinstance(research, dict) and _same_listing(research, item["instrument_data"])
        maps_security = mapping_type == "SAME_SECURITY_DIFFERENT_LISTING" and isinstance(research, dict) and research.get("isin") == item["instrument_data"].get("isin")
        maps_economic = mapping_type in {"ECONOMIC_PROXY", "RELATED_EXPOSURE_NOT_IDENTICAL"} and isinstance(research, dict) and research.get("instrument_id") == item["instrument_data"].get("instrument_id")
        if maps_exact or item["instrument"] == intent["instrument"]: findings.append(f"EXACT_INSTRUMENT_OVERLAP:{item['id']}:{intent['intent_id']}{lineage}")
        elif maps_security or item["security"] == intent["security"]: findings.append(f"SAME_SECURITY_OVERLAP:{item['id']}:{intent['intent_id']}{lineage}")
        elif maps_economic: findings.append(f"{mapping_type}_OVERLAP:{item['id']}:{intent['intent_id']}{lineage}")
        elif item["sector"] == intent["sector"] or item["theme"] == intent["theme"]: findings.append(f"RELATED_EXPOSURE_REVIEW:{item['id']}:{intent['intent_id']}{lineage}")
    return findings


def _decision(item: dict[str, Any], decision: str, requested: Decimal, permitted: Decimal, binding: str | None, reason: str) -> dict[str, Any]:
    return {"intent_id": item["intent_id"], "decision": decision, "requested_notional": _d(requested), "maximum_permitted_notional": _d(permitted), "binding_limit": binding, "reason": reason}


def _groups(items: list[dict[str, Any]], key: str) -> dict[str, str]:
    totals: dict[str, Decimal] = {}
    for item in items: totals[item[key]] = totals.get(item[key], Decimal("0")) + item["value"]
    return {name: _d(value) for name, value in sorted(totals.items())}


def _concentrations(items: list[dict[str, Any]], total: Decimal) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, Decimal]] = {key: {} for key in ("single_instrument", "issuer", "sector", "theme", "asset_class", "currency", "broker", "product_type", "mapped_economic_exposure")}
    for item in items:
        for name, key in (("single_instrument", "instrument"), ("issuer", "issuer"), ("sector", "sector"), ("theme", "theme"), ("asset_class", "asset_class"), ("currency", "currency"), ("broker", "broker"), ("product_type", "product_type"), ("mapped_economic_exposure", "economic_proxy")):
            result[name][item[key]] = result[name].get(item[key], Decimal("0")) + item["value"]
    return {name: {key: _d(value / total if total else Decimal("0")) for key, value in sorted(group.items())} for name, group in result.items()}


def _breaches(metrics: dict[str, dict[str, str]], limits: dict[str, Any]) -> list[str]:
    names = {"single_instrument": "maximum_single_instrument_percentage", "issuer": "maximum_issuer_percentage", "sector": "maximum_sector_percentage", "theme": "maximum_theme_percentage", "asset_class": "maximum_asset_class_percentage", "currency": "maximum_currency_percentage", "broker": "maximum_broker_percentage", "product_type": "maximum_product_type_percentage"}
    return sorted(f"{name}:{key}" for name, limit in names.items() for key, value in metrics[name].items() if Decimal(value) > limits[limit])


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str): raise ValueError(f"invalid {name}")
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise ValueError(f"invalid {name}") from exc
    if parsed.tzinfo is None: raise ValueError(f"invalid {name}")
    return parsed


def _money(value: Any) -> Decimal:
    try: result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc: raise ValueError("missing valuation") from exc
    if not result.is_finite() or result < 0: raise ValueError("invalid valuation")
    return result


def _positive(value: Any, name: str) -> Decimal:
    result = _money(value)
    if result <= 0: raise ValueError(f"invalid {name}")
    return result


def _pct(value: Any, name: str) -> Decimal:
    result = _money(value)
    if result > 1: raise ValueError(f"invalid {name}")
    return result


def _d(value: Decimal) -> str: return format(value.quantize(Decimal("0.01")), "f")
def _sha(value: Any) -> str: return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False, default=str).encode()).hexdigest()
