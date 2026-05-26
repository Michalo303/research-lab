import pandas as pd

from research_lab.data_quality import audit_ohlcv_panel, run_data_quality_audit


def test_audit_ohlcv_panel_detects_core_quality_issues():
    dates = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-05", "2026-01-08"])
    panel = pd.DataFrame(
        {
            ("SPY", "open"): [100.0, 101.0, 101.0, 0.0],
            ("SPY", "high"): [101.0, 102.0, 102.0, 1.0],
            ("SPY", "low"): [99.0, 100.0, 100.0, -1.0],
            ("SPY", "close"): [100.0, 101.0, 202.0, 1.0],
            ("SPY", "volume"): [1000.0, 1000.0, 1000.0, 0.0],
        },
        index=dates,
    )
    manifest = {"source": "massive", "adjusted": True, "symbols": ["SPY"]}

    rows = audit_ohlcv_panel(panel, manifest, required_symbols=["SPY", "QQQ"])

    checks = {row["check"]: row for row in rows}
    assert checks["duplicate_dates"]["status"] == "fail"
    assert checks["missing_bars"]["status"] == "fail"
    assert checks["zero_or_negative_prices"]["status"] == "fail"
    assert checks["zero_or_negative_volume"]["status"] == "fail"
    assert checks["extreme_returns"]["status"] == "fail"
    assert checks["symbol_coverage"]["status"] == "fail"
    assert checks["adjustment_assumption"]["status"] == "pass"


def test_run_data_quality_audit_writes_registry_and_report(tmp_path):
    processed = tmp_path / "data" / "processed"
    manifests = tmp_path / "data" / "manifests"
    processed.mkdir(parents=True)
    manifests.mkdir(parents=True)
    pd.DataFrame(
        {
            "SPY.open": [100.0, 101.0],
            "SPY.high": [101.0, 102.0],
            "SPY.low": [99.0, 100.0],
            "SPY.close": [100.0, 101.0],
            "SPY.volume": [1000.0, 1000.0],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    ).to_csv(processed / "massive_daily_universe.csv")
    (manifests / "daily_universe.json").write_text('{"source":"massive","adjusted":true,"symbols":["SPY"]}', encoding="utf-8")

    result = run_data_quality_audit(tmp_path, "2026-W21")

    assert result["csv_path"].exists()
    assert result["report_path"].exists()
    assert any(row["check"] == "adjustment_assumption" for row in result["rows"])
