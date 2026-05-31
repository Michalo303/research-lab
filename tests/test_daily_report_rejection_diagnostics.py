from research_lab.reports import write_daily_report


def test_daily_report_includes_rejection_diagnostics(tmp_path):
    result = {
        "strategy_id": "SWING_ETF_1D_RSI_PULLBACK_TEST",
        "family": "SWING",
        "asset_class": "ETF",
        "timeframe": "1D",
        "data_manifest": {
            "source": "eodhd",
            "start": "1993-01-29",
            "end": "2026-05-29",
            "rows": 8390,
            "years": 33.3,
        },
        "split_metrics": {
            "train": {"cagr": 0.01},
            "validation": {"cagr": 0.02},
            "unseen": {
                "cagr": -0.001292,
                "sharpe": -0.1,
                "mar": -0.01,
                "max_drawdown": -0.1048,
                "profit_factor": 0.99,
                "trade_count": 23,
            },
        },
        "cost_stress": {
            "normal_cost_bps": 5.0,
            "double_cost_bps": 10.0,
            "survives_double_cost": False,
            "double_unseen_cagr": -0.0041,
        },
        "tier": "Rejected",
        "tier_reason": "Negative unseen result.",
    }
    path = tmp_path / "daily.md"

    write_daily_report(path, [result])

    report = path.read_text(encoding="utf-8")
    assert "## Rejection Diagnostics" in report
    assert (
        "| SWING_ETF_1D_RSI_PULLBACK_TEST | Negative unseen result. | "
        "Too few unseen trades for a trade-based strategy.; Double transaction-cost stress destroys unseen profitability. | "
        "unseen_cagr | -0.13% | > 0.00% |"
    ) in report
