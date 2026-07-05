from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_lab.reports import _dirty_paths_from_git_status, classify_git_dirty_paths, write_daily_report_artifacts


def test_daily_report_artifacts_include_run_id_metadata_and_expected_paths(tmp_path):
    timestamp = datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc)
    git_info = {"commit": "abcdef1234567890", "branch": "codex/test", "dirty": False}

    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=timestamp,
        git_info=git_info,
        command=["scripts/run_daily_research.py"],
        runner_name="run_daily_research",
    )

    assert outcome["metadata"]["run_id"] == "20260603T120304000000Z-abcdef1"
    assert outcome["latest_report_path"] == tmp_path / "reports" / "daily" / "2026-06-03.md"
    assert outcome["run_report_path"] == (
        tmp_path
        / "reports"
        / "runs"
        / "2026-06-03"
        / "20260603T120304000000Z-abcdef1"
        / "daily_report.md"
    )
    assert outcome["metadata_path"] == outcome["run_report_path"].with_name("run_metadata.json")
    assert outcome["latest_report_path"].read_text(encoding="utf-8").startswith("# Daily Research Report - 2026-06-03")
    assert outcome["run_report_path"].read_text(encoding="utf-8") == outcome["latest_report_path"].read_text(encoding="utf-8")

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["run_id"] == "20260603T120304000000Z-abcdef1"
    assert metadata["timestamp_utc"] == "2026-06-03T12:03:04+00:00"
    assert metadata["git"] == {
        **git_info,
        "code_dirty": False,
        "runtime_artifacts_dirty": False,
        "dirty_files": [],
        "dirty_classification": "clean",
    }
    assert metadata["runner"] == "run_daily_research"
    assert metadata["command"] == ["scripts/run_daily_research.py"]
    assert metadata["latest_report_path"] == "reports/daily/2026-06-03.md"
    assert metadata["run_report_path"] == "reports/runs/2026-06-03/20260603T120304000000Z-abcdef1/daily_report.md"
    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "- git_dirty: False" in report
    assert "- code_dirty: False" in report
    assert "- runtime_artifacts_dirty: False" in report
    assert "- dirty_classification: clean" in report
    assert "- dirty_files: none" in report


def test_two_runs_on_same_date_create_distinct_immutable_artifacts(tmp_path):
    first = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )
    second = write_daily_report_artifacts(
        tmp_path,
        [_result("massive")],
        timestamp=datetime(2026, 6, 3, 12, 4, 5, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    assert first["latest_report_path"] == second["latest_report_path"]
    assert first["run_report_path"] != second["run_report_path"]
    assert first["run_report_path"].exists()
    assert second["run_report_path"].exists()
    assert "- data sources: massive" in second["latest_report_path"].read_text(encoding="utf-8")
    assert "- data sources: eodhd" in first["run_report_path"].read_text(encoding="utf-8")


def test_reusing_same_run_id_requires_explicit_allowance(tmp_path):
    kwargs = {
        "timestamp": datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        "git_info": {"commit": "abcdef1234567890", "branch": "main", "dirty": False},
        "run_id": "manual-rerun-id",
    }

    write_daily_report_artifacts(tmp_path, [_result("eodhd")], **kwargs)

    with pytest.raises(FileExistsError, match="manual-rerun-id"):
        write_daily_report_artifacts(tmp_path, [_result("massive")], **kwargs)

    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("massive")],
        allow_existing_run_id=True,
        **kwargs,
    )
    assert outcome["run_report_path"].read_text(encoding="utf-8").startswith("# Daily Research Report")


def test_metadata_includes_provider_history_summary_without_secrets(tmp_path):
    result = _result("eodhd")
    result["data_manifest"] |= {
        "api_key": "SUPERSECRET",
        "symbol_diagnostics": [
            {
                "requested_symbol": "SPY",
                "selected_provider": "eodhd",
                "first_date": "1993-01-29",
                "last_date": "2026-06-02",
                "daily_bars": 8390,
                "history_years": 33.3,
                "token": "SUPERSECRET",
            }
        ],
    }

    outcome = write_daily_report_artifacts(
        tmp_path,
        [result],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": True},
        command=["python", "scripts/run_daily_research.py", "--api-key", "SUPERSECRET", "--token=SUPERSECRET"],
    )

    metadata_text = outcome["metadata_path"].read_text(encoding="utf-8")
    metadata = json.loads(metadata_text)
    assert metadata["data_sources"] == ["eodhd"]
    assert metadata["provider_history_summary"] == [
        {
            "source": "eodhd",
            "start": "1993-01-29",
            "end": "2026-06-02",
            "rows": 9000,
            "years": 33.3,
            "symbols": ["SPY"],
            "symbol_history": [
                {
                    "requested_symbol": "SPY",
                    "selected_provider": "eodhd",
                    "first_date": "1993-01-29",
                    "last_date": "2026-06-02",
                    "daily_bars": 8390,
                    "history_years": 33.3,
                }
            ],
        }
    ]
    assert "SUPERSECRET" not in metadata_text
    assert metadata["command"] == ["python", "scripts/run_daily_research.py", "--api-key", "<redacted>", "--token=<redacted>"]


def test_metadata_includes_mixed_source_summary(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd"), _intraday_result("synthetic")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["data_source_summary"] == {
        "classification": "mixed_real_eod_with_synthetic_intraday_auxiliary",
        "summary_text": "ETF universe: eodhd, no fallback; Intraday BTCUSDT: synthetic auxiliary path; Synthetic candidates are not promotion-eligible.",
        "real_eod_candidate_count": 1,
        "synthetic_candidate_count": 1,
        "synthetic_intraday_auxiliary_count": 1,
        "provider_fallback_candidate_count": 0,
        "data_quality_promotion_block_count": 1,
    }


def test_metadata_and_report_include_compact_daily_experiment_funnel(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd", tier="A"), _result("eodhd", strategy_id="R2", tier="Rejected", tier_reason="Unseen max drawdown exceeds 15%", split_metrics={"unseen": {"max_drawdown": -0.20}})],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
        extra_metadata={
            "daily_experiment_selection": {
                "budget": 18,
                "recent_window": 50,
                "proposed": 7,
                "family_filtered": 2,
                "source_filtered": 1,
                "invalid_filtered": 0,
                "recent_duplicate_skipped": 1,
                "in_batch_duplicate_skipped": 1,
                "budget_skipped": 0,
                "selected": 2,
                "attempted": 2,
                "completed": 2,
                "missing_data_skipped": 0,
                "queue_rows_consumed": False,
            }
        },
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["daily_experiment_funnel"]["selector_counts"] == {
        "proposed": 7,
        "family_filtered": 2,
        "source_filtered": 1,
        "invalid_filtered": 0,
        "recent_duplicate_skipped": 1,
        "in_batch_duplicate_skipped": 1,
        "budget_skipped": 0,
        "selected": 2,
    }
    assert metadata["daily_experiment_funnel"]["execution_counts"] == {
        "attempted": 2,
        "completed": 2,
        "missing_data_skipped": 0,
    }
    assert metadata["daily_experiment_funnel"]["execution_failure_contract"] == "fail_fast_no_completed_report"
    assert metadata["daily_experiment_funnel"]["result_diagnostics"]["tier_ab"] == 1
    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "## Compact Funnel" in report
    assert "| proposed | selector outcome | 7 |" in report
    assert "- queue rows consumed: false" in report


def test_recovery_report_separates_recent_coverage_from_new_execution_counts(tmp_path):
    coverage = {
        "strategy_id": "RECENT_EODHD_1",
        "fingerprint": '{"builder":"long_term_vol_target"}',
        "data_source": "eodhd",
        "data_start": "1993-01-29",
        "data_end": "2026-07-02",
        "tier": "C",
        "tier_reason": "walk-forward shortfall",
        "unseen_cagr": 0.08,
        "unseen_max_drawdown": -0.09,
        "unseen_trade_count": 42,
        "double_cost_unseen_cagr": 0.07,
        "double_cost_pass": True,
        "walk_forward_window_count": 7,
        "walk_forward_pass_rate": 0.57,
    }
    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd", strategy_id="NEW_RESULT")],
        timestamp=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
        extra_metadata={
            "daily_experiment_selection": {
                "selection_mode": "bounded_recovery",
                "candidate_source": "internal_recovery_manifest",
                "queue_inspected": False,
                "queue_consumed": False,
                "proposed": 4,
                "selected": 3,
                "selected_new": 3,
                "covered_by_recent_real": 1,
                "recovery_target": 4,
                "recovery_resolved": 4,
                "recovery_shortfall": 0,
                "nonqualifying_recent_matches": 2,
                "covered_recent_results": [coverage],
                "attempted": 1,
                "completed": 1,
                "missing_data_skipped": 0,
            }
        },
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    funnel = metadata["daily_experiment_funnel"]
    assert funnel["recovery_counts"] == {
        "manifest_candidates": 4,
        "selected_new": 3,
        "covered_by_recent_real": 1,
        "nonqualifying_recent_matches": 2,
        "recovery_resolved": 4,
        "recovery_shortfall": 0,
    }
    assert funnel["execution_counts"] == {
        "attempted": 1,
        "completed": 1,
        "missing_data_skipped": 0,
    }
    assert funnel["result_diagnostics"]["positive_oos"] == 1
    assert metadata["daily_experiment_selection"]["covered_recent_results"] == [coverage]
    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "| manifest_candidates | recovery resolution | 4 |" in report
    assert "| covered_by_recent_real | recovery resolution | 1 |" in report
    assert "RECENT_EODHD_1" in report
    assert "coverage provenance" in report
    assert "result diagnostics cover only results completed in this run" in report


def test_metadata_includes_bounded_walk_forward_diagnostics_for_etf_tier_c_near_miss(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [
            _result(
                "eodhd",
                strategy_id="LONGTERM_ETF_1D_TREND_VOL_CAP_20260626_006",
                family="LONGTERM",
                short_name="TREND_VOL_CAP",
                tier="C",
                tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                split_metrics={
                    "train": {"cagr": 0.0340},
                    "validation": {"cagr": 0.0545},
                    "unseen": {"cagr": 0.0399, "max_drawdown": -0.1339},
                },
                walk_forward={
                    "method": "true_rolling_oos",
                    "status": "ok",
                    "window_count": 7,
                    "passed_windows": 4,
                    "pass_rate": 0.5714,
                    "median_test_cagr": 0.01,
                    "worst_test_drawdown": -0.18,
                    "regime_summary": "bull:2/3;bear:1/2;sideways:1/2",
                    "windows": [
                        {
                            "window": 1,
                            "test_start": "2019-01-01",
                            "test_end": "2019-12-31",
                            "test_cagr": 0.12,
                            "test_max_drawdown": -0.05,
                            "regime": "bull",
                            "passed": True,
                        },
                        {
                            "window": 2,
                            "test_start": "2020-01-01",
                            "test_end": "2020-12-31",
                            "test_cagr": -0.03,
                            "test_max_drawdown": -0.23,
                            "regime": "crisis",
                            "passed": False,
                        },
                        {
                            "window": 3,
                            "test_start": "2021-01-01",
                            "test_end": "2021-12-31",
                            "test_cagr": 0.02,
                            "test_max_drawdown": -0.11,
                            "regime": "bull",
                            "passed": True,
                        },
                        {
                            "window": 4,
                            "test_start": "2022-01-01",
                            "test_end": "2022-12-31",
                            "test_cagr": -0.01,
                            "test_max_drawdown": -0.21,
                            "regime": "bear",
                            "passed": False,
                        },
                        {
                            "window": 5,
                            "test_start": "2023-01-01",
                            "test_end": "2023-12-31",
                            "test_cagr": -0.04,
                            "test_max_drawdown": -0.24,
                            "regime": "sideways",
                            "passed": False,
                        },
                        {
                            "window": 6,
                            "test_start": "2024-01-01",
                            "test_end": "2024-12-31",
                            "test_cagr": 0.01,
                            "test_max_drawdown": -0.08,
                            "regime": "bull",
                            "passed": True,
                        },
                        {
                            "window": 7,
                            "test_start": "2025-01-01",
                            "test_end": "2025-12-31",
                            "test_cagr": 0.03,
                            "test_max_drawdown": -0.10,
                            "regime": "sideways",
                            "passed": True,
                        },
                    ],
                },
            )
        ],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["walk_forward_diagnostics"] == [
        {
            "strategy_id": "LONGTERM_ETF_1D_TREND_VOL_CAP_20260626_006",
            "window_count": 7,
            "passed_windows": 4,
            "total_windows": 7,
            "pass_rate": 0.5714,
            "required_pass_rate": 0.67,
            "median_test_cagr": 0.01,
            "worst_test_drawdown": -0.18,
            "failed_window_count": 3,
            "worst_failed_windows": [
                {
                    "window": 5,
                    "test_start": "2023-01-01",
                    "test_end": "2023-12-31",
                    "regime": "sideways",
                    "test_cagr": -0.04,
                    "test_max_drawdown": -0.24,
                },
                {
                    "window": 2,
                    "test_start": "2020-01-01",
                    "test_end": "2020-12-31",
                    "regime": "crisis",
                    "test_cagr": -0.03,
                    "test_max_drawdown": -0.23,
                },
                {
                    "window": 4,
                    "test_start": "2022-01-01",
                    "test_end": "2022-12-31",
                    "regime": "bear",
                    "test_cagr": -0.01,
                    "test_max_drawdown": -0.21,
                },
            ],
            "regime_summary": "bull:2/3;bear:1/2;sideways:1/2",
        }
    ]
    assert "windows" not in metadata["walk_forward_diagnostics"][0]
    assert len(metadata["walk_forward_diagnostics"][0]["worst_failed_windows"]) == 3


def test_metadata_derives_failed_window_count_from_aggregate_counts_without_windows(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [
            _result(
                "eodhd",
                strategy_id="LONGTERM_ETF_1D_TREND_VOL_CAP_20260626_006",
                family="LONGTERM",
                short_name="TREND_VOL_CAP",
                tier="C",
                tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                split_metrics={
                    "train": {"cagr": 0.0340},
                    "validation": {"cagr": 0.0545},
                    "unseen": {"cagr": 0.0399, "max_drawdown": -0.1339},
                },
                walk_forward={
                    "method": "true_rolling_oos",
                    "status": "ok",
                    "window_count": 7,
                    "passed_windows": 4,
                    "pass_rate": 0.5714,
                    "median_test_cagr": 0.01,
                    "worst_test_drawdown": -0.18,
                },
            )
        ],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["walk_forward_diagnostics"] == [
        {
            "strategy_id": "LONGTERM_ETF_1D_TREND_VOL_CAP_20260626_006",
            "window_count": 7,
            "passed_windows": 4,
            "total_windows": 7,
            "pass_rate": 0.5714,
            "required_pass_rate": 0.67,
            "median_test_cagr": 0.01,
            "worst_test_drawdown": -0.18,
            "failed_window_count": 3,
            "worst_failed_windows": [],
        }
    ]
    assert "windows" not in metadata["walk_forward_diagnostics"][0]


def test_metadata_omits_bounded_walk_forward_diagnostics_for_unrelated_etf_tier_c_reject(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [
            _result(
                "eodhd",
                strategy_id="ROTATION_ETF_1D_DUAL_MOMENTUM_20260626_002",
                family="ROTATION",
                short_name="DUAL_MOMENTUM",
                tier="C",
                tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                split_metrics={
                    "train": {"cagr": 0.0340},
                    "validation": {"cagr": 0.0545},
                    "unseen": {"cagr": 0.0399, "max_drawdown": -0.1339},
                },
                walk_forward={
                    "method": "true_rolling_oos",
                    "status": "ok",
                    "window_count": 7,
                    "pass_rate": 0.5714,
                    "median_test_cagr": 0.01,
                    "worst_test_drawdown": -0.18,
                },
            )
        ],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["walk_forward_diagnostics"] == []


def test_metadata_omits_bounded_walk_forward_diagnostics_for_non_etf_auxiliary_candidate(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [
            _result(
                "synthetic",
                strategy_id="INTRADAY_BTCUSDT_15M_VWAP_RSI_RECLAIM_20260626_004",
                family="INTRADAY",
                asset_class="CRYPTO",
                timeframe="15M",
                short_name="VWAP_RSI_RECLAIM",
                tier="C",
                tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                split_metrics={
                    "train": {"cagr": 0.0340},
                    "validation": {"cagr": 0.0545},
                    "unseen": {"cagr": 0.0399, "max_drawdown": -0.1339},
                },
                walk_forward={
                    "method": "true_rolling_oos",
                    "status": "ok",
                    "window_count": 7,
                    "pass_rate": 0.5714,
                    "median_test_cagr": 0.01,
                    "worst_test_drawdown": -0.18,
                },
            )
        ],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["walk_forward_diagnostics"] == []


def test_metadata_counts_all_failed_windows_while_bounding_worst_window_details(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [
            _result(
                "eodhd",
                strategy_id="LONGTERM_ETF_1D_TREND_VOL_CAP_20260626_006",
                family="LONGTERM",
                short_name="TREND_VOL_CAP",
                tier="C",
                tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                split_metrics={
                    "train": {"cagr": 0.0340},
                    "validation": {"cagr": 0.0545},
                    "unseen": {"cagr": 0.0399, "max_drawdown": -0.1339},
                },
                walk_forward={
                    "method": "true_rolling_oos",
                    "status": "ok",
                    "window_count": 7,
                    "passed_windows": 3,
                    "pass_rate": 0.5714,
                    "median_test_cagr": 0.01,
                    "worst_test_drawdown": -0.18,
                    "windows": [
                        {"window": 1, "test_start": "2019-01-01", "test_end": "2019-12-31", "test_cagr": 0.12, "test_max_drawdown": -0.05, "regime": "bull", "passed": True},
                        {"window": 2, "test_start": "2020-01-01", "test_end": "2020-12-31", "test_cagr": -0.03, "test_max_drawdown": -0.23, "regime": "crisis", "passed": False},
                        {"window": 3, "test_start": "2021-01-01", "test_end": "2021-12-31", "test_cagr": -0.02, "test_max_drawdown": -0.22, "regime": "bear", "passed": False},
                        {"window": 4, "test_start": "2022-01-01", "test_end": "2022-12-31", "test_cagr": -0.01, "test_max_drawdown": -0.21, "regime": "bear", "passed": False},
                        {"window": 5, "test_start": "2023-01-01", "test_end": "2023-12-31", "test_cagr": -0.04, "test_max_drawdown": -0.24, "regime": "sideways", "passed": False},
                        {"window": 6, "test_start": "2024-01-01", "test_end": "2024-12-31", "test_cagr": 0.01, "test_max_drawdown": -0.08, "regime": "bull", "passed": True},
                        {"window": 7, "test_start": "2025-01-01", "test_end": "2025-12-31", "test_cagr": 0.03, "test_max_drawdown": -0.10, "regime": "sideways", "passed": True},
                    ],
                },
            )
        ],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["walk_forward_diagnostics"][0]["failed_window_count"] == 4
    assert len(metadata["walk_forward_diagnostics"][0]["worst_failed_windows"]) == 3
    assert [window["window"] for window in metadata["walk_forward_diagnostics"][0]["worst_failed_windows"]] == [5, 2, 3]


def test_metadata_omits_bounded_walk_forward_diagnostics_for_partial_legacy_payload(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [
            _result(
                "eodhd",
                strategy_id="LONGTERM_ETF_1D_TREND_VOL_CAP_20260626_006",
                family="LONGTERM",
                short_name="TREND_VOL_CAP",
                tier="C",
                tier_reason="Positive unseen result, but rolling walk-forward is not strong enough for promotion.",
                split_metrics={
                    "train": {"cagr": 0.0340},
                    "validation": {"cagr": 0.0545},
                    "unseen": {"cagr": 0.0399, "max_drawdown": -0.1339},
                },
                walk_forward={
                    "method": "true_rolling_oos",
                    "status": "ok",
                    "window_count": None,
                    "pass_rate": None,
                    "median_test_cagr": None,
                    "worst_test_drawdown": None,
                },
            )
        ],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["walk_forward_diagnostics"] == []


def test_existing_caller_can_still_read_latest_daily_report_path(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    latest_report_path = tmp_path / "reports" / "daily" / "2026-06-03.md"
    assert outcome["latest_report_path"] == latest_report_path
    report = latest_report_path.read_text(encoding="utf-8")
    assert "# Daily Research Report - 2026-06-03" in report
    assert "| S1 | LONGTERM | ETF | 1D | eodhd |" in report


def test_report_metadata_separates_runtime_artifact_dirty_from_code_dirty(tmp_path):
    git_info = {
        "commit": "abcdef1234567890",
        "branch": "main",
        **classify_git_dirty_paths(["registry/leaderboard.csv"]),
    }

    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info=git_info,
    )

    metadata = json.loads(outcome["metadata_path"].read_text(encoding="utf-8"))
    assert metadata["git"]["dirty"] is True
    assert metadata["git"]["code_dirty"] is False
    assert metadata["git"]["runtime_artifacts_dirty"] is True
    assert metadata["git"]["dirty_files"] == ["registry/leaderboard.csv"]
    assert metadata["git"]["dirty_classification"] == "runtime_artifacts_only"

    report = outcome["run_report_path"].read_text(encoding="utf-8")
    assert "- git_dirty: True" in report
    assert "- code_dirty: False" in report
    assert "- runtime_artifacts_dirty: True" in report
    assert "- dirty_classification: runtime_artifacts_only" in report
    assert "- dirty_files: registry/leaderboard.csv" in report


def test_report_artifacts_are_written_only_under_supplied_root(tmp_path):
    outcome = write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    for path in (outcome["latest_report_path"], outcome["run_report_path"], outcome["metadata_path"]):
        assert path.is_relative_to(tmp_path)


def test_report_artifact_writer_does_not_stage_runtime_outputs(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)

    write_daily_report_artifacts(
        tmp_path,
        [_result("eodhd")],
        timestamp=datetime(2026, 6, 3, 12, 3, 4, tzinfo=timezone.utc),
        git_info={"commit": "abcdef1234567890", "branch": "main", "dirty": False},
    )

    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert staged.stdout == ""


@pytest.mark.parametrize(
    ("dirty_paths", "expected"),
    [
        (
            [],
            {
                "dirty": False,
                "code_dirty": False,
                "runtime_artifacts_dirty": False,
                "dirty_files": [],
                "dirty_classification": "clean",
            },
        ),
        (
            ["registry/leaderboard.csv"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["registry/leaderboard.csv"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
        (
            ["research_lab/runner.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": False,
                "dirty_files": ["research_lab/runner.py"],
                "dirty_classification": "code_or_config_dirty",
            },
        ),
        (
            ["registry/leaderboard.csv", "research_lab/runner.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["registry/leaderboard.csv", "research_lab/runner.py"],
                "dirty_classification": "mixed_code_and_runtime_dirty",
            },
        ),
        (
            ["tests/test_x.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": False,
                "dirty_files": ["tests/test_x.py"],
                "dirty_classification": "code_or_config_dirty",
            },
        ),
        (
            ["data/manifests/eodhd_manifest.json"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["data/manifests/eodhd_manifest.json"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
    ],
)
def test_classify_git_dirty_paths(dirty_paths, expected):
    assert classify_git_dirty_paths(dirty_paths) == expected


@pytest.mark.parametrize(
    ("status_lines", "expected"),
    [
        (
            [" M data/manifests/daily_universe.json"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["data/manifests/daily_universe.json"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
        (
            [" M data/manifests/daily_universe.json", " M registry/leaderboard.csv"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["data/manifests/daily_universe.json", "registry/leaderboard.csv"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
        (
            ["M  registry/leaderboard.csv"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["registry/leaderboard.csv"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
        (
            [" M research_lab/reports.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": False,
                "dirty_files": ["research_lab/reports.py"],
                "dirty_classification": "code_or_config_dirty",
            },
        ),
        (
            [" M data/manifests/daily_universe.json", " M research_lab/reports.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["data/manifests/daily_universe.json", "research_lab/reports.py"],
                "dirty_classification": "mixed_code_and_runtime_dirty",
            },
        ),
        (
            ["?? tests/test_new_file.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": False,
                "dirty_files": ["tests/test_new_file.py"],
                "dirty_classification": "code_or_config_dirty",
            },
        ),
        (
            ["A  research_lab/new_file.py"],
            {
                "dirty": True,
                "code_dirty": True,
                "runtime_artifacts_dirty": False,
                "dirty_files": ["research_lab/new_file.py"],
                "dirty_classification": "code_or_config_dirty",
            },
        ),
        (
            ["MM registry/experiments.jsonl"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["registry/experiments.jsonl"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
        (
            ["R  data/manifests/old_daily_universe.json -> data/manifests/daily_universe.json"],
            {
                "dirty": True,
                "code_dirty": False,
                "runtime_artifacts_dirty": True,
                "dirty_files": ["data/manifests/daily_universe.json"],
                "dirty_classification": "runtime_artifacts_only",
            },
        ),
    ],
)
def test_dirty_metadata_classifies_raw_git_short_status(status_lines, expected):
    dirty_paths = _dirty_paths_from_git_status("\n".join(status_lines))

    assert classify_git_dirty_paths(dirty_paths) == expected


def _result(
    source: str,
    *,
    strategy_id: str = "S1",
    family: str = "LONGTERM",
    asset_class: str = "ETF",
    timeframe: str = "1D",
    short_name: str | None = None,
    builder: str = "",
    tier: str = "C",
    tier_reason: str = "test",
    split_metrics: dict | None = None,
    walk_forward: dict | None = None,
) -> dict:
    metrics = {
        "train": {"cagr": 0.1},
        "validation": {"cagr": 0.1},
        "unseen": {
            "cagr": 0.1,
            "sharpe": 1.0,
            "mar": 1.0,
            "max_drawdown": -0.05,
            "profit_factor": 1.2,
            "trade_count": 10,
        },
    }
    for split_name, overrides in (split_metrics or {}).items():
        metrics[split_name].update(overrides)

    return {
        "strategy_id": strategy_id,
        "family": family,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "short_name": short_name if short_name is not None else "TREND",
        "hypothesis": "test",
        "rules": "test",
        "parameters": {},
        "builder": builder,
        "data_manifest": {
            "source": source,
            "start": "1993-01-29",
            "end": "2026-06-02",
            "rows": 9000,
            "years": 33.3,
            "symbols": ["SPY"],
        },
        "data_source": source,
        "cost_stress": {
            "normal_cost_bps": 5.0,
            "double_cost_bps": 10.0,
            "survives_double_cost": True,
            "double_unseen_cagr": 0.05,
        },
        "split_metrics": metrics,
        "walk_forward": walk_forward,
        "tier": tier,
        "tier_reason": tier_reason,
        "average_exposure": 1.0,
        "average_turnover": 0.1,
    }


def _intraday_result(source: str) -> dict:
    result = _result(source)
    result["strategy_id"] = "BTC1"
    result["family"] = "INTRADAY"
    result["asset_class"] = "BTCUSDT"
    result["timeframe"] = "15M"
    result["short_name"] = "VWAP_RSI_RECLAIM"
    result["data_manifest"]["symbols"] = ["BTCUSDT"]
    result["split_metrics"]["validation"]["cagr"] = -0.01
    result["split_metrics"]["unseen"]["cagr"] = -0.02
    result["split_metrics"]["unseen"]["trade_count"] = 22
    result["cost_stress"]["double_unseen_cagr"] = -0.03
    return result
