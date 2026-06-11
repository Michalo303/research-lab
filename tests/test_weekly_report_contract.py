from datetime import datetime, timezone

from scripts.run_weekly_deep_research import build_weekly_data_provider_section, build_weekly_validation_gate_section


FIXED_TIME = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)


def test_weekly_report_contract_renders_validation_gate_from_fake_weekly_outputs(tmp_path):
    robustness_rows = [
        {
            "strategy_id": "alpha",
            "data_source": "eodhd",
            "data_years": 12.0,
            "median_test_cagr": 0.11,
            "unseen_cagr": 0.08,
            "unseen_max_drawdown": -0.12,
            "pass_rate": 0.75,
            "robustness_verdict": "pass",
        },
        {
            "strategy_id": "beta",
            "data_source": "eodhd",
            "data_years": 12.0,
            "median_test_cagr": 0.04,
            "unseen_cagr": -0.02,
            "unseen_max_drawdown": -0.18,
            "pass_rate": 0.25,
            "robustness_verdict": "fail",
        },
    ]
    deployment_rows = [
        {"strategy_id": "alpha", "paper_eligible": True},
        {"strategy_id": "beta", "paper_eligible": False},
    ]

    section = build_weekly_validation_gate_section(
        robustness_rows,
        deployment_rows,
        evaluated_at=FIXED_TIME,
    )
    report = tmp_path / "weekly.md"
    report.write_text("\n".join(["# Weekly Deep Research Report", "", *section]) + "\n", encoding="utf-8")
    markdown = report.read_text(encoding="utf-8")

    assert "## Weekly Validation Gate" in markdown
    assert "- status: PASS" in markdown
    assert "- tier: DEPLOYABLE" in markdown
    assert "all_deployable_thresholds_met" in markdown
    assert "- key metrics:" in markdown
    assert "accepted_count: 1.0" in markdown
    assert "rejected_count: 1.0" in markdown
    assert "data_years: 12.0" in markdown
    assert "data_source: eodhd" in markdown
    assert "synthetic_used: False" in markdown


def test_weekly_report_contract_missing_rows_render_deterministic_failure(tmp_path):
    section = build_weekly_validation_gate_section([], [], evaluated_at=FIXED_TIME)
    report = tmp_path / "weekly_missing.md"
    report.write_text("\n".join(section) + "\n", encoding="utf-8")
    markdown = report.read_text(encoding="utf-8")

    assert "## Weekly Validation Gate" in markdown
    assert "- status: FAIL" in markdown
    assert "- tier: REJECTED" in markdown
    assert "missing_required_metrics:" in markdown
    assert "no_experiments_run" in markdown
    assert "- key metrics:" in markdown


def test_weekly_report_contract_includes_provider_history_diagnostics(tmp_path):
    section = build_weekly_data_provider_section(
        {
            "requested_provider": "eodhd",
            "selected_provider": "eodhd",
            "actual_provider": "eodhd",
            "symbols": ["SPY", "QQQ"],
            "start_date": "1990-01-02",
            "end_date": "2026-06-10",
            "data_years": 36.4,
            "fallback_used": False,
            "fallback_reason": "",
        }
    )
    report = tmp_path / "weekly_provider.md"
    report.write_text("\n".join(section) + "\n", encoding="utf-8")
    markdown = report.read_text(encoding="utf-8")

    assert "## Data Provider Diagnostics" in markdown
    assert "- requested provider: eodhd" in markdown
    assert "- selected provider: eodhd" in markdown
    assert "- actual provider used: eodhd" in markdown
    assert "- universe: SPY, QQQ" in markdown
    assert "- data range: 1990-01-02 to 2026-06-10" in markdown
    assert "- data years: 36.40" in markdown
    assert "- fallback occurred: False" in markdown
