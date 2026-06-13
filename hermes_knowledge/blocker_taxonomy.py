"""Deterministic evidence-search terms for blocker-first book research."""

from __future__ import annotations

from dataclasses import dataclass
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

BLOCKERS: Mapping[str, BlockerDefinition] = MappingProxyType(
    {_WALK_FORWARD_FAIL.name: _WALK_FORWARD_FAIL}
)


def get_blocker_definition(blocker: str) -> BlockerDefinition:
    normalized = str(blocker).strip().casefold()
    try:
        return BLOCKERS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported blocker: {blocker}") from exc
