"""Deterministic evidence-search terms for blocker-first book research."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class BlockerDefinition:
    name: str
    term_weights: Mapping[str, float]


_WALK_FORWARD_FAIL = BlockerDefinition(
    name="walk_forward_fail",
    term_weights=MappingProxyType(
        {
            "walk-forward": 16.0,
            "walk forward": 16.0,
            "robustness": 12.0,
            "robust": 8.0,
            "parameter stability": 15.0,
            "parameter sensitivity": 12.0,
            "overfitting": 14.0,
            "regime": 10.0,
            "market state": 9.0,
            "adaptive trading": 9.0,
            "volatility normalization": 9.0,
            "sample splitting": 8.0,
            "trend persistence": 7.0,
            "model decay": 10.0,
            "trading systems": 8.0,
            "system methods": 5.0,
        }
    ),
)

_DRAWDOWN_FAIL = BlockerDefinition(
    name="drawdown_fail",
    term_weights=MappingProxyType(
        {
            "drawdown control": 18.0,
            "drawdown": 14.0,
            "risk management": 16.0,
            "money management": 14.0,
            "volatility targeting": 15.0,
            "defensive allocation": 14.0,
            "position sizing": 13.0,
            "crisis protection": 12.0,
            "portfolio risk": 12.0,
            "regime aware risk control": 11.0,
            "risk control": 10.0,
            "capital preservation": 10.0,
            "hedge": 8.0,
            "tail risk": 8.0,
        }
    ),
)

BLOCKERS: Mapping[str, BlockerDefinition] = MappingProxyType(
    {
        _WALK_FORWARD_FAIL.name: _WALK_FORWARD_FAIL,
        _DRAWDOWN_FAIL.name: _DRAWDOWN_FAIL,
    }
)


def canonicalize_blocker_id(raw: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(raw).casefold()).strip()
    if not normalized:
        return None
    tokens = set(normalized.split())
    walk_forward_phrase = "walk forward" in normalized
    wf_rate_phrase = "wf pass rate" in normalized
    if (
        (walk_forward_phrase and tokens & {"fail", "failed", "below", "insufficient", "robustness"})
        or (wf_rate_phrase and tokens & {"below", "fail", "failed", "insufficient"})
    ):
        return "walk_forward_fail"
    if "drawdown" in tokens:
        return "drawdown_fail"
    if "cost stress" in normalized or "slippage stress" in normalized:
        return "cost_stress"
    if normalized in {"walk forward fail", "walk forward robustness"}:
        return "walk_forward_fail"
    if normalized in {"drawdown", "cost stress"}:
        return normalized.replace(" ", "_")
    return None


def get_blocker_definition(blocker: str) -> BlockerDefinition:
    normalized = str(blocker).strip().casefold()
    try:
        return BLOCKERS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported blocker: {blocker}") from exc
