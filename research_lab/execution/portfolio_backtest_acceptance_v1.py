from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any


REQUEST_VERSION = "portfolio_backtest_acceptance_request_v1"
RESULT_VERSION = "portfolio_backtest_acceptance_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def run_portfolio_backtest_acceptance(request: dict[str, Any]) -> dict[str, Any]:
    """Run a deterministic synthetic-only, delayed-fill portfolio backtest."""
    _mapping(request, "portfolio backtest request")
    _reject_unknown(
        request,
        {
            "version",
            "synthetic_data_only",
            "initial_cash",
            "execution_policy",
            "market_data",
            "decisions",
            "provenance",
        },
        "portfolio backtest request",
    )
    if request.get("version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    if request.get("synthetic_data_only") is not True:
        raise ValueError("synthetic_data_only must be true.")
    initial_cash = _positive_money(request.get("initial_cash"), "initial_cash")
    policy = _execution_policy(request.get("execution_policy"))
    market_data = _market_data(request.get("market_data"))
    decisions = _decisions(request.get("decisions"), market_data=market_data)
    provenance = _json_mapping(request.get("provenance"), "provenance")

    pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
    unfilled: list[dict[str, Any]] = []
    for decision in decisions:
        bars = market_data[decision["symbol"]]
        index = next(
            i for i, bar in enumerate(bars) if bar["timestamp"] == decision["decision_timestamp"]
        )
        fill_index = index + policy["fill_delay_bars"]
        if fill_index >= len(bars):
            unfilled.append(_public_decision(decision))
        else:
            pending.setdefault(
                (decision["symbol"], bars[fill_index]["timestamp"]), []
            ).append(decision)
    for queued in pending.values():
        queued.sort(key=_decision_key)

    cash = initial_cash
    positions: dict[tuple[str, str, str], dict[str, Any]] = {}
    fills: list[dict[str, Any]] = []
    runtime_rejections: list[dict[str, Any]] = []
    realized_gross = Decimal("0")
    transaction_costs = Decimal("0")
    slippage_costs = Decimal("0")
    latest_close: dict[str, Decimal] = {}
    equity_curve: list[dict[str, Any]] = []
    peak_equity = initial_cash
    maximum_drawdown = Decimal("0")
    timestamps = sorted(
        {bar["timestamp"] for bars in market_data.values() for bar in bars}
    )
    bars_by_key = {
        (symbol, bar["timestamp"]): bar
        for symbol, bars in market_data.items()
        for bar in bars
    }

    for timestamp in timestamps:
        symbols = sorted(
            symbol for symbol in market_data if (symbol, timestamp) in bars_by_key
        )
        entered_or_changed: set[tuple[str, str, str]] = set()
        for symbol in symbols:
            bar = bars_by_key[(symbol, timestamp)]
            latest_close[symbol] = bar["close"]
            for decision in pending.get((symbol, timestamp), []):
                position_key = _position_key(decision)
                prior_quantity = positions.get(position_key, {}).get("quantity", Decimal("0"))
                delta = decision["target_quantity"] - prior_quantity
                if delta == 0:
                    if decision["target_intent"] == "LONG" and position_key in positions:
                        positions[position_key]["protective_exit_price"] = decision[
                            "protective_exit_price"
                        ]
                    continue
                side = "BUY" if delta > 0 else "SELL"
                quantity = abs(delta)
                reference_price = bar["open"]
                fill_price = _slipped_price(
                    reference_price, side=side, slippage_bps=policy["slippage_bps"]
                )
                commission = policy["commission_per_fill"]
                notional = quantity * fill_price
                if side == "BUY" and notional + commission > cash:
                    runtime_rejections.append(
                        {
                            **_decision_lineage(decision),
                            "reason": "INSUFFICIENT_CASH",
                            "required_cash": _float(notional + commission),
                            "available_cash": _float(cash),
                        }
                    )
                    continue
                slippage = quantity * abs(fill_price - reference_price)
                if side == "BUY":
                    cash -= notional + commission
                    prior = positions.get(position_key)
                    prior_cost = (
                        prior["average_cost"] * prior["quantity"]
                        if prior is not None
                        else Decimal("0")
                    )
                    new_quantity = prior_quantity + quantity
                    positions[position_key] = {
                        **_decision_lineage(decision),
                        "quantity": new_quantity,
                        "average_cost": (prior_cost + notional) / new_quantity,
                        "protective_exit_price": decision["protective_exit_price"],
                        "entry_timestamp": timestamp,
                        "source_decision_id": decision["decision_id"],
                    }
                else:
                    prior = positions[position_key]
                    cash += notional - commission
                    realized_gross += quantity * (fill_price - prior["average_cost"])
                    new_quantity = prior_quantity - quantity
                    if new_quantity == 0:
                        del positions[position_key]
                    else:
                        prior["quantity"] = new_quantity
                        if decision["target_intent"] == "LONG":
                            prior["protective_exit_price"] = decision["protective_exit_price"]
                transaction_costs += commission
                slippage_costs += slippage
                entered_or_changed.add(position_key)
                fills.append(
                    _fill_artifact(
                        decision=decision,
                        fill_timestamp=timestamp,
                        side=side,
                        quantity=quantity,
                        reference_price=reference_price,
                        fill_price=fill_price,
                        commission=commission,
                        slippage=slippage,
                        reason="TARGET_ADJUSTMENT",
                    )
                )

            for position_key, position in sorted(list(positions.items())):
                if position["symbol"] != symbol or position_key in entered_or_changed:
                    continue
                stop = position["protective_exit_price"]
                if bar["low"] > stop:
                    continue
                quantity = position["quantity"]
                reference_price = min(bar["open"], stop)
                fill_price = _slipped_price(
                    reference_price, side="SELL", slippage_bps=policy["slippage_bps"]
                )
                commission = policy["commission_per_fill"]
                notional = quantity * fill_price
                cash += notional - commission
                realized_gross += quantity * (fill_price - position["average_cost"])
                transaction_costs += commission
                slippage = quantity * abs(fill_price - reference_price)
                slippage_costs += slippage
                synthetic_decision = {
                    "decision_id": position["source_decision_id"],
                    "decision_timestamp": position["entry_timestamp"],
                    "stage_lineage": position["stage_lineage"],
                    **_decision_lineage(position),
                }
                fills.append(
                    _fill_artifact(
                        decision=synthetic_decision,
                        fill_timestamp=timestamp,
                        side="SELL",
                        quantity=quantity,
                        reference_price=reference_price,
                        fill_price=fill_price,
                        commission=commission,
                        slippage=slippage,
                        reason="PROTECTIVE_EXIT",
                    )
                )
                del positions[position_key]

        market_value = sum(
            (
                position["quantity"] * latest_close[position["symbol"]]
                for position in positions.values()
            ),
            Decimal("0"),
        )
        equity = cash + market_value
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        equity_curve.append(
            {
                "timestamp": timestamp,
                "cash": _float(cash),
                "gross_exposure": _float(market_value),
                "net_exposure": _float(market_value),
                "equity": _float(equity),
                "drawdown_fraction": _float(drawdown),
            }
        )

    ending_market_value = sum(
        (
            position["quantity"] * latest_close[position["symbol"]]
            for position in positions.values()
        ),
        Decimal("0"),
    )
    ending_equity = cash + ending_market_value
    unrealized = sum(
        (
            position["quantity"]
            * (latest_close[position["symbol"]] - position["average_cost"])
            for position in positions.values()
        ),
        Decimal("0"),
    )
    net_realized = realized_gross - transaction_costs
    equity_from_pnl = initial_cash + net_realized + unrealized
    turnover_notional = sum(
        (Decimal(str(fill["notional"])) for fill in fills), Decimal("0")
    )
    ending_positions = [
        {
            **_decision_lineage(position),
            "quantity": _float(position["quantity"]),
            "average_cost": _float(position["average_cost"]),
            "last_close": _float(latest_close[position["symbol"]]),
            "market_value": _float(
                position["quantity"] * latest_close[position["symbol"]]
            ),
            "protective_exit_price": _float(position["protective_exit_price"]),
            "source_decision_id": position["source_decision_id"],
        }
        for _, position in sorted(positions.items())
    ]
    supplied_rejected_allocations = sorted(
        {
            value
            for decision in decisions
            for value in decision["rejected_allocations"]
        }
    )
    rejected_allocations: list[Any] = [*supplied_rejected_allocations, *runtime_rejections]
    canonical_request = {
        "version": REQUEST_VERSION,
        "synthetic_data_only": True,
        "initial_cash": _float(initial_cash),
        "execution_policy": _public_policy(policy),
        "market_data": {
            symbol: [_public_bar(bar) for bar in bars]
            for symbol, bars in market_data.items()
        },
        "decisions": [_public_decision(decision) for decision in decisions],
        "provenance": provenance,
    }
    result = {
        "version": RESULT_VERSION,
        "request_sha256": _canonical_sha256(canonical_request),
        "acceptance_status": "ACCEPTED_REVIEW_ONLY",
        "fills": fills,
        "unfilled_decisions": unfilled,
        "ending_positions": ending_positions,
        "equity_curve": equity_curve,
        "initial_cash": _float(initial_cash),
        "ending_cash": _float(cash),
        "ending_market_value": _float(ending_market_value),
        "ending_equity": _float(ending_equity),
        "realized_pnl": _float(net_realized),
        "unrealized_pnl": _float(unrealized),
        "transaction_costs": _float(transaction_costs),
        "slippage_costs": _float(slippage_costs),
        "turnover": _float(turnover_notional / initial_cash),
        "maximum_drawdown_fraction": _float(maximum_drawdown),
        "rejected_signals": sorted(
            {
                value for decision in decisions for value in decision["rejected_signals"]
            }
        ),
        "rejected_allocations": rejected_allocations,
        "risk_limit_events": sorted(
            {
                value for decision in decisions for value in decision["risk_limit_events"]
            }
        ),
        "cash_reconciled": cash + ending_market_value == ending_equity,
        "equity_reconciled": equity_from_pnl == ending_equity,
        "no_same_bar_fill_proof": all(
            fill["fill_timestamp"] > fill["decision_timestamp"] for fill in fills
        ),
        "chronological_execution_proof": [fill["fill_timestamp"] for fill in fills]
        == sorted(fill["fill_timestamp"] for fill in fills),
        "no_future_data_used": True,
        "backtest_artifact_sha256": _canonical_sha256(
            {"fills": fills, "equity_curve": equity_curve, "ending_positions": ending_positions}
        ),
        "provenance": provenance,
        "provider_calls_used": 0,
        "network_used": False,
        "optimization_performed": False,
        "parameter_search_performed": False,
        "strategy_generation_performed": False,
        "generated_code_executed": False,
        "broker_orders_emitted": False,
        "paper_trading_performed": False,
        "broker_integration_used": False,
        "production_runtime_supported": False,
    }
    result["output_sha256"] = _canonical_sha256(result)
    return result


def _execution_policy(raw: Any) -> dict[str, Any]:
    policy = _mapping(raw, "execution_policy")
    _reject_unknown(
        policy,
        {"fill_delay_bars", "same_bar_fill", "slippage_bps", "commission_per_fill"},
        "execution_policy",
    )
    delay = policy.get("fill_delay_bars")
    if isinstance(delay, bool) or not isinstance(delay, int) or delay != 1:
        raise ValueError("fill_delay_bars must be exactly 1.")
    if policy.get("same_bar_fill") is not False:
        raise ValueError("same_bar_fill must be false.")
    return {
        "fill_delay_bars": 1,
        "same_bar_fill": False,
        "slippage_bps": _non_negative_number(policy.get("slippage_bps"), "slippage_bps"),
        "commission_per_fill": _money(
            policy.get("commission_per_fill"), "commission_per_fill"
        ),
    }


def _market_data(raw: Any) -> dict[str, list[dict[str, Any]]]:
    mapping = _mapping(raw, "market_data")
    if not mapping:
        raise ValueError("market_data must not be empty.")
    result: dict[str, list[dict[str, Any]]] = {}
    for raw_symbol, raw_bars in sorted(mapping.items()):
        symbol = _text(raw_symbol, "market symbol").upper()
        if symbol != raw_symbol:
            raise ValueError("market_data symbols must be canonical uppercase values.")
        if not isinstance(raw_bars, list) or len(raw_bars) < 2:
            raise ValueError("each market symbol requires at least two bars.")
        bars = [_bar(item) for item in raw_bars]
        timestamps = [bar["timestamp"] for bar in bars]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("market bars must be strictly chronological and unique.")
        result[symbol] = bars
    return result


def _bar(raw: Any) -> dict[str, Any]:
    bar = _mapping(raw, "market bar")
    _reject_unknown(
        bar,
        {"timestamp", "open", "high", "low", "close", "volume", "source_input_sha256"},
        "market bar",
    )
    timestamp = _format_timestamp(_timestamp(bar.get("timestamp"), "bar timestamp"))
    open_price = _positive_money(bar.get("open"), "bar open")
    high = _positive_money(bar.get("high"), "bar high")
    low = _positive_money(bar.get("low"), "bar low")
    close = _positive_money(bar.get("close"), "bar close")
    volume = _money(bar.get("volume"), "bar volume")
    if high < max(open_price, close) or low > min(open_price, close) or high < low:
        raise ValueError("market bar OHLC values are inconsistent.")
    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "source_input_sha256": _sha256(
            bar.get("source_input_sha256"), "bar source_input_sha256"
        ),
    }


def _decisions(raw: Any, *, market_data: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("decisions must be a non-empty list.")
    result = [_decision(item) for item in raw]
    ids = [item["decision_id"] for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate decision_id is not allowed.")
    for decision in result:
        if decision["symbol"] not in market_data:
            raise ValueError("decision symbol has no market data.")
        bar_timestamps = {bar["timestamp"] for bar in market_data[decision["symbol"]]}
        if decision["decision_timestamp"] not in bar_timestamps:
            raise ValueError("decision timestamp must exactly match a market bar.")
    return sorted(result, key=_decision_key)


def _decision(raw: Any) -> dict[str, Any]:
    decision = _mapping(raw, "decision")
    _reject_unknown(
        decision,
        {
            "decision_id",
            "decision_timestamp",
            "strategy_id",
            "strategy_version",
            "strategy_builder",
            "variant_id",
            "symbol",
            "target_intent",
            "target_quantity",
            "protective_exit_price",
            "stage_lineage",
            "rejected_signals",
            "rejected_allocations",
            "risk_limit_events",
            "provenance",
        },
        "decision",
    )
    intent = _text(decision.get("target_intent"), "target_intent")
    if intent not in {"LONG", "FLAT"}:
        raise ValueError("target_intent must be LONG or FLAT; shorting is unsupported.")
    quantity = _money(decision.get("target_quantity"), "target_quantity")
    if intent == "LONG":
        if quantity <= 0:
            raise ValueError("LONG target_quantity must be positive.")
        stop = _positive_money(
            decision.get("protective_exit_price"), "protective_exit_price"
        )
    else:
        if quantity != 0 or decision.get("protective_exit_price") is not None:
            raise ValueError("FLAT decisions require zero quantity and null protective_exit_price.")
        stop = None
    return {
        "decision_id": _text(decision.get("decision_id"), "decision_id"),
        "decision_timestamp": _format_timestamp(
            _timestamp(decision.get("decision_timestamp"), "decision_timestamp")
        ),
        "strategy_id": _text(decision.get("strategy_id"), "strategy_id"),
        "strategy_version": _text(decision.get("strategy_version"), "strategy_version"),
        "strategy_builder": _text(decision.get("strategy_builder"), "strategy_builder"),
        "variant_id": _text(decision.get("variant_id"), "variant_id"),
        "symbol": _text(decision.get("symbol"), "symbol").upper(),
        "target_intent": intent,
        "target_quantity": quantity,
        "protective_exit_price": stop,
        "stage_lineage": _stage_lineage(decision.get("stage_lineage")),
        "rejected_signals": _text_list(decision.get("rejected_signals"), "rejected_signals"),
        "rejected_allocations": _text_list(
            decision.get("rejected_allocations"), "rejected_allocations"
        ),
        "risk_limit_events": _text_list(
            decision.get("risk_limit_events"), "risk_limit_events"
        ),
        "provenance": _json_mapping(decision.get("provenance"), "decision provenance"),
    }


def _stage_lineage(raw: Any) -> dict[str, str]:
    lineage = _mapping(raw, "stage_lineage")
    fields = {
        "aggregation_sha256",
        "capital_allocation_sha256",
        "risk_overlay_sha256",
        "position_sizing_sha256",
    }
    _reject_unknown(lineage, fields, "stage_lineage")
    return {field: _sha256(lineage.get(field), field) for field in sorted(fields)}


def _fill_artifact(
    *,
    decision: dict[str, Any],
    fill_timestamp: str,
    side: str,
    quantity: Decimal,
    reference_price: Decimal,
    fill_price: Decimal,
    commission: Decimal,
    slippage: Decimal,
    reason: str,
) -> dict[str, Any]:
    payload = {
        "decision_id": decision["decision_id"],
        "decision_timestamp": decision["decision_timestamp"],
        "fill_timestamp": fill_timestamp,
        **_decision_lineage(decision),
        "side": side,
        "quantity": _float(quantity),
        "reference_price": _float(reference_price),
        "fill_price": _float(fill_price),
        "notional": _float(quantity * fill_price),
        "commission": _float(commission),
        "slippage_cost": _float(slippage),
        "reason": reason,
        "stage_lineage": decision["stage_lineage"],
    }
    payload["fill_id"] = _canonical_sha256(payload)
    return payload


def _slipped_price(price: Decimal, *, side: str, slippage_bps: float) -> Decimal:
    fraction = Decimal(str(slippage_bps)) / Decimal("10000")
    return price * (Decimal("1") + fraction if side == "BUY" else Decimal("1") - fraction)


def _position_key(decision: dict[str, Any]) -> tuple[str, str, str]:
    return decision["strategy_id"], decision["variant_id"], decision["symbol"]


def _decision_key(decision: dict[str, Any]) -> tuple[str, ...]:
    return (
        decision["decision_timestamp"],
        decision["symbol"],
        decision["strategy_id"],
        decision["variant_id"],
        decision["decision_id"],
    )


def _decision_lineage(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": decision["strategy_id"],
        "strategy_version": decision["strategy_version"],
        "strategy_builder": decision["strategy_builder"],
        "variant_id": decision["variant_id"],
        "symbol": decision["symbol"],
        "stage_lineage": decision["stage_lineage"],
    }


def _public_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_id": decision["decision_id"],
        "decision_timestamp": decision["decision_timestamp"],
        **_decision_lineage(decision),
        "target_intent": decision["target_intent"],
        "target_quantity": _float(decision["target_quantity"]),
        "protective_exit_price": None
        if decision["protective_exit_price"] is None
        else _float(decision["protective_exit_price"]),
        "rejected_signals": decision["rejected_signals"],
        "rejected_allocations": decision["rejected_allocations"],
        "risk_limit_events": decision["risk_limit_events"],
        "provenance": decision["provenance"],
    }


def _public_bar(bar: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _float(value) if isinstance(value, Decimal) else value
        for key, value in bar.items()
    }


def _public_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        **policy,
        "commission_per_fill": _float(policy["commission_per_fill"]),
    }


def _text_list(raw: Any, name: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a list.")
    values = [_text(value, name) for value in raw]
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates.")
    return sorted(values)


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


def _non_negative_number(raw: Any, name: str) -> float:
    value = _number(raw, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative.")
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
