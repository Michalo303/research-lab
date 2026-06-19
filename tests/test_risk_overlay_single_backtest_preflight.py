from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_lab.orchestration.risk_overlay_controlled_backtest_v1 import (
    build_controlled_backtest_request,
)
from research_lab.orchestration.risk_overlay_execution_adapter_v1 import (
    build_risk_overlay_execution_spec,
)
from research_lab.orchestration.risk_overlay_hypothesis_queue import (
    build_risk_overlay_hypothesis_queue_entry,
)
from research_lab.orchestration.risk_overlay_single_backtest_preflight_v1 import (
    build_single_backtest_preflight,
)
from research_lab.orchestration.risk_overlay_single_controlled_backtest_v1 import (
    build_single_controlled_backtest_plan,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "orchestration" / "risk_overlay_single_backtest_preflight_v1.py"
SCRIPT_PATH = ROOT / "scripts" / "build_risk_overlay_single_backtest_preflight.py"


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
                }
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
            "primary_metrics": ["max_drawdown", "drawdown_duration", "recovery_time", "survival_rate"],
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


def _single_controlled_backtest_plan() -> dict[str, object]:
    entry = build_risk_overlay_hypothesis_queue_entry(
        _draft(),
        source_draft="tmp/risk_overlay_candidate_draft.json",
    )
    queue_row = copy.deepcopy(entry["queue_row"])
    queue_row["source_notes"] = copy.deepcopy(_draft()["source"]["source_notes"])
    entry["queue_row"] = queue_row
    execution_spec = build_risk_overlay_execution_spec(entry, source_artifact_path="tmp/review_candidate.json")
    request = build_controlled_backtest_request(
        execution_spec,
        source_execution_spec_path="tmp/execution_spec.json",
    )
    return build_single_controlled_backtest_plan(
        request,
        source_controlled_backtest_request_path="tmp/controlled_backtest_request.json",
    )


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_valid_plan_converts_to_deterministic_preflight_artifact():
    artifact = _single_controlled_backtest_plan()

    first = build_single_backtest_preflight(
        artifact,
        source_single_controlled_backtest_plan_path="tmp/single_controlled_backtest_plan.json",
    )
    second = build_single_backtest_preflight(
        copy.deepcopy(artifact),
        source_single_controlled_backtest_plan_path="tmp/single_controlled_backtest_plan.json",
    )

    assert first == second
    assert first["preflight_version"] == "risk_overlay_single_backtest_preflight_v1"
    assert first["execution_performed"] is False
    assert first["appendable_to_registry"] is False
    assert first["promotion_allowed"] is False
    assert first["deployment_allowed"] is False
    assert first["requires_human_review"] is True
    assert first["source_single_controlled_backtest_plan_hash"] == _canonical_sha256(artifact)
    assert first["runner_interface_available"] is False
    assert first["side_effect_risk"] == "unsafe"
    assert isinstance(first["blocking_reasons"], list) and first["blocking_reasons"]
    assert first["provenance"] == artifact["provenance"]


def test_wrong_single_controlled_backtest_version_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["single_controlled_backtest_version"] = "risk_overlay_single_controlled_backtest_v0"

    with pytest.raises(ValueError, match="single_controlled_backtest_version"):
        build_single_backtest_preflight(artifact)


def test_execution_performed_true_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["execution_performed"] = True

    with pytest.raises(ValueError, match="execution_performed=false"):
        build_single_backtest_preflight(artifact)


def test_explicit_execution_required_false_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["explicit_execution_required"] = False

    with pytest.raises(ValueError, match="explicit_execution_required=true"):
        build_single_backtest_preflight(artifact)


def test_appendable_to_registry_true_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["appendable_to_registry"] = True

    with pytest.raises(ValueError, match="appendable_to_registry=false"):
        build_single_backtest_preflight(artifact)


def test_promotion_allowed_true_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["promotion_allowed"] = True

    with pytest.raises(ValueError, match="promotion_allowed=false"):
        build_single_backtest_preflight(artifact)


def test_deployment_allowed_true_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["deployment_allowed"] = True

    with pytest.raises(ValueError, match="deployment_allowed=false"):
        build_single_backtest_preflight(artifact)


def test_requires_human_review_false_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["requires_human_review"] = False

    with pytest.raises(ValueError, match="requires_human_review=true"):
        build_single_backtest_preflight(artifact)


def test_missing_provenance_fails_closed():
    artifact = _single_controlled_backtest_plan()
    artifact["provenance"] = {}

    with pytest.raises(ValueError, match="provenance"):
        build_single_backtest_preflight(artifact)


@pytest.mark.parametrize("missing_key", ["source_controlled_backtest_request_hash", "execution_spec"])
def test_missing_required_input_fields_fail_closed(missing_key):
    artifact = _single_controlled_backtest_plan()
    artifact.pop(missing_key, None)

    with pytest.raises(ValueError, match=missing_key):
        build_single_backtest_preflight(artifact)


def test_output_hash_is_canonical_sha256_of_full_input_plan():
    artifact = _single_controlled_backtest_plan()

    payload = build_single_backtest_preflight(artifact)

    assert payload["source_single_controlled_backtest_plan_hash"] == _canonical_sha256(artifact)


def test_side_effect_risk_defaults_to_unknown_or_unsafe_when_not_proven_safe():
    artifact = _single_controlled_backtest_plan()

    payload = build_single_backtest_preflight(artifact)

    assert payload["side_effect_risk"] in {"unknown", "unsafe"}
    assert payload["side_effect_risk"] != "safe"


def test_cli_writes_only_the_explicit_output_path(tmp_path):
    input_path = tmp_path / "single_controlled_backtest_plan.json"
    output_path = tmp_path / "out" / "single_backtest_preflight.json"
    input_path.write_text(json.dumps(_single_controlled_backtest_plan(), indent=2) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "out/single_backtest_preflight.json",
        "single_controlled_backtest_plan.json",
    ]


def test_no_registry_append_path_is_called(monkeypatch):
    calls: list[tuple[Path, dict]] = []

    def _unexpected_append(path: Path, payload: dict) -> None:
        calls.append((path, payload))
        raise AssertionError("append_jsonl must not be called")

    monkeypatch.setattr("research_lab.registry.append_jsonl", _unexpected_append)

    payload = build_single_backtest_preflight(_single_controlled_backtest_plan())

    assert payload["appendable_to_registry"] is False
    assert calls == []


def test_module_and_cli_do_not_import_provider_pdf_backtest_or_registry_append_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
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
