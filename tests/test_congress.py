import json

from research_lab.congress import import_congress_disclosures, normalize_congress_event


def test_normalize_congress_event_marks_research_only_source():
    row = normalize_congress_event(
        {
            "representative": "Jane Doe",
            "ticker": "AAPL",
            "transaction_type": "Purchase",
            "amount_range": "$1,001 - $15,000",
            "transaction_date": "2026-01-02",
            "disclosure_date": "2026-01-20",
            "source_url": "https://example.test/report",
        }
    )

    assert row["representative"] == "Jane Doe"
    assert row["ticker"] == "AAPL"
    assert row["research_only"] is True
    assert row["event_source_only"] is True
    assert row["not_trading_signal"] is True
    assert row["disclosure_lag_days"] == 18
    assert row["amount_range_valid"] is True


def test_import_congress_disclosures_reports_quality(tmp_path):
    source = tmp_path / "data" / "raw" / "congress_sample.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            [
                {
                    "representative": "Jane Doe",
                    "ticker": "AAPL",
                    "transaction_type": "Purchase",
                    "amount_range": "$1,001 - $15,000",
                    "transaction_date": "2026-01-02",
                    "disclosure_date": "2026-01-20",
                    "source_url": "https://example.test/report",
                },
                {
                    "representative": "Jane Doe",
                    "ticker": "AAPL",
                    "transaction_type": "Purchase",
                    "amount_range": "unknown",
                    "transaction_date": "",
                    "disclosure_date": "2026-01-20",
                    "source_url": "https://example.test/report",
                },
            ]
        ),
        encoding="utf-8",
    )

    result = import_congress_disclosures(tmp_path, source, "2026-W21")

    assert result["events_path"].exists()
    assert result["quality_path"].exists()
    assert result["summary"]["event_count"] == 2
    assert result["summary"]["missing_dates"] == 1
    assert result["summary"]["malformed_amount_ranges"] == 1
    assert result["summary"]["duplicate_events"] == 0
