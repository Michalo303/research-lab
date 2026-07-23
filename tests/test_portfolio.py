from research_lab import portfolio
from research_lab.portfolio import (
    _portfolio_score,
    run_portfolio_combination_backtest,
    run_portfolio_scoring,
    summarize_portfolio_scoring,
)


def test_portfolio_score_requires_cost_survival():
    assert _portfolio_score(0.2, -0.1, False, "plausible", 0.0) == 0.0
    assert _portfolio_score(0.2, -0.1, True, "plausible", 0.0) > 0.0


def test_portfolio_summary_handles_empty_rows():
    assert summarize_portfolio_scoring([]) == ["- portfolio scoring: no eligible candidates"]


def test_portfolio_scoring_writes_candidates(tmp_path):
    run_dir = tmp_path / "backtests" / "runs" / "S1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        """
        {
          "strategy_id": "S1",
          "family": "ROTATION",
          "asset_class": "ETF",
          "short_name": "DUAL_MOMENTUM",
          "tier": "C",
          "hypothesis": "Momentum rotation edge",
          "rules": "Rank by momentum",
          "data_manifest": {"source": "massive"},
          "cost_stress": {"survives_double_cost": true},
          "split_metrics": {
            "unseen": {"cagr": 0.2, "max_drawdown": -0.1}
          }
        }
        """,
        encoding="utf-8",
    )

    result = run_portfolio_scoring(tmp_path, "2026-W21")

    assert result["path"].exists()
    assert result["rows"][0]["strategy_id"] == "S1"
    assert result["rows"][0]["suggested_weight_pct"] > 0


def test_portfolio_scoring_accepts_eodhd_candidates(tmp_path):
    run_dir = tmp_path / "backtests" / "runs" / "EODHD1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        """
        {
          "strategy_id": "EODHD1",
          "family": "ROTATION",
          "asset_class": "ETF",
          "short_name": "DUAL_MOMENTUM",
          "tier": "C",
          "hypothesis": "Momentum rotation edge",
          "rules": "Rank by momentum",
          "data_manifest": {"source": "eodhd"},
          "cost_stress": {"survives_double_cost": true},
          "split_metrics": {
            "unseen": {"cagr": 0.2, "max_drawdown": -0.1}
          }
        }
        """,
        encoding="utf-8",
    )

    result = run_portfolio_scoring(tmp_path, "2026-W21")

    assert result["rows"][0]["strategy_id"] == "EODHD1"
    assert result["rows"][0]["suggested_weight_pct"] > 0


def test_portfolio_combination_backtest_writes_equity_curve(tmp_path):
    run_dir = tmp_path / "backtests" / "runs" / "S1"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        """
        {
          "strategy_id": "S1",
          "family": "ROTATION",
          "asset_class": "ETF",
          "short_name": "DUAL_MOMENTUM",
          "tier": "C",
          "hypothesis": "Momentum rotation edge",
          "rules": "Rank by momentum",
          "data_manifest": {"source": "massive"},
          "cost_stress": {"survives_double_cost": true},
          "split_metrics": {
            "unseen": {"cagr": 0.2, "max_drawdown": -0.1}
          },
          "return_series": [
            {"date": "2026-01-01", "value": 0.00},
            {"date": "2026-01-02", "value": 0.01},
            {"date": "2026-01-05", "value": 0.01}
          ]
        }
        """,
        encoding="utf-8",
    )
    candidates = run_portfolio_scoring(tmp_path, "2026-W21")

    result = run_portfolio_combination_backtest(tmp_path, "2026-W21", candidates["rows"])

    assert result["path"].exists()
    assert result["equity_path"].exists()
    assert result["summary"]["status"] == "ok"
    assert result["summary"]["strategy_count"] == 1
    assert result["summary"]["portfolio_verdict"] == "pass"
    assert result["summary"]["rebalance_count"] >= 1
    assert "gross_exposure_pct" in result["summary"]
    assert "net_exposure_pct" in result["summary"]


def test_portfolio_combination_loads_series_only_for_selected_candidates(monkeypatch, tmp_path):
    calls = []

    def fake_load_backtest_results(root, *, return_series_strategy_ids=None):
        calls.append(return_series_strategy_ids)
        return [
            {
                "strategy_id": "SELECTED",
                "data_manifest": {"source": "eodhd"},
                "return_series": [
                    {"date": "2026-01-01", "value": 0.00},
                    {"date": "2026-01-02", "value": 0.01},
                ],
            },
            {
                "strategy_id": "UNSELECTED",
                "data_manifest": {"source": "eodhd"},
            },
        ]

    monkeypatch.setattr(portfolio, "load_backtest_results", fake_load_backtest_results)

    result = run_portfolio_combination_backtest(
        tmp_path,
        "2026-W21",
        [
            {
                "strategy_id": "SELECTED",
                "suggested_weight_pct": 25.0,
            }
        ],
    )

    assert calls == [{"SELECTED"}]
    assert result["summary"]["strategy_count"] == 1


def test_portfolio_combination_backtest_blocks_synthetic_only_results(tmp_path):
    run_dir = tmp_path / "backtests" / "runs" / "SYNTH"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        """
        {
          "strategy_id": "SYNTH",
          "family": "ROTATION",
          "asset_class": "ETF",
          "short_name": "DUAL_MOMENTUM",
          "tier": "C",
          "hypothesis": "Synthetic smoke result",
          "rules": "Rank by momentum",
          "data_manifest": {"source": "synthetic"},
          "cost_stress": {"survives_double_cost": true},
          "split_metrics": {
            "unseen": {"cagr": 0.2, "max_drawdown": -0.1}
          },
          "return_series": [
            {"date": "2026-01-01", "value": 0.00},
            {"date": "2026-01-02", "value": 0.01}
          ]
        }
        """,
        encoding="utf-8",
    )

    result = run_portfolio_combination_backtest(tmp_path, "2026-W21")

    assert result["summary"]["status"] == "blocked_no_real_data_candidates"
    assert result["summary"]["portfolio_verdict"] == "blocked"
