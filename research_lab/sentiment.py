from __future__ import annotations

import csv
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import pstdev
from typing import Iterable

DEFAULT_PILOT_UNIVERSE = ["IREN", "CRWV", "NBIS", "WULF", "VRT", "CEG", "OKLO", "SMR", "AI", "NVDA", "PLTR", "SOUN"]
APIFY_PILOT_UNIVERSE = ["IREN", "CRWV", "NBIS", "WULF", "VRT", "CEG", "OKLO", "SMR"]
APIFY_SOURCE_ACTORS = {
    "reddit": "logiover/reddit-search-scraper",
    "stocktwits": "saswave/stocktwits-stock-ticker-news-scraper",
    "stocktwits_fallback": "shahidirfan/stocktwits-sentiment-scraper",
    "news": "vnx0/google-news-actor",
}
APIFY_SOURCE_ENV = {
    "reddit": "APIFY_REDDIT_ACTOR_ID",
    "stocktwits": "APIFY_STOCKTWITS_ACTOR_ID",
    "news": "APIFY_NEWS_ACTOR_ID",
}

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


def normalize_apify_payload(source: str, payload: list[dict]) -> list[dict]:
    source = _normalize_source_name(source)
    normalized = []
    for row in payload:
        item = _normalize_apify_row(source, row)
        if item:
            normalized.append(item)
    return normalized


def run_apify_source_pilot(
    source: str,
    tickers: list[str] | None = None,
    fixture_path: Path | None = None,
    live: bool = False,
    max_items: int = 25,
    max_cost_usd: float = 1.0,
) -> dict:
    source = _normalize_source_name(source)
    tickers = [ticker.upper().strip() for ticker in (tickers or APIFY_PILOT_UNIVERSE) if ticker.strip()]
    max_items = max(1, min(int(max_items), 100))
    max_cost_usd = max(0.1, min(float(max_cost_usd), 5.0))
    default_actor = APIFY_SOURCE_ACTORS[source]
    if fixture_path is not None:
        raw_items = load_file_items(Path(fixture_path))[:max_items]
        items = [item for item in normalize_apify_payload(source, raw_items) if item["ticker"] in tickers]
        status = "available" if items else "missing"
        return {
            "coverage_status": status,
            "source_coverage_status": {source: status},
            "reason": "fixture normalization only; live Apify disabled",
            "items": items[:max_items],
            "raw_items": raw_items,
            "actor_id": default_actor,
            "max_items": max_items,
            "max_cost_usd": max_cost_usd,
            "live": False,
        }
    if not live:
        return {
            "coverage_status": "missing",
            "source_coverage_status": {source: "missing"},
            "reason": "live Apify disabled; provide fixture_path or explicit live=True",
            "items": [],
            "raw_items": [],
            "actor_id": default_actor,
            "max_items": max_items,
            "max_cost_usd": max_cost_usd,
            "live": False,
        }
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        return {
            "coverage_status": "missing",
            "source_coverage_status": {source: "missing"},
            "reason": "APIFY_TOKEN missing",
            "items": [],
            "raw_items": [],
            "actor_id": default_actor,
            "max_items": max_items,
            "max_cost_usd": max_cost_usd,
            "live": True,
        }
    actor_env = APIFY_SOURCE_ENV[source]
    actor_id = os.getenv(actor_env, "").strip()
    if not actor_id:
        return {
            "coverage_status": "missing",
            "source_coverage_status": {source: "missing"},
            "reason": f"{actor_env} actor id missing",
            "items": [],
            "raw_items": [],
            "actor_id": default_actor,
            "max_items": max_items,
            "max_cost_usd": max_cost_usd,
            "live": True,
        }
    try:
        raw_items = _run_apify_actor(actor_id, token, _apify_run_input(source, tickers, max_items), max_items)
    except Exception as exc:
        return {
            "coverage_status": "error",
            "source_coverage_status": {source: "error"},
            "reason": f"Apify run failed: {exc}",
            "items": [],
            "raw_items": [],
            "actor_id": actor_id,
            "max_items": max_items,
            "max_cost_usd": max_cost_usd,
            "live": True,
        }
    items = [item for item in normalize_apify_payload(source, raw_items) if item["ticker"] in tickers]
    status = "available" if items else "missing"
    return {
        "coverage_status": status,
        "source_coverage_status": {source: status},
        "reason": "live Apify run completed with bounded max_items",
        "items": items[:max_items],
        "raw_items": raw_items[:max_items],
        "actor_id": actor_id,
        "max_items": max_items,
        "max_cost_usd": max_cost_usd,
        "live": True,
    }


def write_apify_raw_sample(root: Path, source: str, payload: list[dict], created_at: datetime | None = None) -> Path:
    created_at = created_at or datetime.now(timezone.utc)
    out_dir = root / "registry" / "sentiment_raw_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = created_at.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{_normalize_source_name(source)}_{stamp}.json"
    safe_payload = [_sanitize_raw_item(item) for item in payload[:100]]
    path.write_text(json.dumps(safe_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


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
            "provider": _coalesce_source_value(ticker_items, "provider", "file"),
            "source_type": _snapshot_source_type(ticker_items),
            "source_name": _coalesce_source_value(ticker_items, "source", "file_adapter"),
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
    coverage_path = registry / "sentiment_source_coverage.csv"
    fields = list(snapshots[0].keys()) if snapshots else ["ticker", "coverage_status", "research_only", "not_trading_signal"]
    for path in (snap_path, cand_path):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in snapshots:
                writer.writerow(row)
    coverage_rows = _coverage_rows(snapshots)
    coverage_fields = ["ticker", "source_name", "provider", "source_type", "coverage_status", "raw_source_count", "created_at"]
    with coverage_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=coverage_fields)
        writer.writeheader()
        for row in coverage_rows:
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
        weekly_coverage = weekly / f"{report_stem}_sentiment_source_coverage.csv"
        with weekly_coverage.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=coverage_fields)
            writer.writeheader()
            for row in coverage_rows:
                writer.writerow(row)
        summary_path = weekly / f"{report_stem}_sentiment_sources.md"
        summary_path.write_text("\n".join(_source_summary_lines(coverage_rows)) + "\n", encoding="utf-8")
    return {"snapshot_path": str(snap_path), "candidates_path": str(cand_path), "coverage_path": str(coverage_path)}


def _normalize_apify_row(source: str, row: dict) -> dict | None:
    text = _first_value(row, "body", "selftext", "text", "message", "description", "summary", "content")
    title = _first_value(row, "title", "headline") or text[:80]
    combined = f"{title} {text}"
    ticker = _first_value(row, "ticker", "symbol") or _infer_ticker(combined)
    if not ticker:
        return None
    timestamp = _first_value(row, "createdAt", "created_at", "datetime", "publishedAt", "published", "date")
    if not timestamp:
        return None
    source_name = "google_news" if source == "news" else source
    return {
        "ticker": ticker.upper(),
        "provider": "apify_fixture",
        "source": source_name,
        "timestamp": timestamp,
        "title": title,
        "text": text,
        "url": _first_value(row, "url", "permalink", "originalUrl", "finalUrl") or "",
        "author": _author_from_row(row),
        "engagement_score": _engagement_score(source, row),
        "source_type": "news" if source == "news" else "social",
    }


def _run_apify_actor(actor_id: str, token: str, run_input: dict, max_items: int) -> list[dict]:
    encoded_actor = actor_id.replace("/", "~")
    query = urllib.parse.urlencode({"token": token, "timeout": "120", "memory": "512"})
    url = f"https://api.apify.com/v2/acts/{encoded_actor}/run-sync-get-dataset-items?{query}"
    request = urllib.request.Request(
        url,
        data=json.dumps(run_input).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "research-lab/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload[:max_items] if isinstance(payload, list) else []


def _apify_run_input(source: str, tickers: list[str], max_items: int) -> dict:
    queries = [f"{ticker} stock" for ticker in tickers]
    if source == "reddit":
        return {"queries": queries, "maxItems": max_items, "timeRange": "week"}
    if source == "stocktwits":
        return {"symbols": tickers, "maxItems": max_items}
    return {"queries": queries, "maxItems": max_items, "language": "en"}


def _normalize_source_name(source: str) -> str:
    source = source.lower().strip()
    if source in {"google_news", "public_news"}:
        return "news"
    if source not in {"reddit", "stocktwits", "news"}:
        raise ValueError(f"unsupported Apify sentiment source: {source}")
    return source


def _first_value(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _infer_ticker(text: str) -> str:
    upper = text.upper()
    for ticker in APIFY_PILOT_UNIVERSE:
        if re.search(rf"\b{re.escape(ticker)}\b", upper):
            return ticker
    return ""


def _author_from_row(row: dict) -> str:
    user = row.get("user")
    if isinstance(user, dict):
        return str(user.get("username") or user.get("name") or "")
    return _first_value(row, "author", "username", "userName")


def _engagement_score(source: str, row: dict) -> int:
    if source == "reddit":
        return _int(row.get("score")) + _int(row.get("numComments"))
    if source == "stocktwits":
        return _int(row.get("likes")) + _int(row.get("replies"))
    return _int(row.get("engagement_score"))


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sanitize_raw_item(item: dict) -> dict:
    sanitized = {}
    for key, value in item.items():
        lowered = str(key).lower()
        if any(secret in lowered for secret in ("token", "secret", "password", "api_key")):
            sanitized[key] = "[redacted]"
        elif isinstance(value, str):
            sanitized[key] = re.sub(r"(?i)(token|api_key|secret|password)=([^&\s]+)", r"\1=[redacted]", value)[:1000]
        else:
            sanitized[key] = value
    return sanitized


def _snapshot_source_type(rows: list[dict]) -> str:
    types = {str(row.get("source_type", "")).strip() for row in rows if row.get("source_type")}
    if len(types) == 1:
        return next(iter(types))
    if len(types) > 1:
        return "mixed"
    return "mixed"


def _coalesce_source_value(rows: list[dict], key: str, default: str) -> str:
    values = [str(row.get(key, "")).strip() for row in rows if row.get(key)]
    unique = sorted(set(values))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return "mixed"
    return default


def _coverage_rows(snapshots: list[dict]) -> list[dict]:
    return [
        {
            "ticker": row.get("ticker", ""),
            "source_name": row.get("source_name", ""),
            "provider": row.get("provider", ""),
            "source_type": row.get("source_type", ""),
            "coverage_status": row.get("coverage_status", ""),
            "raw_source_count": row.get("raw_source_count", 0),
            "created_at": row.get("created_at", ""),
        }
        for row in snapshots
    ]


def _source_summary_lines(rows: list[dict]) -> list[str]:
    lines = [
        "# Sentiment Source Coverage",
        "",
        "- mode: READ ONLY",
        "- research_only=true",
        "- not_trading_signal=true",
        "",
    ]
    if not rows:
        return lines + ["- sentiment source coverage not available"]
    for row in rows:
        lines.append(f"- {row['ticker']} / {row['source_name']}: {row['coverage_status']}")
    return lines
