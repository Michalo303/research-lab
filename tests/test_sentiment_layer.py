from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone

from research_lab.sentiment import (
    ApifySentimentAdapter,
    FileSentimentAdapter,
    PriceSentimentThresholds,
    RawSentimentItem,
    build_sentiment_snapshot,
    classify_narratives,
    classify_price_confirmed_sentiment,
    compute_attention_metrics,
    default_pilot_universe,
    run_sentiment_pilot,
    score_sentiment_texts,
)


AS_OF = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)


def item(ticker: str, days_ago: int, title: str, text: str = "", source_type: str = "news", source: str = "fixture") -> RawSentimentItem:
    return RawSentimentItem(
        ticker=ticker,
        source=source,
        timestamp=AS_OF - timedelta(days=days_ago),
        title=title,
        text=text,
        url="https://example.com/item",
        author="tester",
        engagement_score=1.0,
        source_type=source_type,
    )


def test_schema_marks_missing_data_without_fake_zeroes():
    snapshot = build_sentiment_snapshot("IREN", [], as_of=AS_OF, provider="file", source_name="empty")

    assert snapshot.coverage_status == "missing"
    assert snapshot.news_count_1d is None
    assert snapshot.social_mentions_7d is None
    assert snapshot.combined_sentiment_score is None
    assert snapshot.mentions_zscore is None
    assert snapshot.raw_source_count == 0


def test_schema_marks_partial_when_text_exists_but_history_is_incomplete():
    snapshot = build_sentiment_snapshot(
        "IREN",
        [item("IREN", 1, "IREN raises guidance on AI cloud demand", "record revenue and capacity expansion")],
        as_of=AS_OF,
        provider="file",
        source_name="fixture",
    )

    assert snapshot.coverage_status == "partial"
    assert snapshot.news_count_7d == 1
    assert snapshot.mentions_zscore is None
    assert snapshot.combined_sentiment_score is not None


def test_narrative_classifier_detects_core_narratives_and_catalysts():
    tags, catalysts = classify_narratives("IREN expands AI cloud GPU data center capacity with NVIDIA systems")
    assert {"AI infrastructure", "GPU cloud", "data center"}.issubset(set(tags))

    tags, catalysts = classify_narratives("Bitcoin miner reports hashrate growth as BTC miner demand rises")
    assert {"bitcoin mining", "crypto beta"}.issubset(set(tags))

    tags, catalysts = classify_narratives("Company launches ATM offering and share issuance")
    assert "offering / dilution" in catalysts

    tags, catalysts = classify_narratives("Analyst upgrade after price target raised")
    assert "analyst upgrade" in catalysts

    tags, catalysts = classify_narratives("Short report alleges fraud and regulatory violations")
    assert "short report" in catalysts
    assert "regulatory risk" in catalysts


def test_sentiment_scoring_is_rule_based_and_missing_text_is_not_neutral():
    positive = score_sentiment_texts(["beat raised guidance contract partnership AI demand"])
    negative = score_sentiment_texts(["miss cut guidance downgrade offering dilution lawsuit"])
    mixed = score_sentiment_texts(["upgrade and contract but also dilution and delayed"])
    missing = score_sentiment_texts(["", "   "])

    assert positive.coverage_status == "available"
    assert positive.score > 0
    assert negative.score < 0
    assert abs(mixed.score) <= 0.25
    assert mixed.coverage_status == "available"
    assert missing.coverage_status == "missing"
    assert missing.score is None


def test_attention_metrics_counts_delta_and_zscore_only_with_baseline():
    items = [
        item("IREN", 0, "today 1"),
        item("IREN", 2, "this week 1"),
        item("IREN", 6, "this week 2"),
        item("IREN", 8, "prior week 1"),
        item("IREN", 10, "prior week 2"),
        item("IREN", 21, "baseline 1"),
        item("IREN", 25, "baseline 2"),
    ]

    metrics = compute_attention_metrics(items, AS_OF)

    assert metrics.mentions_1d == 1
    assert metrics.mentions_7d == 3
    assert metrics.mentions_30d == 7
    assert metrics.attention_delta_7d == 1
    assert metrics.mentions_zscore is not None
    assert compute_attention_metrics(items[:3], AS_OF).mentions_zscore is None


def test_price_confirmed_sentiment_rules():
    thresholds = PriceSentimentThresholds(
        sentiment_up_threshold=0.2,
        attention_up_threshold=1.0,
        price_up_threshold=0.03,
        volume_up_threshold=1.0,
    )

    assert classify_price_confirmed_sentiment(0.5, 0.2, 2.0, 0.04, 1.5, thresholds) == "confirmed_momentum"
    assert classify_price_confirmed_sentiment(0.5, 0.2, 2.0, -0.04, 1.5, thresholds) == "failed_hype_or_distribution"
    assert classify_price_confirmed_sentiment(-0.5, -0.2, 0.5, 0.04, 0.2, thresholds) == "squeeze_or_positioning"
    assert classify_price_confirmed_sentiment(0.0, 0.0, 0.2, 0.04, 0.2, thresholds) == "stealth_momentum"
    assert classify_price_confirmed_sentiment(None, None, 0.0, 0.04, 0.2, thresholds) == "price_only"
    assert classify_price_confirmed_sentiment(0.4, 0.1, 0.5, None, None, thresholds) == "sentiment_only_unconfirmed"


def test_file_adapter_parses_jsonl_fixture():
    adapter = FileSentimentAdapter("tests/fixtures/sentiment_sample.jsonl")
    result = adapter.fetch(["IREN", "WULF"], as_of=AS_OF, max_items=10)

    assert result.coverage_status == "available"
    assert len(result.items) == 3
    assert {row.ticker for row in result.items} == {"IREN", "WULF"}


def test_apify_adapter_missing_configuration_is_controlled(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    adapter = ApifySentimentAdapter(actor_id="actor/news")
    result = adapter.fetch(["IREN"], as_of=AS_OF, max_items=10)

    assert result.coverage_status == "missing"
    assert result.source_coverage_status == "missing"
    assert "APIFY_TOKEN" in result.stale_reason

    monkeypatch.setenv("APIFY_TOKEN", "token-for-test")
    adapter = ApifySentimentAdapter(actor_id=None)
    result = adapter.fetch(["IREN"], as_of=AS_OF, max_items=10)

    assert result.coverage_status == "missing"
    assert "actor id" in result.stale_reason.lower()


def test_outputs_write_snapshot_and_research_only_candidates(tmp_path):
    result = run_sentiment_pilot(
        root=tmp_path,
        provider="file",
        input_path="tests/fixtures/sentiment_sample.jsonl",
        tickers=["IREN", "WULF", "NBIS"],
        max_items=20,
        write=True,
        dry_run=False,
        as_of=AS_OF,
        price_context={
            "IREN": {"price_return_5d": 0.12, "price_return_20d": 0.25, "volume_zscore": 2.0},
            "WULF": {"price_return_5d": 0.04, "volume_zscore": 1.2},
            "NBIS": {"price_return_5d": -0.03, "volume_zscore": 1.0},
        },
        report_stem="2026-W21",
    )

    snapshot_path = tmp_path / "registry" / "sentiment_snapshot.csv"
    candidates_path = tmp_path / "registry" / "sentiment_candidates.csv"
    weekly_candidates_path = tmp_path / "reports" / "weekly" / "2026-W21_sentiment_candidates.csv"
    summary_path = tmp_path / "reports" / "weekly" / "2026-W21_narrative_summary.md"

    assert result["snapshot_path"] == snapshot_path
    assert snapshot_path.exists()
    assert candidates_path.exists()
    assert weekly_candidates_path.exists()
    assert summary_path.exists()

    rows = list(csv.DictReader(candidates_path.open(newline="", encoding="utf-8")))
    assert rows[0]["research_only"] == "true"
    assert rows[0]["not_trading_signal"] == "true"
    assert "IREN" in {row["ticker"] for row in rows}


def test_default_pilot_universe_includes_attention_names_and_leaderboard(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "leaderboard.csv").write_text("ticker,strategy_id\nAPP,S1\nVRT,S2\n", encoding="utf-8")

    universe = default_pilot_universe(tmp_path)

    assert {"IREN", "CRWV", "NBIS", "WULF", "VRT", "CEG", "OKLO", "SMR", "AI", "NVDA", "PLTR", "SOUN", "APP"}.issubset(set(universe))
