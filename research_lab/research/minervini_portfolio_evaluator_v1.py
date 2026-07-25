from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd


RESULT_VERSION = "minervini_portfolio_evaluator_result_v1"
MAXIMUM_POSITIONS = 8
RISK_FRACTION = 0.005
MAXIMUM_POSITION_FRACTION = 0.125
MAXIMUM_GROSS_FRACTION = 1.0


def run_minervini_portfolio_v1(
    *,
    panel: pd.DataFrame,
    signals: pd.DataFrame,
    instrument_types: Mapping[str, str],
    initial_cash: float = 100_000.0,
    cost_bps_per_side: float = 15.0,
    evaluation_start: pd.Timestamp | str | None = None,
    evaluation_end: pd.Timestamp | str | None = None,
    terminal_values: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Run one chronological, long-only Minervini portfolio ledger."""
    symbols = _validate_inputs(panel, signals, instrument_types)
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be positive and finite.")
    if not math.isfinite(cost_bps_per_side) or cost_bps_per_side < 0:
        raise ValueError("cost_bps_per_side must be finite and non-negative.")
    validated_terminal_values = _validated_terminal_values(
        terminal_values, symbols
    )

    close = _field(panel, symbols, "close")
    sma50 = close.rolling(50, min_periods=50).mean()
    start = _evaluation_boundary(
        evaluation_start, panel.index[0], "evaluation_start"
    )
    end = _evaluation_boundary(
        evaluation_end, panel.index[-1], "evaluation_end"
    )
    if start > end:
        raise ValueError("evaluation_start must not follow evaluation_end.")
    mask = (panel.index >= start) & (panel.index <= end)
    if not bool(mask.any()):
        raise ValueError("evaluation window does not overlap the panel.")
    panel = panel.loc[mask]
    signals = signals.loc[mask]
    sma50 = sma50.loc[mask]
    cash = float(initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    pending_entries: list[dict[str, Any]] = []
    pending_exits: set[str] = set()
    trades: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []
    total_costs = 0.0
    turnover_notional = 0.0
    maximum_concurrent = 0
    maximum_gross_fraction = 0.0
    peak_equity = float(initial_cash)
    maximum_drawdown = 0.0
    cost_fraction = cost_bps_per_side / 10_000.0

    for position_index, timestamp in enumerate(panel.index):
        is_last = position_index == len(panel.index) - 1
        for symbol in sorted(list(positions)):
            if _complete_bar(panel, timestamp, symbol):
                continue
            terminal = validated_terminal_values.get(symbol)
            if terminal is None or terminal["timestamp"] != timestamp:
                raise ValueError(
                    f"{symbol} has a missing held-position bar at "
                    f"{timestamp.isoformat()}; terminal-value evidence is required."
                )
            cash, costs, notional = _exit_position(
                positions=positions,
                symbol=symbol,
                timestamp=timestamp,
                reference_price=float(terminal["price"]),
                reason="DELISTING_TERMINAL_VALUE",
                cost_fraction=cost_fraction,
                cash=cash,
                trades=trades,
                terminal_value_evidence_sha256=str(
                    terminal["evidence_sha256"]
                ),
            )
            total_costs += costs
            turnover_notional += notional

        for symbol in sorted(pending_exits):
            if symbol not in positions:
                continue
            reference = float(panel.loc[timestamp, (symbol, "open")])
            cash, costs, notional = _exit_position(
                positions=positions,
                symbol=symbol,
                timestamp=timestamp,
                reference_price=reference,
                reason="SMA50_EXIT",
                cost_fraction=cost_fraction,
                cash=cash,
                trades=trades,
            )
            total_costs += costs
            turnover_notional += notional
        pending_exits.clear()

        pending_entries.sort(
            key=lambda item: (-float(item["rs_percentile"]), str(item["symbol"]))
        )
        for entry in pending_entries:
            symbol = str(entry["symbol"])
            if symbol in positions:
                rejected.append(_rejection(timestamp, symbol, "ALREADY_HELD"))
                continue
            if len(positions) >= MAXIMUM_POSITIONS:
                rejected.append(
                    _rejection(timestamp, symbol, "MAXIMUM_POSITIONS")
                )
                continue
            if not _complete_bar(panel, timestamp, symbol):
                rejected.append(
                    _rejection(timestamp, symbol, "MISSING_ENTRY_BAR")
                )
                continue
            open_price = float(panel.loc[timestamp, (symbol, "open")])
            pivot = float(entry["pivot"])
            stop = float(entry["structural_stop"])
            atr20 = float(entry["atr20"])
            if open_price > pivot * 1.02:
                rejected.append(
                    _rejection(timestamp, symbol, "GAP_ABOVE_PIVOT")
                )
                continue
            stop_distance = open_price - stop
            if stop_distance <= 0 or stop_distance < 2.0 * atr20:
                rejected.append(
                    _rejection(timestamp, symbol, "STOP_INSIDE_TWO_ATR")
                )
                continue
            if stop_distance / open_price > 0.07:
                rejected.append(
                    _rejection(timestamp, symbol, "STOP_BEYOND_SEVEN_PERCENT")
                )
                continue
            equity_before = _equity(cash, positions, panel, timestamp)
            risk_quantity = math.floor(
                equity_before * RISK_FRACTION / stop_distance
            )
            position_quantity = math.floor(
                equity_before * MAXIMUM_POSITION_FRACTION / open_price
            )
            current_gross = _gross_value(positions, panel, timestamp)
            gross_room_quantity = math.floor(
                max(equity_before * MAXIMUM_GROSS_FRACTION - current_gross, 0.0)
                / open_price
            )
            fill_price = open_price * (1.0 + cost_fraction)
            cash_quantity = math.floor(cash / fill_price)
            quantity = min(
                risk_quantity,
                position_quantity,
                gross_room_quantity,
                cash_quantity,
            )
            if quantity <= 0:
                rejected.append(
                    _rejection(timestamp, symbol, "INSUFFICIENT_CAPACITY")
                )
                continue
            notional = quantity * fill_price
            cost = quantity * (fill_price - open_price)
            cash -= notional
            total_costs += cost
            turnover_notional += notional
            positions[symbol] = {
                "symbol": symbol,
                "signal_timestamp": str(entry["signal_timestamp"]),
                "entry_timestamp": timestamp.isoformat(),
                "quantity": quantity,
                "entry_reference_price": open_price,
                "entry_price": fill_price,
                "entry_notional": quantity * open_price,
                "initial_stop": stop,
                "stop": stop,
                "initial_risk_per_share": stop_distance,
                "initial_account_risk": quantity * stop_distance,
                "break_even_activated": False,
                "entry_cost": cost,
            }
        pending_entries = []

        for symbol in sorted(list(positions)):
            position = positions[symbol]
            low = float(panel.loc[timestamp, (symbol, "low")])
            if low > float(position["stop"]):
                continue
            open_price = float(panel.loc[timestamp, (symbol, "open")])
            reference = min(open_price, float(position["stop"]))
            cash, costs, notional = _exit_position(
                positions=positions,
                symbol=symbol,
                timestamp=timestamp,
                reference_price=reference,
                reason="PROTECTIVE_STOP",
                cost_fraction=cost_fraction,
                cash=cash,
                trades=trades,
            )
            total_costs += costs
            turnover_notional += notional

        for symbol, position in sorted(positions.items()):
            closing_price = float(panel.loc[timestamp, (symbol, "close")])
            two_r = float(position["entry_reference_price"]) + 2.0 * float(
                position["initial_risk_per_share"]
            )
            if not position["break_even_activated"] and closing_price >= two_r:
                position["break_even_activated"] = True
                position["stop"] = max(
                    float(position["stop"]),
                    float(position["entry_reference_price"]),
                )
            moving_average = sma50.loc[timestamp, symbol]
            if (
                position["break_even_activated"]
                and pd.notna(moving_average)
                and closing_price < float(moving_average)
            ):
                pending_exits.add(symbol)

        if is_last:
            for symbol in sorted(list(positions)):
                reference = float(panel.loc[timestamp, (symbol, "close")])
                cash, costs, notional = _exit_position(
                    positions=positions,
                    symbol=symbol,
                    timestamp=timestamp,
                    reference_price=reference,
                    reason="END_OF_DATA",
                    cost_fraction=cost_fraction,
                    cash=cash,
                    trades=trades,
                )
                total_costs += costs
                turnover_notional += notional

        equity = _equity(cash, positions, panel, timestamp)
        gross = _gross_value(positions, panel, timestamp)
        peak_equity = max(peak_equity, equity)
        drawdown = equity / peak_equity - 1.0
        maximum_drawdown = min(maximum_drawdown, drawdown)
        gross_fraction = gross / equity if equity > 0 else 0.0
        maximum_gross_fraction = max(maximum_gross_fraction, gross_fraction)
        maximum_concurrent = max(maximum_concurrent, len(positions))
        equity_curve.append(
            {
                "timestamp": timestamp.isoformat(),
                "cash": cash,
                "gross_exposure": gross,
                "equity": equity,
                "drawdown": drawdown,
                "position_count": len(positions),
            }
        )

        if not is_last:
            for symbol in symbols:
                if bool(signals.loc[timestamp, (symbol, "signal")]):
                    pending_entries.append(
                        {
                            "symbol": symbol,
                            "signal_timestamp": timestamp.isoformat(),
                            "pivot": float(
                                signals.loc[timestamp, (symbol, "pivot")]
                            ),
                            "structural_stop": float(
                                signals.loc[
                                    timestamp, (symbol, "structural_stop")
                                ]
                            ),
                            "atr20": float(
                                signals.loc[timestamp, (symbol, "atr20")]
                            ),
                            "rs_percentile": float(
                                signals.loc[
                                    timestamp, (symbol, "rs_percentile")
                                ]
                            ),
                        }
                    )

    ending_equity = cash
    cumulative_return = ending_equity / initial_cash - 1.0
    years = len(panel.index) / 252.0
    cagr = (
        (ending_equity / initial_cash) ** (1.0 / years) - 1.0
        if years > 0 and ending_equity > 0
        else -1.0
    )
    trade_returns = [float(item["return_fraction"]) for item in trades]
    wins = [value for value in trade_returns if value > 0]
    losses = [value for value in trade_returns if value <= 0]
    average_exposure = float(
        np.mean(
            [
                row["gross_exposure"] / row["equity"]
                if row["equity"] > 0
                else 0.0
                for row in equity_curve
            ]
        )
    )
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "initial_cash": initial_cash,
        "evaluation_start": panel.index[0].isoformat(),
        "evaluation_end": panel.index[-1].isoformat(),
        "ending_cash": cash,
        "ending_equity": ending_equity,
        "cumulative_return": cumulative_return,
        "cagr": cagr,
        "maximum_drawdown": maximum_drawdown,
        "mar": cagr / abs(maximum_drawdown)
        if maximum_drawdown < 0
        else None,
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "average_win": float(np.mean(wins)) if wins else 0.0,
        "average_loss": float(np.mean(losses)) if losses else 0.0,
        "average_exposure_fraction": average_exposure,
        "maximum_gross_exposure_fraction": maximum_gross_fraction,
        "maximum_concurrent_positions": maximum_concurrent,
        "turnover": turnover_notional / initial_cash,
        "transaction_costs": total_costs,
        "cost_drag_fraction": total_costs / initial_cash,
        "trades": trades,
        "rejected_entries": rejected,
        "equity_curve": equity_curve,
        "cash_reconciled": not positions and math.isclose(
            cash, ending_equity, rel_tol=0.0, abs_tol=1e-8
        ),
        "no_same_bar_fill_proof": all(
            item["entry_timestamp"] > item["signal_timestamp"] for item in trades
        ),
        "provider_calls_used": 0,
        "network_used": False,
        "broker_actions_used": 0,
        "registry_write_performed": False,
        "promotion_performed": False,
        "deployment_performed": False,
        "production_runtime_supported": False,
    }
    result["output_sha256"] = _hash(result)
    return result


def _evaluation_boundary(
    value: pd.Timestamp | str | None,
    default: pd.Timestamp,
    name: str,
) -> pd.Timestamp:
    if value is None:
        return default
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid timestamp.") from exc
    if timestamp.tzinfo is not None:
        raise ValueError(f"{name} must be timezone-naive.")
    return timestamp


def _validated_terminal_values(
    values: Mapping[str, Mapping[str, object]] | None,
    symbols: list[str],
) -> dict[str, dict[str, object]]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ValueError("terminal_values must be a mapping.")
    unknown = sorted(set(values) - set(symbols))
    if unknown:
        raise ValueError("terminal_values contains an unknown symbol.")
    result: dict[str, dict[str, object]] = {}
    for symbol, raw in values.items():
        if not isinstance(raw, Mapping) or set(raw) != {
            "timestamp",
            "price",
            "evidence_sha256",
        }:
            raise ValueError("terminal value evidence fields are invalid.")
        timestamp = _evaluation_boundary(
            raw["timestamp"], pd.Timestamp.min, "terminal value timestamp"
        )
        price = raw["price"]
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(float(price))
            or float(price) < 0
        ):
            raise ValueError("terminal value price must be finite and non-negative.")
        evidence = raw["evidence_sha256"]
        if (
            not isinstance(evidence, str)
            or len(evidence) != 64
            or any(character not in "0123456789abcdef" for character in evidence)
        ):
            raise ValueError(
                "terminal value evidence_sha256 must be a lowercase SHA-256."
            )
        result[str(symbol)] = {
            "timestamp": timestamp,
            "price": float(price),
            "evidence_sha256": evidence,
        }
    return result


def _complete_bar(
    panel: pd.DataFrame,
    timestamp: pd.Timestamp,
    symbol: str,
) -> bool:
    return all(
        math.isfinite(float(panel.loc[timestamp, (symbol, field)]))
        for field in ("open", "high", "low", "close")
    )


def _validate_inputs(
    panel: pd.DataFrame,
    signals: pd.DataFrame,
    instrument_types: Mapping[str, str],
) -> list[str]:
    if not isinstance(panel, pd.DataFrame) or panel.empty:
        raise ValueError("panel must be a non-empty DataFrame.")
    if not isinstance(panel.index, pd.DatetimeIndex):
        raise ValueError("panel index must be a DatetimeIndex.")
    if not panel.index.is_monotonic_increasing or not panel.index.is_unique:
        raise ValueError("panel index must be monotonic and unique.")
    if not isinstance(panel.columns, pd.MultiIndex):
        raise ValueError("panel columns must be a MultiIndex.")
    if not isinstance(signals, pd.DataFrame) or not signals.index.equals(
        panel.index
    ):
        raise ValueError("signals must match the panel index exactly.")
    symbols = sorted({str(value) for value in panel.columns.get_level_values(0)})
    required_bars = {"open", "high", "low", "close", "volume"}
    required_signals = {
        "signal",
        "pivot",
        "structural_stop",
        "atr20",
        "rs_percentile",
    }
    for symbol in symbols:
        if instrument_types.get(symbol, "").casefold() != "common stock":
            raise ValueError("portfolio symbols must be common stocks.")
        if not all((symbol, field) in panel.columns for field in required_bars):
            raise ValueError(f"{symbol} is missing OHLCV fields.")
        if not all(
            (symbol, field) in signals.columns for field in required_signals
        ):
            raise ValueError(f"{symbol} is missing signal fields.")
    return symbols


def _field(
    panel: pd.DataFrame, symbols: list[str], field: str
) -> pd.DataFrame:
    return pd.DataFrame(
        {symbol: panel.loc[:, (symbol, field)].astype(float) for symbol in symbols},
        index=panel.index,
    )


def _equity(
    cash: float,
    positions: Mapping[str, Mapping[str, object]],
    panel: pd.DataFrame,
    timestamp: pd.Timestamp,
) -> float:
    return cash + _gross_value(positions, panel, timestamp)


def _gross_value(
    positions: Mapping[str, Mapping[str, object]],
    panel: pd.DataFrame,
    timestamp: pd.Timestamp,
) -> float:
    return sum(
        int(position["quantity"])
        * float(panel.loc[timestamp, (symbol, "close")])
        for symbol, position in positions.items()
    )


def _exit_position(
    *,
    positions: dict[str, dict[str, Any]],
    symbol: str,
    timestamp: pd.Timestamp,
    reference_price: float,
    reason: str,
    cost_fraction: float,
    cash: float,
    trades: list[dict[str, object]],
    terminal_value_evidence_sha256: str | None = None,
) -> tuple[float, float, float]:
    position = positions.pop(symbol)
    quantity = int(position["quantity"])
    fill_price = reference_price * (1.0 - cost_fraction)
    notional = quantity * fill_price
    cost = quantity * (reference_price - fill_price)
    cash += notional
    entry_price = float(position["entry_price"])
    net_pnl = quantity * (fill_price - entry_price)
    trade = {
        "symbol": symbol,
        "signal_timestamp": position["signal_timestamp"],
        "entry_timestamp": position["entry_timestamp"],
        "exit_timestamp": timestamp.isoformat(),
        "quantity": quantity,
        "entry_price": entry_price,
        "entry_notional": float(position["entry_notional"]),
        "initial_stop": float(position["initial_stop"]),
        "initial_account_risk": float(position["initial_account_risk"]),
        "exit_price": fill_price,
        "exit_reason": reason,
        "break_even_activated": bool(position["break_even_activated"]),
        "net_pnl": net_pnl,
        "return_fraction": fill_price / entry_price - 1.0,
        "transaction_costs": float(position["entry_cost"]) + cost,
    }
    if terminal_value_evidence_sha256 is not None:
        trade["terminal_value_evidence_sha256"] = (
            terminal_value_evidence_sha256
        )
    trades.append(trade)
    return cash, cost, notional


def _rejection(
    timestamp: pd.Timestamp, symbol: str, reason: str
) -> dict[str, object]:
    return {
        "timestamp": timestamp.isoformat(),
        "symbol": symbol,
        "reason": reason,
    }


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
