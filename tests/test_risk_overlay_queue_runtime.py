from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research_lab.strategies.baselines import _spec_from_hypothesis, queued_hypothesis_strategies


THIS_FILE = Path(__file__).resolve()


def _risk_overlay_row(*, include_base_strategy: bool) -> dict[str, object]:
    row: dict[str, object] = {
        "hypothesis_id": "RISK_OVERLAY_TEST_001",
        "family": "RISK_OVERLAY",
        "title": "Fixed-fractional overlay on capped trend",
        "rationale": "Reduce drawdown without changing base signals.",
        "source_title": "risk overlay review",
        "source_note_ids": ["note-1111111111111111", "note-2222222222222222"],
        "target_failure_mode": "drawdown_fail",
        "base_strategy_selection": {
            "mode": "explicit_base_strategy",
            "allowed_to_modify_signals": False,
            "allowed_to_modify_entries": False,
            "allowed_to_modify_exits": False,
        },
        "risk_overlay": {
            "position_sizing": {
                "type": "fixed_fractional",
                "risk_per_trade_pct_candidates": [0.25, 0.5, 0.75, 1.0],
            },
            "portfolio_drawdown_circuit_breaker": {
                "type": "staged_derisking",
                "thresholds": [
                    {"drawdown_pct": 5, "gross_exposure_multiplier": 0.75},
                    {"drawdown_pct": 8, "gross_exposure_multiplier": 0.5},
                    {"drawdown_pct": 10, "gross_exposure_multiplier": 0.0},
                ],
                "reentry_rule": {
                    "type": "equity_recovery",
                    "recovery_from_peak_pct": 2,
                    "cooldown_days": 10,
                },
            },
            "loser_addition_rule": {"add_to_losers_allowed": False},
        },
        "validation_plan": {
            "primary_metrics": ["max_drawdown", "drawdown_duration", "recovery_time", "survival_rate"],
            "secondary_metrics": ["CAGR", "Sharpe", "turnover", "cost_stress"],
            "comparison": "same signals with and without risk overlay",
            "required_gates": ["walk_forward", "drawdown", "cost_stress", "stability"],
        },
    }
    if include_base_strategy:
        row["base_strategy"] = {
            "family": "LONGTERM",
            "asset_class": "ETF",
            "timeframe": "1D",
            "short_name": "TREND_VOL_CAP",
            "builder": "long_term_vol_target_cap",
            "parameters": {
                "symbol": "SPY",
                "sma": 200,
                "vol_window": 63,
                "target_vol": 0.10,
                "max_weight": 0.75,
            },
            "rules": "Hold SPY above SMA200 with realized-volatility targeting capped at 75% exposure; otherwise hold cash.",
        }
    return row


def _legacy_row(family: str) -> dict[str, object]:
    rows = {
        "ROTATION": {
            "hypothesis_id": "ROTATION_TEST",
            "family": "ROTATION",
            "title": "Rotation queue",
            "rationale": "Test mapping",
            "parameters": {"lookback": 84, "top_n": 1, "risk_sma": 180},
        },
        "SWING": {
            "hypothesis_id": "SWING_TEST",
            "family": "SWING",
            "ticker": "QQQ",
            "title": "Swing queue",
            "rationale": "Test mapping",
            "parameters": {"fast_sma": 30, "slow_sma": 120, "rsi_entry": 38, "rsi_exit": 60, "atr_stop": 1.5},
        },
        "LONGTERM": {
            "hypothesis_id": "LONGTERM_TEST",
            "family": "LONGTERM",
            "title": "Long-term queue",
            "rationale": "Test mapping",
            "parameters": {"sma": 180, "vol_window": 84, "target_vol": 0.09},
        },
    }
    return rows[family]


def test_risk_overlay_rows_fail_explicitly_without_execution_runtime():
    with pytest.raises(ValueError, match="RISK_OVERLAY queue rows are not executable with the current runtime"):
        _spec_from_hypothesis(_risk_overlay_row(include_base_strategy=True))


def test_explicit_failure_preserves_required_runtime_details():
    with pytest.raises(ValueError) as excinfo:
        _spec_from_hypothesis(_risk_overlay_row(include_base_strategy=True))

    message = str(excinfo.value)
    assert "source_note_ids" in message
    assert "fixed_fractional" in message
    assert "thresholds" in message
    assert "reentry_rule" in message
    assert "add_to_losers_allowed" in message
    assert "validation_plan" in message


def test_missing_base_strategy_binding_fails_explicitly():
    with pytest.raises(ValueError, match="base strategy binding"):
        _spec_from_hypothesis(_risk_overlay_row(include_base_strategy=False))


def test_existing_rotation_swing_and_longterm_queue_mappings_remain_unchanged():
    rotation = _spec_from_hypothesis(_legacy_row("ROTATION"))
    swing = _spec_from_hypothesis(_legacy_row("SWING"))
    longterm = _spec_from_hypothesis(_legacy_row("LONGTERM"))

    assert rotation is not None
    assert rotation.builder == "rotation_momentum_drawdown_filter"
    assert rotation.short_name == "QUEUE_MOM_DD"
    assert rotation.parameters["lookback"] == 84
    assert rotation.parameters["top_n"] == 1
    assert rotation.parameters["risk_sma"] == 180

    assert swing is not None
    assert swing.builder == "swing_trend_filtered_pullback"
    assert swing.short_name == "QUEUE_PULLBACK"
    assert swing.parameters["fast_sma"] == 30
    assert swing.parameters["slow_sma"] == 120
    assert swing.parameters["atr_stop"] == 1.5

    assert longterm is not None
    assert longterm.builder == "long_term_vol_target"
    assert longterm.short_name == "QUEUE_VOL_TARGET"
    assert longterm.parameters["sma"] == 180
    assert longterm.parameters["vol_window"] == 84
    assert longterm.parameters["target_vol"] == 0.09


def test_queue_loader_does_not_append_registry_files_on_explicit_failure(tmp_path):
    queue_path = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(json.dumps(_risk_overlay_row(include_base_strategy=True)) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="RISK_OVERLAY queue rows are not executable with the current runtime"):
        queued_hypothesis_strategies(tmp_path, limit=4)

    files = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert files == ["registry/hypothesis_queue.jsonl"]


def test_new_runtime_tests_do_not_directly_import_forbidden_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.deployment_gate",
        "research_lab.backtest",
        "research_lab.walk_forward",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.hermes",
        "research_lab.llm",
        "pypdf",
        "PyPDF2",
        "fitz",
        "requests",
        "aiohttp",
        "urllib",
        "http",
        "socket",
        "ibapi",
        "ib_insync",
    )
    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"{THIS_FILE.name} imported forbidden module {import_name}"
