import json

from execution.ibkr.reconciliation import reconcile_paper_ledger_to_ibkr


def test_reconciliation_detects_match_missing_extra_and_diff(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "paper_ledger_daily.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "date": "2026-05-24",
                        "equity": 100000.0,
                        "positions": [
                            {"symbol": "SPY", "quantity": 10, "notional": 5000.0},
                            {"symbol": "QQQ", "quantity": 5, "notional": 2000.0},
                            {"symbol": "TLT", "quantity": 3, "notional": 300.0},
                        ],
                    }
                )
            ]
        ),
        encoding="utf-8",
    )
    snapshot = {
        "positions": [
            {"symbol": "SPY", "position": 10, "avg_cost": 500.0},
            {"symbol": "TLT", "position": 1, "avg_cost": 100.0},
            {"symbol": "GLD", "position": 2, "avg_cost": 200.0},
        ]
    }

    result = reconcile_paper_ledger_to_ibkr(tmp_path, snapshot, as_of="2026-05-24")

    verdicts = {row["symbol"]: row["verdict"] for row in result["rows"]}
    assert verdicts == {"GLD": "extra", "QQQ": "missing", "SPY": "match", "TLT": "diff"}
    tlt = next(row for row in result["rows"] if row["symbol"] == "TLT")
    assert tlt["target_quantity"] == 3.0
    assert tlt["ibkr_quantity"] == 1.0
    assert tlt["notional_diff"] == -200.0
    assert (tmp_path / "reports" / "execution" / "ibkr_reconciliation_2026-05-24.csv").exists()
    assert (tmp_path / "reports" / "execution" / "ibkr_reconciliation_2026-05-24.json").exists()
