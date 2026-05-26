import pandas as pd

from research_lab.event_study import compute_event_windows, run_event_window_study


def test_compute_event_windows_uses_future_returns_only_and_lags():
    close = pd.DataFrame(
        {"SPY": [100.0, 101.0, 105.0, 103.0, 110.0, 112.0, 113.0]},
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]),
    )
    events = [
        {
            "event_id": "E1",
            "ticker": "SPY",
            "event_date": "2026-01-02",
            "disclosure_date": "2026-01-05",
            "observed_date": "2026-01-06",
            "event_source": "13f",
        }
    ]

    rows = compute_event_windows(events, close, windows=[1, 5])

    assert rows[0]["return_1d"] == (105.0 / 101.0) - 1.0
    assert rows[0]["return_5d"] == (113.0 / 101.0) - 1.0
    assert rows[0]["disclosure_lag_days"] == 3
    assert rows[0]["observed_lag_days"] == 4
    assert rows[0]["no_lookahead"] is True
    assert rows[0]["data_complete"] is True


def test_run_event_window_study_writes_empty_research_report(tmp_path):
    result = run_event_window_study(tmp_path, "2026-W21")

    assert result["csv_path"].exists()
    assert result["report_path"].exists()
    assert result["rows"] == []
