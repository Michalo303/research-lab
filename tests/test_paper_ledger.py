import json

from research_lab.paper_ledger import append_daily_paper_ledger


def _ledger_payload(equity, daily_pnl):
    return {
        "date": "2026-05-24",
        "cash": 50000.0,
        "positions": [{"symbol": "SPY", "notional": 25000.0}],
        "target_weights": {"SPY": 0.25},
        "equity": equity,
        "daily_pnl": daily_pnl,
        "cumulative_pnl": equity - 100000.0,
        "gross_exposure": 0.25,
        "net_exposure": 0.25,
        "source_strategy_ids": ["S1"],
        "latest_signals": [{"strategy_id": "S1", "action": "hold"}],
        "data_source": "massive",
    }


def test_append_daily_paper_ledger_is_append_only_jsonl(tmp_path):
    first = append_daily_paper_ledger(tmp_path, _ledger_payload(101000.0, 1000.0))
    second = append_daily_paper_ledger(tmp_path, _ledger_payload(102000.0, 1000.0))

    assert first == second
    rows = [json.loads(line) for line in first.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["equity"] == 101000.0
    assert rows[1]["equity"] == 102000.0
    assert rows[0]["research_only"] is True
    assert rows[0]["positions"] == [{"symbol": "SPY", "notional": 25000.0}]
