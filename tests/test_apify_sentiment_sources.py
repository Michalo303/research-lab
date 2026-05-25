from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from research_lab.sentiment import (
    APIFY_PILOT_UNIVERSE,
    normalize_apify_payload,
    run_apify_source_pilot,
    write_apify_raw_sample,
    write_outputs,
)


FIXTURES = Path("tests/fixtures")


def load_fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_normalizes_reddit_fixture_to_sentiment_items():
    items = normalize_apify_payload("reddit", load_fixture("apify_reddit_raw.json"))

    assert len(items) == 2
    assert items[0]["ticker"] == "IREN"
    assert items[0]["source"] == "reddit"
    assert items[0]["source_type"] == "social"
    assert items[0]["timestamp"] == "2026-05-25T09:30:00Z"
    assert "GPU capacity" in items[0]["title"]
    assert items[0]["engagement_score"] == 226


def test_normalizes_stocktwits_primary_and_fallback_fixtures():
    primary = normalize_apify_payload("stocktwits", load_fixture("apify_stocktwits_saswave_raw.json"))
    fallback = normalize_apify_payload("stocktwits", load_fixture("apify_stocktwits_shahidirfan_raw.json"))

    assert {item["ticker"] for item in primary} == {"NBIS", "OKLO"}
    assert all(item["source"] == "stocktwits" for item in primary)
    assert fallback[0]["ticker"] == "SMR"
    assert fallback[0]["author"] == "alt_energy"


def test_normalizes_google_news_fixture_to_news_items():
    items = normalize_apify_payload("news", load_fixture("apify_google_news_raw.json"))

    assert {item["ticker"] for item in items} == {"CRWV", "CEG"}
    assert all(item["source_type"] == "news" for item in items)
    assert items[0]["source"] == "google_news"
    assert "AI cloud" in items[0]["title"]


def test_apify_source_pilot_uses_fixtures_without_live_call(monkeypatch):
    def fail_live_call(*args, **kwargs):
        raise AssertionError("live Apify call should not run without explicit flag")

    monkeypatch.setattr("research_lab.sentiment._run_apify_actor", fail_live_call)
    result = run_apify_source_pilot(
        source="reddit",
        tickers=["IREN", "WULF"],
        fixture_path=FIXTURES / "apify_reddit_raw.json",
        live=False,
        max_items=50,
        max_cost_usd=1.0,
    )

    assert result["coverage_status"] == "available"
    assert result["source_coverage_status"]["reddit"] == "available"
    assert len(result["items"]) == 2
    assert result["actor_id"] == "logiover/reddit-search-scraper"


def test_live_apify_requires_explicit_flag_token_and_actor(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_REDDIT_ACTOR_ID", raising=False)

    not_live = run_apify_source_pilot(source="reddit", tickers=["IREN"], live=False)
    assert not_live["coverage_status"] == "missing"
    assert "live Apify disabled" in not_live["reason"]

    live_missing_token = run_apify_source_pilot(source="reddit", tickers=["IREN"], live=True)
    assert live_missing_token["coverage_status"] == "missing"
    assert "APIFY_TOKEN" in live_missing_token["reason"]

    monkeypatch.setenv("APIFY_TOKEN", "token-for-test")
    live_missing_actor = run_apify_source_pilot(source="reddit", tickers=["IREN"], live=True)
    assert live_missing_actor["coverage_status"] == "missing"
    assert "actor" in live_missing_actor["reason"].lower()


def test_raw_sample_and_normalized_outputs_are_written(tmp_path):
    raw = load_fixture("apify_google_news_raw.json")
    raw_path = write_apify_raw_sample(tmp_path, "news", raw, created_at=datetime(2026, 5, 25, tzinfo=timezone.utc))
    assert raw_path.exists()
    assert "APIFY_TOKEN" not in raw_path.read_text(encoding="utf-8")

    items = normalize_apify_payload("news", raw)
    from research_lab.sentiment import build_snapshots

    snapshots = build_snapshots(items, as_of=datetime(2026, 5, 25, tzinfo=timezone.utc))
    output = write_outputs(tmp_path, snapshots, report_stem="2026-W21")

    assert Path(output["snapshot_path"]).exists()
    assert Path(output["candidates_path"]).exists()
    assert (tmp_path / "reports" / "weekly" / "2026-W21_sentiment_candidates.csv").exists()
    assert (tmp_path / "reports" / "weekly" / "2026-W21_sentiment_source_coverage.csv").exists()
    assert (tmp_path / "reports" / "weekly" / "2026-W21_sentiment_sources.md").exists()
    assert {row["ticker"] for row in snapshots} == {"CRWV", "CEG"}


def test_pilot_universe_is_bounded_to_requested_names():
    assert APIFY_PILOT_UNIVERSE == ["IREN", "CRWV", "NBIS", "WULF", "VRT", "CEG", "OKLO", "SMR"]


def test_cli_fixture_mode_writes_raw_and_normalized_outputs(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_sentiment_pilot.py",
            "--provider",
            "apify",
            "--source",
            "reddit",
            "--fixture",
            "tests/fixtures/apify_reddit_raw.json",
            "--tickers",
            "IREN,WULF",
            "--max-items",
            "50",
            "--write",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (tmp_path / "registry" / "sentiment_snapshot.csv").exists()
    assert (tmp_path / "registry" / "sentiment_candidates.csv").exists()
    assert (tmp_path / "registry" / "sentiment_source_coverage.csv").exists()
    assert list((tmp_path / "registry" / "sentiment_raw_samples").glob("reddit_*.json"))
