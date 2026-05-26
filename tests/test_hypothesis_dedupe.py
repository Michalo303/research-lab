import json

from research_lab.hypothesis_dedupe import audit_hypothesis_queue, dedupe_hypotheses, hypothesis_fingerprint


def test_hypothesis_fingerprint_dedupes_smartmoney_ticker_score_changes():
    first = {
        "family": "SWING",
        "ticker": "AAPL",
        "title": "Smart-money accumulation pullback",
        "source_key": "smartmoney:AAPL:70:80",
        "tags": ["smart_money", "13f"],
    }
    second = {
        "family": "SWING",
        "ticker": "AAPL",
        "title": "Smart-money accumulation pullback",
        "source_key": "smartmoney:AAPL:75:82",
        "tags": ["smart_money", "13f"],
    }

    assert hypothesis_fingerprint(first) == hypothesis_fingerprint(second)


def test_dedupe_hypotheses_keeps_first_duplicate_family_source_edge():
    items = [
        {
            "hypothesis_id": "A",
            "family": "ROTATION",
            "title": "Regime-filtered momentum rotation",
            "source_title": "Seed: momentum with volatility targeting",
            "tags": ["momentum"],
        },
        {
            "hypothesis_id": "B",
            "family": "ROTATION",
            "title": "Regime-filtered momentum rotation",
            "source_title": "Seed: momentum with volatility targeting",
            "tags": ["momentum"],
        },
    ]

    kept, duplicates = dedupe_hypotheses(items)

    assert [item["hypothesis_id"] for item in kept] == ["A"]
    assert [item["hypothesis_id"] for item in duplicates] == ["B"]


def test_apply_dedupe_archives_queue_with_timestamp_before_rewrite(tmp_path, monkeypatch):
    queue = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text(
        json.dumps({"hypothesis_id": "A", "family": "ROTATION", "title": "Regime-filtered momentum rotation"}) + "\n"
        + json.dumps({"hypothesis_id": "B", "family": "ROTATION", "title": "Regime-filtered momentum rotation"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("research_lab.hypothesis_dedupe._archive_stamp", lambda: "20260524T120000Z")

    result = audit_hypothesis_queue(tmp_path, apply=True)

    archive_path = result["archive_path"]
    assert result["applied"] is True
    assert result["total"] == 2
    assert result["kept"] == 1
    assert result["duplicates"] == 1
    assert archive_path.name == "hypothesis_queue.20260524T120000Z.before_dedupe.jsonl"
    assert archive_path.exists()
    assert "hypothesis_id" in archive_path.read_text(encoding="utf-8")
    assert len(queue.read_text(encoding="utf-8").splitlines()) == 1
    report = result["report_path"].read_text(encoding="utf-8")
    assert "archive_path:" in report
