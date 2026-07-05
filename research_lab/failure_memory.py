from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_lab.reports import build_rejection_diagnostics
from research_lab.jsonl import tail_jsonl


REASON_WEIGHTS = {
    "max drawdown too deep": 5,
    "insufficient walk-forward robustness": 4,
    "too few unseen trades": 4,
    "failed cost stress": 3,
    "validation return below threshold": 2,
    "unseen return below threshold": 2,
    "synthetic/fallback data used": 1,
    "missing required provider data": 1,
    "insufficient real data history": 1,
}

IGNORED_REASONS = {"failed promotion gate", "no accepted tier reached"}
NON_EXECUTABLE_PARAMETER_KEYS = {
    "risk_overlay_changed",
    "walk_forward_repair",
    "trade_count_repair",
    "min_unseen_trades_target",
    "cost_stress_repair",
    "turnover_repair",
    "source_hypothesis_id",
    "source_title",
    "source_strategy_id",
    "source_hermes_run_id",
    "source_hermes_provider",
    "source_risk_overlay_changed",
    "source_walk_forward_repair",
    "source_trade_count_repair",
    "source_min_unseen_trades_target",
    "source_cost_stress_repair",
    "source_turnover_repair",
    "source_material_design_change",
    "source_edge_repair",
    "source_validation_repair",
    "source_duplicate_hint",
}


@dataclass(frozen=True)
class SpecFailurePenalty:
    score: int
    reasons: tuple[str, ...] = ()
    matched_keys: tuple[str, ...] = ()


@dataclass
class FailurePattern:
    key: str
    reason_counts: Counter[str] = field(default_factory=Counter)
    strategy_ids: set[str] = field(default_factory=set)
    parameter_signatures: set[str] = field(default_factory=set)

    @property
    def base_score(self) -> int:
        return sum(REASON_WEIGHTS.get(reason, 1) * count for reason, count in self.reason_counts.items())


@dataclass
class FailureMemory:
    patterns: dict[str, FailurePattern] = field(default_factory=dict)

    def penalty_for_spec(self, spec: Any) -> SpecFailurePenalty:
        score = 0
        reasons: Counter[str] = Counter()
        matched_keys: list[str] = []
        for key in _spec_keys(spec):
            pattern = self.patterns.get(key)
            if not pattern:
                continue
            matched_keys.append(key)
            key_score = _scope_weight(key) * _repaired_score(pattern, spec)
            score += key_score
            reasons.update(pattern.reason_counts)
        return SpecFailurePenalty(
            score=int(score),
            reasons=tuple(sorted(reasons)),
            matched_keys=tuple(matched_keys),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_count": len(self.patterns),
            "patterns": [
                {
                    "key": key,
                    "reason_counts": dict(pattern.reason_counts),
                    "strategy_ids": sorted(pattern.strategy_ids),
                    "parameter_signatures": sorted(pattern.parameter_signatures),
                    "base_score": pattern.base_score,
                }
                for key, pattern in sorted(self.patterns.items())
            ],
        }


def build_failure_memory(root: Path, max_results: int = 200) -> FailureMemory:
    memory = FailureMemory()
    for result in _recent_experiment_results(root, max_results=max_results):
        if not _is_valid_failure_memory_result(result):
            continue
        reasons = [reason for reason in build_rejection_diagnostics(result) if reason not in IGNORED_REASONS]
        if not reasons:
            continue
        strategy_id = str(result.get("strategy_id") or "")
        parameter_signature = execution_parameter_signature(result.get("parameters") or {})
        for key in _result_keys(result):
            pattern = memory.patterns.setdefault(key, FailurePattern(key=key))
            pattern.reason_counts.update(reasons)
            pattern.parameter_signatures.add(parameter_signature)
            if strategy_id:
                pattern.strategy_ids.add(strategy_id)
    return memory


def _recent_experiment_results(root: Path, max_results: int) -> list[dict[str, Any]]:
    path = root / "registry" / "experiments.jsonl"
    return tail_jsonl(path, max_results)


def _result_keys(result: dict[str, Any]) -> list[str]:
    family = str(result.get("family") or "")
    short_name = str(result.get("short_name") or _short_name_from_strategy_id(str(result.get("strategy_id") or "")))
    builder = str(result.get("builder") or "")
    keys = []
    if family:
        keys.append(f"family:{family}")
    if family and short_name:
        keys.append(f"family_short:{family}:{short_name}")
    if builder:
        keys.append(f"builder:{builder}")
    return keys


def _spec_keys(spec: Any) -> list[str]:
    family = str(getattr(spec, "family", "") or "")
    short_name = str(getattr(spec, "short_name", "") or "")
    builder = str(getattr(spec, "builder", "") or "")
    keys = []
    if family:
        keys.append(f"family:{family}")
    if family and short_name:
        keys.append(f"family_short:{family}:{short_name}")
    if builder:
        keys.append(f"builder:{builder}")
    return keys


def execution_relevant_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in parameters.items()
        if str(key) not in NON_EXECUTABLE_PARAMETER_KEYS
    }


def execution_parameter_signature(parameters: dict[str, Any]) -> str:
    return json.dumps(_normalize(execution_relevant_parameters(parameters)), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _repaired_score(pattern: FailurePattern, spec: Any) -> int:
    total = 0
    parameter_changed = _has_executable_parameter_change(pattern, spec)
    for reason, count in pattern.reason_counts.items():
        weight = REASON_WEIGHTS.get(reason, 1)
        if parameter_changed:
            weight = max(1, weight // 3)
        total += weight * count
    return total


def _has_executable_parameter_change(pattern: FailurePattern, spec: Any) -> bool:
    if not pattern.parameter_signatures:
        return False
    signature = execution_parameter_signature(getattr(spec, "parameters", {}) or {})
    return signature not in pattern.parameter_signatures


def _is_valid_failure_memory_result(result: dict[str, Any]) -> bool:
    if not str(result.get("strategy_id") or "").strip():
        return False
    if not str(result.get("family") or "").strip():
        return False
    if not (str(result.get("builder") or "").strip() or str(result.get("short_name") or "").strip()):
        return False
    if not str(result.get("tier") or "").strip():
        return False

    split_metrics = result.get("split_metrics")
    if not isinstance(split_metrics, dict):
        return False
    validation = split_metrics.get("validation")
    unseen = split_metrics.get("unseen")
    if not isinstance(validation, dict) or not isinstance(unseen, dict):
        return False
    if not _has_keys(validation, {"cagr"}):
        return False
    if not _has_keys(unseen, {"cagr", "max_drawdown", "trade_count"}):
        return False

    data_manifest = result.get("data_manifest")
    if not isinstance(data_manifest, dict) or not _has_keys(data_manifest, {"source", "years"}):
        return False

    cost_stress = result.get("cost_stress")
    if not isinstance(cost_stress, dict) or "survives_double_cost" not in cost_stress:
        return False
    return True


def _has_keys(item: dict[str, Any], keys: set[str]) -> bool:
    return all(key in item and item[key] is not None for key in keys)


def _normalize(value: Any, key_path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            normalized_key: _normalize(item, (*key_path, normalized_key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if (normalized_key := str(key).strip().lower())
        }
    if isinstance(value, list):
        return [_normalize(item, key_path) for item in value]
    if isinstance(value, tuple):
        return _normalize(list(value), key_path)
    return value


def _scope_weight(key: str) -> int:
    if key.startswith("family_short:"):
        return 3
    if key.startswith("builder:"):
        return 3
    return 1


def _short_name_from_strategy_id(strategy_id: str) -> str:
    marker = "_1D_"
    if marker not in strategy_id:
        return ""
    tail = strategy_id.split(marker, 1)[1]
    parts = tail.split("_")
    if len(parts) <= 2:
        return tail
    return "_".join(parts[:-2])
