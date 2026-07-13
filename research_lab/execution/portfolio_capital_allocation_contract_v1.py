from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from decimal import Decimal, ROUND_FLOOR
from typing import Any


REQUEST_VERSION = "portfolio_capital_allocation_request_v1"
RESULT_VERSION = "portfolio_capital_allocation_contract_v1"
POLICIES = {
    "EQUAL_CAPITAL",
    "EQUAL_RISK_BUDGET",
    "FIXED_STRATEGY_WEIGHTS",
    "BOUNDED_SCORE_WEIGHTED",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_portfolio_capital_allocation_contract(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Allocate bounded research capital without quantities or execution authority."""
    _mapping(request, "capital allocation request")
    _reject_unknown(
        request,
        {
            "version",
            "policy",
            "total_research_capital",
            "cash_reserve",
            "per_strategy_maximum",
            "per_asset_maximum",
            "minimum_allocation",
            "maximum_aggregate_allocation",
            "leverage_policy",
            "deterministic_rounding",
            "fixed_strategy_weights",
            "candidates",
            "provenance",
        },
        "capital allocation request",
    )
    if request.get("version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    policy = _text(request.get("policy"), "policy")
    if policy not in POLICIES:
        raise ValueError("policy is unsupported.")

    total = _money(request.get("total_research_capital"), "total_research_capital")
    if total <= 0:
        raise ValueError("total_research_capital must be positive.")
    cash_reserve = _money(request.get("cash_reserve"), "cash_reserve")
    strategy_maximum = _money(
        request.get("per_strategy_maximum"), "per_strategy_maximum"
    )
    asset_maximum = _money(request.get("per_asset_maximum"), "per_asset_maximum")
    minimum = _money(request.get("minimum_allocation"), "minimum_allocation")
    aggregate_maximum = _money(
        request.get("maximum_aggregate_allocation"),
        "maximum_aggregate_allocation",
    )
    if cash_reserve > total:
        raise ValueError("cash_reserve must not exceed total_research_capital.")
    for name, value in (
        ("per_strategy_maximum", strategy_maximum),
        ("per_asset_maximum", asset_maximum),
        ("maximum_aggregate_allocation", aggregate_maximum),
    ):
        if value <= 0 or value > total:
            raise ValueError(f"{name} must be positive and no greater than total capital.")
    if minimum > strategy_maximum or minimum > asset_maximum:
        raise ValueError("minimum_allocation must not exceed concentration maxima.")

    _leverage_policy(request.get("leverage_policy"))
    increment = _rounding(request.get("deterministic_rounding"))
    total = _floor(total, increment)
    cash_reserve = _floor(cash_reserve, increment)
    strategy_maximum = _floor(strategy_maximum, increment)
    asset_maximum = _floor(asset_maximum, increment)
    minimum = _floor(minimum, increment)
    aggregate_maximum = _floor(aggregate_maximum, increment)
    provenance = _json_mapping(request.get("provenance"), "provenance")
    fixed_weights = _weights(request.get("fixed_strategy_weights"))

    raw_candidates = request.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("candidates must be a non-empty list.")
    candidates = sorted((_candidate(item) for item in raw_candidates), key=_candidate_key)
    identities = [_candidate_identity(item) for item in candidates]
    duplicates = sorted(identity for identity, count in Counter(identities).items() if count > 1)
    if duplicates:
        raise ValueError("duplicate candidate identity is not allowed.")

    strategy_ids = {item["strategy_id"] for item in candidates}
    if policy == "FIXED_STRATEGY_WEIGHTS" and set(fixed_weights) != strategy_ids:
        raise ValueError("fixed strategy weights must exactly match candidate strategies.")
    if policy == "BOUNDED_SCORE_WEIGHTED" and any(
        item["score"] is None for item in candidates
    ):
        raise ValueError("score is required for BOUNDED_SCORE_WEIGHTED.")
    raw_weights = _policy_weights(policy, candidates, fixed_weights)
    weight_total = sum((Decimal(str(weight)) for weight in raw_weights), Decimal("0"))
    if weight_total <= 0:
        raise ValueError("policy weights must have a positive total.")

    allocatable = min(total - cash_reserve, aggregate_maximum)
    strategy_used: dict[str, Decimal] = {}
    asset_used: dict[str, Decimal] = {}
    allocations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    binding_constraints: set[str] = set()
    for candidate, raw_weight in zip(candidates, raw_weights, strict=True):
        target = _floor(
            allocatable * Decimal(str(raw_weight)) / weight_total,
            increment,
        )
        strategy_id = candidate["strategy_id"]
        symbol = candidate["symbol"]
        strategy_remaining = strategy_maximum - strategy_used.get(strategy_id, Decimal("0"))
        asset_remaining = asset_maximum - asset_used.get(symbol, Decimal("0"))
        bounded = min(target, strategy_remaining, asset_remaining)
        if bounded < target:
            if strategy_remaining <= bounded:
                binding_constraints.add(f"per_strategy_maximum:{strategy_id}")
            if asset_remaining <= bounded:
                binding_constraints.add(f"per_asset_maximum:{symbol}")
        bounded = max(Decimal("0"), _floor(bounded, increment))
        if bounded < minimum:
            rejected.append(
                {
                    **_candidate_lineage(candidate),
                    "target_allocation": _float(target),
                    "bounded_allocation": _float(bounded),
                    "reason": "BELOW_MINIMUM_ALLOCATION",
                }
            )
            continue
        strategy_used[strategy_id] = strategy_used.get(strategy_id, Decimal("0")) + bounded
        asset_used[symbol] = asset_used.get(symbol, Decimal("0")) + bounded
        allocation = {
            **_candidate_lineage(candidate),
            "policy_weight": raw_weight,
            "target_allocation": _float(target),
            "allocated_capital": _float(bounded),
            "risk_evidence": candidate["risk_evidence"],
            "asset_lineage": candidate["asset_lineage"],
            "provenance": candidate["provenance"],
        }
        allocation["allocation_sha256"] = _canonical_sha256(allocation)
        allocations.append(allocation)

    allocated = sum(
        (Decimal(str(item["allocated_capital"])) for item in allocations), Decimal("0")
    )
    residual_cash = total - allocated
    unallocated = residual_cash - cash_reserve
    if allocated > aggregate_maximum or residual_cash < cash_reserve:
        raise ValueError("allocation concentration or leverage invariant failed.")
    strategy_summary = [
        {"strategy_id": key, "allocated_capital": _float(value)}
        for key, value in sorted(strategy_used.items())
    ]
    asset_summary = [
        {"symbol": key, "allocated_capital": _float(value)}
        for key, value in sorted(asset_used.items())
    ]
    canonical_request = {
        "version": REQUEST_VERSION,
        "policy": policy,
        "total_research_capital": _float(total),
        "cash_reserve": _float(cash_reserve),
        "per_strategy_maximum": _float(strategy_maximum),
        "per_asset_maximum": _float(asset_maximum),
        "minimum_allocation": _float(minimum),
        "maximum_aggregate_allocation": _float(aggregate_maximum),
        "leverage_policy": {"allowed": False, "maximum_gross_multiplier": 1.0},
        "deterministic_rounding": {"increment": _float(increment), "mode": "FLOOR"},
        "fixed_strategy_weights": fixed_weights,
        "candidates": candidates,
        "provenance": provenance,
    }
    result = {
        "version": RESULT_VERSION,
        "request_sha256": _canonical_sha256(canonical_request),
        "policy": policy,
        "total_research_capital": _float(total),
        "requested_cash_reserve": _float(cash_reserve),
        "maximum_aggregate_allocation": _float(aggregate_maximum),
        "research_allocations": allocations,
        "rejected_allocations": rejected,
        "allocated_capital": _float(allocated),
        "unallocated_capital": _float(unallocated),
        "residual_cash": _float(residual_cash),
        "strategy_allocation_summary": strategy_summary,
        "asset_allocation_summary": asset_summary,
        "binding_constraints": sorted(binding_constraints),
        "capital_reconciled": allocated + residual_cash == total,
        "allocations_sha256": _canonical_sha256(allocations),
        "provenance": provenance,
        "quantities_emitted": False,
        "broker_orders_emitted": False,
        "automatic_allocation_application_performed": False,
        "production_runtime_supported": False,
    }
    result["output_sha256"] = _canonical_sha256(result)
    return result


def _candidate(raw: Any) -> dict[str, Any]:
    _mapping(raw, "candidate")
    _reject_unknown(
        raw,
        {
            "strategy_id",
            "strategy_version",
            "strategy_builder",
            "variant_id",
            "symbol",
            "target_intent",
            "score",
            "risk_evidence",
            "asset_lineage",
            "provenance",
        },
        "candidate",
    )
    if raw.get("target_intent") != "LONG":
        raise ValueError("target_intent must be LONG; short and FLAT candidates are not allocatable.")
    score = (
        None
        if raw.get("score") is None
        else _bounded_number(raw.get("score"), "score", allow_zero=True)
    )
    risk = _mapping(raw.get("risk_evidence"), "risk_evidence")
    _reject_unknown(
        risk,
        {"estimated_loss_fraction", "protective_exit_sha256", "source_input_sha256"},
        "risk_evidence",
    )
    loss_fraction = _bounded_number(
        risk.get("estimated_loss_fraction"), "estimated_loss_fraction", allow_zero=False
    )
    protective_hash = _sha256(risk.get("protective_exit_sha256"), "protective_exit_sha256")
    source_hash = _sha256(risk.get("source_input_sha256"), "source_input_sha256")
    asset_lineage = _json_mapping(raw.get("asset_lineage"), "asset_lineage")
    _reject_unknown(
        asset_lineage,
        {"dataset_id", "symbol", "market_data_sha256"},
        "asset_lineage",
    )
    symbol = _text(raw.get("symbol"), "symbol").upper()
    lineage_symbol = _text(asset_lineage.get("symbol"), "asset_lineage symbol").upper()
    if lineage_symbol != symbol:
        raise ValueError("asset_lineage symbol must exactly match candidate symbol.")
    return {
        "strategy_id": _text(raw.get("strategy_id"), "strategy_id"),
        "strategy_version": _text(raw.get("strategy_version"), "strategy_version"),
        "strategy_builder": _text(raw.get("strategy_builder"), "strategy_builder"),
        "variant_id": _text(raw.get("variant_id"), "variant_id"),
        "symbol": symbol,
        "target_intent": "LONG",
        "score": score,
        "risk_evidence": {
            "estimated_loss_fraction": loss_fraction,
            "protective_exit_sha256": protective_hash,
            "source_input_sha256": source_hash,
        },
        "asset_lineage": {
            "dataset_id": _text(asset_lineage.get("dataset_id"), "asset_lineage dataset_id"),
            "symbol": lineage_symbol,
            "market_data_sha256": _sha256(
                asset_lineage.get("market_data_sha256"),
                "asset_lineage market_data_sha256",
            ),
        },
        "provenance": _json_mapping(raw.get("provenance"), "candidate provenance"),
    }


def _policy_weights(
    policy: str,
    candidates: list[dict[str, Any]],
    fixed_weights: dict[str, float],
) -> list[float]:
    if policy == "EQUAL_CAPITAL":
        return [1.0] * len(candidates)
    if policy == "EQUAL_RISK_BUDGET":
        return [1.0 / item["risk_evidence"]["estimated_loss_fraction"] for item in candidates]
    if policy == "BOUNDED_SCORE_WEIGHTED":
        return [float(item["score"]) for item in candidates]
    counts = Counter(item["strategy_id"] for item in candidates)
    return [fixed_weights[item["strategy_id"]] / counts[item["strategy_id"]] for item in candidates]


def _candidate_lineage(candidate: dict[str, Any]) -> dict[str, str]:
    return {
        "strategy_id": candidate["strategy_id"],
        "strategy_version": candidate["strategy_version"],
        "strategy_builder": candidate["strategy_builder"],
        "variant_id": candidate["variant_id"],
        "symbol": candidate["symbol"],
    }


def _candidate_identity(candidate: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_candidate_lineage(candidate).values())


def _candidate_key(candidate: dict[str, Any]) -> tuple[str, ...]:
    return _candidate_identity(candidate)


def _leverage_policy(raw: Any) -> None:
    policy = _mapping(raw, "leverage_policy")
    _reject_unknown(policy, {"allowed", "maximum_gross_multiplier"}, "leverage_policy")
    if policy.get("allowed") is not False:
        raise ValueError("leverage is not supported; allowed must be false.")
    multiplier = _number(policy.get("maximum_gross_multiplier"), "maximum_gross_multiplier")
    if multiplier != 1.0:
        raise ValueError("maximum_gross_multiplier must be 1.0 without leverage.")


def _rounding(raw: Any) -> Decimal:
    rounding = _mapping(raw, "deterministic_rounding")
    _reject_unknown(rounding, {"increment", "mode"}, "deterministic_rounding")
    if rounding.get("mode") != "FLOOR":
        raise ValueError("deterministic_rounding mode must be FLOOR.")
    increment = _money(rounding.get("increment"), "rounding increment")
    if increment <= 0:
        raise ValueError("rounding increment must be positive.")
    return increment


def _weights(raw: Any) -> dict[str, float]:
    mapping = _mapping(raw, "fixed_strategy_weights")
    result: dict[str, float] = {}
    for key, raw_weight in mapping.items():
        strategy_id = _text(key, "fixed strategy weight key")
        result[strategy_id] = _positive_number(raw_weight, f"weight for {strategy_id}")
    return dict(sorted(result.items()))


def _floor(value: Decimal, increment: Decimal) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_FLOOR) * increment


def _money(raw: Any, name: str) -> Decimal:
    value = _number(raw, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative.")
    return Decimal(str(raw))


def _bounded_number(raw: Any, name: str, *, allow_zero: bool) -> float:
    value = _number(raw, name)
    if value > 1.0 or value < 0.0 or (not allow_zero and value == 0.0):
        qualifier = "greater than 0 and" if not allow_zero else ""
        raise ValueError(f"{name} must be {qualifier} no greater than 1.")
    return value


def _positive_number(raw: Any, name: str) -> float:
    value = _number(raw, name)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return value


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
