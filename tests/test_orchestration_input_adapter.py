from __future__ import annotations

import ast
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from research_lab.orchestration.input_adapter import build_orchestration_input
from research_lab.orchestration.orchestrator import orchestrate_research_step


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "research_lab" / "orchestration" / "input_adapter.py"
BUILD_SCRIPT_PATH = ROOT / "scripts" / "build_orchestration_input.py"
DECISION_SCRIPT_PATH = ROOT / "scripts" / "run_orchestration_decision.py"
RUNNER_PATH = ROOT / "research_lab" / "runner.py"
DEPLOYMENT_GATE_PATH = ROOT / "research_lab" / "deployment_gate.py"
LEGACY_SCRIPT_PATH = ROOT / "scripts" / "run_research_orchestrator.py"
FORBIDDEN_IMPORT_TARGETS = (
    "research_lab.runner",
    "research_lab.deployment_gate",
    "research_lab.backtest",
    "research_lab.walk_forward",
    "research_lab.strategies",
    "research_lab.reports",
    "research_lab.hermes",
    "deploy",
    "ops.systemd",
    "requests",
    "urllib",
    "httpx",
    "socket",
)


def test_missing_artifacts_return_empty_lists(tmp_path):
    payload = build_orchestration_input(tmp_path)

    assert payload == {
        "recent_failures": [],
        "daily_results": [],
        "deployment_gate_rows": [],
    }


def test_experiments_jsonl_reads_valid_rows_and_ignores_malformed_lines(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    path = registry / "experiments.jsonl"
    rows = [
        {"strategy_id": "ok-1", "tier": "A", "tier_reason": "", "data_source": "eodhd", "history_length": 12.0},
        {"strategy_id": "rej-1", "tier": "Rejected", "tier_reason": "rolling walk-forward not passed", "data_manifest": {"source": "massive", "years": 5.0}},
        {"strategy_id": "rej-2", "tier": "C", "tier_reason": "cost stress failed", "data_manifest": {"source": "eodhd", "years": 11.0}},
    ]
    path.write_text(
        "\n".join(
            [
                json.dumps(rows[0]),
                "{bad json",
                "",
                json.dumps(rows[1]),
                json.dumps(rows[2]),
            ]
        ),
        encoding="utf-8",
    )

    payload = build_orchestration_input(tmp_path)

    assert [row["strategy_id"] for row in payload["daily_results"]] == ["rej-1", "rej-2"]
    assert payload["daily_results"][0]["data_source"] == "massive"
    assert payload["daily_results"][0]["history_length"] == 5.0
    assert payload["daily_results"][1]["data_source"] == "eodhd"
    assert payload["daily_results"][1]["history_length"] == 11.0


def test_experiments_jsonl_limits_to_most_recent_rows(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    path = registry / "experiments.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "strategy_id": f"rej-{index}",
                    "tier": "Rejected",
                    "tier_reason": f"failure-{index}",
                    "data_source": "eodhd",
                    "history_length": float(index),
                }
            )
            for index in range(5)
        ),
        encoding="utf-8",
    )

    payload = build_orchestration_input(tmp_path, max_experiments=2)

    assert [row["strategy_id"] for row in payload["daily_results"]] == ["rej-3", "rej-4"]


def test_recent_failures_uses_only_explicit_blockers_from_experiments(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    path = registry / "experiments.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "strategy_id": "rej-1",
                        "tier": "Rejected",
                        "tier_reason": "drawdown",
                        "blockers": ["drawdown_fail", "unknown_blocker"],
                    }
                ),
                json.dumps(
                    {
                        "strategy_id": "rej-2",
                        "tier": "Rejected",
                        "tier_reason": "walk-forward not passed",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    payload = build_orchestration_input(tmp_path)

    assert payload["recent_failures"] == [{"experiment_id": "rej-1", "blockers": ["drawdown_fail"]}]


def test_deployment_gate_uses_latest_file_and_reads_only_failing_rows(tmp_path):
    weekly = tmp_path / "reports" / "weekly"
    weekly.mkdir(parents=True)
    older = weekly / "2026-W10_deployment_gate.csv"
    newer = weekly / "2026-W11_deployment_gate.csv"

    _write_gate_csv(
        older,
        [{"strategy_id": "old", "gate_verdict": "fail", "paper_eligible": "False", "reasons": "['drawdown_below_threshold']"}],
    )
    _write_gate_csv(
        newer,
        [
            {"strategy_id": "keep-1", "gate_verdict": "fail", "paper_eligible": "False", "reasons": "['rolling_walk_forward_not_passed', 'drawdown_below_threshold']"},
            {"strategy_id": "skip-1", "gate_verdict": "pass", "paper_eligible": "True", "reasons": "[]"},
            {"strategy_id": "keep-2", "gate_verdict": "blocked", "paper_eligible": "False", "reasons": ""},
        ],
    )
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    payload = build_orchestration_input(tmp_path)

    assert [row["strategy_id"] for row in payload["deployment_gate_rows"]] == ["keep-1", "keep-2"]
    assert payload["deployment_gate_rows"][0]["gate_verdict"] == "fail"
    assert "original_gate_verdict" not in payload["deployment_gate_rows"][0]
    assert payload["deployment_gate_rows"][0]["reasons"] == [
        "rolling_walk_forward_not_passed",
        "drawdown_below_threshold",
    ]
    assert payload["deployment_gate_rows"][1]["gate_verdict"] == "fail"
    assert payload["deployment_gate_rows"][1]["original_gate_verdict"] == "blocked"
    assert payload["deployment_gate_rows"][1]["reasons"] == []


def test_deployment_gate_parses_json_list_python_repr_and_semicolon_strings(tmp_path):
    weekly = tmp_path / "reports" / "weekly"
    weekly.mkdir(parents=True)
    path = weekly / "2026-W12_deployment_gate.csv"
    _write_gate_csv(
        path,
        [
            {"strategy_id": "json", "gate_verdict": "fail", "paper_eligible": "False", "reasons": '["insufficient_history","drawdown_below_threshold"]'},
            {"strategy_id": "repr", "gate_verdict": "fail", "paper_eligible": "False", "reasons": "['parameter_verdict_not_passed']"},
            {"strategy_id": "semi", "gate_verdict": "fail", "paper_eligible": "False", "reasons": "rolling_walk_forward_not_passed;drawdown_below_threshold"},
        ],
    )

    payload = build_orchestration_input(tmp_path)
    reasons_by_id = {row["strategy_id"]: row["reasons"] for row in payload["deployment_gate_rows"]}

    assert reasons_by_id["json"] == ["insufficient_history", "drawdown_below_threshold"]
    assert reasons_by_id["repr"] == ["parameter_verdict_not_passed"]
    assert reasons_by_id["semi"] == ["rolling_walk_forward_not_passed", "drawdown_below_threshold"]


def test_deployment_gate_limits_rows(tmp_path):
    weekly = tmp_path / "reports" / "weekly"
    weekly.mkdir(parents=True)
    path = weekly / "2026-W12_deployment_gate.csv"
    _write_gate_csv(
        path,
        [
            {"strategy_id": f"id-{index}", "gate_verdict": "fail", "paper_eligible": "False", "reasons": "[]"}
            for index in range(5)
        ],
    )

    payload = build_orchestration_input(tmp_path, max_gate_rows=2)

    assert [row["strategy_id"] for row in payload["deployment_gate_rows"]] == ["id-0", "id-1"]


def test_input_adapter_and_builder_script_avoid_forbidden_import_targets():
    for path in (ADAPTER_PATH, BUILD_SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        for forbidden in FORBIDDEN_IMPORT_TARGETS:
            assert forbidden not in imports


def test_cli_writes_only_requested_output_and_handles_missing_artifacts(tmp_path):
    output_path = tmp_path / "nested" / "orchestrator_input.json"

    subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT_PATH),
            "--root",
            str(tmp_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {
        "recent_failures": [],
        "daily_results": [],
        "deployment_gate_rows": [],
    }
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        "nested/orchestrator_input.json"
    ]


def test_cli_output_is_compatible_with_orchestrator(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "experiments.jsonl").write_text(
        json.dumps(
            {
                "strategy_id": "rej-1",
                "tier": "Rejected",
                "tier_reason": "rolling walk-forward not passed",
                "data_manifest": {"source": "eodhd", "years": 12.0},
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "orchestrator_input.json"

    subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT_PATH),
            "--root",
            str(tmp_path),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    decision = orchestrate_research_step(payload, created_at="2026-06-14T12:00:00Z")

    assert decision.to_dict()["version"] == "orchestration_decision_v1"


def test_cli_smoke_builds_input_then_decision_from_fixtures(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "experiments.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "strategy_id": "rej-1",
                        "tier": "Rejected",
                        "tier_reason": "rolling walk-forward not passed",
                        "data_manifest": {"source": "eodhd", "years": 12.0},
                    }
                ),
                json.dumps(
                    {
                        "strategy_id": "rej-2",
                        "tier": "Rejected",
                        "tier_reason": "Unseen max drawdown exceeds 15%.",
                        "blockers": ["drawdown_fail"],
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    weekly = tmp_path / "reports" / "weekly"
    weekly.mkdir(parents=True)
    _write_gate_csv(
        weekly / "2026-W99_deployment_gate.csv",
        [
            {
                "strategy_id": "rej-1",
                "gate_verdict": "fail",
                "paper_eligible": "False",
                "reasons": "rolling_walk_forward_not_passed;drawdown_below_threshold",
            }
        ],
    )
    input_path = tmp_path / "out" / "orchestrator_input.json"
    decision_path = tmp_path / "out" / "orchestrator_decision.json"

    subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT_PATH),
            "--root",
            str(tmp_path),
            "--output",
            str(input_path),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(DECISION_SCRIPT_PATH),
            "--input",
            str(input_path),
            "--output",
            str(decision_path),
        ],
        cwd=ROOT,
        check=True,
    )

    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["version"] == "orchestration_decision_v1"
    assert decision["selected_blocker"] in {"walk_forward_fail", "drawdown_fail"}


def test_protected_files_remain_unmodified_in_worktree():
    for path in (RUNNER_PATH, DEPLOYMENT_GATE_PATH, LEGACY_SCRIPT_PATH):
        result = subprocess.run(
            ["git", "diff", "--name-only", "--", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == ""


def _write_gate_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["strategy_id", "gate_verdict", "paper_eligible", "reasons"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
