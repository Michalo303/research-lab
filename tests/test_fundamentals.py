import json

from research_lab.fundamentals import enrich_smartmoney_fundamentals, fundamental_coverage_rows


def test_fundamental_coverage_missing_does_not_create_fake_values():
    rows = fundamental_coverage_rows([{"ticker": "SPY", "family": "SWING"}], fundamentals_by_ticker={})

    row = rows[0]
    assert row["ticker"] == "SPY"
    assert row["coverage_status"] == "missing"
    assert row["valuation"] == {}
    assert row["quality"] == {}
    assert row["debt"] == {}
    assert row["growth"] == {}


def test_enrich_smartmoney_fundamentals_writes_missing_coverage(tmp_path):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text(json.dumps({"ticker": "SPY", "tags": ["smart_money"], "family": "SWING"}) + "\n", encoding="utf-8")

    result = enrich_smartmoney_fundamentals(tmp_path, "2026-W21")

    assert result["csv_path"].exists()
    assert result["report_path"].exists()
    assert result["rows"][0]["coverage_status"] == "missing"
    assert result["rows"][0]["valuation"] == {}
