from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from research_lab.registry import append_jsonl
from research_lab.risk_management import RISK_CONTROL_GUIDANCE, apply_risk_guidance


HERMES_SYSTEM_CONTRACT = """You are a creative hypothesis generator for a trading research lab.

Allowed:
- propose strategy hypotheses
- summarize public research
- suggest parameter neighborhoods
- identify failure modes

Forbidden:
- placing trades
- calling broker or exchange APIs
- editing production bots
- changing validation results
- promoting a strategy to deployment
- hiding rejected experiments

Risk management is a first-class research objective.
- Do not weaken existing gates.
- Do not relax max drawdown thresholds.
- Optimize explicitly for survival, drawdown containment, walk-forward robustness, and portfolio-level risk.
- Near-miss candidates such as LONGTERM_ETF_1D_TREND_VOL_CAP must be mutated primarily through risk controls, not return-chasing parameters.
- Strategies with high CAGR but unstable drawdown must be deprioritized.
- Rotation strategies with historically extreme drawdowns must not be expanded until a stronger risk overlay exists.
- Synthetic/fallback-data candidates remain blocked from promotion.

Every output must be a hypothesis, not a conclusion. The deterministic Python
runner is the only component allowed to calculate metrics and assign tiers.
"""


def build_hermes_prompt(root: Path, max_sources: int = 12) -> str:
    sources = _read_jsonl(root / "registry" / "source_items.jsonl")[-max_sources:]
    leaderboard = _read_csv_text(root / "registry" / "leaderboard.csv")
    source_lines = []
    for source in sources:
        source_lines.append(
            f"- title: {source.get('title', '')}\n"
            f"  source: {source.get('source', '')}\n"
            f"  url: {source.get('url', '')}\n"
            f"  tags: {', '.join(source.get('tags', []))}"
        )
    return "\n".join(
        [
            HERMES_SYSTEM_CONTRACT,
            "",
            "Current leaderboard:",
            "```csv",
            leaderboard[:4000],
            "```",
            "",
            "Recent research sources:",
            "\n".join(source_lines) or "- none",
            "",
            "Required risk-management controls to consider in every hypothesis:",
            "\n".join(f"- {key}: {value}" for key, value in RISK_CONTROL_GUIDANCE.items()),
            "",
            "Return 5-15 hypotheses as JSON Lines. Each line must contain:",
            (
                '{"title": "...", "family": "LONGTERM|ROTATION|SWING|INTRADAY", '
                '"rationale": "...", "tags": ["..."], "source_url": "...", '
                '"risk_controls": {"volatility_targeting": "...", "drawdown_circuit_breakers": "...", '
                '"cash_defensive_regimes": "...", "exposure_caps": "...", '
                '"correlation_aware_portfolio_risk": "...", "crisis_period_diagnostics": "...", '
                '"cost_slippage_stress": "...", "parameter_neighborhood_stability": "..."}}'
            ),
        ]
    )


def write_hermes_prompt(root: Path) -> Path:
    path = root / "reports" / "llm" / f"hermes_prompt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_hermes_prompt(root), encoding="utf-8")
    return path


def ingest_llm_hypotheses(root: Path, jsonl_text: str, source_name: str = "hermes") -> list[dict]:
    ingested = []
    for idx, line in enumerate(jsonl_text.splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        family = str(item.get("family", "")).upper()
        if family not in {"LONGTERM", "ROTATION", "SWING", "INTRADAY"}:
            continue
        payload = apply_risk_guidance(
            {
                "hypothesis_id": f"HYP_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{source_name.upper()}_{idx:03d}",
                "title": str(item.get("title", "")).strip(),
                "family": family,
                "rationale": str(item.get("rationale", "")).strip(),
                "source_title": source_name,
                "source_url": str(item.get("source_url", "")).strip(),
                "source_key": f"llm:{source_name}:{item.get('title', '')}".lower().replace(" ", "-"),
                "tags": item.get("tags", []),
                "status": "queued",
                "research_only": True,
                "llm_generated": True,
                "risk_controls": item.get("risk_controls", {}),
            }
        )
        append_jsonl(root / "registry" / "hypothesis_queue.jsonl", payload)
        ingested.append(payload)
    return ingested


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_csv_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
