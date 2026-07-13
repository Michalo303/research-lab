from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from research_lab.execution.e2e_macro_aware_research_acceptance_v1 import (
    RESULT_VERSION as ACCEPTANCE_RESULT_VERSION,
    STATUS_FAILED,
    _canonical_sha256,
    run_e2e_macro_aware_research_acceptance,
)


REQUEST_VERSION = "macro_aware_pilot_run_request_v1"
RUNNER_VERSION = "macro_aware_pilot_runner_v1"
RUN_REPORT_VERSION = "macro_aware_pilot_run_report_v1"
CHECKSUMS_VERSION = "macro_aware_pilot_checksums_v1"
COMPLETE_VERSION = "macro_aware_pilot_complete_v1"
PILOT_RUN_LABEL = "SYNTHETIC_MACRO_INTEGRATION_PILOT"
PRIVATE_RUN_ROOT = Path("/opt/trading/private/research_orchestrator_runs")
OUTPUT_FILES = (
    "request.json",
    "macro_aware_result.json",
    "run_report.json",
    "checksums.json",
    "COMPLETE",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FLAGS = {
    "provider_calls_used": 0,
    "network_used": False,
    "registry_write_performed": False,
    "broker_actions_used": 0,
    "paper_trading_performed": False,
    "deployment_performed": False,
    "promotion_performed": False,
    "generated_code_executed": False,
    "automatic_strategy_application_performed": False,
    "production_runtime_supported": False,
}


def run_macro_aware_pilot(request: dict[str, object], *, output_dir: str | Path) -> dict[str, Any]:
    validated = _validate_request(request)
    output_path = Path(output_dir).expanduser()
    staging_path = output_path.with_name(f".{output_path.name}.staging")
    _validate_output_path(output_path, staging_path=staging_path)
    staging_path.mkdir(parents=False, exist_ok=False)
    try:
        acceptance_result = run_e2e_macro_aware_research_acceptance(
            copy.deepcopy(validated["acceptance_request"])
        )
        _validate_acceptance_result(validated, acceptance_result)
        report = _build_run_report(validated, acceptance_result)
        hashes = {
            "request.json": _write_verified_json(staging_path / "request.json", validated),
            "macro_aware_result.json": _write_verified_json(
                staging_path / "macro_aware_result.json", acceptance_result
            ),
            "run_report.json": _write_verified_json(staging_path / "run_report.json", report),
        }
        checksums = {
            "version": CHECKSUMS_VERSION,
            "run_id": validated["run_id"],
            "files": dict(sorted(hashes.items())),
        }
        _write_verified_json(staging_path / "checksums.json", checksums)
        _write_verified_json(
            staging_path / "COMPLETE",
            {"version": COMPLETE_VERSION, "run_id": validated["run_id"], "status": "COMPLETE"},
        )
        if {item.name for item in staging_path.iterdir()} != set(OUTPUT_FILES):
            raise OSError("staged artifact set is not exact")
        os.replace(staging_path, output_path)
        return report
    except Exception as exc:
        _write_incomplete(staging_path, run_id=validated["run_id"], failure_reason=str(exc))
        raise


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, "request")
    allowed = {
        "version",
        "run_id",
        "run_label",
        "acceptance_request",
        "expected_acceptance_request_sha256",
        "expected_market_dataset_identity",
        "expected_market_symbol",
        "expected_market_bars_sha256",
        "expected_market_source_artifact_sha256",
        "expected_synthetic_macro_label",
        "created_at",
        "provenance",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"request contains unknown field(s): {', '.join(unknown)}")
    if _required_text(payload, "version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    run_label = _required_text(payload, "run_label")
    if run_label != PILOT_RUN_LABEL:
        raise ValueError(f"run_label must be {PILOT_RUN_LABEL}.")
    acceptance_request = _required_mapping(payload.get("acceptance_request"), "acceptance_request")
    expected_request_hash = _required_sha256(payload, "expected_acceptance_request_sha256")
    if _canonical_sha256(acceptance_request) != expected_request_hash:
        raise ValueError("expected_acceptance_request_sha256 mismatch.")
    expected_dataset = _required_text(payload, "expected_market_dataset_identity")
    expected_symbol = _required_text(payload, "expected_market_symbol").upper()
    expected_macro_label = _required_text(payload, "expected_synthetic_macro_label")
    _validate_request_bindings(
        acceptance_request,
        run_label=run_label,
        expected_dataset=expected_dataset,
        expected_symbol=expected_symbol,
        expected_macro_label=expected_macro_label,
        expected_market_bars_sha256=_required_sha256(payload, "expected_market_bars_sha256"),
    )
    provenance = _required_mapping(payload.get("provenance"), "provenance")
    return {
        "version": REQUEST_VERSION,
        "run_id": _required_text(payload, "run_id"),
        "run_label": run_label,
        "acceptance_request": copy.deepcopy(acceptance_request),
        "expected_acceptance_request_sha256": expected_request_hash,
        "expected_market_dataset_identity": expected_dataset,
        "expected_market_symbol": expected_symbol,
        "expected_market_bars_sha256": _required_sha256(payload, "expected_market_bars_sha256"),
        "expected_market_source_artifact_sha256": _required_sha256(
            payload, "expected_market_source_artifact_sha256"
        ),
        "expected_synthetic_macro_label": expected_macro_label,
        "created_at": _required_text(payload, "created_at"),
        "provenance": copy.deepcopy(provenance),
    }


def _validate_request_bindings(
    acceptance_request: dict[str, Any],
    *,
    run_label: str,
    expected_dataset: str,
    expected_symbol: str,
    expected_macro_label: str,
    expected_market_bars_sha256: str,
) -> None:
    expected_identities = _required_mapping(
        acceptance_request.get("expected_identities"), "acceptance_request.expected_identities"
    )
    if expected_identities.get("market_data_identity") != expected_dataset:
        raise ValueError("expected market dataset identity mismatch.")
    market_request = _required_mapping(
        acceptance_request.get("market_data_request"), "acceptance_request.market_data_request"
    )
    if _required_text(market_request, "symbol").upper().removeprefix("SYNTH_") != expected_symbol.removeprefix("SYNTH_"):
        raise ValueError("expected market symbol mismatch.")
    expected_hashes = _required_mapping(
        acceptance_request.get("expected_hashes"), "acceptance_request.expected_hashes"
    )
    if expected_hashes.get("market_data_sha256") != expected_market_bars_sha256:
        raise ValueError("expected market bars sha256 mismatch.")
    provenance = _required_mapping(acceptance_request.get("provenance"), "acceptance_request.provenance")
    if provenance.get("run_label") != run_label:
        raise ValueError("acceptance request run label mismatch.")
    if provenance.get("synthetic_macro_label") != expected_macro_label:
        raise ValueError("synthetic macro label mismatch.")
    series_requests = acceptance_request.get("macro_series_requests")
    if not isinstance(series_requests, list) or not series_requests:
        raise ValueError("acceptance_request.macro_series_requests must be a non-empty list.")
    for series in series_requests:
        series_payload = _required_mapping(series, "macro series request")
        if _required_text(series_payload, "provider").upper() != "SYNTHETIC":
            raise ValueError("macro series provider must be SYNTHETIC.")
        series_provenance = _required_mapping(series_payload.get("provenance"), "macro series provenance")
        if series_provenance.get("synthetic_macro_label") != expected_macro_label:
            raise ValueError("macro series synthetic label mismatch.")


def _validate_acceptance_result(validated: dict[str, Any], result: dict[str, Any]) -> None:
    if result.get("version") != ACCEPTANCE_RESULT_VERSION:
        raise ValueError("macro-aware result version mismatch.")
    if result.get("status") == STATUS_FAILED:
        raise ValueError("macro-aware pilot acceptance returned FAILED_VALIDATION.")
    if result.get("input_sha256") != validated["expected_acceptance_request_sha256"]:
        raise ValueError("macro-aware result input hash mismatch.")
    lineage = _required_mapping(result.get("lineage"), "macro_aware_result.lineage")
    if lineage.get("market_data_identity") != validated["expected_market_dataset_identity"]:
        raise ValueError("result market dataset identity mismatch.")
    if str(lineage.get("market_symbol", "")).upper().removeprefix("SYNTH_") != validated["expected_market_symbol"].removeprefix("SYNTH_"):
        raise ValueError("result market symbol mismatch.")
    if lineage.get("market_data_sha256") != validated["expected_market_bars_sha256"]:
        raise ValueError("result market bars sha256 mismatch.")
    if lineage.get("market_source_artifact_sha256") != validated["expected_market_source_artifact_sha256"]:
        raise ValueError("result market source artifact sha256 mismatch.")
    if result.get("provenance", {}).get("synthetic_macro_label") != validated["expected_synthetic_macro_label"]:
        raise ValueError("result synthetic macro label mismatch.")
    if result.get("safety_flags") != _SAFE_FLAGS:
        raise ValueError("macro-aware result safety flags are unsafe.")
    if not all(result.get("no_look_ahead_proof", {}).values()):
        raise ValueError("no-look-ahead proof must be true.")
    if not all(result.get("baseline_preservation_proof", {}).values()):
        raise ValueError("baseline-preservation proof must be true.")
    if not all(result.get("protective_exit_preservation_proof", {}).values()):
        raise ValueError("protective-exit-preservation proof must be true.")
    _validate_child_safety(result)
    if result.get("output_payload_sha256") != _canonical_sha256(
        {key: value for key, value in result.items() if key != "output_payload_sha256"}
    ):
        raise ValueError("macro-aware result output hash mismatch.")


def _validate_child_safety(result: dict[str, Any]) -> None:
    common_flags = {
        "provider_calls_used": 0,
        "network_used": False,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_performed": False,
        "production_runtime_supported": False,
    }
    _require_child_values(
        result["macro_snapshot_result"]["safe_flags"],
        {**common_flags, "hermes_state_touched": False},
        name="macro snapshot",
    )
    _require_child_values(
        result["alignment_result"]["safety_flags"],
        common_flags,
        name="macro alignment",
    )
    _require_child_values(
        result["feature_set_result"],
        {"production_runtime_supported": False},
        name="macro feature set",
    )
    _require_child_values(
        result["macro_regime_candidate_result"],
        {
            "provider_calls_used": 0,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_performed": False,
            "automatic_strategy_application_performed": False,
            "production_runtime_supported": False,
            "candidate_only": True,
        },
        name="macro regime candidate",
    )
    strategy_safe_flags = {
        "provider_calls_used": 0,
        "broker_actions_used": 0,
        "registry_write_performed": False,
        "deployment_gate_run": False,
        "hermes_write_performed": False,
        "backtest_run_performed": False,
    }
    _require_child_values(
        result["baseline_strategy_result"]["safe_flags"],
        strategy_safe_flags,
        name="baseline strategy",
    )
    _require_child_values(
        result["baseline_strategy_result"],
        {"production_runtime_supported": False},
        name="baseline strategy",
    )
    _require_child_values(
        result["macro_filter_evaluator_result"],
        {
            "provider_calls_used": 0,
            "network_used": False,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "deployment_performed": False,
            "promotion_performed": False,
            "generated_code_executed": False,
            "automatic_strategy_application_performed": False,
            "production_runtime_supported": False,
            "candidate_only": True,
        },
        name="macro filter evaluator",
    )
    review = result["review_artifact"]
    _require_child_values(
        review,
        {
            "provider_calls_used": 0,
            "registry_write_performed": False,
            "broker_actions_used": 0,
            "promotion_performed": False,
            "deployment_gate_run": False,
            "hermes_state_touched": False,
            "hetzner_state_touched": False,
        },
        name="review artifact",
    )
    adapter_flags = {
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "backtest_run": False,
    }
    _require_child_values(review["adapter_safety_flags"], adapter_flags, name="review adapter")
    _require_child_values(
        review["bridge_result"]["safe_flags"],
        strategy_safe_flags,
        name="strategy bridge",
    )
    _require_child_values(
        review["bridge_result"],
        {"strategy_runtime_supported": False},
        name="strategy bridge",
    )


def _require_child_values(payload: dict[str, Any], expected: dict[str, Any], *, name: str) -> None:
    for field, expected_value in expected.items():
        if payload.get(field) != expected_value:
            raise ValueError(f"child safety violation: {name}.{field}")


def _build_run_report(validated: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    report = {
        "version": RUN_REPORT_VERSION,
        "runner_version": RUNNER_VERSION,
        "run_id": validated["run_id"],
        "run_label": validated["run_label"],
        "created_at": validated["created_at"],
        "execution_status": "COMPLETED",
        "acceptance_status": result["status"],
        "evaluator_classification": result["evaluator_classification"],
        "acceptance_request_sha256": validated["expected_acceptance_request_sha256"],
        "acceptance_result_sha256": result["output_payload_sha256"],
        "market_dataset_identity": validated["expected_market_dataset_identity"],
        "market_symbol": validated["expected_market_symbol"],
        "market_bars_sha256": validated["expected_market_bars_sha256"],
        "market_source_artifact_sha256": validated["expected_market_source_artifact_sha256"],
        "synthetic_macro_label": validated["expected_synthetic_macro_label"],
        "lineage": result["lineage"],
        "no_look_ahead_proof": result["no_look_ahead_proof"],
        "baseline_preservation_proof": result["baseline_preservation_proof"],
        "protective_exit_preservation_proof": result["protective_exit_preservation_proof"],
        **_SAFE_FLAGS,
        "written_files": list(OUTPUT_FILES),
        "provenance": validated["provenance"],
    }
    report["output_payload_sha256"] = _canonical_sha256(report)
    return report


def _validate_output_path(output_path: Path, *, staging_path: Path) -> None:
    if any(part == ".." for part in output_path.parts):
        raise ValueError("unsafe_output_dir: parent-directory traversal is forbidden.")
    allowed_root = PRIVATE_RUN_ROOT.expanduser().resolve(strict=False)
    resolved_output = output_path.resolve(strict=False)
    try:
        relative = resolved_output.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("unsafe_output_dir: outside permitted private research run root.") from exc
    if not relative.parts:
        raise ValueError("unsafe_output_dir: output directory must be below the permitted root.")
    current = output_path
    while current != current.parent:
        if current.exists() and current.is_symlink():
            raise ValueError("unsafe_output_dir: symlink destinations are forbidden.")
        if current.resolve(strict=False) == allowed_root:
            break
        current = current.parent
    if not output_path.parent.exists() or not output_path.parent.is_dir():
        raise ValueError("unsafe_output_dir: parent directory must already exist.")
    if output_path.exists():
        raise ValueError("output_dir must be fresh; overwrite is forbidden.")
    if staging_path.exists():
        raise ValueError("staging directory already exists.")


def _write_verified_json(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    expected = _canonical_sha256(payload)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)
    observed = json.loads(path.read_text(encoding="utf-8"))
    if _canonical_sha256(observed) != expected:
        raise OSError(f"post-write verification failed for {path.name}")
    return expected


def _write_incomplete(staging_path: Path, *, run_id: str, failure_reason: str) -> None:
    if not staging_path.exists():
        return
    try:
        _write_verified_json(
            staging_path / "INCOMPLETE",
            {
                "version": RUN_REPORT_VERSION,
                "run_id": run_id,
                "execution_status": "INCOMPLETE",
                "failure_reason": failure_reason,
                **_SAFE_FLAGS,
            },
        )
    except OSError:
        return


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_sha256(payload: dict[str, Any], field: str) -> str:
    value = _required_text(payload, field)
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase sha256 hex digest.")
    return value
