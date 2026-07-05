from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_lab.failure_memory import build_failure_memory, execution_relevant_parameters
from research_lab.hermes.schema import validate_hypothesis
from research_lab.jsonl import iter_jsonl, tail_jsonl
from research_lab.queue_dedupe import candidate_fingerprint
from research_lab.research_orchestrator import build_research_guidance, score_candidate_direction, summarize_recent_failures
from research_lab.risk_management import has_strong_rotation_risk_overlay


MAX_QUEUE_DEDUPE_SKIP_DETAILS = 50
DAILY_EXPERIMENT_BUDGET = 18
DAILY_RECENT_EXPERIMENT_WINDOW = 50
RECOVERY_ALLOWED_BUILDERS = {
    "long_term_vol_target",
    "long_term_vol_target_cap",
    "swing_rsi_pullback",
    "swing_trend_filtered_pullback",
    "defensive_asset_rotation",
}
RECOVERY_ALLOWED_FAMILIES = {"LONGTERM", "SWING", "ROTATION"}
RECOVERY_BUILDER_FAMILIES = {
    "long_term_vol_target": "LONGTERM",
    "long_term_vol_target_cap": "LONGTERM",
    "swing_rsi_pullback": "SWING",
    "swing_trend_filtered_pullback": "SWING",
    "defensive_asset_rotation": "ROTATION",
}
RECOVERY_EXECUTABLE_PARAMETER_SCHEMAS = {
    "long_term_vol_target": ("symbol", "sma", "vol_window", "target_vol"),
    "long_term_vol_target_cap": ("symbol", "sma", "vol_window", "target_vol", "max_weight"),
    "swing_rsi_pullback": ("symbol", "trend_sma", "rsi_entry", "rsi_exit"),
    "swing_trend_filtered_pullback": (
        "symbol",
        "fast_sma",
        "slow_sma",
        "rsi_entry",
        "rsi_exit",
        "atr_stop",
        "max_exposure",
    ),
    "defensive_asset_rotation": (
        "risk_assets",
        "defensive_assets",
        "lookback",
        "top_n",
        "risk_symbol",
        "risk_sma",
    ),
}
RECOVERY_PARAMETER_DEFAULTS = {
    "long_term_vol_target": {"symbol": "SPY", "sma": 150, "vol_window": 63, "target_vol": 0.12},
    "long_term_vol_target_cap": {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.10, "max_weight": 0.75},
    "swing_rsi_pullback": {"symbol": "SPY", "trend_sma": 100, "rsi_entry": 35, "rsi_exit": 55},
    "swing_trend_filtered_pullback": {
        "symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 40,
        "rsi_exit": 58, "atr_stop": 2.0, "max_exposure": 1.0,
    },
    "defensive_asset_rotation": {
        "risk_assets": ["SPY", "QQQ"], "defensive_assets": ["TLT", "GLD"],
        "lookback": 126, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 200,
    },
}
RECOVERY_REQUIRED_PARAMETER_KEYS = {
    "swing_rsi_pullback": ("symbol", "trend_sma", "rsi_entry", "rsi_exit"),
}

# Explicit rows, not a parameter grid. Each tuple is one recovery day.
RECOVERY_EXPERIMENT_ROWS = (
    (
        ("long_term_vol_target_cap", {"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.08, "max_weight": 0.60}),
        ("swing_trend_filtered_pullback", {"symbol": "SPY", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 38, "rsi_exit": 58, "atr_stop": 2.0, "max_exposure": 0.50}),
        ("defensive_asset_rotation", {"risk_assets": ["SPY", "QQQ", "IWM"], "defensive_assets": ["IEF", "TLT", "GLD", "SHY"], "lookback": 126, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 200}),
        ("long_term_vol_target", {"symbol": "IEF", "sma": 150, "vol_window": 42, "target_vol": 0.06}),
    ),
    (
        ("long_term_vol_target_cap", {"symbol": "QQQ", "sma": 250, "vol_window": 84, "target_vol": 0.06, "max_weight": 0.40}),
        ("swing_trend_filtered_pullback", {"symbol": "QQQ", "fast_sma": 100, "slow_sma": 200, "rsi_entry": 32, "rsi_exit": 65, "atr_stop": 3.0, "max_exposure": 0.25}),
        ("defensive_asset_rotation", {"risk_assets": ["SPY", "QQQ", "IWM"], "defensive_assets": ["SHY", "IEF", "TLT", "GLD"], "lookback": 252, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 150}),
        ("swing_rsi_pullback", {"symbol": "IWM", "trend_sma": 200, "rsi_entry": 32, "rsi_exit": 65}),
    ),
    (
        ("long_term_vol_target_cap", {"symbol": "IWM", "sma": 150, "vol_window": 42, "target_vol": 0.10, "max_weight": 0.60}),
        ("swing_trend_filtered_pullback", {"symbol": "IWM", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 32, "rsi_exit": 58, "atr_stop": 3.0, "max_exposure": 0.25}),
        ("defensive_asset_rotation", {"risk_assets": ["IWM", "SPY", "QQQ"], "defensive_assets": ["IEF", "GLD", "TLT", "SHY"], "lookback": 126, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 150}),
        ("long_term_vol_target", {"symbol": "GLD", "sma": 250, "vol_window": 63, "target_vol": 0.08}),
    ),
    (
        ("long_term_vol_target_cap", {"symbol": "GLD", "sma": 200, "vol_window": 84, "target_vol": 0.08, "max_weight": 0.75}),
        ("swing_trend_filtered_pullback", {"symbol": "SPY", "fast_sma": 100, "slow_sma": 200, "rsi_entry": 32, "rsi_exit": 65, "atr_stop": 2.0, "max_exposure": 0.25}),
        ("defensive_asset_rotation", {"risk_assets": ["QQQ", "SPY", "IWM"], "defensive_assets": ["TLT", "IEF", "GLD", "SHY"], "lookback": 252, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 200}),
        ("swing_rsi_pullback", {"symbol": "SPY", "trend_sma": 150, "rsi_entry": 38, "rsi_exit": 58}),
    ),
    (
        ("long_term_vol_target_cap", {"symbol": "IEF", "sma": 250, "vol_window": 42, "target_vol": 0.06, "max_weight": 0.40}),
        ("swing_trend_filtered_pullback", {"symbol": "QQQ", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 38, "rsi_exit": 65, "atr_stop": 3.0, "max_exposure": 0.50}),
        ("defensive_asset_rotation", {"risk_assets": ["SPY", "IWM", "QQQ"], "defensive_assets": ["GLD", "IEF", "TLT", "SHY"], "lookback": 126, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 200}),
        ("long_term_vol_target", {"symbol": "SPY", "sma": 250, "vol_window": 84, "target_vol": 0.06}),
    ),
    (
        ("long_term_vol_target_cap", {"symbol": "SPY", "sma": 150, "vol_window": 84, "target_vol": 0.10, "max_weight": 0.40}),
        ("swing_trend_filtered_pullback", {"symbol": "IWM", "fast_sma": 100, "slow_sma": 200, "rsi_entry": 38, "rsi_exit": 58, "atr_stop": 2.0, "max_exposure": 0.50}),
        ("defensive_asset_rotation", {"risk_assets": ["IWM", "QQQ", "SPY"], "defensive_assets": ["SHY", "GLD", "IEF", "TLT"], "lookback": 252, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 150}),
        ("swing_rsi_pullback", {"symbol": "QQQ", "trend_sma": 200, "rsi_entry": 38, "rsi_exit": 65}),
    ),
    (
        ("long_term_vol_target_cap", {"symbol": "QQQ", "sma": 200, "vol_window": 42, "target_vol": 0.08, "max_weight": 0.60}),
        ("swing_trend_filtered_pullback", {"symbol": "SPY", "fast_sma": 50, "slow_sma": 150, "rsi_entry": 32, "rsi_exit": 58, "atr_stop": 3.0, "max_exposure": 0.25}),
        ("defensive_asset_rotation", {"risk_assets": ["QQQ", "IWM", "SPY"], "defensive_assets": ["IEF", "SHY", "GLD", "TLT"], "lookback": 126, "top_n": 1, "risk_symbol": "SPY", "risk_sma": 150}),
        ("long_term_vol_target", {"symbol": "IWM", "sma": 200, "vol_window": 63, "target_vol": 0.10}),
    ),
)


@dataclass(frozen=True)
class StrategySpec:
    family: str
    asset_class: str
    timeframe: str
    short_name: str
    hypothesis: str
    parameters: dict
    rules: str
    builder: str

    def strategy_id(self, sequence: int) -> str:
        stamp = date.today().strftime("%Y%m%d")
        return f"{self.family}_{self.asset_class}_{self.timeframe}_{self.short_name}_{stamp}_{sequence:03d}"


@dataclass(frozen=True)
class CandidateProposal:
    spec: StrategySpec
    source: str
    order: int
    source_key: str
    hypothesis_id: str = ""


def baseline_strategies() -> list[StrategySpec]:
    return [
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_FILTER",
            hypothesis="A long-only equity allocation with a 200-day trend filter should reduce drawdown versus always-on exposure.",
            parameters={"symbol": "SPY", "sma": 200},
            rules="Hold SPY when close is above its 200-day SMA; otherwise hold cash.",
            builder="long_term_trend_filter",
        ),
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="DUAL_MOMENTUM",
            hypothesis="Monthly top-N momentum rotation across equity, bond, gold, and growth ETFs may improve risk-adjusted return.",
            parameters={"symbols": ["SPY", "QQQ", "TLT", "GLD"], "lookback": 126, "top_n": 2},
            rules="At month end rank by 126-day momentum and hold the top two assets equally for the next month.",
            builder="active_momentum_rotation",
        ),
        StrategySpec(
            family="SWING",
            asset_class="ETF",
            timeframe="1D",
            short_name="RSI_PULLBACK",
            hypothesis="Buying oversold pullbacks only inside a rising long-term trend may produce positive expectancy with bounded exposure.",
            parameters={"symbol": "SPY", "trend_sma": 100, "rsi_entry": 35, "rsi_exit": 55},
            rules="Enter long when SPY is above SMA100 and RSI14 is below 35; exit when RSI14 exceeds 55 or price closes below SMA100.",
            builder="swing_rsi_pullback",
        ),
        StrategySpec(
            family="INTRADAY",
            asset_class="BTCUSDT",
            timeframe="15M",
            short_name="VWAP_RSI_RECLAIM",
            hypothesis="A VWAP reclaim after weak RSI can capture short intraday continuation if fills survive realistic costs.",
            parameters={"symbol": "BTCUSDT", "rsi_reclaim": 50, "rsi_washout": 45},
            rules="Enter when close reclaims session VWAP and RSI14 crosses above 50 after sub-45 weakness; exit on VWAP loss or session end.",
            builder="intraday_vwap_rsi_reclaim",
        ),
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_STRICT_CASH",
            hypothesis="A stricter equity trend filter may reduce long-history ETF drawdowns by requiring both price and intermediate trend confirmation.",
            parameters={"symbol": "SPY", "sma": 200, "confirmation_sma": 50},
            rules="Hold SPY only when close is above SMA200 and SMA50 is above SMA200; otherwise hold cash.",
            builder="long_term_strict_cash_filter",
        ),
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_VOL_CAP",
            hypothesis="A capped volatility-targeted SPY trend sleeve may reduce drawdown without changing existing promotion gates.",
            parameters={"symbol": "SPY", "sma": 200, "vol_window": 63, "target_vol": 0.10, "max_weight": 0.75},
            rules="Hold SPY above SMA200 with realized-volatility targeting capped at 75% exposure; otherwise hold cash.",
            builder="long_term_vol_target_cap",
        ),
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="DUAL_MOMENTUM_DD_CB",
            hypothesis="A drawdown circuit breaker on dual momentum may reduce crisis-period losses by forcing cash after deep SPY drawdowns.",
            parameters={
                "symbols": ["SPY", "QQQ", "TLT", "GLD"],
                "lookback": 126,
                "top_n": 2,
                "risk_symbol": "SPY",
                "drawdown_threshold": -0.12,
                "recovery_sma": 200,
            },
            rules="Run monthly top-2 dual momentum, but move fully to cash once SPY is down 12% from its peak until SPY recovers above SMA200.",
            builder="rotation_momentum_circuit_breaker",
        ),
        StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="DEFENSIVE_ROTATION",
            hypothesis="A simple defensive rotation into TLT, GLD, or cash during equity risk-off periods may reduce ETF drawdowns.",
            parameters={
                "risk_assets": ["SPY", "QQQ"],
                "defensive_assets": ["TLT", "GLD"],
                "lookback": 126,
                "top_n": 1,
                "risk_symbol": "SPY",
                "risk_sma": 200,
            },
            rules="When SPY is above SMA200, hold the top risk asset by 126-day momentum; otherwise hold the stronger of TLT or GLD if its momentum is positive, else cash.",
            builder="defensive_asset_rotation",
        ),
    ]


def select_daily_experiment_candidates(
    root: Path,
    *,
    budget: int = DAILY_EXPERIMENT_BUDGET,
    recent_window: int = DAILY_RECENT_EXPERIMENT_WINDOW,
    recovery_day: int,
) -> dict[str, Any]:
    budget = max(int(budget), 0)
    recent_window = max(int(recent_window), 0)
    proposals, terminal_counts = _daily_candidate_proposals(root, recovery_day)
    recent_fingerprints = _recent_executable_fingerprints(root, max_rows=recent_window)
    diagnostics = {
        "budget": budget,
        "recent_window": recent_window,
        "recovery_day": recovery_day,
        "queue_rows_consumed": False,
        "proposed": len(proposals) + sum(terminal_counts.values()),
        "family_filtered": terminal_counts["family_filtered"],
        "source_filtered": terminal_counts["source_filtered"],
        "invalid_filtered": terminal_counts["invalid_filtered"],
        "recent_duplicate_skipped": 0,
        "in_batch_duplicate_skipped": 0,
        "budget_skipped": 0,
        "selected": 0,
        "selected_fingerprints": [],
        "selected_sources": [],
        "retained_count": 0,
        "skipped_count": 0,
        "reasons": {},
        "skipped": [],
    }
    selected: list[StrategySpec] = []
    seen_batch: set[str] = set()
    for proposal in proposals:
        spec = proposal.spec
        if not _is_allowed_recovery_spec(spec):
            diagnostics["family_filtered"] += 1
            continue
        fingerprint = strategy_execution_fingerprint(spec)
        if fingerprint in recent_fingerprints:
            diagnostics["recent_duplicate_skipped"] += 1
            _record_daily_selector_skip(diagnostics, "recent_executable_duplicate", proposal, fingerprint)
            continue
        if fingerprint in seen_batch:
            diagnostics["in_batch_duplicate_skipped"] += 1
            _record_daily_selector_skip(diagnostics, "effective_parameter_duplicate", proposal, fingerprint)
            continue
        seen_batch.add(fingerprint)
        if len(selected) >= budget:
            diagnostics["budget_skipped"] += 1
            continue
        selected.append(spec)
        diagnostics["selected_fingerprints"].append(fingerprint)
        diagnostics["selected_sources"].append(proposal.source)
    diagnostics["selected"] = len(selected)
    diagnostics["budget_selected"] = len(selected)  # compatibility with older report readers
    diagnostics["retained_count"] = len(selected)
    return {"specs": selected, "diagnostics": diagnostics}


def _record_daily_selector_skip(
    diagnostics: dict[str, Any], reason: str, proposal: CandidateProposal, fingerprint: str
) -> None:
    diagnostics["skipped_count"] += 1
    diagnostics["reasons"][reason] = diagnostics["reasons"].get(reason, 0) + 1
    if len(diagnostics["skipped"]) < MAX_QUEUE_DEDUPE_SKIP_DETAILS:
        diagnostics["skipped"].append(
            {
                "reason_code": reason,
                "hypothesis_id": proposal.hypothesis_id,
                "source": proposal.source,
                "source_key": proposal.source_key,
                "fingerprint": fingerprint,
            }
        )


def recovery_manifest_specs(recovery_day: int) -> list[StrategySpec]:
    if recovery_day < 1 or recovery_day > len(RECOVERY_EXPERIMENT_ROWS):
        return []
    specs = []
    for index, (builder, parameters) in enumerate(RECOVERY_EXPERIMENT_ROWS[recovery_day - 1], start=1):
        family = RECOVERY_BUILDER_FAMILIES[builder]
        specs.append(
            StrategySpec(
                family=family,
                asset_class="ETF",
                timeframe="1D",
                short_name=f"RECOVERY_D{recovery_day}_{index}",
                hypothesis=f"Deterministic bounded recovery experiment day {recovery_day}, row {index}.",
                parameters=dict(parameters),
                rules=f"Execute existing builder {builder} with the explicit recovery manifest parameters.",
                builder=builder,
            )
        )
    fingerprints = [strategy_execution_fingerprint(spec) for spec in specs]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError(f"Recovery manifest day {recovery_day} contains duplicate executable fingerprints")
    return specs


def queued_hypothesis_strategies(root: Path, limit: int = 4) -> list[StrategySpec]:
    return select_queued_hypothesis_candidates(root, limit=limit)["specs"]


def select_queued_hypothesis_candidates(root: Path, limit: int = 4) -> dict[str, Any]:
    queue_path = root / "registry" / "hypothesis_queue.jsonl"
    if not queue_path.exists():
        return {
            "specs": [],
            "diagnostics": {
                "input_count": 0,
                "retained_count": 0,
                "selected_count": 0,
                "skipped_count": 0,
                "non_executable_count": 0,
                "risk_filtered_count": 0,
                "reasons": {},
                "skipped": [],
            },
        }
    candidates = []
    extreme_rotation_drawdown = _has_recent_extreme_rotation_drawdown(root)
    diagnostics = {
        "input_count": 0,
        "retained_count": 0,
        "selected_count": 0,
        "skipped_count": 0,
        "non_executable_count": 0,
        "risk_filtered_count": 0,
        "reasons": {},
        "skipped": [],
    }
    seen_semantic_executable: dict[tuple[str, str], str] = {}
    seen_source_executable: dict[tuple[str, str], str] = {}
    seen_executable: dict[str, str] = {}
    for order, item in enumerate(iter_jsonl(queue_path)):
        diagnostics["input_count"] += 1
        key = str(item.get("source_key") or item.get("hypothesis_id") or f"{item.get('family', '')}:{item.get('ticker', '')}:{item.get('title', '')}")
        hypothesis_id = str(item.get("hypothesis_id") or "")
        family = str(item.get("family") or "")
        spec = _spec_from_hypothesis(item)
        if spec is None:
            diagnostics["non_executable_count"] += 1
            continue
        if spec.family == "ROTATION" and extreme_rotation_drawdown and not has_strong_rotation_risk_overlay(item):
            diagnostics["risk_filtered_count"] += 1
            continue

        fingerprint = _safe_candidate_fingerprint(item)
        spec_fingerprint = strategy_execution_fingerprint(spec)
        retained_id = hypothesis_id or key
        if fingerprint is not None:
            duplicate_of = seen_semantic_executable.get((fingerprint, spec_fingerprint))
            if duplicate_of is not None:
                _record_queue_skip(diagnostics, "semantic_queue_duplicate", hypothesis_id, family, key, duplicate_of)
                continue
        source_execution_key = (key, spec_fingerprint)
        duplicate_of = seen_source_executable.get(source_execution_key)
        if duplicate_of is not None:
            _record_queue_skip(diagnostics, "source_key_duplicate", hypothesis_id, family, key, duplicate_of)
            continue
        duplicate_of = seen_executable.get(spec_fingerprint)
        if duplicate_of is not None:
            _record_queue_skip(diagnostics, "effective_parameter_duplicate", hypothesis_id, family, key, duplicate_of)
            continue

        if fingerprint is not None:
            seen_semantic_executable[(fingerprint, spec_fingerprint)] = retained_id
        seen_source_executable[source_execution_key] = retained_id
        seen_executable[spec_fingerprint] = retained_id
        candidates.append((order, key, spec))
    guidance = build_research_guidance(summarize_recent_failures(root))
    failure_memory = build_failure_memory(root)
    ranked = sorted(
        candidates,
        key=lambda item: (
            failure_memory.penalty_for_spec(item[2]).score,
            score_candidate_direction(item[2], guidance),
            _conservative_preference_rank(item[2]),
            item[0],
            item[1],
        ),
    )
    diagnostics["retained_count"] = len(ranked)
    specs = [spec for _order, _key, spec in ranked]
    if limit is not None:
        specs = specs[: max(int(limit), 0)]
    diagnostics["selected_count"] = len(specs)
    return {"specs": specs, "diagnostics": diagnostics}


def _daily_candidate_proposals(root: Path, recovery_day: int) -> tuple[list[CandidateProposal], dict[str, int]]:
    proposals: list[CandidateProposal] = []
    for order, spec in enumerate(recovery_manifest_specs(recovery_day)):
        proposals.append(
            CandidateProposal(
                spec=spec,
                source="recovery_manifest",
                order=order,
                source_key=f"recovery:{recovery_day}:{strategy_execution_fingerprint(spec)}",
            )
        )
    proposals.sort(
        key=lambda proposal: (
            strategy_execution_fingerprint(proposal.spec),
            proposal.source_key,
        )
    )
    return proposals, {"family_filtered": 0, "source_filtered": 0, "invalid_filtered": 0}


def next_run_guided_strategies(root: Path, limit: int = 2) -> list[StrategySpec]:
    near_misses = [result for result in reversed(_recent_experiment_results(root)) if _is_near_miss_trend_vol_cap(result)]
    specs: list[StrategySpec] = []
    seen_targets: set[str] = set()
    for result in near_misses:
        target_key = _strategy_family_key(result)
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)
        specs.extend(_trend_vol_cap_conservative_mutations(result))
        if len(specs) >= limit:
            break
    return dedupe_strategy_specs(specs)[: max(int(limit), 0)]


def dedupe_strategy_specs(specs: list[StrategySpec]) -> list[StrategySpec]:
    seen: set[str] = set()
    retained: list[StrategySpec] = []
    for spec in specs:
        fingerprint = strategy_execution_fingerprint(spec)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        retained.append(spec)
    return retained


def strategy_execution_fingerprint(spec: StrategySpec) -> str:
    builder = str(spec.builder).strip().lower()
    if builder not in RECOVERY_EXECUTABLE_PARAMETER_SCHEMAS:
        parameters = execution_relevant_parameters(spec.parameters)
    else:
        parameters = _recovery_executable_parameters(builder, spec.parameters)
    payload = {
        "timeframe": str(spec.timeframe).strip().upper(),
        "builder": builder,
        "parameters": parameters,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def result_execution_fingerprint(result: dict[str, Any]) -> str | None:
    timeframe = str(result.get("timeframe") or "").strip()
    short_name = str(result.get("short_name") or _short_name_from_strategy_id(str(result.get("strategy_id") or ""))).strip()
    builder = str(result.get("builder") or _builder_for_short_name(short_name)).strip()
    if not (timeframe and builder):
        return None
    spec = StrategySpec(
        family=str(result.get("family") or RECOVERY_BUILDER_FAMILIES.get(builder, "")),
        asset_class=str(result.get("asset_class") or ""),
        timeframe=timeframe,
        short_name=short_name,
        hypothesis="",
        parameters=result.get("parameters") or {},
        rules="",
        builder=builder,
    )
    return strategy_execution_fingerprint(spec)


def queued_daily_symbols(root: Path, limit: int = 8) -> list[str]:
    queue_path = root / "registry" / "hypothesis_queue.jsonl"
    if not queue_path.exists():
        return []
    symbols: OrderedDict[str, None] = OrderedDict()
    for item in iter_jsonl(queue_path):
        if str(item.get("family", "")).upper() == "INTRADAY" or str(item.get("timeframe", "")).upper() == "15M":
            continue
        item_symbols = [item.get("ticker")]
        parameters = item.get("parameters")
        if isinstance(parameters, dict):
            item_symbols.extend([parameters.get("symbol"), parameters.get("risk_symbol")])
            for key in ("symbols", "risk_assets", "defensive_assets"):
                value = parameters.get(key)
                if isinstance(value, list):
                    item_symbols.extend(value)
        for value in item_symbols:
            ticker = str(value or "").strip().upper()
            if not ticker:
                continue
            symbols.pop(ticker, None)
            symbols[ticker] = None
            while len(symbols) > max(int(limit), 0):
                symbols.popitem(last=False)
    return list(symbols)


def build_weights(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    builders = {
        "long_term_trend_filter": long_term_trend_filter,
        "long_term_vol_target": long_term_vol_target,
        "long_term_strict_cash_filter": long_term_strict_cash_filter,
        "long_term_vol_target_cap": long_term_vol_target_cap,
        "active_momentum_rotation": active_momentum_rotation,
        "rotation_momentum_drawdown_filter": rotation_momentum_drawdown_filter,
        "rotation_momentum_circuit_breaker": rotation_momentum_circuit_breaker,
        "defensive_asset_rotation": defensive_asset_rotation,
        "swing_rsi_pullback": swing_rsi_pullback,
        "swing_trend_filtered_pullback": swing_trend_filtered_pullback,
        "intraday_vwap_rsi_reclaim": intraday_vwap_rsi_reclaim,
    }
    return builders[spec.builder](spec, daily_panel, intraday)


def long_term_trend_filter(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters["symbol"]
    close = daily_panel[(symbol, "close")]
    sma = close.rolling(spec.parameters["sma"]).mean()
    weights = pd.DataFrame({symbol: (close > sma).astype(float)}, index=close.index)
    return weights


def long_term_vol_target(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "SPY")
    close = daily_panel[(symbol, "close")]
    returns = close.pct_change()
    sma = close.rolling(spec.parameters.get("sma", 150)).mean()
    realized_vol = returns.rolling(spec.parameters.get("vol_window", 63)).std() * np.sqrt(252)
    target_vol = spec.parameters.get("target_vol", 0.12)
    raw_weight = (target_vol / realized_vol).clip(lower=0.0, upper=1.0)
    weight = raw_weight.where(close > sma, 0.0).fillna(0.0)
    return pd.DataFrame({symbol: weight}, index=close.index)


def long_term_strict_cash_filter(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "SPY")
    close = daily_panel[(symbol, "close")]
    slow = close.rolling(spec.parameters.get("sma", 200)).mean()
    confirmation = close.rolling(spec.parameters.get("confirmation_sma", 50)).mean()
    risk_on = (close > slow) & (confirmation > slow)
    return pd.DataFrame({symbol: risk_on.astype(float).fillna(0.0)}, index=close.index)


def long_term_vol_target_cap(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "SPY")
    close = daily_panel[(symbol, "close")]
    returns = close.pct_change()
    sma = close.rolling(spec.parameters.get("sma", 200)).mean()
    realized_vol = returns.rolling(spec.parameters.get("vol_window", 63)).std() * np.sqrt(252)
    target_vol = spec.parameters.get("target_vol", 0.10)
    max_weight = spec.parameters.get("max_weight", 0.75)
    raw_weight = (target_vol / realized_vol).clip(lower=0.0, upper=max_weight)
    weight = raw_weight.where(close > sma, 0.0).fillna(0.0)
    return pd.DataFrame({symbol: weight}, index=close.index)


def active_momentum_rotation(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbols = spec.parameters["symbols"]
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    momentum = close.pct_change(spec.parameters["lookback"])
    month_end_signal = momentum.resample("ME").last()
    ranks = month_end_signal.rank(axis=1, ascending=False, method="first")
    selected = (ranks <= spec.parameters["top_n"]).astype(float) / float(spec.parameters["top_n"])
    weights = selected.reindex(close.index, method="ffill").fillna(0.0)
    return weights


def rotation_momentum_drawdown_filter(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbols = spec.parameters.get("symbols", ["SPY", "QQQ", "TLT", "GLD"])
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    momentum = close.pct_change(spec.parameters.get("lookback", 126))
    month_end_signal = momentum.resample("ME").last()
    ranks = month_end_signal.rank(axis=1, ascending=False, method="first")
    selected = (ranks <= spec.parameters.get("top_n", 2)).astype(float) / float(spec.parameters.get("top_n", 2))
    weights = selected.reindex(close.index, method="ffill").fillna(0.0)
    risk_symbol = spec.parameters.get("risk_symbol", "SPY")
    risk_sma = close[risk_symbol].rolling(spec.parameters.get("risk_sma", 200)).mean()
    risk_on = (close[risk_symbol] > risk_sma).astype(float)
    weights = weights.mul(risk_on, axis=0)
    return weights


def rotation_momentum_circuit_breaker(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbols = spec.parameters.get("symbols", ["SPY", "QQQ", "TLT", "GLD"])
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    momentum = close.pct_change(spec.parameters.get("lookback", 126))
    weights = _monthly_top_n_weights(momentum, close.index, spec.parameters.get("top_n", 2))
    risk_symbol = spec.parameters.get("risk_symbol", "SPY")
    risk_close = close[risk_symbol]
    drawdown = risk_close / risk_close.cummax() - 1.0
    recovery = risk_close.rolling(spec.parameters.get("recovery_sma", 200)).mean()
    threshold = spec.parameters.get("drawdown_threshold", -0.12)
    risk_on = []
    circuit_open = False
    for ts in risk_close.index:
        if circuit_open:
            if risk_close.loc[ts] > recovery.loc[ts]:
                circuit_open = False
        elif drawdown.loc[ts] <= threshold:
            circuit_open = True
        risk_on.append(0.0 if circuit_open else 1.0)
    return weights.mul(pd.Series(risk_on, index=close.index), axis=0)


def defensive_asset_rotation(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    risk_assets = spec.parameters.get("risk_assets", ["SPY", "QQQ"])
    defensive_assets = spec.parameters.get("defensive_assets", ["TLT", "GLD"])
    symbols = list(dict.fromkeys([*risk_assets, *defensive_assets]))
    close = daily_panel.loc[:, pd.IndexSlice[symbols, "close"]]
    close.columns = close.columns.get_level_values(0)
    lookback = spec.parameters.get("lookback", 126)
    momentum = close.pct_change(lookback)
    risk_symbol = spec.parameters.get("risk_symbol", "SPY")
    risk_sma = close[risk_symbol].rolling(spec.parameters.get("risk_sma", 200)).mean()
    risk_on = close[risk_symbol] > risk_sma
    weights = pd.DataFrame(0.0, index=close.index, columns=symbols)
    top_n = int(spec.parameters.get("top_n", 1))
    for ts in close.index:
        if risk_on.loc[ts]:
            selected = _select_positive_momentum(momentum.loc[ts, risk_assets], top_n, require_positive=False)
        else:
            selected = _select_positive_momentum(momentum.loc[ts, defensive_assets], 1, require_positive=True)
        if selected:
            allocation = 1.0 / len(selected)
            for symbol in selected:
                weights.loc[ts, symbol] = allocation
    return weights


def swing_rsi_pullback(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters["symbol"]
    close = daily_panel[(symbol, "close")]
    rsi = _rsi(close)
    sma = close.rolling(spec.parameters["trend_sma"]).mean()
    position = []
    active = False
    for ts in close.index:
        if not active and close.loc[ts] > sma.loc[ts] and rsi.loc[ts] < spec.parameters["rsi_entry"]:
            active = True
        elif active and (rsi.loc[ts] > spec.parameters["rsi_exit"] or close.loc[ts] < sma.loc[ts]):
            active = False
        position.append(1.0 if active else 0.0)
    return pd.DataFrame({symbol: position}, index=close.index)


def swing_trend_filtered_pullback(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    symbol = spec.parameters.get("symbol", "QQQ")
    close = daily_panel[(symbol, "close")]
    rsi = _rsi(close)
    fast = close.rolling(spec.parameters.get("fast_sma", 50)).mean()
    slow = close.rolling(spec.parameters.get("slow_sma", 150)).mean()
    atr = (daily_panel[(symbol, "high")] - daily_panel[(symbol, "low")]).rolling(14).mean()
    max_exposure = _bounded_float(spec.parameters.get("max_exposure", 1.0), lower=0.0, upper=1.0)
    position = []
    entry_price = 0.0
    active = False
    for ts in close.index:
        trend_ok = fast.loc[ts] > slow.loc[ts]
        pullback = rsi.loc[ts] < spec.parameters.get("rsi_entry", 40)
        if not active and trend_ok and pullback:
            active = True
            entry_price = close.loc[ts]
        elif active:
            stop = close.loc[ts] < entry_price - spec.parameters.get("atr_stop", 2.0) * atr.loc[ts]
            exit_signal = rsi.loc[ts] > spec.parameters.get("rsi_exit", 58) or close.loc[ts] < slow.loc[ts] or stop
            if exit_signal:
                active = False
        position.append(max_exposure if active else 0.0)
    return pd.DataFrame({symbol: position}, index=close.index)


def intraday_vwap_rsi_reclaim(spec: StrategySpec, daily_panel: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.DataFrame:
    if intraday is None:
        raise ValueError("intraday data is required")
    symbol = spec.parameters["symbol"]
    close = intraday["close"]
    typical = (intraday["high"] + intraday["low"] + intraday["close"]) / 3.0
    session = intraday.index.normalize()
    vwap = (typical * intraday["volume"]).groupby(session).cumsum() / intraday["volume"].groupby(session).cumsum()
    rsi = _rsi(close)
    washed_out = rsi.groupby(session).cummin() < spec.parameters["rsi_washout"]
    reclaim = (close > vwap) & (close.shift(1) <= vwap.shift(1)) & (rsi > spec.parameters["rsi_reclaim"]) & washed_out
    position = []
    active = False
    last_session = None
    for ts in close.index:
        current_session = ts.normalize()
        if last_session is not None and current_session != last_session:
            active = False
        if not active and reclaim.loc[ts]:
            active = True
        elif active and close.loc[ts] < vwap.loc[ts]:
            active = False
        position.append(1.0 if active else 0.0)
        last_session = current_session
    return pd.DataFrame({symbol: position}, index=close.index)


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).rolling(window).mean()
    loss = -delta.clip(upper=0.0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _monthly_top_n_weights(momentum: pd.DataFrame, index: pd.Index, top_n: int) -> pd.DataFrame:
    month_end_signal = momentum.resample("ME").last()
    ranks = month_end_signal.rank(axis=1, ascending=False, method="first")
    selected = (ranks <= top_n).astype(float) / float(top_n)
    weights = selected.reindex(index, method="ffill").fillna(0.0)
    if weights.sum(axis=1).eq(0.0).all() and len(momentum) > 0:
        ranks = momentum.rank(axis=1, ascending=False, method="first")
        weights = ((ranks <= top_n).astype(float) / float(top_n)).fillna(0.0)
    return weights


def _select_positive_momentum(row: pd.Series, top_n: int, require_positive: bool) -> list[str]:
    clean = row.dropna().sort_values(ascending=False)
    if require_positive:
        clean = clean[clean > 0]
    return [str(symbol) for symbol in clean.head(top_n).index]


def _dedupe_ordered_specs(candidates: list[tuple[int, str, StrategySpec]]) -> list[tuple[int, str, StrategySpec]]:
    seen_keys: set[str] = set()
    seen_specs: set[str] = set()
    retained: list[tuple[int, str, StrategySpec]] = []
    for order, key, spec in candidates:
        spec_fingerprint = strategy_execution_fingerprint(spec)
        if key in seen_keys or spec_fingerprint in seen_specs:
            continue
        seen_keys.add(key)
        seen_specs.add(spec_fingerprint)
        retained.append((order, key, spec))
    return retained


def _safe_candidate_fingerprint(item: dict[str, Any]) -> str | None:
    try:
        return candidate_fingerprint(item)
    except ValueError:
        return None


def _record_queue_skip(
    diagnostics: dict[str, Any],
    reason_code: str,
    hypothesis_id: str,
    family: str,
    source_key: str,
    duplicate_of: str,
) -> None:
    diagnostics["reasons"][reason_code] = diagnostics["reasons"].get(reason_code, 0) + 1
    diagnostics["skipped_count"] += 1
    if len(diagnostics["skipped"]) < MAX_QUEUE_DEDUPE_SKIP_DETAILS:
        diagnostics["skipped"].append(
            {
                "reason_code": reason_code,
                "hypothesis_id": hypothesis_id,
                "family": family,
                "source_key": source_key,
                "duplicate_of": duplicate_of,
            }
        )


def _is_allowed_recovery_spec(spec: StrategySpec) -> bool:
    builder = str(spec.builder).strip().lower()
    return builder in RECOVERY_ALLOWED_BUILDERS and spec.family == RECOVERY_BUILDER_FAMILIES[builder]


def _recent_executable_fingerprints(root: Path, max_rows: int) -> set[str]:
    fingerprints: set[str] = set()
    for result in _recent_experiment_results(root, max_rows=max_rows):
        fingerprint = result_execution_fingerprint(result)
        if fingerprint:
            fingerprints.add(fingerprint)
    return fingerprints


def _recent_experiment_results(root: Path, max_rows: int = 200) -> list[dict[str, Any]]:
    path = root / "registry" / "experiments.jsonl"
    return tail_jsonl(path, max_rows)


def _recent_drawdown_penalties(root: Path) -> dict[str, int]:
    penalties: dict[str, int] = {}
    for result in _recent_experiment_results(root):
        drawdown = _safe_float(result.get("split_metrics", {}).get("unseen", {}).get("max_drawdown"), 0.0)
        penalty = _drawdown_penalty(drawdown)
        if penalty <= 0:
            continue
        for key in _penalty_keys(result):
            penalties[key] = max(penalties.get(key, 0), penalty)
    return penalties


def _has_recent_extreme_rotation_drawdown(root: Path) -> bool:
    for result in _recent_experiment_results(root):
        if str(result.get("family") or "") != "ROTATION":
            continue
        drawdown = _safe_float(result.get("split_metrics", {}).get("unseen", {}).get("max_drawdown"), 0.0)
        if drawdown <= -0.50:
            return True
    return False


def _drawdown_penalty_for_spec(spec: StrategySpec, penalties: dict[str, int]) -> int:
    keys = {
        f"family:{spec.family}",
        f"family_short:{spec.family}:{spec.short_name}",
        f"builder:{spec.builder}",
    }
    return max((penalties.get(key, 0) for key in keys), default=0)


def _drawdown_penalty(max_drawdown: float) -> int:
    if max_drawdown <= -0.60:
        return 5
    if max_drawdown <= -0.50:
        return 4
    if max_drawdown <= -0.30:
        return 3
    if max_drawdown < -0.15:
        return 2
    return 0


def _penalty_keys(result: dict[str, Any]) -> list[str]:
    family = str(result.get("family") or "")
    short_name = str(result.get("short_name") or _short_name_from_strategy_id(str(result.get("strategy_id") or "")))
    builder = str(result.get("builder") or _builder_for_short_name(short_name))
    keys = []
    if family:
        keys.append(f"family:{family}")
    if family and short_name:
        keys.append(f"family_short:{family}:{short_name}")
    if builder:
        keys.append(f"builder:{builder}")
    return keys


def _conservative_preference_rank(spec: StrategySpec) -> int:
    text = " ".join(
        [
            spec.family,
            spec.short_name,
            spec.builder,
            spec.hypothesis,
            spec.rules,
            json.dumps(spec.parameters, sort_keys=True, default=str),
        ]
    ).lower()
    conservative_terms = ("vol", "target", "defensive", "cash", "drawdown", "cap", "circuit")
    return 0 if any(term in text for term in conservative_terms) else 1


def _is_near_miss_trend_vol_cap(result: dict[str, Any]) -> bool:
    if result.get("tier") != "C":
        return False
    if str(result.get("family") or "") != "LONGTERM":
        return False
    short_name = str(result.get("short_name") or _short_name_from_strategy_id(str(result.get("strategy_id") or "")))
    builder = str(result.get("builder") or _builder_for_short_name(short_name))
    if short_name != "TREND_VOL_CAP" and builder != "long_term_vol_target_cap":
        return False
    split = result.get("split_metrics", {})
    if _safe_float(split.get("train", {}).get("cagr"), 0.0) <= 0:
        return False
    if _safe_float(split.get("validation", {}).get("cagr"), 0.0) <= 0:
        return False
    unseen = split.get("unseen", {})
    if _safe_float(unseen.get("cagr"), 0.0) <= 0:
        return False
    if _safe_float(unseen.get("max_drawdown"), -1.0) < -0.15:
        return False
    walk_forward = result.get("walk_forward", {})
    return (
        isinstance(walk_forward, dict)
        and walk_forward.get("method") == "true_rolling_oos"
        and walk_forward.get("status") == "ok"
        and 0.50 <= _safe_float(walk_forward.get("pass_rate"), 0.0) < 0.67
    )


def _trend_vol_cap_conservative_mutations(result: dict[str, Any]) -> list[StrategySpec]:
    params = dict(result.get("parameters") or {})
    symbol = str(params.get("symbol") or "SPY")
    sma = int(_safe_float(params.get("sma"), 200))
    vol_window = int(_safe_float(params.get("vol_window"), 63))
    target_vol = _safe_float(params.get("target_vol"), 0.10)
    max_weight = _safe_float(params.get("max_weight"), 0.75)
    source_strategy_id = str(result.get("strategy_id") or "unknown")
    base = {
        "symbol": symbol,
        "sma": sma,
        "vol_window": vol_window,
        "source_strategy_id": source_strategy_id,
    }
    return [
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_VOL_CAP_CONSERVATIVE",
            hypothesis=f"Conservative mutation of {source_strategy_id}: reduce volatility target and exposure cap while preserving the trend plus volatility-cap structure.",
            parameters={
                **base,
                "target_vol": min(target_vol * 0.80, 0.08),
                "max_weight": min(max_weight * 0.80, 0.60),
            },
            rules="Hold SPY above the long-term SMA with lower realized-volatility targeting and a stricter exposure cap; otherwise hold cash.",
            builder="long_term_vol_target_cap",
        ),
        StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="TREND_VOL_CAP_STABLE",
            hypothesis=f"Stability mutation of {source_strategy_id}: smooth volatility estimates and reduce max exposure while preserving the trend plus volatility-cap structure.",
            parameters={
                **base,
                "vol_window": max(vol_window, 84),
                "target_vol": min(target_vol * 0.90, 0.09),
                "max_weight": min(max_weight * 0.87, 0.65),
            },
            rules="Hold SPY above the long-term SMA with smoother realized-volatility targeting capped below the original exposure; otherwise hold cash.",
            builder="long_term_vol_target_cap",
        ),
    ]


def _recovery_executable_parameters(builder: str, parameters: dict[str, Any]) -> dict[str, Any]:
    defaults = RECOVERY_PARAMETER_DEFAULTS[builder]
    missing = [key for key in RECOVERY_REQUIRED_PARAMETER_KEYS.get(builder, ()) if key not in parameters]
    if missing:
        raise ValueError(f"{builder} missing required executable parameter: {missing[0]}")
    normalized = {}
    for key in RECOVERY_EXECUTABLE_PARAMETER_SCHEMAS[builder]:
        value = parameters.get(key, defaults[key])
        if key in {"symbol", "risk_symbol"}:
            value = str(value).strip().upper()
        elif key in {"risk_assets", "defensive_assets"}:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{builder}.{key} must be an ordered list of ticker symbols")
            value = [str(symbol).strip().upper() for symbol in value]
        elif isinstance(value, np.generic):
            value = value.item()
        normalized[key] = value
    return normalized


def _short_name_from_strategy_id(strategy_id: str) -> str:
    marker = "_1D_"
    if marker not in strategy_id:
        return ""
    tail = strategy_id.split(marker, 1)[1]
    parts = tail.split("_")
    if len(parts) <= 2:
        return tail
    return "_".join(parts[:-2])


def _builder_for_short_name(short_name: str) -> str:
    return {
        "RSI_PULLBACK": "swing_rsi_pullback",
        "QUEUE_PULLBACK": "swing_trend_filtered_pullback",
        "TREND_VOL_CAP_CONSERVATIVE": "long_term_vol_target_cap",
        "TREND_VOL_CAP_STABLE": "long_term_vol_target_cap",
        "QUEUE_VOL_TARGET": "long_term_vol_target",
        "TREND_VOL_CAP": "long_term_vol_target_cap",
        "DEFENSIVE_ROTATION": "defensive_asset_rotation",
    }.get(short_name, "")


def _strategy_family_key(result: dict[str, Any]) -> str:
    return ":".join(
        [
            str(result.get("family") or ""),
            str(result.get("asset_class") or ""),
            str(result.get("timeframe") or ""),
            str(result.get("short_name") or _short_name_from_strategy_id(str(result.get("strategy_id") or ""))),
        ]
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _spec_from_hypothesis(item: dict) -> StrategySpec | None:
    if item.get("llm_generated"):
        return _validated_hermes_spec(item)
    family = item.get("family")
    title = item.get("title", "Queued hypothesis")
    source_title = item.get("source_title", "unknown source")
    hypothesis_id = item.get("hypothesis_id", "")
    source_feedback = _source_feedback_parameters(item)
    if family == "RISK_OVERLAY":
        return _unsupported_risk_overlay_spec(item)
    if family == "ROTATION":
        parameter_overrides = _parameter_overrides_from_hypothesis(
            item,
            {"lookback", "top_n", "risk_sma"},
        )
        return StrategySpec(
            family="ROTATION",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_MOM_DD",
            hypothesis=f"{title}: {item.get('rationale', '')}",
            parameters={
                "symbols": ["SPY", "QQQ", "TLT", "GLD"],
                "lookback": 126,
                "top_n": 2,
                "risk_symbol": "SPY",
                "risk_sma": 200,
                **parameter_overrides,
                "source_hypothesis_id": hypothesis_id,
                "source_title": source_title,
                **source_feedback,
            },
            rules="Monthly top-2 momentum rotation, but de-risk to cash when SPY is below SMA200.",
            builder="rotation_momentum_drawdown_filter",
        )
    if family == "SWING":
        ticker = str(item.get("ticker", "QQQ")).strip().upper() or "QQQ"
        parameter_overrides = _parameter_overrides_from_hypothesis(
            item,
            {"fast_sma", "slow_sma", "rsi_entry", "rsi_exit", "atr_stop"},
        )
        return StrategySpec(
            family="SWING",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_PULLBACK",
            hypothesis=f"{title}: {item.get('rationale', '')}",
            parameters={
                "symbol": ticker,
                "fast_sma": 50,
                "slow_sma": 150,
                "rsi_entry": 40,
                "rsi_exit": 58,
                "atr_stop": 2.0,
                **_risk_overlay_execution_parameters(item),
                **parameter_overrides,
                "source_hypothesis_id": hypothesis_id,
                "source_title": source_title,
                **source_feedback,
            },
            rules="Enter QQQ pullbacks in an uptrend; exit on RSI recovery, slow-trend break, or ATR stop.",
            builder="swing_trend_filtered_pullback",
        )
    if family == "LONGTERM":
        parameter_overrides = _parameter_overrides_from_hypothesis(
            item,
            {"sma", "vol_window", "target_vol"},
        )
        return StrategySpec(
            family="LONGTERM",
            asset_class="ETF",
            timeframe="1D",
            short_name="QUEUE_VOL_TARGET",
            hypothesis=f"{title}: {item.get('rationale', '')}",
            parameters={
                "symbol": "SPY",
                "sma": 150,
                "vol_window": 63,
                "target_vol": 0.12,
                **parameter_overrides,
                "source_hypothesis_id": hypothesis_id,
                "source_title": source_title,
                **source_feedback,
            },
            rules="Hold SPY above SMA150 with exposure scaled down when realized volatility exceeds target.",
            builder="long_term_vol_target",
        )
    return None


def _validated_hermes_spec(item: dict[str, Any]) -> StrategySpec | None:
    validation = validate_hypothesis(item)
    if not validation.accepted or validation.hypothesis is None:
        return None
    hypothesis = validation.hypothesis
    builder = hypothesis["builder"]
    parameters = {
        **hypothesis["parameters"],
        "source_hypothesis_id": str(item.get("hypothesis_id", "")),
        "source_title": str(item.get("source_title", "hermes")),
        "source_hermes_run_id": str(item.get("hermes_run_id", "")),
        "source_hermes_provider": str(item.get("hermes_provider", "")),
    }
    return StrategySpec(
        family=hypothesis["family"],
        asset_class=str(item.get("asset_class") or ("CRYPTO" if hypothesis["family"] == "INTRADAY" else "ETF")),
        timeframe=str(item.get("timeframe") or ("15M" if hypothesis["family"] == "INTRADAY" else "1D")),
        short_name=f"HERMES_{builder.upper()}",
        hypothesis=f"{hypothesis['title']}: {hypothesis['rationale']}",
        parameters=parameters,
        rules=f"Execute existing whitelisted builder {builder} with locally validated parameters.",
        builder=builder,
    )


def _source_feedback_parameters(item: dict[str, Any]) -> dict[str, Any]:
    passthrough = {}
    for key in (
        "risk_overlay_changed",
        "walk_forward_repair",
        "trade_count_repair",
        "cost_stress_repair",
        "turnover_repair",
        "material_design_change",
        "edge_repair",
        "validation_repair",
        "min_unseen_trades_target",
        "duplicate_hint",
    ):
        if key in item:
            passthrough[f"source_{key}"] = item[key]
    return passthrough


def _unsupported_risk_overlay_spec(item: dict[str, Any]) -> StrategySpec | None:
    _validate_risk_overlay_queue_row(item)
    raise ValueError(_risk_overlay_runtime_error())


def _risk_overlay_execution_parameters(item: dict[str, Any]) -> dict[str, Any]:
    if not item.get("risk_overlay_changed"):
        return {}
    return {"max_exposure": _bounded_float(item.get("max_exposure", 0.50), lower=0.05, upper=1.0)}


def _parameter_overrides_from_hypothesis(item: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    parameters = item.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    return {key: parameters[key] for key in allowed if key in parameters}


def _validate_risk_overlay_queue_row(item: dict[str, Any]) -> None:
    source_note_ids = item.get("source_note_ids")
    if not isinstance(source_note_ids, list) or not all(str(note_id).strip() for note_id in source_note_ids):
        raise ValueError("RISK_OVERLAY queue rows require non-empty source_note_ids provenance.")

    base_strategy_selection = item.get("base_strategy_selection")
    if not isinstance(base_strategy_selection, dict):
        raise ValueError("RISK_OVERLAY queue rows require base_strategy_selection.")
    if any(base_strategy_selection.get(key) is not False for key in ("allowed_to_modify_signals", "allowed_to_modify_entries", "allowed_to_modify_exits")):
        raise ValueError("RISK_OVERLAY queue rows must preserve base signals, entries, and exits.")

    base_strategy = item.get("base_strategy")
    if not isinstance(base_strategy, dict):
        raise ValueError("RISK_OVERLAY queue rows require explicit base strategy binding via base_strategy.")
    for key in ("family", "asset_class", "timeframe", "short_name", "builder", "parameters", "rules"):
        if key not in base_strategy:
            raise ValueError(f"RISK_OVERLAY queue rows require base strategy binding field base_strategy.{key}.")
    if not isinstance(base_strategy.get("parameters"), dict):
        raise ValueError("RISK_OVERLAY queue rows require base strategy binding field base_strategy.parameters.")

    risk_overlay = item.get("risk_overlay")
    if not isinstance(risk_overlay, dict):
        raise ValueError("RISK_OVERLAY queue rows require risk_overlay.")

    position_sizing = risk_overlay.get("position_sizing")
    if not isinstance(position_sizing, dict) or not isinstance(position_sizing.get("risk_per_trade_pct_candidates"), list):
        raise ValueError("RISK_OVERLAY queue rows require risk_overlay.position_sizing.risk_per_trade_pct_candidates.")

    circuit_breaker = risk_overlay.get("portfolio_drawdown_circuit_breaker")
    if not isinstance(circuit_breaker, dict):
        raise ValueError("RISK_OVERLAY queue rows require risk_overlay.portfolio_drawdown_circuit_breaker.")
    if not isinstance(circuit_breaker.get("thresholds"), list):
        raise ValueError("RISK_OVERLAY queue rows require risk_overlay.portfolio_drawdown_circuit_breaker.thresholds.")
    if not isinstance(circuit_breaker.get("reentry_rule"), dict):
        raise ValueError("RISK_OVERLAY queue rows require risk_overlay.portfolio_drawdown_circuit_breaker.reentry_rule.")

    loser_addition_rule = risk_overlay.get("loser_addition_rule")
    if not isinstance(loser_addition_rule, dict) or "add_to_losers_allowed" not in loser_addition_rule:
        raise ValueError("RISK_OVERLAY queue rows require risk_overlay.loser_addition_rule.add_to_losers_allowed.")

    validation_plan = item.get("validation_plan")
    if not isinstance(validation_plan, dict):
        raise ValueError("RISK_OVERLAY queue rows require validation_plan.")
    for key in ("primary_metrics", "secondary_metrics", "comparison", "required_gates"):
        if key not in validation_plan:
            raise ValueError(f"RISK_OVERLAY queue rows require validation_plan.{key}.")


def _risk_overlay_runtime_error() -> str:
    return (
        "RISK_OVERLAY queue rows are not executable with the current runtime. "
        "A dedicated overlay execution hook is still required to apply source_note_ids provenance, "
        "fixed_fractional position sizing, portfolio drawdown circuit breaker thresholds, "
        "reentry_rule enforcement, add_to_losers_allowed enforcement, and validation_plan "
        "without changing base signals, entries, or exits."
    )


def _bounded_float(value: Any, *, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = lower
    return min(max(number, lower), upper)
