from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any

from research_lab.execution.risk_execution_contract_v1 import (
    build_protective_exit_contract,
)


REQUEST_VERSION = "portfolio_risk_overlay_request_v1"
RESULT_VERSION = "portfolio_risk_overlay_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def build_portfolio_risk_overlay(request: dict[str, Any]) -> dict[str, Any]:
    """Apply explicit portfolio risk limits without granting execution authority."""
    _mapping(request, "portfolio risk overlay request")
    _reject_unknown(
        request,
        {
            "version",
            "as_of_timestamp",
            "current_equity",
            "peak_equity",
            "current_cash",
            "limits",
            "correlation_evidence",
            "allocations",
            "provenance",
        },
        "portfolio risk overlay request",
    )
    if request.get("version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    as_of = _timestamp(request.get("as_of_timestamp"), "as_of_timestamp")
    current_equity = _positive_money(request.get("current_equity"), "current_equity")
    peak_equity = _positive_money(request.get("peak_equity"), "peak_equity")
    current_cash = _money(request.get("current_cash"), "current_cash")
    if peak_equity < current_equity:
        raise ValueError("peak_equity must be no less than current_equity.")
    limits = _limits(request.get("limits"), current_equity=current_equity)
    evidence = _correlation_evidence(
        request.get("correlation_evidence"),
        as_of=as_of,
        required_groups=set(limits["correlation_group_limits"]),
    )
    raw_allocations = request.get("allocations")
    if not isinstance(raw_allocations, list) or not raw_allocations:
        raise ValueError("allocations must be a non-empty list.")
    allocations = sorted(
        (
            _allocation(
                item,
                concentration_groups=set(limits["concentration_group_limits"]),
                correlation_groups=set(limits["correlation_group_limits"]),
            )
            for item in raw_allocations
        ),
        key=lambda item: item["allocation_id"],
    )
    duplicate_ids = sorted(
        allocation_id
        for allocation_id, count in Counter(item["allocation_id"] for item in allocations).items()
        if count > 1
    )
    if duplicate_ids:
        raise ValueError("duplicate allocation_id is not allowed.")
    _bind_correlation_symbols(allocations, evidence)
    provenance = _json_mapping(request.get("provenance"), "provenance")

    drawdown = (peak_equity - current_equity) / peak_equity
    accepted: list[dict[str, Any]] = []
    clipped: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    binding: set[str] = set()
    if drawdown > limits["portfolio_drawdown_limit_fraction"]:
        binding.add("portfolio_drawdown_limit_fraction")
        rejected = [
            {
                **_disposition_evidence(item),
                "requested_capital": _float(item["allocated_capital"]),
                "reason": "PORTFOLIO_DRAWDOWN_LIMIT",
            }
            for item in allocations
        ]
        totals = _empty_totals()
        review_status = "REJECTED_DRAWDOWN_LIMIT"
    else:
        totals = _empty_totals()
        for allocation in allocations:
            requested = allocation["allocated_capital"]
            capacities = _capacities(
                allocation,
                totals=totals,
                limits=limits,
                current_equity=current_equity,
                current_cash=current_cash,
            )
            accepted_capital = max(Decimal("0"), min([requested, *capacities.values()]))
            constrained_by = sorted(
                name
                for name, capacity in capacities.items()
                if capacity == accepted_capital and accepted_capital < requested
            )
            binding.update(constrained_by)
            if accepted_capital <= 0:
                rejected.append(
                    {
                        **_disposition_evidence(allocation),
                        "requested_capital": _float(requested),
                        "reason": "NO_RISK_CAPACITY",
                        "binding_constraints": constrained_by,
                    }
                )
                continue
            normalized = {
                **_lineage(allocation),
                "allocated_capital": _float(accepted_capital),
                "estimated_loss_fraction": allocation["estimated_loss_fraction"],
                "estimated_loss_at_stop": _float(
                    accepted_capital * Decimal(str(allocation["estimated_loss_fraction"]))
                ),
                "protective_exit": allocation["protective_exit"],
                "source_allocation_sha256": allocation["source_allocation_sha256"],
                "concentration_group_ids": allocation["concentration_group_ids"],
                "correlation_group_ids": allocation["correlation_group_ids"],
                "provenance": allocation["provenance"],
            }
            normalized["overlay_allocation_sha256"] = _canonical_sha256(normalized)
            _add_totals(totals, allocation, accepted_capital)
            if accepted_capital == requested:
                accepted.append(normalized)
            else:
                clipped.append(
                    {
                        **_disposition_evidence(allocation),
                        "requested_capital": _float(requested),
                        "accepted_capital": _float(accepted_capital),
                        "estimated_loss_at_stop": normalized["estimated_loss_at_stop"],
                        "binding_constraints": constrained_by,
                    }
                )
        review_status = (
            "REJECTED_NO_RISK_CAPACITY"
            if not accepted and not clipped
            else "ACCEPTED_WITH_CLIPPING"
            if clipped or rejected
            else "ACCEPTED_REVIEW_ONLY"
        )

    gross = totals["gross"]
    result = {
        "version": RESULT_VERSION,
        "request_sha256": _canonical_sha256(
            {
                "version": REQUEST_VERSION,
                "as_of_timestamp": _format_timestamp(as_of),
                "current_equity": _float(current_equity),
                "peak_equity": _float(peak_equity),
                "current_cash": _float(current_cash),
                "limits": _public_limits(limits),
                "correlation_evidence": evidence,
                "allocations": [_public_allocation(item) for item in allocations],
                "provenance": provenance,
            }
        ),
        "as_of_timestamp": _format_timestamp(as_of),
        "accepted_allocations": accepted,
        "clipped_allocations": clipped,
        "rejected_allocations": rejected,
        "binding_constraints": sorted(binding),
        "exposure_summary": {
            "gross_exposure": _float(gross),
            "net_exposure": _float(gross),
            "leverage": _float(gross / current_equity),
            "cash_after_allocations": _float(current_cash - gross),
        },
        "concentration_summary": {
            "assets": _summary(totals["assets"], "symbol"),
            "strategies": _summary(totals["strategies"], "strategy_id"),
            "concentration_groups": _summary(totals["concentration_groups"], "group_id"),
            "correlation_groups": _summary(totals["correlation_groups"], "group_id"),
        },
        "portfolio_drawdown_fraction": _float(drawdown),
        "estimated_loss_at_stops": _float(totals["loss"]),
        "review_status": review_status,
        "overlay_allocations_sha256": _canonical_sha256(
            {"accepted": accepted, "clipped": clipped, "rejected": rejected}
        ),
        "provenance": provenance,
        "execution_authority_granted": False,
        "broker_orders_emitted": False,
        "automatic_allocation_application_performed": False,
        "production_runtime_supported": False,
    }
    result["output_sha256"] = _canonical_sha256(result)
    return result


def _capacities(
    allocation: dict[str, Any],
    *,
    totals: dict[str, Any],
    limits: dict[str, Any],
    current_equity: Decimal,
    current_cash: Decimal,
) -> dict[str, Decimal]:
    symbol = allocation["symbol"]
    strategy_id = allocation["strategy_id"]
    capacities = {
        "maximum_gross_exposure": limits["maximum_gross_exposure"] - totals["gross"],
        "maximum_net_exposure": limits["maximum_net_exposure"] - totals["gross"],
        f"per_asset_concentration:{symbol}": limits["per_asset_concentration"]
        - totals["assets"].get(symbol, Decimal("0")),
        f"per_strategy_concentration:{strategy_id}": limits["per_strategy_concentration"]
        - totals["strategies"].get(strategy_id, Decimal("0")),
        "leverage_limit": current_equity * Decimal(str(limits["leverage_limit"]))
        - totals["gross"],
        "minimum_cash": current_cash - limits["minimum_cash"] - totals["gross"],
        "maximum_estimated_total_loss_at_stops": (
            limits["maximum_estimated_total_loss_at_stops"] - totals["loss"]
        )
        / Decimal(str(allocation["estimated_loss_fraction"])),
    }
    for group_id in allocation["concentration_group_ids"]:
        capacities[f"concentration_group_limit:{group_id}"] = (
            limits["concentration_group_limits"][group_id]
            - totals["concentration_groups"].get(group_id, Decimal("0"))
        )
    for group_id in allocation["correlation_group_ids"]:
        capacities[f"correlation_group_limit:{group_id}"] = (
            limits["correlation_group_limits"][group_id]
            - totals["correlation_groups"].get(group_id, Decimal("0"))
        )
    return capacities


def _add_totals(totals: dict[str, Any], allocation: dict[str, Any], capital: Decimal) -> None:
    totals["gross"] += capital
    totals["loss"] += capital * Decimal(str(allocation["estimated_loss_fraction"]))
    _increment(totals["assets"], allocation["symbol"], capital)
    _increment(totals["strategies"], allocation["strategy_id"], capital)
    for group_id in allocation["concentration_group_ids"]:
        _increment(totals["concentration_groups"], group_id, capital)
    for group_id in allocation["correlation_group_ids"]:
        _increment(totals["correlation_groups"], group_id, capital)


def _allocation(
    raw: Any,
    *,
    concentration_groups: set[str],
    correlation_groups: set[str],
) -> dict[str, Any]:
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
            "direction",
            "allocated_capital",
            "estimated_loss_fraction",
            "protective_exit",
            "source_allocation_sha256",
            "concentration_group_ids",
            "correlation_group_ids",
            "provenance",
        },
        "allocation",
    )
    if raw.get("direction") != "LONG":
        raise ValueError("direction must be LONG; short exposure is unsupported.")
    allocation_groups = _group_ids(
        raw.get("concentration_group_ids"),
        name="concentration group ids",
        configured=concentration_groups,
    )
    allocation_correlations = _group_ids(
        raw.get("correlation_group_ids"),
        name="correlation group ids",
        configured=correlation_groups,
    )
    return {
        "allocation_id": _text(raw.get("allocation_id"), "allocation_id"),
        "strategy_id": _text(raw.get("strategy_id"), "strategy_id"),
        "strategy_version": _text(raw.get("strategy_version"), "strategy_version"),
        "strategy_builder": _text(raw.get("strategy_builder"), "strategy_builder"),
        "variant_id": _text(raw.get("variant_id"), "variant_id"),
        "symbol": _text(raw.get("symbol"), "symbol").upper(),
        "direction": "LONG",
        "allocated_capital": _positive_money(raw.get("allocated_capital"), "allocated_capital"),
        "estimated_loss_fraction": _fraction(
            raw.get("estimated_loss_fraction"), "estimated_loss_fraction", positive=True
        ),
        "protective_exit": build_protective_exit_contract(
            _mapping(raw.get("protective_exit"), "protective_exit")
        ),
        "source_allocation_sha256": _sha256(
            raw.get("source_allocation_sha256"), "source_allocation_sha256"
        ),
        "concentration_group_ids": allocation_groups,
        "correlation_group_ids": allocation_correlations,
        "provenance": _json_mapping(raw.get("provenance"), "allocation provenance"),
    }


def _limits(raw: Any, *, current_equity: Decimal) -> dict[str, Any]:
    limits = _mapping(raw, "limits")
    allowed = {
        "maximum_gross_exposure",
        "maximum_net_exposure",
        "per_asset_concentration",
        "per_strategy_concentration",
        "concentration_group_limits",
        "correlation_group_limits",
        "portfolio_drawdown_limit_fraction",
        "leverage_limit",
        "minimum_cash",
        "maximum_estimated_total_loss_at_stops",
    }
    _reject_unknown(limits, allowed, "limits")
    result = {
        "maximum_gross_exposure": _positive_money(
            limits.get("maximum_gross_exposure"), "maximum_gross_exposure"
        ),
        "maximum_net_exposure": _positive_money(
            limits.get("maximum_net_exposure"), "maximum_net_exposure"
        ),
        "per_asset_concentration": _positive_money(
            limits.get("per_asset_concentration"), "per_asset_concentration"
        ),
        "per_strategy_concentration": _positive_money(
            limits.get("per_strategy_concentration"), "per_strategy_concentration"
        ),
        "concentration_group_limits": _money_map(
            limits.get("concentration_group_limits"), "concentration_group_limits"
        ),
        "correlation_group_limits": _money_map(
            limits.get("correlation_group_limits"), "correlation_group_limits"
        ),
        "portfolio_drawdown_limit_fraction": _fraction(
            limits.get("portfolio_drawdown_limit_fraction"),
            "portfolio_drawdown_limit_fraction",
            positive=True,
        ),
        "leverage_limit": _fraction(limits.get("leverage_limit"), "leverage_limit", positive=True),
        "minimum_cash": _money(limits.get("minimum_cash"), "minimum_cash"),
        "maximum_estimated_total_loss_at_stops": _positive_money(
            limits.get("maximum_estimated_total_loss_at_stops"),
            "maximum_estimated_total_loss_at_stops",
        ),
    }
    if result["leverage_limit"] > 1.0:
        raise ValueError("leverage_limit must not exceed 1.0; leverage is unsupported.")
    if result["minimum_cash"] > current_equity:
        raise ValueError("minimum_cash must not exceed current_equity.")
    return result


def _correlation_evidence(
    raw: Any, *, as_of: datetime, required_groups: set[str]
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("correlation evidence must be a list.")
    normalized: list[dict[str, Any]] = []
    for item in raw:
        evidence = _mapping(item, "correlation evidence")
        _reject_unknown(
            evidence,
            {
                "correlation_group_id",
                "symbols",
                "window_start",
                "window_end",
                "as_of_timestamp",
                "maximum_observed_correlation",
                "evidence_sha256",
            },
            "correlation evidence",
        )
        start = _timestamp(evidence.get("window_start"), "correlation window_start")
        end = _timestamp(evidence.get("window_end"), "correlation window_end")
        evidence_as_of = _timestamp(
            evidence.get("as_of_timestamp"), "correlation as_of_timestamp"
        )
        if not start < end or end > evidence_as_of or evidence_as_of > as_of:
            raise ValueError("correlation evidence contains a future or invalid time window.")
        symbols = _unique_text_list(evidence.get("symbols"), "correlation symbols")
        normalized.append(
            {
                "correlation_group_id": _text(
                    evidence.get("correlation_group_id"), "correlation_group_id"
                ),
                "symbols": sorted(symbol.upper() for symbol in symbols),
                "window_start": _format_timestamp(start),
                "window_end": _format_timestamp(end),
                "as_of_timestamp": _format_timestamp(evidence_as_of),
                "maximum_observed_correlation": _correlation(
                    evidence.get("maximum_observed_correlation")
                ),
                "evidence_sha256": _sha256(evidence.get("evidence_sha256"), "evidence_sha256"),
            }
        )
    normalized.sort(key=lambda item: item["correlation_group_id"])
    groups = [item["correlation_group_id"] for item in normalized]
    if len(groups) != len(set(groups)) or set(groups) != required_groups:
        raise ValueError("correlation evidence must exactly cover configured correlation groups.")
    return normalized


def _bind_correlation_symbols(
    allocations: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> None:
    by_group = {item["correlation_group_id"]: set(item["symbols"]) for item in evidence}
    for allocation in allocations:
        for group_id in allocation["correlation_group_ids"]:
            if allocation["symbol"] not in by_group[group_id]:
                raise ValueError("correlation evidence symbol membership is incomplete.")


def _group_ids(raw: Any, *, name: str, configured: set[str]) -> list[str]:
    values = _unique_text_list(raw, name)
    normalized = sorted(values)
    if configured and not normalized:
        raise ValueError(f"{name} must include an explicit configured group.")
    if set(normalized) - configured:
        raise ValueError(f"{name} contains an unconfigured group.")
    return normalized


def _public_limits(limits: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            {group: _float(value) for group, value in sorted(raw.items())}
            if isinstance(raw, dict)
            else _float(raw)
            if isinstance(raw, Decimal)
            else raw
        )
        for key, raw in limits.items()
    }


def _empty_totals() -> dict[str, Any]:
    return {
        "gross": Decimal("0"),
        "loss": Decimal("0"),
        "assets": {},
        "strategies": {},
        "concentration_groups": {},
        "correlation_groups": {},
    }


def _lineage(item: dict[str, Any]) -> dict[str, str]:
    return {
        "allocation_id": item["allocation_id"],
        "strategy_id": item["strategy_id"],
        "strategy_version": item["strategy_version"],
        "strategy_builder": item["strategy_builder"],
        "variant_id": item["variant_id"],
        "symbol": item["symbol"],
        "direction": item["direction"],
    }


def _public_allocation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **_lineage(item),
        "allocated_capital": _float(item["allocated_capital"]),
        "estimated_loss_fraction": item["estimated_loss_fraction"],
        "protective_exit": item["protective_exit"],
        "source_allocation_sha256": item["source_allocation_sha256"],
        "concentration_group_ids": item["concentration_group_ids"],
        "correlation_group_ids": item["correlation_group_ids"],
        "provenance": item["provenance"],
    }


def _disposition_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **_lineage(item),
        "estimated_loss_fraction": item["estimated_loss_fraction"],
        "protective_exit": item["protective_exit"],
        "source_allocation_sha256": item["source_allocation_sha256"],
        "concentration_group_ids": item["concentration_group_ids"],
        "correlation_group_ids": item["correlation_group_ids"],
        "provenance": item["provenance"],
    }


def _summary(values: dict[str, Decimal], key: str) -> list[dict[str, Any]]:
    return [{key: item, "exposure": _float(value)} for item, value in sorted(values.items())]


def _increment(values: dict[str, Decimal], key: str, amount: Decimal) -> None:
    values[key] = values.get(key, Decimal("0")) + amount


def _money_map(raw: Any, name: str) -> dict[str, Decimal]:
    mapping = _mapping(raw, name)
    return {
        _text(key, f"{name} key"): _positive_money(value, f"{name} value")
        for key, value in sorted(mapping.items())
    }


def _unique_text_list(raw: Any, name: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a list.")
    values = [_text(item, name) for item in raw]
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates.")
    return values


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


def _correlation(raw: Any) -> float:
    value = _number(raw, "maximum_observed_correlation")
    if value < -1.0 or value > 1.0:
        raise ValueError("maximum_observed_correlation must be between -1 and 1.")
    return value


def _fraction(raw: Any, name: str, *, positive: bool) -> float:
    value = _number(raw, name)
    if value < 0.0 or value > 1.0 or (positive and value == 0.0):
        raise ValueError(f"{name} must be greater than 0 and no greater than 1.")
    return value


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
