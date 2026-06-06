from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from research_lab.config import REAL_EOD_DATA_SOURCES
from research_lab.queue_dedupe import candidate_fingerprint


ACCEPTED_TIERS = {"A", "B"}

CATEGORY_ORDER = [
    "synthetic/fallback data",
    "risk/drawdown",
    "walk-forward robustness",
    "too few trades",
    "cost stress failure",
    "unseen return weakness",
    "validation return weakness",
    "duplicate or near-duplicate hypothesis",
    "promotion gate failure",
]

CATEGORY_WEIGHTS = {
    "risk/drawdown": 8,
    "walk-forward robustness": 7,
    "too few trades": 6,
    "cost stress failure": 6,
    "unseen return weakness": 5,
    "validation return weakness": 4,
    "duplicate or near-duplicate hypothesis": 4,
    "synthetic/fallback data": 3,
    "promotion gate failure": 2,
}


@dataclass(frozen=True)
class BlockerSummary:
    category: str
    signal_count: int
    strategy_count: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidatePenalty:
    pattern_key: str
    score: int
    categories: tuple[str, ...] = ()
    reason_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class CandidateDirection:
    name: str
    priority: int
    rationale: str
    required_features: tuple[str, ...] = ()


@dataclass
class FailureMemory:
    recent_result_count: int = 0
    blocker_counts: Counter[str] = field(default_factory=Counter)
    strategies_by_blocker: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    reason_counts: Counter[str] = field(default_factory=Counter)
    pattern_failures: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    duplicate_patterns: tuple[dict[str, Any], ...] = ()

    def blocker_summaries(self) -> tuple[BlockerSummary, ...]:
        summaries = []
        for category in _ordered_categories(self.blocker_counts):
            summaries.append(
                BlockerSummary(
                    category=category,
                    signal_count=self.blocker_counts[category],
                    strategy_count=len(self.strategies_by_blocker.get(category, set())),
                    reasons=tuple(reason for reason, _count in self.reason_counts.most_common() if classify_rejection_reason(reason) == category),
                )
            )
        return tuple(summaries)


@dataclass(frozen=True)
class ResearchGuidance:
    dominant_blocker_category: str
    blocker_mix: dict[str, int]
    blocker_summaries: tuple[BlockerSummary, ...] = ()
    prioritized_next_directions: tuple[CandidateDirection, ...] = ()
    deprioritized_candidate_types: tuple[CandidatePenalty, ...] = ()
    data_quality_limited: bool = False
    data_quality_limitations: tuple[str, ...] = ()
    promotion_blocked: bool = False
    confidence: str = "insufficient"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dominant_blocker_category": self.dominant_blocker_category,
            "blocker_mix": dict(self.blocker_mix),
            "blocker_summaries": [summary.__dict__ for summary in self.blocker_summaries],
            "prioritized_next_directions": [direction.__dict__ for direction in self.prioritized_next_directions],
            "deprioritized_candidate_types": [
                {
                    "pattern_key": penalty.pattern_key,
                    "score": penalty.score,
                    "categories": list(penalty.categories),
                    "reason_counts": dict(penalty.reason_counts),
                }
                for penalty in self.deprioritized_candidate_types
            ],
            "data_quality_limited": self.data_quality_limited,
            "data_quality_limitations": list(self.data_quality_limitations),
            "promotion_blocked": self.promotion_blocked,
            "confidence": self.confidence,
        }


def classify_rejection_reason(reason: str) -> str:
    text = " ".join(str(reason or "").lower().split())
    if "drawdown" in text:
        return "risk/drawdown"
    if "cost stress" in text or "double transaction-cost" in text:
        return "cost stress failure"
    if "walk-forward" in text or "rolling oos" in text:
        return "walk-forward robustness"
    if "too few" in text and "trade" in text:
        return "too few trades"
    if "synthetic" in text or "fallback" in text or "missing required provider" in text or "insufficient real data history" in text:
        return "synthetic/fallback data"
    if "duplicate" in text or "near-duplicate" in text:
        return "duplicate or near-duplicate hypothesis"
    if "validation return" in text:
        return "validation return weakness"
    if "unseen return" in text or "negative unseen" in text:
        return "unseen return weakness"
    if "promotion gate" in text or "no accepted tier" in text:
        return "promotion gate failure"
    return "promotion gate failure"


def summarize_recent_failures(source: Path | Iterable[dict[str, Any]], max_results: int = 200) -> FailureMemory:
    results = _load_recent_results(source, max_results=max_results)
    memory = FailureMemory(recent_result_count=len(results))
    duplicate_inputs: list[dict[str, Any]] = []
    for result in results:
        reasons = result_rejection_reasons(result)
        if not reasons:
            continue
        strategy_id = str(result.get("strategy_id") or "unknown")
        duplicate_inputs.append(_candidate_like_payload(result))
        for reason in reasons:
            category = classify_rejection_reason(reason)
            memory.blocker_counts[category] += 1
            memory.strategies_by_blocker[category].add(strategy_id)
            memory.reason_counts[reason] += 1
            for key in _result_pattern_keys(result):
                memory.pattern_failures[key][reason] += 1
    memory.duplicate_patterns = tuple(detect_duplicate_candidate_patterns(duplicate_inputs))
    return memory


def build_research_guidance(memory_or_results: FailureMemory | Path | Iterable[dict[str, Any]]) -> ResearchGuidance:
    memory = memory_or_results if isinstance(memory_or_results, FailureMemory) else summarize_recent_failures(memory_or_results)
    blocker_mix = {category: memory.blocker_counts[category] for category in _ordered_categories(memory.blocker_counts)}
    dominant = next(iter(blocker_mix), "inconclusive")
    penalties = _candidate_penalties(memory)
    data_quality_limited = memory.blocker_counts.get("synthetic/fallback data", 0) > 0
    limitations = ("synthetic/fallback data present; do not promote affected candidates",) if data_quality_limited else ()
    return ResearchGuidance(
        dominant_blocker_category=dominant,
        blocker_mix=blocker_mix,
        blocker_summaries=memory.blocker_summaries(),
        prioritized_next_directions=_candidate_directions(dominant, data_quality_limited),
        deprioritized_candidate_types=penalties,
        data_quality_limited=data_quality_limited,
        data_quality_limitations=limitations,
        promotion_blocked=data_quality_limited,
        confidence="sufficient" if memory.blocker_counts else "insufficient",
    )


def score_candidate_direction(spec: Any, guidance: ResearchGuidance) -> int:
    score = 0
    spec_keys = set(_spec_pattern_keys(spec))
    for penalty in guidance.deprioritized_candidate_types:
        if penalty.pattern_key not in spec_keys:
            continue
        penalty_score = penalty.score * _pattern_scope_weight(penalty.pattern_key)
        categories = set(penalty.categories)
        if any(_spec_repairs_category(spec, category) for category in categories):
            penalty_score = max(1, penalty_score // 3)
        score += penalty_score

    dominant = guidance.dominant_blocker_category
    if dominant != "inconclusive":
        if _spec_repairs_category(spec, dominant):
            score -= CATEGORY_WEIGHTS.get(dominant, 1) * 2
        elif dominant in {"risk/drawdown", "walk-forward robustness", "too few trades", "cost stress failure"}:
            score += CATEGORY_WEIGHTS.get(dominant, 1)

    if _looks_duplicate_without_material_change(spec):
        score += CATEGORY_WEIGHTS["duplicate or near-duplicate hypothesis"]
    return max(score, 0)


def detect_duplicate_candidate_patterns(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for candidate in candidates:
        try:
            counts[candidate_fingerprint(candidate)] += 1
        except ValueError:
            continue
    patterns = [
        {"fingerprint": fingerprint, "count": count, "duplicate_count": count - 1}
        for fingerprint, count in counts.items()
        if count > 1
    ]
    return sorted(patterns, key=lambda item: (-int(item["count"]), str(item["fingerprint"])))


def result_rejection_reasons(result: dict[str, Any]) -> list[str]:
    if result.get("tier") in ACCEPTED_TIERS:
        return []
    explicit = result.get("rejection_reasons")
    if isinstance(explicit, list) and explicit:
        return [str(reason) for reason in explicit if str(reason).strip()]

    split = result.get("split_metrics", {})
    validation = split.get("validation", {})
    unseen = split.get("unseen", {})
    data_manifest = result.get("data_manifest", {})
    walk_forward = result.get("walk_forward")
    family = str(result.get("family") or "")
    reasons = []

    if _safe_float(validation.get("cagr"), 0.0) <= 0:
        reasons.append("validation return below threshold")
    if _safe_float(unseen.get("cagr"), 0.0) <= 0:
        reasons.append("unseen return below threshold")
    if _safe_float(unseen.get("max_drawdown"), 0.0) < -0.15:
        reasons.append("max drawdown too deep")
    if family in {"SWING", "INTRADAY"} and _safe_int(unseen.get("trade_count"), 0) < 100:
        reasons.append("too few unseen trades")
    cost_stress = result.get("cost_stress", {})
    if not bool(cost_stress.get("survives_double_cost", True)):
        reasons.append("failed cost stress")
    if isinstance(walk_forward, dict) and _walk_forward_is_insufficient(walk_forward):
        reasons.append("insufficient walk-forward robustness")
    if data_manifest.get("fallback_reason") or result.get("fallback_reason") or result.get("missing_symbols") or data_manifest.get("missing_symbols"):
        reasons.append("missing required provider data")
    source = str(data_manifest.get("source") or result.get("data_source") or "")
    if bool(data_manifest.get("fallback_used") or result.get("fallback_used")) or source not in REAL_EOD_DATA_SOURCES:
        reasons.append("synthetic/fallback data used")
    if _insufficient_history(family, _safe_float(data_manifest.get("years", result.get("history_length")), 0.0)):
        reasons.append("insufficient real data history")
    duplicate_reasons = result.get("duplicate_reasons")
    if isinstance(duplicate_reasons, list):
        reasons.extend(str(reason) for reason in duplicate_reasons if str(reason).strip())
    if result.get("tier") not in {"Rejected", *ACCEPTED_TIERS}:
        reasons.append("failed promotion gate")
        reasons.append("no accepted tier reached")
    return _dedupe_preserving_order(reasons)


def _load_recent_results(source: Path | Iterable[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    if isinstance(source, Path):
        path = source / "registry" / "experiments.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines()[-max_results:]:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows
    return [item for item in list(source)[-max_results:] if isinstance(item, dict)]


def _candidate_penalties(memory: FailureMemory) -> tuple[CandidatePenalty, ...]:
    penalties = []
    for key, reason_counts in memory.pattern_failures.items():
        category_counts: Counter[str] = Counter()
        for reason, count in reason_counts.items():
            category_counts[classify_rejection_reason(reason)] += count
        score = sum(CATEGORY_WEIGHTS.get(category, 1) * count for category, count in category_counts.items())
        if score <= 0:
            continue
        penalties.append(
            CandidatePenalty(
                pattern_key=key,
                score=score,
                categories=tuple(_ordered_categories(category_counts)),
                reason_counts=tuple(sorted(reason_counts.items())),
            )
        )
    return tuple(sorted(penalties, key=lambda item: (-item.score, item.pattern_key)))


def _candidate_directions(dominant: str, data_quality_limited: bool) -> tuple[CandidateDirection, ...]:
    directions = []
    if data_quality_limited:
        directions.append(
            CandidateDirection(
                name="data_quality_repair",
                priority=0,
                rationale="remove synthetic/fallback data limitations before interpreting promotion readiness",
                required_features=("real_provider_data", "no_fallback", "adequate_history"),
            )
        )
    direction_map = {
        "risk/drawdown": CandidateDirection(
            name="risk_overlay_repair",
            priority=1,
            rationale="prioritize volatility targeting, exposure caps, trend/cash filters, circuit breakers, or defensive allocation",
            required_features=("risk_overlay_changed", "drawdown_control"),
        ),
        "walk-forward robustness": CandidateDirection(
            name="robustness_simplification",
            priority=1,
            rationale="prioritize simpler parameterizations and parameter-neighborhood stability",
            required_features=("walk_forward_repair", "parameter_stability"),
        ),
        "too few trades": CandidateDirection(
            name="trade_sample_repair",
            priority=1,
            rationale="avoid promotion attempts until unseen trade-count adequacy is plausible",
            required_features=("trade_count_repair", "min_unseen_trades_target"),
        ),
        "cost stress failure": CandidateDirection(
            name="cost_robustness_repair",
            priority=1,
            rationale="prioritize lower turnover and strategies that survive double-cost stress",
            required_features=("cost_stress_repair", "turnover_repair"),
        ),
        "duplicate or near-duplicate hypothesis": CandidateDirection(
            name="hypothesis_deduplication",
            priority=1,
            rationale="deprioritize repeated templates unless the executable design materially changes",
            required_features=("material_design_change",),
        ),
        "unseen return weakness": CandidateDirection(
            name="positive_unseen_edge_repair",
            priority=1,
            rationale="prioritize candidates with a concrete edge expected to improve unseen CAGR",
            required_features=("edge_repair",),
        ),
        "validation return weakness": CandidateDirection(
            name="validation_edge_repair",
            priority=1,
            rationale="prioritize candidates with positive validation performance before deeper promotion work",
            required_features=("validation_repair",),
        ),
    }
    if dominant in direction_map:
        directions.append(direction_map[dominant])
    if not directions:
        directions.append(
            CandidateDirection(
                name="diagnostic_collection",
                priority=9,
                rationale="collect more rejected or non-accepted diagnostics before changing research direction",
                required_features=("more_recent_results",),
            )
        )
    return tuple(sorted(directions, key=lambda item: (item.priority, item.name)))


def _ordered_categories(counts: Counter[str] | dict[str, int]) -> list[str]:
    priority = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    return sorted(counts, key=lambda category: (-int(counts[category]), priority.get(category, len(priority)), category))


def _result_pattern_keys(result: dict[str, Any]) -> list[str]:
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


def _spec_pattern_keys(spec: Any) -> list[str]:
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


def _spec_repairs_category(spec: Any, category: str) -> bool:
    parameters = getattr(spec, "parameters", {}) or {}
    text = _spec_text(spec)
    if category == "risk/drawdown":
        return bool(parameters.get("risk_overlay_changed") or parameters.get("source_risk_overlay_changed")) or any(
            term in text for term in ("vol target", "volatility", "exposure cap", "defensive", "cash", "circuit", "drawdown")
        )
    if category == "walk-forward robustness":
        return bool(parameters.get("walk_forward_repair") or parameters.get("source_walk_forward_repair")) or any(
            term in text for term in ("stable", "stability", "simpler", "smooth")
        )
    if category == "too few trades":
        target = max(
            _safe_int(parameters.get("min_unseen_trades_target"), 0),
            _safe_int(parameters.get("source_min_unseen_trades_target"), 0),
        )
        return bool(parameters.get("trade_count_repair") or parameters.get("source_trade_count_repair")) or target >= 100
    if category == "cost stress failure":
        return bool(
            parameters.get("cost_stress_repair")
            or parameters.get("source_cost_stress_repair")
            or parameters.get("turnover_repair")
            or parameters.get("source_turnover_repair")
        )
    if category == "duplicate or near-duplicate hypothesis":
        return bool(parameters.get("material_design_change") or parameters.get("source_material_design_change"))
    if category == "unseen return weakness":
        return bool(parameters.get("edge_repair") or parameters.get("source_edge_repair"))
    if category == "validation return weakness":
        return bool(parameters.get("validation_repair") or parameters.get("source_validation_repair"))
    return False


def _looks_duplicate_without_material_change(spec: Any) -> bool:
    parameters = getattr(spec, "parameters", {}) or {}
    return bool(parameters.get("source_duplicate_hint")) and not _spec_repairs_category(spec, "duplicate or near-duplicate hypothesis")


def _spec_text(spec: Any) -> str:
    return " ".join(
        [
            str(getattr(spec, "family", "")),
            str(getattr(spec, "short_name", "")),
            str(getattr(spec, "builder", "")),
            str(getattr(spec, "hypothesis", "")),
            str(getattr(spec, "rules", "")),
            json.dumps(getattr(spec, "parameters", {}) or {}, sort_keys=True, default=str),
        ]
    ).lower()


def _pattern_scope_weight(key: str) -> int:
    if key.startswith("family_short:") or key.startswith("builder:"):
        return 3
    return 1


def _candidate_like_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": result.get("family"),
        "asset_class": result.get("asset_class"),
        "timeframe": result.get("timeframe"),
        "template": result.get("builder") or result.get("short_name"),
        "title": result.get("short_name") or result.get("strategy_id"),
        "parameters": result.get("parameters") or {},
        "rules": result.get("rules"),
    }


def _walk_forward_is_insufficient(walk_forward: dict[str, Any]) -> bool:
    return (
        walk_forward.get("method") != "true_rolling_oos"
        or walk_forward.get("status") != "ok"
        or _safe_float(walk_forward.get("pass_rate"), 0.0) < 0.67
        or _safe_int(walk_forward.get("window_count"), 0) < 3
        or _safe_float(walk_forward.get("median_test_cagr"), 0.0) <= 0
        or _safe_float(walk_forward.get("worst_test_drawdown"), -1.0) < -0.20
    )


def _insufficient_history(family: str, years: float) -> bool:
    if family in {"LONGTERM", "ROTATION"}:
        return years < 10.0
    if family == "SWING":
        return years < 3.0
    return False


def _short_name_from_strategy_id(strategy_id: str) -> str:
    marker = "_1D_"
    if marker not in strategy_id:
        return ""
    tail = strategy_id.split(marker, 1)[1]
    parts = tail.split("_")
    if len(parts) <= 2:
        return tail
    return "_".join(parts[:-2])


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen = set()
    retained = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        retained.append(item)
    return retained


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
