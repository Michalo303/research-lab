from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_lab.orchestration.risk_overlay_candidate import build_risk_overlay_candidate_draft


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_risk_overlay_candidate_draft.py"
MODULE_PATH = ROOT / "research_lab" / "orchestration" / "risk_overlay_candidate.py"


def _note(note_id: str, *, blocker: str = "drawdown_fail", book_title: str = "Money Management Risk Control For Traders (2002)") -> dict[str, object]:
    return {
        "version": "extracted_book_note_v1",
        "note_id": note_id,
        "book_id": "book-risk-control-2002",
        "book_title": book_title,
        "page_start": 44,
        "page_end": 46,
        "blocker": blocker,
        "confidence": "medium",
        "promotion_status": "not_promoted",
        "extracted_claim": "Trading accuracy cannot compensate for poor money management.",
        "trading_hypothesis": "Keep the signal logic but reduce drawdowns with explicit risk overlays.",
        "why_relevant_to_blocker": "The note argues that preservation and drawdown control matter more than signal tweaking.",
        "implementation_hint": "Apply fixed fractional sizing and a staged drawdown circuit breaker.",
        "risk_controls": ["fixed fractional sizing", "drawdown circuit breaker"],
        "validation_hint": "Compare drawdown severity and recovery time against the same signals without overlays.",
    }


def _is_forbidden_import(import_name: str, forbidden_root: str) -> bool:
    return import_name == forbidden_root or import_name.startswith(forbidden_root + ".")


def test_build_candidate_experiment_draft_from_drawdown_notes():
    draft = build_risk_overlay_candidate_draft(
        [
            _note("note-1111111111111111"),
            _note("note-2222222222222222"),
        ]
    )

    assert draft["version"] == "candidate_experiment_draft_v1"
    assert draft["source"]["blocker"] == "drawdown_fail"
    assert [item["note_id"] for item in draft["source"]["source_notes"]] == [
        "note-1111111111111111",
        "note-2222222222222222",
    ]
    assert draft["hypothesis"] == (
        "Fixed-fractional risk sizing plus a portfolio drawdown circuit breaker reduces "
        "drawdown severity and recovery time while preserving existing signal logic."
    )
    assert draft["target_failure_mode"] == "drawdown_fail"


def test_draft_locks_signal_changes_and_adds_risk_overlay_plan():
    draft = build_risk_overlay_candidate_draft([_note("note-1111111111111111")])

    assert draft["base_strategy_selection"] == {
        "mode": "near_miss_drawdown",
        "allowed_to_modify_signals": False,
        "allowed_to_modify_entries": False,
        "allowed_to_modify_exits": False,
    }
    assert draft["risk_overlay"]["position_sizing"] == {
        "type": "fixed_fractional",
        "risk_per_trade_pct_candidates": [0.25, 0.5, 0.75, 1.0],
    }
    assert draft["risk_overlay"]["portfolio_drawdown_circuit_breaker"] == {
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
    }
    assert draft["risk_overlay"]["loser_addition_rule"] == {"add_to_losers_allowed": False}


def test_draft_includes_validation_plan_and_locked_safety():
    draft = build_risk_overlay_candidate_draft([_note("note-1111111111111111")])

    assert draft["validation_plan"] == {
        "primary_metrics": [
            "max_drawdown",
            "drawdown_duration",
            "recovery_time",
            "survival_rate",
        ],
        "secondary_metrics": [
            "CAGR",
            "Sharpe",
            "turnover",
            "cost_stress",
        ],
        "comparison": "same signals with and without risk overlay",
        "required_gates": [
            "walk_forward",
            "drawdown",
            "cost_stress",
            "stability",
        ],
    }
    assert draft["safety"] == {
        "promotion_allowed": False,
        "registry_write_allowed": False,
        "backtest_allowed_in_this_step": False,
        "strategy_code_modification_allowed": False,
        "requires_manual_review": True,
    }


def test_builder_keeps_only_drawdown_fail_source_notes():
    draft = build_risk_overlay_candidate_draft(
        [
            _note("note-1111111111111111"),
            _note("note-2222222222222222", blocker="walk_forward_fail"),
        ]
    )

    assert [item["note_id"] for item in draft["source"]["source_notes"]] == ["note-1111111111111111"]


def test_cli_writes_only_requested_output_file(tmp_path):
    notes_path = tmp_path / "notes.jsonl"
    output_path = tmp_path / "out" / "risk_overlay_candidate_draft.json"
    notes_path.write_text(json.dumps(_note("note-1111111111111111")) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--notes", str(notes_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8"))["version"] == "candidate_experiment_draft_v1"
    files = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert files == ["notes.jsonl", "out/risk_overlay_candidate_draft.json"]


def test_module_and_cli_do_not_import_provider_or_runtime_modules():
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
