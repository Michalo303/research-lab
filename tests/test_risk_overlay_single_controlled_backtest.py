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
from research_lab.orchestration.risk_overlay_single_controlled_backtest_v1 import (
    build_single_controlled_backtest_plan,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "orchestration" / "risk_overlay_single_controlled_backtest_v1.py"
SCRIPT_PATH = ROOT / "scripts" / "build_risk_overlay_single_controlled_backtest_plan.py"


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


def _controlled_backtest_request() -> dict[str, object]:
    entry = build_risk_overlay_hypothesis_queue_entry(
        _draft(),
        source_draft="tmp/risk_overlay_candidate_draft.json",
    )
    queue_row = copy.deepcopy(entry["queue_row"])
    queue_row["source_notes"] = copy.deepcopy(_draft()["source"]["source_notes"])
    entry["queue_row"] = queue_row
    execution_spec = build_risk_overlay_execution_spec(entry, source_artifact_path="tmp/review_candidate.json")
    return build_controlled_backtest_request(
        execution_spec,
        source_execution_spec_path="tmp/execution_spec.json",
    )


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_valid_controlled_backtest_request_converts_to_deterministic_single_plan():
    artifact = _controlled_backtest_request()

    first = build_single_controlled_backtest_plan(
        artifact,
        source_controlled_backtest_request_path="tmp/controlled_backtest_request.json",
    )
    second = build_single_controlled_backtest_plan(
        copy.deepcopy(artifact),
        source_controlled_backtest_request_path="tmp/controlled_backtest_request.json",
    )

    assert first == second
    assert first["single_controlled_backtest_version"] == "risk_overlay_single_controlled_backtest_v1"
    assert first["execution_performed"] is False
    assert first["appendable_to_registry"] is False
    assert first["promotion_allowed"] is False
    assert first["deployment_allowed"] is False
    assert first["requires_human_review"] is True
    assert first["explicit_execution_required"] is True
    assert first["runner_compatibility_checked"] is True
    assert first["source_controlled_backtest_request_hash"] == _canonical_sha256(artifact)
    assert first["source_execution_spec_hash"] == artifact["source_execution_spec_hash"]
    assert first["provenance"] == artifact["provenance"]
    assert first["execution_spec"] == artifact["execution_spec"]


def test_missing_input_artifact_version_fails_closed():
    artifact = _controlled_backtest_request()
    artifact.pop("version", None)

    with pytest.raises(ValueError, match="artifact.version"):
        build_single_controlled_backtest_plan(artifact)


def test_wrong_input_artifact_version_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["version"] = "controlled_backtest_request_v0"

    with pytest.raises(ValueError, match="artifact.version"):
        build_single_controlled_backtest_plan(artifact)


def test_wrong_controlled_backtest_version_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["controlled_backtest_version"] = "risk_overlay_controlled_backtest_v0"

    with pytest.raises(ValueError, match="controlled_backtest_version"):
        build_single_controlled_backtest_plan(artifact)


def test_execution_enabled_by_default_true_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["execution_enabled_by_default"] = True

    with pytest.raises(ValueError, match="execution_enabled_by_default=false"):
        build_single_controlled_backtest_plan(artifact)


def test_appendable_to_registry_true_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["appendable_to_registry"] = True

    with pytest.raises(ValueError, match="appendable_to_registry=false"):
        build_single_controlled_backtest_plan(artifact)


def test_promotion_allowed_true_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["promotion_allowed"] = True

    with pytest.raises(ValueError, match="promotion_allowed=false"):
        build_single_controlled_backtest_plan(artifact)


def test_deployment_allowed_true_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["deployment_allowed"] = True

    with pytest.raises(ValueError, match="deployment_allowed=false"):
        build_single_controlled_backtest_plan(artifact)


def test_requires_human_review_false_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["requires_human_review"] = False

    with pytest.raises(ValueError, match="requires_human_review=true"):
        build_single_controlled_backtest_plan(artifact)


def test_missing_provenance_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["provenance"] = {}

    with pytest.raises(ValueError, match="provenance"):
        build_single_controlled_backtest_plan(artifact)


@pytest.mark.parametrize("missing_key", ["source_execution_spec_hash", "execution_spec"])
def test_missing_required_input_fields_fail_closed(missing_key):
    artifact = _controlled_backtest_request()
    artifact.pop(missing_key, None)

    with pytest.raises(ValueError, match=missing_key):
        build_single_controlled_backtest_plan(artifact)


def test_incompatible_forwarded_execution_spec_fails_closed():
    artifact = _controlled_backtest_request()
    artifact["execution_spec"]["builder"] = "risk_overlay_execution_adapter_v0"

    with pytest.raises(ValueError, match="execution_spec.builder"):
        build_single_controlled_backtest_plan(artifact)


def test_explicit_execution_request_fails_closed_without_calling_runner():
    artifact = _controlled_backtest_request()

    with pytest.raises(ValueError, match="disabled in v1"):
        build_single_controlled_backtest_plan(artifact, run_single_controlled_backtest=True)


def test_cli_writes_only_the_explicit_output_path(tmp_path):
    input_path = tmp_path / "controlled_backtest_request.json"
    output_path = tmp_path / "out" / "single_controlled_backtest_plan.json"
    input_path.write_text(json.dumps(_controlled_backtest_request(), indent=2) + "\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "controlled_backtest_request.json",
        "out/single_controlled_backtest_plan.json",
    ]


def test_cli_execution_flag_fails_closed_and_still_does_not_call_real_backtest(tmp_path):
    input_path = tmp_path / "controlled_backtest_request.json"
    output_path = tmp_path / "out" / "single_controlled_backtest_plan.json"
    input_path.write_text(json.dumps(_controlled_backtest_request(), indent=2) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--run-single-controlled-backtest",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "disabled in v1" in result.stderr
    assert not output_path.exists()


def test_no_registry_append_path_is_called(monkeypatch):
    calls: list[tuple[Path, dict]] = []

    def _unexpected_append(path: Path, payload: dict) -> None:
        calls.append((path, payload))
        raise AssertionError("append_jsonl must not be called")

    monkeypatch.setattr("research_lab.registry.append_jsonl", _unexpected_append)

    payload = build_single_controlled_backtest_plan(_controlled_backtest_request())

    assert payload["appendable_to_registry"] is False
    assert calls == []


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
