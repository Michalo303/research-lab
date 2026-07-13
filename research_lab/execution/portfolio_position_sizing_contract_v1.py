from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from research_lab.execution.risk_execution_contract_v1 import (
    build_protective_exit_contract,
)


REQUEST_VERSION = "portfolio_position_sizing_request_v1"
RESULT_VERSION = "portfolio_position_sizing_contract_v1"
POLICIES = {
    "FIXED_FRACTIONAL_RISK",
    "ATR_SIZING",
    "VOLATILITY_TARGETING",
    "BOUNDED_FRACTIONAL_KELLY_CANDIDATE",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOLERANCE = 1e-9


def build_portfolio_position_sizing_contract(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Build bounded review-only quantities without broker-order authority."""
    _mapping(request, "position sizing request")
    _reject_unknown(
        request,
        {
            "version",
            "as_of_timestamp",
            "policy",
            "total_research_capital",
            "available_capital",
            "policy_parameters",
            "quantity_rounding",
            "allocations",
            "provenance",
        },
        "position sizing request",
    )
    if request.get("version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    as_of = _timestamp(request.get("as_of_timestamp"), "as_of_timestamp")
    policy = _text(request.get("policy"), "policy")
    if policy not in POLICIES:
        raise ValueError("policy is unsupported.")
    total_capital = _positive_money(
        request.get("total_research_capital"), "total_research_capital"
    )
    available_capital = _money(request.get("available_capital"), "available_capital")
    if available_capital > total_capital:
        raise ValueError("available_capital must not exceed total_research_capital.")
    parameters = _parameters(request.get("policy_parameters"), policy=policy)
    quantity_increment = _rounding(request.get("quantity_rounding"))
    provenance = _json_mapping(request.get("provenance"), "provenance")
    raw_allocations = request.get("allocations")
    if not isinstance(raw_allocations, list) or not raw_allocations:
        raise ValueError("allocations must be a non-empty list.")
    allocations = sorted(
        (_allocation(item, as_of=as_of) for item in raw_allocations),
        key=lambda item: item["allocation_id"],
    )
    duplicates = [
        key
        for key, count in Counter(item["allocation_id"] for item in allocations).items()
        if count > 1
    ]
    if duplicates:
        raise ValueError("duplicate allocation_id is not allowed.")
    _require_policy_evidence(allocations, policy=policy, parameters=parameters)

    remaining = available_capital
    quantities: list[dict[str, Any]] = []
    binding: set[str] = set()
    for allocation in allocations:
        price = allocation["price_evidence"]["price"]
        raw_quantity, sizing_risk_per_unit, policy_details = _raw_quantity(
            allocation,
            policy=policy,
            parameters=parameters,
        )
        allocation_cap_quantity = allocation["allocated_capital"] / price
        cash_cap_quantity = remaining / price
        bounded_raw = min(raw_quantity, allocation_cap_quantity, cash_cap_quantity)
        quantity = _floor(bounded_raw, quantity_increment)
        if allocation_cap_quantity < raw_quantity:
            binding.add(f"allocated_capital:{allocation['allocation_id']}")
        if cash_cap_quantity < min(raw_quantity, allocation_cap_quantity):
            binding.add("available_capital")
        notional = quantity * price
        stop_risk_used = quantity * allocation["per_unit_risk"]
        sizing_risk_used = quantity * sizing_risk_per_unit
        remaining -= notional
        sized = {
            **_lineage(allocation),
            "policy": policy,
            "quantity": _float(quantity),
            "price": _float(price),
            "notional": _float(notional),
            "risk_used": _float(stop_risk_used),
            "sizing_risk_used": _float(sizing_risk_used),
            "allocated_capital": _float(allocation["allocated_capital"]),
            "protective_exit": allocation["protective_exit"],
            "per_unit_risk": _float(allocation["per_unit_risk"]),
            "price_evidence": _public_price_evidence(allocation["price_evidence"]),
            "policy_evidence": policy_details,
            "source_allocation_sha256": allocation["source_allocation_sha256"],
            "provenance": allocation["provenance"],
        }
        sized["sizing_result_sha256"] = _canonical_sha256(sized)
        quantities.append(sized)

    used_capital = available_capital - remaining
    canonical_request = {
        "version": REQUEST_VERSION,
        "as_of_timestamp": _format_timestamp(as_of),
        "policy": policy,
        "total_research_capital": _float(total_capital),
        "available_capital": _float(available_capital),
        "policy_parameters": parameters,
        "quantity_rounding": {"increment": _float(quantity_increment), "mode": "FLOOR"},
        "allocations": [_public_allocation(item) for item in allocations],
        "provenance": provenance,
    }
    result = {
        "version": RESULT_VERSION,
        "request_sha256": _canonical_sha256(canonical_request),
        "policy": policy,
        "review_only_quantities": quantities,
        "available_capital": _float(available_capital),
        "capital_used": _float(used_capital),
        "residual_cash": _float(remaining),
        "total_risk_used": _float(
            sum((Decimal(str(item["risk_used"])) for item in quantities), Decimal("0"))
        ),
        "binding_constraints": sorted(binding),
        "capital_reconciled": used_capital + remaining == available_capital,
        "quantities_sha256": _canonical_sha256(quantities),
        "provenance": provenance,
        "review_status": "REVIEW_ONLY_QUANTITIES",
        "broker_order_schema_emitted": False,
        "portfolio_authority_granted": False,
        "automatic_strategy_application_performed": False,
        "production_runtime_supported": False,
    }
    result["output_sha256"] = _canonical_sha256(result)
    return result


def _raw_quantity(
    allocation: dict[str, Any],
    *,
    policy: str,
    parameters: dict[str, Any],
) -> tuple[Decimal, Decimal, dict[str, Any]]:
    capital = allocation["allocated_capital"]
    price = allocation["price_evidence"]["price"]
    stop_risk = allocation["per_unit_risk"]
    if policy == "FIXED_FRACTIONAL_RISK":
        risk_budget = capital * Decimal(str(parameters["risk_fraction"]))
        return risk_budget / stop_risk, stop_risk, {"risk_budget": _float(risk_budget)}
    if policy == "ATR_SIZING":
        atr = allocation["atr_evidence"]["atr"]
        atr_risk = atr * Decimal(str(parameters["atr_multiplier"]))
        sizing_risk = max(stop_risk, atr_risk)
        risk_budget = capital * Decimal(str(parameters["risk_fraction"]))
        return (
            risk_budget / sizing_risk,
            sizing_risk,
            {
                "risk_budget": _float(risk_budget),
                "atr": _float(atr),
                "atr_multiplier": parameters["atr_multiplier"],
                "atr_sizing_risk_per_unit": _float(atr_risk),
                "atr_evidence": _public_timed_evidence(allocation["atr_evidence"]),
            },
        )
    if policy == "VOLATILITY_TARGETING":
        volatility = allocation["volatility_evidence"]["annualized_volatility"]
        multiplier = min(
            Decimal("1"),
            Decimal(str(parameters["target_annualized_volatility"]))
            / Decimal(str(volatility)),
        )
        target_notional = capital * multiplier
        return (
            target_notional / price,
            stop_risk,
            {
                "target_notional": _float(target_notional),
                "volatility_multiplier": _float(multiplier),
                "volatility_evidence": _public_volatility_evidence(
                    allocation["volatility_evidence"]
                ),
            },
        )
    kelly = allocation["kelly_evidence"]
    raw_fraction = max(
        0.0,
        kelly["win_probability"]
        - (1.0 - kelly["win_probability"]) / kelly["payoff_ratio"],
    )
    bounded_fraction = min(
        parameters["kelly_cap_fraction"], raw_fraction * parameters["kelly_haircut"]
    )
    target_notional = capital * Decimal(str(bounded_fraction))
    return (
        target_notional / price,
        stop_risk,
        {
            "raw_kelly_fraction": raw_fraction,
            "bounded_kelly_fraction": bounded_fraction,
            "target_notional": _float(target_notional),
            "candidate_only": True,
            "kelly_evidence": _public_kelly_evidence(kelly),
        },
    )


def _allocation(raw: Any, *, as_of: datetime) -> dict[str, Any]:
    _mapping(raw, "allocation")
    _reject_unknown(
        raw,
        {
            "allocation_id",
            "strategy_id",
            "strategy_version",
            "strategy_builder",
            "variant_id",
            "symbol",
            "allocated_capital",
            "price_evidence",
            "protective_exit",
            "per_unit_risk",
            "atr_evidence",
            "volatility_evidence",
            "kelly_evidence",
            "source_allocation_sha256",
            "provenance",
        },
        "allocation",
    )
    symbol = _text(raw.get("symbol"), "symbol").upper()
    price_evidence = _price_evidence(
        raw.get("price_evidence"), as_of=as_of, expected_symbol=symbol
    )
    protective_exit = build_protective_exit_contract(
        _mapping(raw.get("protective_exit"), "protective_exit")
    )
    per_unit_risk = _positive_money(raw.get("per_unit_risk"), "per_unit_risk")
    expected_risk = Decimal(str(protective_exit["per_unit_loss_to_protective_exit"]))
    if not math.isclose(
        float(per_unit_risk), float(expected_risk), rel_tol=_TOLERANCE, abs_tol=_TOLERANCE
    ):
        raise ValueError("per_unit_risk must exactly match protective_exit risk.")
    if not math.isclose(
        float(price_evidence["price"]),
        protective_exit["entry_price"],
        rel_tol=_TOLERANCE,
        abs_tol=_TOLERANCE,
    ):
        raise ValueError("price evidence must exactly match protective_exit entry_price.")
    return {
        "allocation_id": _text(raw.get("allocation_id"), "allocation_id"),
        "strategy_id": _text(raw.get("strategy_id"), "strategy_id"),
        "strategy_version": _text(raw.get("strategy_version"), "strategy_version"),
        "strategy_builder": _text(raw.get("strategy_builder"), "strategy_builder"),
        "variant_id": _text(raw.get("variant_id"), "variant_id"),
        "symbol": symbol,
        "allocated_capital": _positive_money(raw.get("allocated_capital"), "allocated_capital"),
        "price_evidence": price_evidence,
        "protective_exit": protective_exit,
        "per_unit_risk": per_unit_risk,
        "atr_evidence": _optional_atr_evidence(
            raw.get("atr_evidence"), as_of=as_of, expected_symbol=symbol
        ),
        "volatility_evidence": _optional_volatility_evidence(
            raw.get("volatility_evidence"), as_of=as_of, expected_symbol=symbol
        ),
        "kelly_evidence": _optional_kelly_evidence(
            raw.get("kelly_evidence"), as_of=as_of, expected_symbol=symbol
        ),
        "source_allocation_sha256": _sha256(
            raw.get("source_allocation_sha256"), "source_allocation_sha256"
        ),
        "provenance": _json_mapping(raw.get("provenance"), "allocation provenance"),
    }


def _require_policy_evidence(
    allocations: list[dict[str, Any]], *, policy: str, parameters: dict[str, Any]
) -> None:
    if policy != "BOUNDED_FRACTIONAL_KELLY_CANDIDATE" and parameters["kelly_enabled"]:
        raise ValueError("kelly must remain disabled for non-Kelly policies.")
    for allocation in allocations:
        if policy == "ATR_SIZING" and allocation["atr_evidence"] is None:
            raise ValueError("atr_evidence is required for ATR_SIZING.")
        if policy == "VOLATILITY_TARGETING" and allocation["volatility_evidence"] is None:
            raise ValueError("volatility_evidence is required for VOLATILITY_TARGETING.")
        if policy == "BOUNDED_FRACTIONAL_KELLY_CANDIDATE":
            evidence = allocation["kelly_evidence"]
            if not parameters["kelly_enabled"] or evidence is None or not evidence["enabled"]:
                raise ValueError("Kelly sizing must be explicitly enabled.")
            if not evidence["candidate_only"]:
                raise ValueError("Kelly sizing must be candidate_only.")
            if evidence["sample_size"] < parameters["kelly_minimum_sample_size"]:
                raise ValueError("Kelly sample size is below the required minimum sample.")


def _parameters(raw: Any, *, policy: str) -> dict[str, Any]:
    parameters = _mapping(raw, "policy_parameters")
    _reject_unknown(
        parameters,
        {
            "risk_fraction",
            "atr_multiplier",
            "target_annualized_volatility",
            "kelly_enabled",
            "kelly_haircut",
            "kelly_cap_fraction",
            "kelly_minimum_sample_size",
        },
        "policy_parameters",
    )
    result = {
        "risk_fraction": _fraction(parameters.get("risk_fraction"), "risk_fraction"),
        "atr_multiplier": _positive_number(parameters.get("atr_multiplier"), "atr_multiplier"),
        "target_annualized_volatility": _fraction(
            parameters.get("target_annualized_volatility"), "target_annualized_volatility"
        ),
        "kelly_enabled": _bool(parameters.get("kelly_enabled"), "kelly_enabled"),
        "kelly_haircut": _fraction(parameters.get("kelly_haircut"), "kelly_haircut"),
        "kelly_cap_fraction": _fraction(
            parameters.get("kelly_cap_fraction"), "kelly_cap_fraction"
        ),
        "kelly_minimum_sample_size": _positive_integer(
            parameters.get("kelly_minimum_sample_size"), "kelly_minimum_sample_size"
        ),
    }
    if policy == "BOUNDED_FRACTIONAL_KELLY_CANDIDATE" and not result["kelly_enabled"]:
        raise ValueError("kelly_enabled must be true for Kelly candidate sizing.")
    return result


def _price_evidence(
    raw: Any, *, as_of: datetime, expected_symbol: str
) -> dict[str, Any]:
    evidence = _mapping(raw, "price_evidence")
    _reject_unknown(
        evidence,
        {"symbol", "price", "observed_at", "source_input_sha256"},
        "price_evidence",
    )
    symbol = _evidence_symbol(evidence.get("symbol"), expected=expected_symbol, name="price evidence")
    observed = _bounded_observed_at(evidence.get("observed_at"), as_of=as_of, name="price observed_at")
    return {
        "symbol": symbol,
        "price": _positive_money(evidence.get("price"), "price"),
        "observed_at": observed,
        "source_input_sha256": _sha256(
            evidence.get("source_input_sha256"), "price source_input_sha256"
        ),
    }


def _optional_atr_evidence(
    raw: Any, *, as_of: datetime, expected_symbol: str
) -> dict[str, Any] | None:
    if raw is None:
        return None
    evidence = _mapping(raw, "atr_evidence")
    _reject_unknown(
        evidence,
        {"symbol", "atr", "observed_at", "source_input_sha256"},
        "atr_evidence",
    )
    return {
        "symbol": _evidence_symbol(
            evidence.get("symbol"), expected=expected_symbol, name="ATR evidence"
        ),
        "atr": _positive_money(evidence.get("atr"), "atr"),
        "observed_at": _bounded_observed_at(
            evidence.get("observed_at"), as_of=as_of, name="atr observed_at"
        ),
        "source_input_sha256": _sha256(
            evidence.get("source_input_sha256"), "atr source_input_sha256"
        ),
    }


def _optional_volatility_evidence(
    raw: Any, *, as_of: datetime, expected_symbol: str
) -> dict[str, Any] | None:
    if raw is None:
        return None
    evidence = _mapping(raw, "volatility_evidence")
    _reject_unknown(
        evidence,
        {"symbol", "annualized_volatility", "observed_at", "window_start", "source_input_sha256"},
        "volatility_evidence",
    )
    observed_dt = _timestamp(evidence.get("observed_at"), "volatility observed_at")
    start_dt = _timestamp(evidence.get("window_start"), "volatility window_start")
    if observed_dt > as_of or start_dt >= observed_dt:
        raise ValueError("volatility evidence contains a future or invalid window.")
    return {
        "symbol": _evidence_symbol(
            evidence.get("symbol"), expected=expected_symbol, name="volatility evidence"
        ),
        "annualized_volatility": _fraction(
            evidence.get("annualized_volatility"), "annualized_volatility"
        ),
        "observed_at": _format_timestamp(observed_dt),
        "window_start": _format_timestamp(start_dt),
        "source_input_sha256": _sha256(
            evidence.get("source_input_sha256"), "volatility source_input_sha256"
        ),
    }


def _optional_kelly_evidence(
    raw: Any, *, as_of: datetime, expected_symbol: str
) -> dict[str, Any] | None:
    if raw is None:
        return None
    evidence = _mapping(raw, "kelly_evidence")
    _reject_unknown(
        evidence,
        {
            "enabled",
            "symbol",
            "candidate_only",
            "win_probability",
            "payoff_ratio",
            "sample_size",
            "observed_at",
            "source_input_sha256",
        },
        "kelly_evidence",
    )
    return {
        "symbol": _evidence_symbol(
            evidence.get("symbol"), expected=expected_symbol, name="Kelly evidence"
        ),
        "enabled": _bool(evidence.get("enabled"), "Kelly enabled"),
        "candidate_only": _bool(evidence.get("candidate_only"), "candidate_only"),
        "win_probability": _fraction(evidence.get("win_probability"), "win_probability"),
        "payoff_ratio": _positive_number(evidence.get("payoff_ratio"), "payoff_ratio"),
        "sample_size": _positive_integer(evidence.get("sample_size"), "sample_size"),
        "observed_at": _bounded_observed_at(
            evidence.get("observed_at"), as_of=as_of, name="Kelly observed_at"
        ),
        "source_input_sha256": _sha256(
            evidence.get("source_input_sha256"), "Kelly source_input_sha256"
        ),
    }


def _public_allocation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **_lineage(item),
        "allocated_capital": _float(item["allocated_capital"]),
        "price_evidence": _public_price_evidence(item["price_evidence"]),
        "protective_exit": item["protective_exit"],
        "per_unit_risk": _float(item["per_unit_risk"]),
        "atr_evidence": None if item["atr_evidence"] is None else _public_timed_evidence(item["atr_evidence"]),
        "volatility_evidence": None
        if item["volatility_evidence"] is None
        else _public_volatility_evidence(item["volatility_evidence"]),
        "kelly_evidence": None
        if item["kelly_evidence"] is None
        else _public_kelly_evidence(item["kelly_evidence"]),
        "source_allocation_sha256": item["source_allocation_sha256"],
        "provenance": item["provenance"],
    }


def _public_price_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "price": _float(item["price"])}


def _public_timed_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _float(value) if isinstance(value, Decimal) else value for key, value in item.items()}


def _public_volatility_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)


def _public_kelly_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)


def _lineage(item: dict[str, Any]) -> dict[str, str]:
    return {
        "allocation_id": item["allocation_id"],
        "strategy_id": item["strategy_id"],
        "strategy_version": item["strategy_version"],
        "strategy_builder": item["strategy_builder"],
        "variant_id": item["variant_id"],
        "symbol": item["symbol"],
    }


def _rounding(raw: Any) -> Decimal:
    rounding = _mapping(raw, "quantity_rounding")
    _reject_unknown(rounding, {"increment", "mode"}, "quantity_rounding")
    if rounding.get("mode") != "FLOOR":
        raise ValueError("quantity_rounding mode must be FLOOR.")
    return _positive_money(rounding.get("increment"), "quantity rounding increment")


def _floor(value: Decimal, increment: Decimal) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_FLOOR) * increment


def _bounded_observed_at(raw: Any, *, as_of: datetime, name: str) -> str:
    observed = _timestamp(raw, name)
    if observed > as_of:
        raise ValueError(f"{name} must not be in the future.")
    return _format_timestamp(observed)


def _evidence_symbol(raw: Any, *, expected: str, name: str) -> str:
    symbol = _text(raw, f"{name} symbol").upper()
    if symbol != expected:
        raise ValueError(f"{name} symbol must exactly match allocation symbol.")
    return symbol


def _timestamp(raw: Any, name: str) -> datetime:
    value = _text(raw, name)
    if not value.endswith("Z"):
        raise ValueError(f"{name} must be UTC and end in Z.")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UTC timestamp.") from exc


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _fraction(raw: Any, name: str) -> float:
    value = _number(raw, name)
    if value <= 0.0 or value > 1.0:
        raise ValueError(f"{name} must be greater than 0 and no greater than 1.")
    return value


def _positive_number(raw: Any, name: str) -> float:
    value = _number(raw, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return value


def _positive_integer(raw: Any, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return raw


def _bool(raw: Any, name: str) -> bool:
    if not isinstance(raw, bool):
        raise ValueError(f"{name} must be boolean.")
    return raw


def _positive_money(raw: Any, name: str) -> Decimal:
    value = _money(raw, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive.")
    return value


def _money(raw: Any, name: str) -> Decimal:
    value = _number(raw, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative.")
    return Decimal(str(raw))


def _number(raw: Any, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    return value


def _sha256(raw: Any, name: str) -> str:
    value = _text(raw, name)
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256.")
    return value


def _text(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
        raise ValueError(f"{name} must be non-empty text without outer whitespace.")
    return raw


def _mapping(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object.")
    return raw


def _json_mapping(raw: Any, name: str) -> dict[str, Any]:
    value = _mapping(raw, name)
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain canonical JSON data.") from exc


def _reject_unknown(payload: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}.")


def _float(value: Decimal) -> float:
    return float(value)


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
