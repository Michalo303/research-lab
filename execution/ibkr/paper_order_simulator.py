from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_lab.registry import append_jsonl


@dataclass(frozen=True)
class PaperOrderSimulationConfig:
    strategy_allowlist: set[str] = field(default_factory=set)
    max_order_notional: float = 5000.0
    spread_bps: float = 5.0
    slippage_bps: float = 10.0
    partial_fill_ratio: float = 1.0
    long_only: bool = True
    mode: str = "paper"

    @classmethod
    def from_env(cls) -> "PaperOrderSimulationConfig":
        allowlist = {
            item.strip()
            for item in os.getenv("PAPER_ORDER_STRATEGY_ALLOWLIST", "").split(",")
            if item.strip()
        }
        return cls(
            strategy_allowlist=allowlist,
            max_order_notional=float(os.getenv("PAPER_ORDER_MAX_NOTIONAL_USD", "5000")),
            spread_bps=float(os.getenv("PAPER_ORDER_SPREAD_BPS", "5")),
            slippage_bps=float(os.getenv("PAPER_ORDER_SLIPPAGE_BPS", "10")),
            partial_fill_ratio=float(os.getenv("PAPER_ORDER_PARTIAL_FILL_RATIO", "1")),
            long_only=os.getenv("PAPER_ORDER_LONG_ONLY", "1") == "1",
            mode=os.getenv("IBKR_MODE", "paper").lower(),
        )


def simulate_paper_orders(
    root: Path,
    candidates: list[dict[str, Any]],
    latest_prices: dict[str, float],
    equity: float,
    config: PaperOrderSimulationConfig | None = None,
) -> dict[str, Any]:
    config = config or PaperOrderSimulationConfig.from_env()
    payload = {
        "simulated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "broker_calls": False,
        "mode": config.mode,
        "orders": [],
    }
    for candidate in candidates:
        strategy_id = str(candidate.get("strategy_id", ""))
        target_weights = candidate.get("target_weights", {}) or {}
        for symbol, target_weight in target_weights.items():
            payload["orders"].append(_simulate_order(strategy_id, str(symbol).upper(), float(target_weight), latest_prices, equity, config))
    append_jsonl(root / "registry" / "paper_order_simulations.jsonl", payload)
    return payload


def _simulate_order(
    strategy_id: str,
    symbol: str,
    target_weight: float,
    latest_prices: dict[str, float],
    equity: float,
    config: PaperOrderSimulationConfig,
) -> dict[str, Any]:
    base = {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "target_weight": target_weight,
        "target_notional": equity * target_weight,
        "status": "rejected",
        "reject_reason": "",
        "fill_price": None,
        "quantity": 0.0,
        "filled_quantity": 0.0,
        "fill_ratio": 0.0,
    }
    if config.mode != "paper":
        return {**base, "reject_reason": "Simulator refuses non-paper mode."}
    if strategy_id not in config.strategy_allowlist:
        return {**base, "reject_reason": f"{strategy_id} not in PAPER_ORDER_STRATEGY_ALLOWLIST."}
    if config.long_only and target_weight < 0:
        return {**base, "reject_reason": "Long-only simulator rejects negative target weights."}
    target_notional = float(base["target_notional"])
    if abs(target_notional) > config.max_order_notional:
        return {**base, "reject_reason": "target_notional exceeds max_order_notional."}
    price = float(latest_prices.get(symbol, 0.0) or 0.0)
    if price <= 0:
        return {**base, "reject_reason": "Missing positive latest price."}
    fill_price = price * (1.0 + (config.spread_bps + config.slippage_bps) / 10000.0)
    quantity = target_notional / fill_price
    fill_ratio = min(max(config.partial_fill_ratio, 0.0), 1.0)
    status = "filled" if fill_ratio >= 1.0 else "partial_filled"
    return {
        **base,
        "status": status,
        "reject_reason": "",
        "fill_price": fill_price,
        "quantity": quantity,
        "filled_quantity": quantity * fill_ratio,
        "fill_ratio": fill_ratio,
    }
