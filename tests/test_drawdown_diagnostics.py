from research_lab.drawdown_diagnostics import compute_drawdown_diagnostics
from research_lab.reports import write_daily_report


def _equity(values, start="2020-01-01"):
    start_year, start_month, start_day = (int(part) for part in start.split("-"))
    return [
        {"date": f"{start_year:04d}-{start_month:02d}-{start_day + offset:02d}", "value": value}
        for offset, value in enumerate(values)
    ]


def test_drawdown_diagnostics_identifies_recovered_drawdown():
    diagnostics = compute_drawdown_diagnostics(
        _equity([100.0, 120.0, 90.0, 100.0, 121.0]),
        cagr=0.10,
    )

    assert diagnostics["worst_drawdown_start"] == "2020-01-02"
    assert diagnostics["worst_drawdown_trough"] == "2020-01-03"
    assert diagnostics["worst_drawdown_recovery"] == "2020-01-05"
    assert diagnostics["drawdown_duration_days"] == 3
    assert diagnostics["max_drawdown"] == -0.25
    assert diagnostics["cagr_to_drawdown_ratio"] == 0.4


def test_drawdown_diagnostics_identifies_unrecovered_drawdown():
    diagnostics = compute_drawdown_diagnostics(
        _equity([100.0, 130.0, 100.0, 110.0]),
        cagr=0.05,
    )

    assert diagnostics["worst_drawdown_start"] == "2020-01-02"
    assert diagnostics["worst_drawdown_trough"] == "2020-01-03"
    assert diagnostics["worst_drawdown_recovery"] == ""
    assert diagnostics["drawdown_duration_days"] == 2
    assert diagnostics["max_drawdown"] == -0.23076923076923073
    assert diagnostics["cagr_to_drawdown_ratio"] == 0.2166666666666667


def test_drawdown_diagnostics_handles_flat_equity_curve():
    diagnostics = compute_drawdown_diagnostics(_equity([1.0, 1.0, 1.0]), cagr=0.0)

    assert diagnostics == {
        "worst_drawdown_start": "",
        "worst_drawdown_trough": "",
        "worst_drawdown_recovery": "",
        "drawdown_duration_days": 0,
        "max_drawdown": 0.0,
        "worst_year_return": 0.0,
        "best_year_return": 0.0,
        "cagr_to_drawdown_ratio": 0.0,
    }


def test_drawdown_diagnostics_handles_monotonic_rising_equity_curve():
    diagnostics = compute_drawdown_diagnostics(_equity([1.0, 1.1, 1.2]), cagr=0.12)

    assert diagnostics["worst_drawdown_start"] == ""
    assert diagnostics["worst_drawdown_trough"] == ""
    assert diagnostics["worst_drawdown_recovery"] == ""
    assert diagnostics["drawdown_duration_days"] == 0
    assert diagnostics["max_drawdown"] == 0.0
    assert diagnostics["cagr_to_drawdown_ratio"] == 0.0


def test_drawdown_diagnostics_computes_best_and_worst_calendar_year_return():
    equity = [
        ("2020-01-01", 100.0),
        ("2020-12-31", 110.0),
        ("2021-01-01", 99.0),
        ("2021-12-31", 118.8),
    ]

    diagnostics = compute_drawdown_diagnostics(equity, cagr=0.08)

    assert diagnostics["worst_year_return"] == 0.10
    assert diagnostics["best_year_return"] == 0.20


def test_daily_report_includes_drawdown_attribution_diagnostics(tmp_path):
    result = {
        "strategy_id": "S1",
        "family": "LONGTERM",
        "asset_class": "ETF",
        "timeframe": "1D",
        "data_manifest": {
            "source": "eodhd",
            "start": "2020-01-01",
            "end": "2021-12-31",
            "rows": 4,
            "years": 2.0,
        },
        "split_metrics": {
            "train": {"cagr": 0.01},
            "validation": {"cagr": 0.02},
            "unseen": {
                "cagr": 0.03,
                "sharpe": 0.2,
                "mar": 0.1,
                "max_drawdown": -0.20,
                "profit_factor": 1.1,
                "trade_count": 10,
            },
        },
        "drawdown_diagnostics": {
            "worst_drawdown_start": "2020-02-01",
            "worst_drawdown_trough": "2020-03-01",
            "worst_drawdown_recovery": "",
            "drawdown_duration_days": 700,
            "max_drawdown": -0.20,
            "worst_year_return": -0.12,
            "best_year_return": 0.08,
            "cagr_to_drawdown_ratio": 0.15,
        },
        "cost_stress": {"survives_double_cost": True},
        "tier": "Rejected",
        "tier_reason": "Unseen max drawdown exceeds 15%.",
    }
    path = tmp_path / "daily.md"

    write_daily_report(path, [result])

    report = path.read_text(encoding="utf-8")
    assert "## Drawdown Diagnostics" in report
    assert (
        "| S1 | 2020-02-01 | 2020-03-01 | unrecovered | 700 | -20.00% | -12.00% | 8.00% | 0.15 |"
        in report
    )
    assert "worst_drawdown_start=2020-02-01" in report
