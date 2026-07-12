from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_lab.execution.orchestrator_run_bundle_contract_v1 import (
    build_orchestrator_run_bundle_contract,
)
from research_lab.execution.review_only_orchestrator_cli_v1 import (
    prepare_review_only_orchestrator_bundle,
)
from tests.test_knihomol_readonly_evidence_adapter_v1 import (
    _corpus,
)
from tests.test_orchestrator_run_bundle_contract_v1 import (
    _orchestrator_request,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research_lab" / "execution" / "review_only_orchestrator_cli_v1.py"
SCRIPT_PATH = ROOT / "scripts" / "run_review_only_orchestrator_cli.py"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _rows() -> list[dict[str, object]]:
    return [
        {
            "timestamp": "2026-01-05T09:30:00-05:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.5,
            "volume": 1_000_000,
        },
        {
            "timestamp": "2026-01-06T09:30:00-05:00",
            "open": 101.0,
            "high": 102.0,
            "low": 100.5,
            "close": 101.5,
            "volume": 1_100_000,
        },
    ]


def _bridge_shallow_evidence() -> dict[str, object]:
    return {
        "notes": [
            {
                "note_id": "note-1111111111111111",
                "status": "validated",
                "topic": "drawdown_fail",
                "summary": "Add a defensive risk overlay.",
                "supports": ["drawdown"],
            },
            {
                "note_id": "note-2222222222222222",
                "status": "validated",
                "topic": "walk_forward_fail",
                "summary": "Increase rolling OOS coverage.",
                "supports": ["walk_forward"],
            },
        ]
    }


def _ohlcv_request(path: Path) -> dict[str, object]:
    return {
        "version": "local_ohlcv_file_input_adapter_request_v1",
        "file_path": str(path.resolve()),
        "format": "json",
        "dataset_id": "QQQ_2026_SAMPLE",
        "symbol": "QQQ",
        "max_bytes": 1_000_000,
        "max_rows": 10,
        "provenance": {"source": "unit_test"},
    }


def _knihomol_request(corpus_path: Path) -> dict[str, object]:
    return {
        "version": "knihomol_readonly_evidence_adapter_request_v1",
        "corpus_base": str(corpus_path.resolve()),
        "requested_notes": [
            {"note_id": "note-1111111111111111", "blocker": "drawdown_fail"},
            {"note_id": "note-2222222222222222", "blocker": "walk_forward_fail"},
        ],
        "evidence_purpose": "robustness_review",
        "provenance": {"source": "unit_test"},
    }


def _adapter_ready_orchestrator_request() -> dict[str, object]:
    request = copy.deepcopy(_orchestrator_request())
    request["experiment_manifest_request"]["experiment_id"] = "EXP-CLI-001"
    request["experiment_manifest_request"]["dataset_identity"] = {
        "dataset_id": "QQQ_2026_SAMPLE",
        "data_source": "synthetic_local_bars",
        "symbol": "SYNTH_QQQ",
        "bar_count": 2,
    }
    request["experiment_manifest_request"]["immutable_input_hashes"]["dataset_identity"] = _canonical_sha256(
        request["experiment_manifest_request"]["dataset_identity"]
    )
    request["experiment_manifest_request"]["knowledge_note_ids"] = [
        "note-1111111111111111",
        "note-2222222222222222",
    ]
    request["robustness_pipeline_request"]["symbol"] = "QQQ"
    request["robustness_pipeline_request"]["input_bars"] = [{"timestamp": "2026-01-01", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}]
    request["robustness_pipeline_request"]["robustness_review_inputs"]["validated_knihomol_evidence"] = _bridge_shallow_evidence()
    return request


def test_prepare_binds_ohlcv_and_knihomol_artifacts_and_writes_bundle_request(tmp_path):
    request_path = tmp_path / "orchestrator-request.json"
    output_path = tmp_path / "prepared-bundle.json"
    bars_path = tmp_path / "bars.json"
    corpus_path = _corpus(tmp_path / "corpus")
    ohlcv_request_path = tmp_path / "ohlcv-request.json"
    knihomol_request_path = tmp_path / "knihomol-request.json"

    _write_json(request_path, _adapter_ready_orchestrator_request())
    _write_json(bars_path, {"dataset_id": "QQQ_2026_SAMPLE", "symbol": "QQQ", "rows": _rows()})
    _write_json(ohlcv_request_path, _ohlcv_request(bars_path))
    _write_json(knihomol_request_path, _knihomol_request(corpus_path))

    result = prepare_review_only_orchestrator_bundle(
        request_path=request_path.resolve(),
        output_path=output_path.resolve(),
        ohlcv_adapter_request_path=ohlcv_request_path.resolve(),
        knihomol_adapter_request_path=knihomol_request_path.resolve(),
    )

    prepared = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["status"] == "prepared"
    assert prepared["version"] == "orchestrator_run_bundle_contract_request_v1"
    assert prepared["expected_dataset_identity"]["symbol"] == "SYNTH_QQQ"
    assert prepared["expected_knihomol_evidence_ids"] == [
        "note-1111111111111111",
        "note-2222222222222222",
    ]
    assert prepared["orchestrator_request"]["robustness_pipeline_request"]["input_bars"][0]["timestamp"] == "2026-01-05T14:30:00Z"
    assert prepared["orchestrator_request"]["robustness_pipeline_request"]["robustness_review_inputs"]["validated_knihomol_evidence"] == _bridge_shallow_evidence()
    assert set(prepared["supplied_input_artifact_hashes"]) == {
        "knihomol_adapter_content_sha256",
        "knihomol_bridge_output_sha256",
        "ohlcv_downstream_adapter_output_sha256",
        "ohlcv_normalized_rows_hash",
        "ohlcv_source_sha256",
        "request_file_sha256",
    }

    bundle_contract = build_orchestrator_run_bundle_contract(copy.deepcopy(prepared))
    assert bundle_contract["expected_identities"]["knihomol_evidence_ids"] == prepared["expected_knihomol_evidence_ids"]


def test_prepare_fails_closed_when_manual_evidence_conflicts_with_adapter_binding(tmp_path):
    request = _adapter_ready_orchestrator_request()
    request["robustness_pipeline_request"]["robustness_review_inputs"]["validated_knihomol_evidence"]["notes"][0]["summary"] = "Conflicting summary"
    request_path = tmp_path / "orchestrator-request.json"
    output_path = tmp_path / "prepared-bundle.json"
    corpus_path = _corpus(tmp_path / "corpus")
    knihomol_request_path = tmp_path / "knihomol-request.json"

    _write_json(request_path, request)
    _write_json(knihomol_request_path, _knihomol_request(corpus_path))

    with pytest.raises(ValueError, match="manually supplied validated_knihomol_evidence"):
        prepare_review_only_orchestrator_bundle(
            request_path=request_path.resolve(),
            output_path=output_path.resolve(),
            knihomol_adapter_request_path=knihomol_request_path.resolve(),
        )


def test_cli_prepare_run_verify_and_replay_round_trip(tmp_path):
    request_path = tmp_path / "orchestrator-request.json"
    prepared_path = tmp_path / "prepared-bundle.json"
    run_dir = tmp_path / "run-dir"
    replay_dir = tmp_path / "replay-dir"
    _write_json(request_path, _orchestrator_request())

    prepare_result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "prepare", "--request", str(request_path.resolve()), "--output", str(prepared_path.resolve())],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepare_result.returncode == 0
    assert json.loads(prepare_result.stdout)["status"] == "prepared"

    run_result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "run", "--prepared-bundle", str(prepared_path.resolve()), "--output-dir", str(run_dir.resolve())],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run_result.returncode == 0
    assert json.loads(run_result.stdout)["execution_status"] == "completed"

    verify_result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "verify", "--run-dir", str(run_dir.resolve())],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify_result.returncode == 0
    assert json.loads(verify_result.stdout)["verification_status"] == "VERIFIED"

    replay_result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "replay", "--run-dir", str(run_dir.resolve()), "--replay-output-dir", str(replay_dir.resolve())],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert replay_result.returncode == 0
    replay_payload = json.loads(replay_result.stdout)
    assert replay_payload["verification_status"] == "REPLAY_MATCH"
    assert (replay_dir / "run_report.json").exists()


def test_module_and_cli_do_not_import_provider_registry_or_runtime_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.reports",
        "research_lab.hermes",
        "research_lab.llm",
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
