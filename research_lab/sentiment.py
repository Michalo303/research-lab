from __future__ import annotations

import csv
import json
import math
import os
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SNAPSHOT_COLUMNS = [
    "ticker",
    "as_of",
    "provider",
    "source_type",
    "source_name",
    "lookback_days",
    "news_count_1d",
    "news_count_7d",
    "news_count_30d",
    "social_mentions_1d",
    "social_mentions_7d",
    "social_mentions_30d",
    "mentions_zscore",
    "attention_delta_1d",
    "attention_delta_7d",
    "news_sentiment_score",
    "social_sentiment_score",
    "combined_sentiment_score",
    "sentiment_delta_7d",
    "positive_ratio",
    "negative_ratio",
    "neutral_ratio",
    "narrative_tags",
    "catalyst_tags",
    "price_return_1d",
    "price_return_5d",
    "price_return_20d",
    "volume_zscore",
    "price_confirmed_sentiment",
    "coverage_status",
    "stale_reason",
    "raw_source_count",
    "raw_item_sample",
    "created_at",
]

CANDIDATE_COLUMNS = [
    "ticker",
    "research_rank",
    "research_score",
    "combined_sentiment_score",
    "sentiment_delta_7d",
    "attention_delta_7d",
    "mentions_zscore",
    "price_return_5d",
    "price_return_20d",
    "volume_zscore",
    "price_confirmed_sentiment",
    "narrative_tags",
    "catalyst_tags",
    "coverage_status",
    "provider",
    "source_name",
    "research_only",
    "not_trading_signal",
]

DEFAULT_PILOT_TICKERS = ["IREN", "CRWV", "NBIS", "WULF", "VRT", "CEG", "OKLO", "SMR", "AI", "NVDA", "PLTR", "SOUN"]

NARRATIVE_RULES = [
    ("AI infrastructure", ["ai infrastructure", "ai demand", "ai cloud", "nvidia", "gpu", "accelerated compute"]),
    ("GPU cloud", ["gpu cloud", "gpu", "nvidia", "ai cloud"]),
    ("neocloud", ["neocloud", "neo cloud"]),
    ("bitcoin mining", ["bitcoin mining", "btc miner", "bitcoin miner", "hashrate"]),
    ("crypto beta", ["btc", "bitcoin", "crypto", "hashrate"]),
    ("power capacity", ["power capacity", "mw", "megawatt", "grid interconnect", "power purchase"]),
    ("data center", ["data center", "datacenter", "colocation"]),
    ("energy infrastructure", ["energy infrastructure", "power plant", "transmission", "substation"]),
    ("nuclear / SMR", ["nuclear", "smr", "small modular reactor"]),
    ("grid / electrification", ["grid", "electrification", "interconnect", "transmission"]),
    ("short squeeze", ["short squeeze", "short interest", "borrow fee"]),
    ("retail attention", ["retail traders", "stocktwits", "reddit", "wallstreetbets", "meme stock"]),
    ("institutional accumulation", ["13f", "institutional accumulation", "smart money", "fund bought"]),
    ("defense", ["defense", "pentagon", "dod", "military"]),
    ("biotech catalyst", ["phase 2", "phase 3", "fda", "clinical trial", "biotech"]),
    ("earnings momentum", ["earnings beat", "record revenue", "raised guidance"]),
    ("rate sensitivity", ["rate cut", "rate hike", "treasury yield", "duration"]),
    ("commodity beta", ["commodity", "oil", "copper", "uranium", "gold"]),
]

CATALYST_RULES = [
    ("earnings beat", ["earnings beat", "beat estimates", "beats expectations"]),
    ("earnings miss", ["earnings miss", "miss estimates", "missed expectations"]),
    ("guidance raise", ["raised guidance", "guidance raise", "raises outlook"]),
    ("guidance cut", ["cut guidance", "guidance cut", "lowers outlook"]),
    ("analyst upgrade", ["analyst upgrade", "upgraded", "price target raised"]),
    ("analyst downgrade", ["analyst downgrade", "downgraded", "price target cut"]),
    ("contract win", ["contract win", "wins contract", "awarded contract"]),
    ("partnership", ["partnership", "partnered with", "joint venture"]),
    ("offering / dilution", ["offering", "atm", "share issuance", "dilution", "secondary"]),
    ("insider buying", ["insider buying", "insider bought"]),
    ("insider selling", ["insider selling", "insider sold"]),
    ("13F / smart money", ["13f", "smart money", "superinvestor"]),
    ("regulatory risk", ["regulatory risk", "investigation", "fraud allegations", "sec probe", "regulatory violations"]),
    ("short report", ["short report", "short seller report", "fraud allegations"]),
    ("index inclusion", ["index inclusion", "added to index", "s&p 500 inclusion"]),
    ("debt refinancing", ["debt refinancing", "refinancing debt"]),
    ("bankruptcy / going concern", ["bankruptcy", "going concern"]),
]

POSITIVE_KEYWORDS = [
    "beat",
    "raised guidance",
    "upgrade",
    "contract",
    "partnership",
    "expansion",
    "capacity",
    "record revenue",
    "profitability",
    "acceleration",
    "demand",
    "backlog",
    "ai demand",
    "data center demand",
]

NEGATIVE_KEYWORDS = [
    "miss",
    "cut guidance",
    "downgrade",
    "offering",
    "dilution",
    "investigation",
    "lawsuit",
    "short report",
    "bankruptcy",
    "going concern",
    "debt concern",
    "margin pressure",
    "delayed",
    "cancelled",
]


@dataclass(frozen=True)
class RawSentimentItem:
    ticker: str
    source: str
    timestamp: datetime
    title: str
    text: str = ""
    url: str = ""
    author: str = ""
    engagement_score: float | None = None
    source_type: str = "news"


@dataclass(frozen=True)
class ProviderFetchResult:
    items: list[RawSentimentItem]
    coverage_status: str
    stale_reason: str = ""
    source_coverage_status: str = ""


@dataclass(frozen=True)
class SentimentScore:
    score: float | None
    positive_ratio: float | None
    negative_ratio: float | None
    neutral_ratio: float | None
    coverage_status: str


@dataclass(frozen=True)
class AttentionMetrics:
    mentions_1d: int | None
    mentions_7d: int | None
    mentions_30d: int | None
    mentions_zscore: float | None
    attention_delta_1d: int | None
    attention_delta_7d: int | None
    raw_source_count: int


@dataclass(frozen=True)
class PriceSentimentThresholds:
    sentiment_up_threshold: float = 0.25
    attention_up_threshold: float = 2.0
    price_up_threshold: float = 0.05
    volume_up_threshold: float = 1.0


@dataclass(frozen=True)
class SentimentSnapshot:
    ticker: str
    as_of: str
    provider: str
    source_type: str
    source_name: str
    lookback_days: int
    news_count_1d: int | None
    news_count_7d: int | None
    news_count_30d: int | None
    social_mentions_1d: int | None
    social_mentions_7d: int | None
    social_mentions_30d: int | None
    mentions_zscore: float | None
    attention_delta_1d: int | None
    attention_delta_7d: int | None
    news_sentiment_score: float | None
    social_sentiment_score: float | None
    combined_sentiment_score: float | None
    sentiment_delta_7d: float | None
    positive_ratio: float | None
    negative_ratio: float | None
    neutral_ratio: float | None
    narrative_tags: str
    catalyst_tags: str
    price_return_1d: float | None
    price_return_5d: float | None
    price_return_20d: float | None
    volume_zscore: float | None
    price_confirmed_sentiment: str
    coverage_status: str
    stale_reason: str
    raw_source_count: int
    raw_item_sample: str
    created_at: str


class FileSentimentAdapter:
    def __init__(self, input_path: str | Path):
        self.input_path = Path(input_path)

    def fetch(self, tickers: list[str], as_of: datetime | None = None, max_items: int = 100) -> ProviderFetchResult:
        if not self.input_path.exists():
            return ProviderFetchResult([], "missing", f"input file not found: {self.input_path}", "missing")
        wanted = {ticker.upper() for ticker in tickers}
        items = []
        for row in _read_input_rows(self.input_path):
            ticker = str(row.get("ticker", "")).strip().upper()
            if wanted and ticker not in wanted:
                continue
            parsed = _row_to_item(row)
            if parsed is not None:
                items.append(parsed)
            if len(items) >= max_items:
                break
        status = "available" if items else "missing"
        return ProviderFetchResult(items, status, "" if items else "no matching rows in input file", status)


class ApifySentimentAdapter:
    def __init__(
        self,
        actor_id: str | None = None,
        token: str | None = None,
        max_cost_usd: float = 1.0,
        source_name: str = "apify",
    ):
        self.actor_id = actor_id or _first_env("APIFY_SENTIMENT_ACTOR_ID", "APIFY_NEWS_ACTOR_ID", "APIFY_REDDIT_ACTOR_ID", "APIFY_STOCKTWITS_ACTOR_ID")
        self.token = token if token is not None else os.getenv("APIFY_TOKEN", "").strip()
        self.max_cost_usd = max_cost_usd
        self.source_name = source_name

    def fetch(self, tickers: list[str], as_of: datetime | None = None, max_items: int = 100) -> ProviderFetchResult:
        if not self.token:
            return ProviderFetchResult([], "missing", "APIFY_TOKEN is not configured", "missing")
        if not self.actor_id:
            return ProviderFetchResult([], "missing", "Apify actor id is not configured", "missing")
        if max_items > 500:
            return ProviderFetchResult([], "error", "max_items guardrail exceeded: 500", "error")
        if self.max_cost_usd <= 0:
            return ProviderFetchResult([], "error", "max_cost guardrail blocks Apify run", "error")
        try:
            rows = self._run_actor(tickers, max_items)
        except Exception as exc:
            return ProviderFetchResult([], "error", f"Apify fetch failed: {exc}", "error")
        items = []
        for row in rows[:max_items]:
            parsed = _row_to_item(row)
            if parsed is not None:
                items.append(parsed)
        return ProviderFetchResult(items, "available" if items else "missing", "" if items else "Apify returned no parseable items", "available" if items else "missing")

    def _run_actor(self, tickers: list[str], max_items: int) -> list[dict[str, Any]]:
        encoded_actor = self.actor_id.replace("/", "~")
        query = urllib.parse.urlencode({"token": self.token, "timeout": "120", "memory": "512"})
        url = f"https://api.apify.com/v2/acts/{encoded_actor}/run-sync-get-dataset-items?{query}"
        run_input = {"tickers": tickers, "maxItems": max_items, "researchOnly": True}
        request = urllib.request.Request(
            url,
            data=json.dumps(run_input).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "research-lab/0.1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []


class EODHDNewsSentimentAdapter:
    """Future provider placeholder for EODHD news/sentiment enrichment."""


class FinnhubSocialSentimentAdapter:
    """Future provider placeholder for Finnhub social sentiment enrichment."""


class FMPNewsContextAdapter:
    """Future provider placeholder for FMP news/company context enrichment."""


def classify_narratives(*texts: str) -> tuple[list[str], list[str]]:
    text = _normalize_text(" ".join(texts))
    return _match_rules(text, NARRATIVE_RULES), _match_rules(text, CATALYST_RULES)


def score_sentiment_texts(texts: list[str]) -> SentimentScore:
    usable = [text for text in texts if str(text).strip()]
    if not usable:
        return SentimentScore(None, None, None, None, "missing")
    item_scores = []
    positive = negative = neutral = 0
    pos_total = neg_total = 0
    for text in usable:
        normalized = _normalize_text(text)
        pos = _keyword_count(normalized, POSITIVE_KEYWORDS)
        neg = _keyword_count(normalized, NEGATIVE_KEYWORDS)
        pos_total += pos
        neg_total += neg
        if pos > neg:
            positive += 1
        elif neg > pos:
            negative += 1
        else:
            neutral += 1
        if pos + neg:
            item_scores.append((pos - neg) / (pos + neg))
        else:
            item_scores.append(0.0)
    total = len(usable)
    score = sum(item_scores) / len(item_scores)
    return SentimentScore(
        max(-1.0, min(1.0, score)),
        positive / total,
        negative / total,
        neutral / total,
        "available",
    )


def compute_attention_metrics(items: list[RawSentimentItem], as_of: datetime) -> AttentionMetrics:
    if not items:
        return AttentionMetrics(None, None, None, None, None, None, 0)
    as_of = _ensure_aware(as_of)
    mentions_1d = _count_between(items, as_of - timedelta(days=1), as_of)
    mentions_7d = _count_between(items, as_of - timedelta(days=7), as_of)
    mentions_30d = _count_between(items, as_of - timedelta(days=30), as_of)
    prior_1d = _count_between(items, as_of - timedelta(days=2), as_of - timedelta(days=1))
    prior_7d = _count_between(items, as_of - timedelta(days=14), as_of - timedelta(days=7))
    baseline_counts = []
    for day in range(8, 31):
        end = as_of - timedelta(days=day - 1)
        start = as_of - timedelta(days=day)
        baseline_counts.append(_count_between(items, start, end))
    baseline_has_history = any(count > 0 for count in baseline_counts)
    zscore = None
    if baseline_has_history:
        mean = sum(baseline_counts) / len(baseline_counts)
        variance = sum((count - mean) ** 2 for count in baseline_counts) / len(baseline_counts)
        stdev = math.sqrt(variance) or 1.0
        zscore = (mentions_1d - mean) / stdev
    return AttentionMetrics(
        mentions_1d,
        mentions_7d,
        mentions_30d,
        zscore,
        mentions_1d - prior_1d,
        mentions_7d - prior_7d,
        len({item.source for item in items if item.source}),
    )


def classify_price_confirmed_sentiment(
    combined_sentiment_score: float | None,
    sentiment_delta_7d: float | None,
    attention_delta_7d: float | None,
    price_return_5d_or_20d: float | None,
    volume_zscore: float | None,
    thresholds: PriceSentimentThresholds | None = None,
) -> str:
    thresholds = thresholds or PriceSentimentThresholds()
    if price_return_5d_or_20d is None:
        return "sentiment_only_unconfirmed"
    price_up = price_return_5d_or_20d >= thresholds.price_up_threshold
    price_down = price_return_5d_or_20d <= -thresholds.price_up_threshold
    volume_up = volume_zscore is not None and volume_zscore >= thresholds.volume_up_threshold
    attention_up = attention_delta_7d is not None and attention_delta_7d >= thresholds.attention_up_threshold
    if combined_sentiment_score is None:
        return "price_only" if price_up else "missing"
    sent_up = combined_sentiment_score >= thresholds.sentiment_up_threshold or (sentiment_delta_7d is not None and sentiment_delta_7d >= thresholds.sentiment_up_threshold)
    sent_down = combined_sentiment_score <= -thresholds.sentiment_up_threshold or (sentiment_delta_7d is not None and sentiment_delta_7d <= -thresholds.sentiment_up_threshold)
    sent_flat = not sent_up and not sent_down
    if sent_up and price_up and volume_up:
        return "confirmed_momentum"
    if sent_up and price_down:
        return "failed_hype_or_distribution"
    if sent_down and price_up:
        return "squeeze_or_positioning"
    if attention_up and price_up and sent_flat:
        return "attention_momentum"
    if attention_up and price_down:
        return "noisy_hype"
    if sent_flat and price_up:
        return "stealth_momentum"
    return "unconfirmed"


def build_sentiment_snapshot(
    ticker: str,
    items: list[RawSentimentItem],
    as_of: datetime | None = None,
    provider: str = "file",
    source_name: str = "",
    lookback_days: int = 30,
    price_context: dict[str, Any] | None = None,
    thresholds: PriceSentimentThresholds | None = None,
) -> SentimentSnapshot:
    as_of = _ensure_aware(as_of or datetime.now(timezone.utc))
    ticker = ticker.upper()
    relevant = [item for item in items if item.ticker.upper() == ticker and item.timestamp <= as_of]
    relevant.sort(key=lambda item: item.timestamp, reverse=True)
    window_start = as_of - timedelta(days=lookback_days)
    window_items = [item for item in relevant if item.timestamp >= window_start]
    price_context = price_context or {}
    created_at = datetime.now(timezone.utc).isoformat()
    if not relevant:
        return SentimentSnapshot(
            ticker=ticker,
            as_of=as_of.isoformat(),
            provider=provider,
            source_type="mock" if provider == "file" else provider,
            source_name=source_name,
            lookback_days=lookback_days,
            news_count_1d=None,
            news_count_7d=None,
            news_count_30d=None,
            social_mentions_1d=None,
            social_mentions_7d=None,
            social_mentions_30d=None,
            mentions_zscore=None,
            attention_delta_1d=None,
            attention_delta_7d=None,
            news_sentiment_score=None,
            social_sentiment_score=None,
            combined_sentiment_score=None,
            sentiment_delta_7d=None,
            positive_ratio=None,
            negative_ratio=None,
            neutral_ratio=None,
            narrative_tags="",
            catalyst_tags="",
            price_return_1d=_maybe_float(price_context.get("price_return_1d")),
            price_return_5d=_maybe_float(price_context.get("price_return_5d")),
            price_return_20d=_maybe_float(price_context.get("price_return_20d")),
            volume_zscore=_maybe_float(price_context.get("volume_zscore")),
            price_confirmed_sentiment=classify_price_confirmed_sentiment(None, None, None, _price_return_for_confirmation(price_context), _maybe_float(price_context.get("volume_zscore")), thresholds),
            coverage_status="missing",
            stale_reason="no sentiment items for ticker",
            raw_source_count=0,
            raw_item_sample="[]",
            created_at=created_at,
        )
    if not window_items:
        coverage_status = "stale"
        stale_reason = f"newest item older than {lookback_days} days"
    else:
        coverage_status = "partial"
        stale_reason = ""
    news_items = [item for item in window_items if item.source_type in {"news", "mixed"}]
    social_items = [item for item in window_items if item.source_type == "social"]
    all_texts = [_item_text(item) for item in window_items]
    news_score = score_sentiment_texts([_item_text(item) for item in news_items])
    social_score = score_sentiment_texts([_item_text(item) for item in social_items])
    combined = score_sentiment_texts(all_texts)
    prior_score = score_sentiment_texts([_item_text(item) for item in relevant if as_of - timedelta(days=14) <= item.timestamp < as_of - timedelta(days=7)])
    sentiment_delta = None
    if combined.score is not None and prior_score.score is not None:
        sentiment_delta = combined.score - prior_score.score
    attention = compute_attention_metrics(window_items, as_of)
    if combined.coverage_status == "available" and attention.mentions_zscore is not None and coverage_status != "stale":
        coverage_status = "available"
    elif combined.coverage_status == "missing" and coverage_status != "stale":
        coverage_status = "partial"
        stale_reason = "items have insufficient text"
    narrative_tags, catalyst_tags = classify_narratives(*all_texts)
    source_type = _snapshot_source_type(window_items, provider)
    price_return = _price_return_for_confirmation(price_context)
    price_label = classify_price_confirmed_sentiment(combined.score, sentiment_delta, attention.attention_delta_7d, price_return, _maybe_float(price_context.get("volume_zscore")), thresholds)
    return SentimentSnapshot(
        ticker=ticker,
        as_of=as_of.isoformat(),
        provider=provider,
        source_type=source_type,
        source_name=source_name,
        lookback_days=lookback_days,
        news_count_1d=_count_between(news_items, as_of - timedelta(days=1), as_of),
        news_count_7d=_count_between(news_items, as_of - timedelta(days=7), as_of),
        news_count_30d=_count_between(news_items, as_of - timedelta(days=30), as_of),
        social_mentions_1d=_count_between(social_items, as_of - timedelta(days=1), as_of),
        social_mentions_7d=_count_between(social_items, as_of - timedelta(days=7), as_of),
        social_mentions_30d=_count_between(social_items, as_of - timedelta(days=30), as_of),
        mentions_zscore=attention.mentions_zscore,
        attention_delta_1d=attention.attention_delta_1d,
        attention_delta_7d=attention.attention_delta_7d,
        news_sentiment_score=news_score.score,
        social_sentiment_score=social_score.score,
        combined_sentiment_score=combined.score,
        sentiment_delta_7d=sentiment_delta,
        positive_ratio=combined.positive_ratio,
        negative_ratio=combined.negative_ratio,
        neutral_ratio=combined.neutral_ratio,
        narrative_tags="|".join(narrative_tags),
        catalyst_tags="|".join(catalyst_tags),
        price_return_1d=_maybe_float(price_context.get("price_return_1d")),
        price_return_5d=_maybe_float(price_context.get("price_return_5d")),
        price_return_20d=_maybe_float(price_context.get("price_return_20d")),
        volume_zscore=_maybe_float(price_context.get("volume_zscore")),
        price_confirmed_sentiment=price_label,
        coverage_status=coverage_status,
        stale_reason=stale_reason,
        raw_source_count=attention.raw_source_count,
        raw_item_sample=_raw_item_sample(window_items),
        created_at=created_at,
    )


def default_pilot_universe(root: Path) -> list[str]:
    tickers = list(DEFAULT_PILOT_TICKERS)
    leaderboard = root / "registry" / "leaderboard.csv"
    if leaderboard.exists():
        with leaderboard.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
                if ticker and ticker not in tickers:
                    tickers.append(ticker)
    return tickers


def run_sentiment_pilot(
    root: Path,
    provider: str = "file",
    input_path: str | Path | None = None,
    tickers: list[str] | None = None,
    max_items: int = 100,
    write: bool = False,
    dry_run: bool = True,
    as_of: datetime | None = None,
    price_context: dict[str, dict[str, Any]] | None = None,
    report_stem: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    as_of = _ensure_aware(as_of or datetime.now(timezone.utc))
    tickers = [ticker.strip().upper() for ticker in (tickers or default_pilot_universe(root)) if ticker.strip()]
    price_context = price_context or {}
    if provider == "file":
        if input_path is None:
            fetch = ProviderFetchResult([], "missing", "file provider requires --input", "missing")
        else:
            fetch = FileSentimentAdapter(input_path).fetch(tickers, as_of=as_of, max_items=max_items)
    elif provider == "apify":
        fetch = ApifySentimentAdapter().fetch(tickers, as_of=as_of, max_items=max_items)
    else:
        fetch = ProviderFetchResult([], "missing", f"unsupported provider: {provider}", "missing")
    snapshots = [
        build_sentiment_snapshot(
            ticker,
            fetch.items,
            as_of=as_of,
            provider=provider,
            source_name=str(input_path or provider),
            price_context=price_context.get(ticker, {}),
        )
        for ticker in tickers
    ]
    candidates = build_sentiment_candidates(snapshots)
    result = {
        "provider_status": fetch.coverage_status,
        "provider_reason": fetch.stale_reason,
        "snapshots": snapshots,
        "candidates": candidates,
        "snapshot_path": root / "registry" / "sentiment_snapshot.csv",
        "candidates_path": root / "registry" / "sentiment_candidates.csv",
    }
    if write and not dry_run:
        write_sentiment_outputs(root, snapshots, candidates, report_stem=report_stem)
    return result


def build_sentiment_candidates(snapshots: list[SentimentSnapshot]) -> list[dict[str, Any]]:
    rows = []
    for snapshot in snapshots:
        coverage_score = {"available": 1.0, "partial": 0.5, "stale": 0.2, "missing": 0.0, "error": 0.0}.get(snapshot.coverage_status, 0.0)
        narrative_score = _narrative_relevance(snapshot.narrative_tags)
        research_score = (
            max(0.0, snapshot.price_return_5d or snapshot.price_return_20d or 0.0) * 4.0
            + max(0.0, snapshot.attention_delta_7d or 0.0) * 0.5
            + max(0.0, snapshot.combined_sentiment_score or 0.0)
            + narrative_score
            + coverage_score
        )
        rows.append(
            {
                "ticker": snapshot.ticker,
                "research_rank": 0,
                "research_score": round(research_score, 6),
                "combined_sentiment_score": snapshot.combined_sentiment_score,
                "sentiment_delta_7d": snapshot.sentiment_delta_7d,
                "attention_delta_7d": snapshot.attention_delta_7d,
                "mentions_zscore": snapshot.mentions_zscore,
                "price_return_5d": snapshot.price_return_5d,
                "price_return_20d": snapshot.price_return_20d,
                "volume_zscore": snapshot.volume_zscore,
                "price_confirmed_sentiment": snapshot.price_confirmed_sentiment,
                "narrative_tags": snapshot.narrative_tags,
                "catalyst_tags": snapshot.catalyst_tags,
                "coverage_status": snapshot.coverage_status,
                "provider": snapshot.provider,
                "source_name": snapshot.source_name,
                "research_only": "true",
                "not_trading_signal": "true",
            }
        )
    rows.sort(key=lambda row: float(row["research_score"]), reverse=True)
    for index, row in enumerate(rows, start=1):
        row["research_rank"] = index
    return rows


def write_sentiment_outputs(root: Path, snapshots: list[SentimentSnapshot], candidates: list[dict[str, Any]], report_stem: str | None = None) -> dict[str, Path]:
    registry = root / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    snapshot_path = registry / "sentiment_snapshot.csv"
    candidates_path = registry / "sentiment_candidates.csv"
    _write_csv(snapshot_path, [snapshot_to_row(snapshot) for snapshot in snapshots], SNAPSHOT_COLUMNS)
    _write_csv(candidates_path, candidates, CANDIDATE_COLUMNS)
    paths = {"snapshot_path": snapshot_path, "candidates_path": candidates_path}
    if report_stem:
        weekly = root / "reports" / "weekly"
        weekly.mkdir(parents=True, exist_ok=True)
        weekly_candidates = weekly / f"{report_stem}_sentiment_candidates.csv"
        narrative_summary = weekly / f"{report_stem}_narrative_summary.md"
        _write_csv(weekly_candidates, candidates, CANDIDATE_COLUMNS)
        narrative_summary.write_text("\n".join(_narrative_summary_lines(candidates)) + "\n", encoding="utf-8")
        paths["weekly_candidates_path"] = weekly_candidates
        paths["narrative_summary_path"] = narrative_summary
    return paths


def summarize_sentiment_for_weekly(root: Path, report_stem: str) -> list[str]:
    path = root / "reports" / "weekly" / f"{report_stem}_sentiment_candidates.csv"
    if not path.exists():
        path = root / "registry" / "sentiment_candidates.csv"
    if not path.exists():
        return ["- sentiment layer not available"]
    rows = _read_csv_rows(path)
    if not rows:
        return ["- sentiment layer not available"]
    top = rows[:5]
    tags = _top_tags(rows)
    return [
        f"- sentiment candidates: {len(rows)}",
        f"- top candidates: {', '.join(row.get('ticker', '') for row in top if row.get('ticker')) or 'not available'}",
        f"- top narrative tags: {', '.join(tags) or 'not available'}",
        "- research_only=true; not_trading_signal=true",
    ]


def snapshot_to_row(snapshot: SentimentSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _read_input_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            items = payload.get("items")
            return items if isinstance(items, list) else [payload]
        return []
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    return []


def _row_to_item(row: dict[str, Any]) -> RawSentimentItem | None:
    ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
    timestamp = _parse_dt(row.get("timestamp") or row.get("published_at") or row.get("date"))
    if not ticker or timestamp is None:
        return None
    return RawSentimentItem(
        ticker=ticker,
        source=str(row.get("source") or row.get("source_name") or ""),
        timestamp=timestamp,
        title=str(row.get("title") or ""),
        text=str(row.get("text") or row.get("description") or row.get("body") or ""),
        url=str(row.get("url") or ""),
        author=str(row.get("author") or ""),
        engagement_score=_maybe_float(row.get("engagement_score")),
        source_type=_normalize_source_type(str(row.get("source_type") or "news")),
    )


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _narrative_summary_lines(candidates: list[dict[str, Any]]) -> list[str]:
    lines = [
        "# Sentiment / Attention Narrative Summary",
        "",
        "- research_only=true",
        "- not_trading_signal=true",
        "",
        "## Top Narrative Tags",
        "",
    ]
    tags = _top_tags(candidates)
    if tags:
        lines.extend(f"- {tag}" for tag in tags)
    else:
        lines.append("- not available")
    lines.extend(["", "## Top Candidates", ""])
    for row in candidates[:10]:
        lines.append(f"- {row.get('ticker')}: {row.get('price_confirmed_sentiment')} | {row.get('narrative_tags')} | coverage={row.get('coverage_status')}")
    return lines


def _top_tags(rows: list[dict[str, Any]]) -> list[str]:
    counter: dict[str, int] = {}
    for row in rows:
        for tag in str(row.get("narrative_tags", "")).split("|"):
            tag = tag.strip()
            if tag:
                counter[tag] = counter.get(tag, 0) + 1
    return [tag for tag, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:8]]


def _match_rules(text: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    tags = []
    for tag, keywords in rules:
        if any(_keyword_present(text, keyword) for keyword in keywords):
            tags.append(tag)
    return tags


def _keyword_count(text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if _keyword_present(text, keyword))


def _keyword_present(text: str, keyword: str) -> bool:
    keyword = _normalize_text(keyword)
    if " " in keyword or "/" in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).lower()).strip()


def _item_text(item: RawSentimentItem) -> str:
    return " ".join(part for part in [item.title, item.text] if part)


def _count_between(items: list[RawSentimentItem], start: datetime, end: datetime) -> int:
    start = _ensure_aware(start)
    end = _ensure_aware(end)
    return sum(1 for item in items if start <= _ensure_aware(item.timestamp) <= end)


def _snapshot_source_type(items: list[RawSentimentItem], provider: str) -> str:
    if provider == "file":
        return "mock"
    types = {item.source_type for item in items}
    if len(types) > 1:
        return "mixed"
    return next(iter(types), "mixed")


def _raw_item_sample(items: list[RawSentimentItem]) -> str:
    sample = []
    for item in items[:3]:
        sample.append(
            {
                "ticker": item.ticker,
                "source": _safe_text(item.source, 80),
                "timestamp": item.timestamp.isoformat(),
                "title": _safe_text(item.title, 160),
                "url": _safe_text(item.url, 160),
                "source_type": item.source_type,
            }
        )
    return json.dumps(sample, ensure_ascii=False)


def _safe_text(value: str, limit: int) -> str:
    text = re.sub(r"(?i)(token|api_key|secret|password)=([^&\s]+)", r"\1=[redacted]", str(value))
    return text[:limit]


def _narrative_relevance(tags: str) -> float:
    high_relevance = {"AI infrastructure", "GPU cloud", "power capacity", "bitcoin mining", "crypto beta", "data center", "nuclear / SMR"}
    return min(1.0, 0.25 * sum(1 for tag in tags.split("|") if tag in high_relevance))


def _price_return_for_confirmation(price_context: dict[str, Any]) -> float | None:
    return _maybe_float(price_context.get("price_return_5d", price_context.get("price_return_20d")))


def _normalize_source_type(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"news", "social", "mixed", "mock"}:
        return lowered
    return "mixed"


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _ensure_aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _maybe_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)
    return value


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None
