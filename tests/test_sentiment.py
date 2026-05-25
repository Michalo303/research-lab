from datetime import datetime, timezone
from pathlib import Path

from research_lab.sentiment import (
    SentimentThresholds,
    build_snapshots,
    classify_price_confirmed,
    classify_tags,
    load_file_items,
    run_apify_scaffold,
    score_texts,
    write_outputs,
)


def test_score_no_text_is_missing_not_fake_zero():
    result = score_texts([])
    assert result["coverage"] == "missing"
    assert result["score"] is None


def test_narrative_and_catalyst_rules():
    n, c = classify_tags("IREN AI cloud GPU data center capacity with analyst upgrade")
    assert "AI infrastructure" in n
    assert "GPU cloud" in n
    assert "data center" in n
    assert "analyst upgrade" in c


def test_bitcoin_and_offering_short_report_rules():
    n, c = classify_tags("BTC miner hashrate jumps after offering and short report fraud allegations")
    assert "bitcoin mining" in n
    assert "crypto beta" in n
    assert "offering / dilution" in c
    assert "short report" in c


def test_price_confirmed_classifier_rules():
    t = SentimentThresholds()
    assert classify_price_confirmed(0.6, 0.4, 0.05, 2.0, t) == "confirmed_momentum"
    assert classify_price_confirmed(0.6, 0.4, -0.03, 0.0, t) == "failed_hype_or_distribution"
    assert classify_price_confirmed(-0.6, 0.0, 0.05, 0.0, t) == "squeeze_or_positioning"
    assert classify_price_confirmed(0.0, 0.0, 0.04, 0.0, t) == "stealth_momentum"
    assert classify_price_confirmed(None, 0.0, 0.04, 0.0, t) == "price_only"


def test_file_adapter_and_output_write(tmp_path: Path):
    fixture = Path("tests/fixtures/sentiment_sample.jsonl")
    items = load_file_items(fixture)
    as_of = datetime(2026, 5, 25, tzinfo=timezone.utc)
    snapshots = build_snapshots(items, as_of=as_of)
    assert snapshots
    assert all("coverage_status" in row for row in snapshots)
    assert all(row["research_only"] is True for row in snapshots)
    output = write_outputs(tmp_path, snapshots, report_stem="2026-W21")
    assert Path(output["snapshot_path"]).exists()
    assert Path(output["candidates_path"]).exists()
    assert (tmp_path / "reports" / "weekly" / "2026-W21_sentiment_candidates.csv").exists()


def test_apify_scaffold_missing_token(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_SENTIMENT_ACTOR_ID", raising=False)
    result = run_apify_scaffold()
    assert result["coverage_status"] == "missing"
