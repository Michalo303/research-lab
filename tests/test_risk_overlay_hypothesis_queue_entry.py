from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from research_lab.orchestration.risk_overlay_hypothesis_queue import (
    build_risk_overlay_hypothesis_queue_entry,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_risk_overlay_hypothesis_queue_entry.py"
MODULE_PATH = ROOT / "research_lab" / "orchestration" / "risk_overlay_hypothesis_queue.py"


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
            "mode": "near_miss_drawdown",
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


def _is_forbidden_import(import_name: str, forbidden_root: str) -> bool:
    return import_name == forbidden_root or import_name.startswith(forbidden_root + ".")


def test_wrapper_version_and_incompatible_shape():
    entry = build_risk_overlay_hypothesis_queue_entry(_draft(), source_draft="tmp/risk_overlay_candidate_draft.json")

    assert entry["version"] == "hypothesis_queue_entry_candidate_v1"
    assert entry["compatible"] is False
    assert entry["queue_row"]["family"] == "RISK_OVERLAY"
    assert entry["source_draft"] == "tmp/risk_overlay_candidate_draft.json"
    assert entry["target_failure_mode"] == "drawdown_fail"
    assert entry["hypothesis_id"]


def test_source_note_ids_are_preserved():
    entry = build_risk_overlay_hypothesis_queue_entry(_draft(), source_draft="tmp/risk_overlay_candidate_draft.json")

    assert entry["source_note_ids"] == [
        "note-1111111111111111",
        "note-2222222222222222",
    ]
    assert entry["queue_row"]["source_note_ids"] == [
        "note-1111111111111111",
        "note-2222222222222222",
    ]


def test_queue_row_preserves_risk_overlay_and_validation_fields():
    entry = build_risk_overlay_hypothesis_queue_entry(_draft(), source_draft="tmp/risk_overlay_candidate_draft.json")

    queue_row = entry["queue_row"]
    assert queue_row["target_failure_mode"] == "drawdown_fail"
    assert queue_row["base_strategy_selection"] == _draft()["base_strategy_selection"]
    assert queue_row["risk_overlay"]["position_sizing"] == _draft()["risk_overlay"]["position_sizing"]
    assert (
        queue_row["risk_overlay"]["portfolio_drawdown_circuit_breaker"]
        == _draft()["risk_overlay"]["portfolio_drawdown_circuit_breaker"]
    )
    assert queue_row["risk_overlay"]["loser_addition_rule"] == _draft()["risk_overlay"]["loser_addition_rule"]
    assert queue_row["validation_plan"] == _draft()["validation_plan"]


def test_reason_mentions_runtime_hook_and_missing_execution_support():
    entry = build_risk_overlay_hypothesis_queue_entry(_draft(), source_draft="tmp/risk_overlay_candidate_draft.json")

    reason = entry["reason"]
    assert "RISK_OVERLAY" in reason
    assert "fixed-fractional position sizing" in reason
    assert "portfolio drawdown circuit breaker" in reason
    assert "loser-addition rule" in reason
    assert "base strategy binding" in reason


def test_safety_forbids_registry_append_backtest_and_promotion():
    entry = build_risk_overlay_hypothesis_queue_entry(_draft(), source_draft="tmp/risk_overlay_candidate_draft.json")

    assert entry["safety"] == {
        "registry_append_allowed": False,
        "backtest_allowed_in_this_step": False,
        "promotion_allowed": False,
        "requires_manual_review": True,
    }


def test_cli_writes_only_requested_output_file(tmp_path):
    draft_path = tmp_path / "risk_overlay_candidate_draft.json"
    output_path = tmp_path / "out" / "risk_overlay_hypothesis_queue_entry.json"
    draft_path.write_text(json.dumps(_draft()) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--draft", str(draft_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["version"] == "hypothesis_queue_entry_candidate_v1"
    assert payload["compatible"] is False
    assert payload["queue_row"]["family"] == "RISK_OVERLAY"
    files = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert files == ["out/risk_overlay_hypothesis_queue_entry.json", "risk_overlay_candidate_draft.json"]


def test_module_and_cli_do_not_import_provider_pdf_backtest_or_registry_modules():
    forbidden_roots = (
        "hermes_knowledge",
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
                _is_forbidden_import(import_name, forbidden_root)
                for forbidden_root in forbidden_roots
            ), f"{path.name} imported forbidden module {import_name}"
