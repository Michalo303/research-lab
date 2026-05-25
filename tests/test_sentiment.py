from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

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
    result = run_apify_scaffold(max_items=9999, max_cost_usd=0.0)
    assert result["coverage_status"] == "missing"
    assert result["max_items"] == 500
    assert result["max_cost_usd"] == 0.1


def test_sentiment_cli_apify_scaffold_runs_without_package_install(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_SENTIMENT_ACTOR_ID", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_sentiment_pilot.py",
            "--provider",
            "apify",
            "--max-items",
            "9999",
            "--max-cost-usd",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "APIFY_TOKEN missing" in result.stdout
    assert "'max_items': 500" in result.stdout
