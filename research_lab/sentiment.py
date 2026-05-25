from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import pstdev
from typing import Iterable

DEFAULT_PILOT_UNIVERSE = ["IREN", "CRWV", "NBIS", "WULF", "VRT", "CEG", "OKLO", "SMR", "AI", "NVDA", "PLTR", "SOUN"]

POSITIVE_KEYWORDS = [
    "beat", "raised guidance", "upgrade", "contract", "partnership", "expansion", "capacity", "record revenue",
    "profitability", "acceleration", "demand", "backlog", "ai demand", "data center demand",
]
NEGATIVE_KEYWORDS = [
    "miss", "cut guidance", "downgrade", "offering", "dilution", "investigation", "lawsuit", "short report",
    "bankruptcy", "going concern", "debt concern", "margin pressure", "delayed", "cancelled",
]

NARRATIVE_RULES = {
    "AI infrastructure": ["ai infrastructure", "ai cloud", "nvidia", "gpu"],
    "GPU cloud": ["gpu", "gpu cloud", "nvidia", "neocloud"],
    "neocloud": ["neocloud"],
    "bitcoin mining": ["bitcoin mining", "btc miner", "hashrate"],
    "crypto beta": ["bitcoin", "crypto", "hashrate"],
    "power capacity": ["power capacity", "mw", "megawatt"],
    "data center": ["data center", "datacenter"],
}
CATALYST_RULES = {
    "offering / dilution": ["offering", "atm", "share issuance", "dilution"],
    "analyst upgrade": ["upgrade", "price target raised"],
    "short report": ["short report", "fraud allegations"],
}


@dataclass
class SentimentThresholds:
    sentiment_up_threshold: float = 0.2
    attention_up_threshold: float = 0.2
    price_up_threshold: float = 0.02
    volume_up_threshold: float = 1.0


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def classify_tags(text: str) -> tuple[list[str], list[str]]:
    low = text.lower()
    narrative = [k for k, rules in NARRATIVE_RULES.items() if any(r in low for r in rules)]
    catalyst = [k for k, rules in CATALYST_RULES.items() if any(r in low for r in rules)]
    return narrative, catalyst


def score_texts(texts: list[str]) -> dict:
    if not texts:
        return {"score": None, "positive_ratio": None, "negative_ratio": None, "neutral_ratio": None, "coverage": "missing"}
    pos = neg = neu = 0
    for raw in texts:
        text = raw.lower()
        p = any(k in text for k in POSITIVE_KEYWORDS)
        n = any(k in text for k in NEGATIVE_KEYWORDS)
        if p and not n:
            pos += 1
        elif n and not p:
            neg += 1
        else:
            neu += 1
    total = len(texts)
    score = (pos - neg) / total
    return {
        "score": max(-1.0, min(1.0, score)),
        "positive_ratio": pos / total,
        "negative_ratio": neg / total,
        "neutral_ratio": neu / total,
        "coverage": "available",
    }


def classify_price_confirmed(combined_sentiment_score, attention_delta_7d, price_return_5d, volume_zscore, thresholds: SentimentThresholds | None = None) -> str:
    t = thresholds or SentimentThresholds()
    if price_return_5d is None:
        return "sentiment_only_unconfirmed"
    if combined_sentiment_score is None:
        return "price_only" if price_return_5d >= t.price_up_threshold else "sentiment_only_unconfirmed"
    sentiment_up = combined_sentiment_score >= t.sentiment_up_threshold
    sentiment_down = combined_sentiment_score <= -t.sentiment_up_threshold
    sentiment_flat = abs(combined_sentiment_score) < t.sentiment_up_threshold
    price_up = price_return_5d >= t.price_up_threshold
    price_down = price_return_5d <= -t.price_up_threshold
    volume_up = (volume_zscore or 0.0) >= t.volume_up_threshold
    attention_up = (attention_delta_7d or 0.0) >= t.attention_up_threshold
    if sentiment_up and price_up and volume_up:
        return "confirmed_momentum"
    if sentiment_up and price_down:
        return "failed_hype_or_distribution"
    if sentiment_down and price_up:
        return "squeeze_or_positioning"
    if attention_up and price_up and sentiment_flat:
        return "attention_momentum"
    if attention_up and price_down:
        return "noisy_hype"
    if sentiment_flat and price_up:
        return "stealth_momentum"
    return "mixed"


def load_file_items(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else payload.get("items", [])
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    raise ValueError(f"unsupported file type: {path.suffix}")


def run_apify_scaffold(max_items: int = 100, max_cost_usd: float = 2.0) -> dict:
    max_items = max(1, min(int(max_items), 500))
    max_cost_usd = max(0.1, float(max_cost_usd))
    if not os.getenv("APIFY_TOKEN", "").strip():
        return {"coverage_status": "missing", "reason": "APIFY_TOKEN missing", "items": [], "max_items": max_items, "max_cost_usd": max_cost_usd}
    actor_id = os.getenv("APIFY_SENTIMENT_ACTOR_ID", "").strip()
    if not actor_id:
        return {"coverage_status": "missing", "reason": "APIFY_SENTIMENT_ACTOR_ID missing", "items": [], "max_items": max_items, "max_cost_usd": max_cost_usd}
    return {
        "coverage_status": "partial",
        "reason": "scaffold only: actor wiring and payload normalization not implemented yet",
        "items": [],
        "max_items": max_items,
        "max_cost_usd": max_cost_usd,
        "actor_id": actor_id,
    }


def build_snapshots(items: Iterable[dict], as_of: datetime | None = None) -> list[dict]:
    as_of = as_of or datetime.now(timezone.utc)
    buckets: dict[str, list[dict]] = {}
    for item in items:
        ticker = str(item.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        buckets.setdefault(ticker, []).append(item)
    snapshots: list[dict] = []
    for ticker, ticker_items in buckets.items():
        texts = [f"{row.get('title', '')} {row.get('text', '')}".strip() for row in ticker_items if (row.get("title") or row.get("text"))]
        sentiment = score_texts(texts)
        narrative_tags = sorted({tag for text in texts for tag in classify_tags(text)[0]})
        catalyst_tags = sorted({tag for text in texts for tag in classify_tags(text)[1]})

        counts_1d = counts_7d = counts_30d = 0
        days_covered = set()
        for row in ticker_items:
            ts = row.get("timestamp")
            if not ts:
                continue
            dt = _parse_ts(str(ts))
            delta = as_of - dt
            if delta <= timedelta(days=1):
                counts_1d += 1
            if delta <= timedelta(days=7):
                counts_7d += 1
            if delta <= timedelta(days=30):
                counts_30d += 1
            days_covered.add(dt.date().isoformat())

        mentions_zscore = None
        attention_delta_7d = None
        coverage = sentiment["coverage"]
        if counts_30d > 0:
            mean = counts_30d / 30.0
            stdev = pstdev([1.0 if str((as_of - timedelta(days=d)).date().isoformat()) in days_covered else 0.0 for d in range(30)])
            mentions_zscore = None if stdev == 0 else (counts_1d - mean) / stdev
            prior_7d = max(counts_30d - counts_7d, 0) / max(30 - 7, 1)
            attention_delta_7d = (counts_7d - (prior_7d * 7)) / max(prior_7d * 7, 1.0)
        else:
            coverage = "partial" if coverage == "available" else coverage

        snapshots.append({
            "ticker": ticker,
            "as_of": as_of.isoformat(),
            "provider": "file",
            "source_type": "mixed",
            "source_name": "file_adapter",
            "lookback_days": 30,
            "news_count_1d": counts_1d,
            "news_count_7d": counts_7d,
            "news_count_30d": counts_30d,
            "social_mentions_1d": None,
            "social_mentions_7d": None,
            "social_mentions_30d": None,
            "mentions_zscore": mentions_zscore,
            "attention_delta_1d": None,
            "attention_delta_7d": attention_delta_7d,
            "news_sentiment_score": sentiment["score"],
            "social_sentiment_score": None,
            "combined_sentiment_score": sentiment["score"],
            "sentiment_delta_7d": None,
            "positive_ratio": sentiment["positive_ratio"],
            "negative_ratio": sentiment["negative_ratio"],
            "neutral_ratio": sentiment["neutral_ratio"],
            "narrative_tags": "|".join(narrative_tags),
            "catalyst_tags": "|".join(catalyst_tags),
            "price_return_1d": None,
            "price_return_5d": None,
            "price_return_20d": None,
            "volume_zscore": None,
            "price_confirmed_sentiment": classify_price_confirmed(sentiment["score"], attention_delta_7d, None, None),
            "coverage_status": coverage,
            "stale_reason": None,
            "raw_source_count": len({str(r.get('source', '')) for r in ticker_items if r.get('source')}),
            "raw_item_sample": json.dumps([{k: row.get(k) for k in ("source", "timestamp", "title", "url", "source_type")} for row in ticker_items[:3]])[:400],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "research_only": True,
            "not_trading_signal": True,
        })
    return snapshots


def write_outputs(root: Path, snapshots: list[dict], report_stem: str | None = None) -> dict:
    registry = root / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    snap_path = registry / "sentiment_snapshot.csv"
    cand_path = registry / "sentiment_candidates.csv"
    fields = list(snapshots[0].keys()) if snapshots else ["ticker", "coverage_status", "research_only", "not_trading_signal"]
    for path in (snap_path, cand_path):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in snapshots:
                writer.writerow(row)
    if report_stem:
        weekly = root / "reports" / "weekly"
        weekly.mkdir(parents=True, exist_ok=True)
        weekly_path = weekly / f"{report_stem}_sentiment_candidates.csv"
        with weekly_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in snapshots:
                writer.writerow(row)
    return {"snapshot_path": str(snap_path), "candidates_path": str(cand_path)}
