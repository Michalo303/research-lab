from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_lab.orchestration.risk_overlay_hypothesis_queue import (
    build_risk_overlay_hypothesis_queue_entry,
)

from research_lab.orchestration.risk_overlay_execution_adapter_v1 import (
    build_risk_overlay_execution_spec,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_risk_overlay_experiment_spec.py"
MODULE_PATH = ROOT / "research_lab" / "orchestration" / "risk_overlay_execution_adapter_v1.py"


def _draft() -> dict[str, object]:
    return {
        "version": "candidate_experiment_draft_v1",
        "source": {
            "blocker": "drawdown_fail",
            "source_notes": [
                {
                    "note_id": "note-1111111111111111",
                    "book_id": "book-risk-control-2002",
                    "book_title": "Money Management Risk Control For Traders (2002)",
                    "page_start": 44,
                    "page_end": 46,
                    "confidence": "medium",
                    "promotion_status": "not_promoted",
                    "extracted_claim": "Trading accuracy cannot compensate for poor money management.",
                    "why_relevant_to_blocker": "Preservation matters more than signal tweaks.",
                    "risk_controls": ["fixed fractional sizing", "drawdown circuit breaker"],
                },
                {
                    "note_id": "note-2222222222222222",
                    "book_id": "book-risk-control-2002",
                    "book_title": "Money Management Risk Control For Traders (2002)",
                    "page_start": 47,
                    "page_end": 49,
                    "confidence": "medium",
                    "promotion_status": "not_promoted",
                    "extracted_claim": "Unless a system is 100% accurate, sound risk management must be part of it.",
                    "why_relevant_to_blocker": "Drawdowns expand when there are no risk controls.",
                    "risk_controls": ["loss cap", "no adding to losers"],
                },
            ],
        },
        "hypothesis": (
            "Fixed-fractional risk sizing plus a portfolio drawdown circuit breaker reduces "
            "drawdown severity and recovery time while preserving existing signal logic."
        ),
        "target_failure_mode": "drawdown_fail",
        "base_strategy_selection": {
            "mode": "explicit_base_strategy",
            "allowed_to_modify_signals": False,
            "allowed_to_modify_entries": False,
            "allowed_to_modify_exits": False,
        },
        "base_strategy": {
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
            "primary_metrics": [
                "max_drawdown",
                "drawdown_duration",
                "recovery_time",
                "survival_rate",
            ],
            "secondary_metrics": ["CAGR", "Sharpe", "turnover", "cost_stress"],
            "comparison": "same signals with and without risk overlay",
            "required_gates": ["walk_forward", "drawdown", "cost_stress", "stability"],
        },
        "safety": {
            "promotion_allowed": False,
            "registry_write_allowed": False,
            "backtest_allowed_in_this_step": False,
            "strategy_code_modification_allowed": False,
            "requires_manual_review": True,
        },
    }


def _candidate_artifact() -> dict[str, object]:
    draft = _draft()
    entry = build_risk_overlay_hypothesis_queue_entry(
        draft,
        source_draft="tmp/risk_overlay_candidate_draft.json",
    )
    entry["candidate_artifact_hash"] = "sha256:review-candidate-001"
    queue_row = copy.deepcopy(entry["queue_row"])
    queue_row["source_notes"] = copy.deepcopy(draft["source"]["source_notes"])
    entry["queue_row"] = queue_row
    return entry


def _queue_row() -> dict[str, object]:
    return copy.deepcopy(_candidate_artifact()["queue_row"])


def test_valid_candidate_converts_to_deterministic_execution_spec():
    artifact = _candidate_artifact()

    first = build_risk_overlay_execution_spec(artifact, source_artifact_path="tmp/review_candidate.json")
    second = build_risk_overlay_execution_spec(copy.deepcopy(artifact), source_artifact_path="tmp/review_candidate.json")

    assert first == second
    assert first["adapter_version"] == "risk_overlay_execution_adapter_v1"
    assert first["execution_spec_supported"] is True
    assert first["appendable_to_registry"] is False
    assert first["requires_human_review"] is True
    assert first["source_runtime_supported"] is False
    assert first["provenance"]["blocker"] == "drawdown_fail"
    assert first["provenance"]["candidate_artifact_hash"] == "sha256:review-candidate-001"
    assert first["provenance"]["source_artifact_type"] == "hypothesis_queue_entry_candidate"
    assert first["provenance"]["source_artifact_version"] == "hypothesis_queue_entry_candidate_v1"
    assert first["provenance"]["source_notes"] == artifact["queue_row"]["source_notes"]
    assert first["execution_spec"] == {
        "family": "LONGTERM",
        "asset_class": "ETF",
        "timeframe": "1D",
        "short_name": "TREND_VOL_CAP_RISK_OVERLAY_V1",
        "hypothesis": f"{artifact['queue_row']['title']}: {artifact['queue_row']['rationale']}",
        "rules": "Hold SPY above SMA200 with realized-volatility targeting capped at 75% exposure; otherwise hold cash.",
        "builder": "risk_overlay_execution_adapter_v1",
        "parameters": {
            "base_strategy": artifact["queue_row"]["base_strategy"],
            "base_strategy_selection": artifact["queue_row"]["base_strategy_selection"],
            "risk_overlay": artifact["queue_row"]["risk_overlay"],
            "validation_plan": artifact["queue_row"]["validation_plan"],
            "source_hypothesis_id": artifact["queue_row"]["hypothesis_id"],
            "source_title": artifact["queue_row"]["source_title"],
            "source_note_ids": artifact["queue_row"]["source_note_ids"],
            "target_failure_mode": "drawdown_fail",
            "requires_human_review": True,
            "source_runtime_supported": False,
            "appendable_to_registry": False,
        },
    }


def test_direct_queue_row_input_converts_with_queue_row_provenance():
    queue_row = _queue_row()
    payload = build_risk_overlay_execution_spec(queue_row, source_artifact_path="tmp/risk_overlay_queue_row.json")

    assert payload["provenance"]["source_artifact_type"] == "risk_overlay_hypothesis_queue_row"
    assert payload["provenance"]["source_artifact_version"] == "risk_overlay_hypothesis_queue_row_v1"
    assert payload["execution_spec"]["parameters"]["source_hypothesis_id"] == queue_row["hypothesis_id"]


def test_missing_provenance_fails_closed():
    artifact = _candidate_artifact()
    artifact["queue_row"]["source_note_ids"] = []

    with pytest.raises(ValueError, match="source_note_ids provenance"):
        build_risk_overlay_execution_spec(artifact)


def test_source_runtime_supported_false_is_required():
    artifact = _candidate_artifact()
    artifact["runtime_supported"] = True

    with pytest.raises(ValueError, match="runtime_supported=false"):
        build_risk_overlay_execution_spec(artifact)


def test_unsupported_blocker_fails_closed():
    artifact = _candidate_artifact()
    artifact["target_failure_mode"] = "negative_unseen_result"
    artifact["queue_row"]["target_failure_mode"] = "negative_unseen_result"

    with pytest.raises(ValueError, match="unsupported blocker"):
        build_risk_overlay_execution_spec(artifact)


def test_unsupported_strategy_family_fails_closed():
    artifact = _candidate_artifact()
    artifact["queue_row"]["base_strategy"]["family"] = "INTRADAY"
    artifact["queue_row"]["base_strategy"]["asset_class"] = "BTCUSDT"
    artifact["queue_row"]["base_strategy"]["timeframe"] = "15M"
    artifact["queue_row"]["base_strategy"]["builder"] = "intraday_vwap_rsi_reclaim"
    artifact["queue_row"]["base_strategy"]["short_name"] = "VWAP_RSI_RECLAIM"

    with pytest.raises(ValueError, match="unsupported strategy family"):
        build_risk_overlay_execution_spec(artifact)


def test_malformed_risk_overlay_parameters_fail_closed():
    artifact = _candidate_artifact()
    artifact["queue_row"]["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["thresholds"] = [
        {"drawdown_pct": 8, "gross_exposure_multiplier": 0.5},
        {"drawdown_pct": 5, "gross_exposure_multiplier": 0.75},
    ]

    with pytest.raises(ValueError, match="thresholds must be strictly increasing"):
        build_risk_overlay_execution_spec(artifact)


@pytest.mark.parametrize(
    ("field_name", "mutate"),
    [
        (
            "risk_per_trade_pct_candidates",
            lambda artifact: artifact["queue_row"]["risk_overlay"]["position_sizing"].__setitem__(
                "risk_per_trade_pct_candidates", [True, 0.5]
            ),
        ),
        (
            "threshold.drawdown_pct",
            lambda artifact: artifact["queue_row"]["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["thresholds"][0].__setitem__(
                "drawdown_pct", True
            ),
        ),
        (
            "threshold.gross_exposure_multiplier",
            lambda artifact: artifact["queue_row"]["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["thresholds"][0].__setitem__(
                "gross_exposure_multiplier", False
            ),
        ),
        (
            "reentry_rule.recovery_from_peak_pct",
            lambda artifact: artifact["queue_row"]["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["reentry_rule"].__setitem__(
                "recovery_from_peak_pct", True
            ),
        ),
        (
            "cooldown_days",
            lambda artifact: artifact["queue_row"]["risk_overlay"]["portfolio_drawdown_circuit_breaker"]["reentry_rule"].__setitem__(
                "cooldown_days", False
            ),
        ),
    ],
)
def test_boolean_numeric_risk_overlay_fields_fail_closed(field_name, mutate):
    artifact = _candidate_artifact()
    mutate(artifact)

    with pytest.raises(ValueError, match="must not be boolean"):
        build_risk_overlay_execution_spec(artifact)


def test_lossy_conversion_fails_closed():
    artifact = _candidate_artifact()
    artifact["queue_row"]["base_strategy"]["builder"] = "long_term_vol_target"

    with pytest.raises(ValueError, match="lossy conversion"):
        build_risk_overlay_execution_spec(artifact)


def test_no_registry_append_path_is_called(monkeypatch):
    calls: list[tuple[Path, dict]] = []

    def _unexpected_append(path: Path, payload: dict) -> None:
        calls.append((path, payload))
        raise AssertionError("append_jsonl must not be called")

    monkeypatch.setattr("research_lab.registry.append_jsonl", _unexpected_append)

    payload = build_risk_overlay_execution_spec(_candidate_artifact())

    assert payload["appendable_to_registry"] is False
    assert calls == []


def test_cli_writes_only_the_explicit_output_json_path_and_is_byte_stable(tmp_path):
    input_path = tmp_path / "review_candidate.json"
    output_path = tmp_path / "out" / "execution_spec.json"
    second_output_path = tmp_path / "out-2" / "execution_spec.json"
    input_path.write_text(json.dumps(_candidate_artifact(), indent=2) + "\n", encoding="utf-8")

    first = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path), "--output", str(second_output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert output_path.read_bytes() == second_output_path.read_bytes()
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "out-2/execution_spec.json",
        "out/execution_spec.json",
        "review_candidate.json",
    ]


def test_module_and_cli_do_not_import_provider_pdf_backtest_or_registry_append_modules():
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
    for path in (MODULE_PATH, SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
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
            ), f"{path.name} imported forbidden module {import_name}"
