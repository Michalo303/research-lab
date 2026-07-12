from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from research_lab.execution.e2e_research_orchestrator_acceptance_v1 import (
    _validate_request as _validate_orchestrator_request,
)
from research_lab.execution.experiment_manifest_contract_v1 import (
    _canonical_sha256,
)
from research_lab.execution.knihomol_orchestrator_evidence_binding_v1 import (
    MAPPING_POLICY_VERSION as KNIHOMOL_MAPPING_POLICY_VERSION,
    build_knihomol_orchestrator_evidence_binding,
)
from research_lab.execution.knihomol_readonly_evidence_adapter_v1 import (
    build_knihomol_readonly_evidence_adapter,
)
from research_lab.execution.local_ohlcv_file_input_adapter_v1 import (
    build_local_ohlcv_file_input_adapter,
)
from research_lab.execution.orchestrator_run_bundle_contract_v1 import (
    REQUEST_VERSION as BUNDLE_REQUEST_VERSION,
    build_orchestrator_run_bundle_contract,
)
from research_lab.execution.orchestrator_run_verifier_replay_v1 import (
    verify_orchestrator_run_directory,
)
from research_lab.execution.isolated_orchestrator_runner_v1 import (
    RUN_REPORT_VERSION,
    run_isolated_orchestrator_runner,
)


CLI_RESULT_VERSION = "review_only_orchestrator_cli_result_v1"
CLI_VERSION = "review_only_orchestrator_cli_v1"
EXIT_VALIDATION_FAILURE = 2
EXIT_IO_FAILURE = 3
EXIT_OUTPUT_EXISTS = 4


def prepare_review_only_orchestrator_bundle(
    *,
    request_path: str | Path,
    output_path: str | Path,
    ohlcv_adapter_request_path: str | Path | None = None,
    knihomol_adapter_request_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved_request_path = _resolved_local_file(request_path)
    resolved_output_path = Path(output_path).expanduser()
    raw_request = _load_json_object(resolved_request_path)
    normalized_request = _validate_orchestrator_request(copy.deepcopy(raw_request))

    supplied_input_artifact_hashes: dict[str, str] = {
        "request_file_sha256": _file_sha256(resolved_request_path),
    }
    manual_evidence = normalized_request["robustness_pipeline_request"]["robustness_review_inputs"]["validated_knihomol_evidence"]

    if ohlcv_adapter_request_path is not None:
        ohlcv_result = _prepare_ohlcv_binding(
            normalized_request=normalized_request,
            adapter_request_path=ohlcv_adapter_request_path,
        )
        supplied_input_artifact_hashes.update(ohlcv_result["artifact_hashes"])

    if knihomol_adapter_request_path is not None:
        knihomol_result = _prepare_knihomol_binding(
            normalized_request=normalized_request,
            adapter_request_path=knihomol_adapter_request_path,
            manual_evidence=manual_evidence,
        )
        supplied_input_artifact_hashes.update(knihomol_result["artifact_hashes"])

    expected_evidence_ids = _knowledge_note_ids(normalized_request)
    bundle_request = {
        "version": BUNDLE_REQUEST_VERSION,
        "run_id": _derived_run_id(normalized_request),
        "orchestrator_request": normalized_request,
        "request_source_metadata": {
            "source_type": "local_request_file",
            "source_path": str(resolved_request_path),
            "source_sha256": supplied_input_artifact_hashes["request_file_sha256"],
        },
        "supplied_input_artifact_hashes": dict(sorted(supplied_input_artifact_hashes.items())),
        "expected_experiment_id": normalized_request["experiment_manifest_request"]["experiment_id"],
        "expected_strategy_identity": copy.deepcopy(normalized_request["experiment_manifest_request"]["strategy_identity"]),
        "expected_dataset_identity": copy.deepcopy(normalized_request["experiment_manifest_request"]["dataset_identity"]),
        "expected_knihomol_evidence_ids": expected_evidence_ids,
        "provenance": copy.deepcopy(normalized_request["provenance"]),
    }
    prepared_bundle = build_orchestrator_run_bundle_contract(copy.deepcopy(bundle_request))
    _write_json_object_no_overwrite(resolved_output_path, bundle_request)
    return {
        "version": CLI_RESULT_VERSION,
        "cli_version": CLI_VERSION,
        "command": "prepare",
        "status": "prepared",
        "failure_reason": None,
        "output_path": str(resolved_output_path),
        "prepared_request_sha256": _canonical_sha256(bundle_request),
        "bundle_manifest_sha256": prepared_bundle["bundle_manifest_sha256"],
        "expected_knihomol_evidence_ids": expected_evidence_ids,
        "supplied_input_artifact_hashes": bundle_request["supplied_input_artifact_hashes"],
        "production_runtime_supported": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review-only orchestrator CLI with explicit prepare, run, verify, and replay commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Build an explicit orchestrator run-bundle request.")
    prepare_parser.add_argument("--request", required=True, help="Path to the orchestrator request JSON.")
    prepare_parser.add_argument("--output", required=True, help="Path to write the prepared run-bundle request JSON.")
    prepare_parser.add_argument("--ohlcv-adapter-request", required=False, help="Path to the local OHLCV adapter request JSON.")
    prepare_parser.add_argument("--knihomol-adapter-request", required=False, help="Path to the Knihomol read-only adapter request JSON.")

    run_parser = subparsers.add_parser("run", help="Run the isolated orchestrator from an explicit bundle request.")
    run_group = run_parser.add_mutually_exclusive_group(required=True)
    run_group.add_argument("--bundle-request", help="Path to an explicit orchestrator run-bundle request JSON.")
    run_group.add_argument("--prepared-bundle", help="Path to a prepared bundle request JSON.")
    run_parser.add_argument("--output-dir", required=True, help="Explicit isolated output directory for run artifacts.")

    verify_parser = subparsers.add_parser("verify", help="Read-only verification of a completed run directory.")
    verify_parser.add_argument("--run-dir", required=True, help="Path to a completed run directory.")

    replay_parser = subparsers.add_parser("replay", help="Replay a verified run into a fresh directory and compare outputs.")
    replay_parser.add_argument("--run-dir", required=True, help="Path to a completed run directory.")
    replay_parser.add_argument("--replay-output-dir", required=True, help="Fresh output directory for replay artifacts.")

    args = parser.parse_args(argv)

    try:
        if args.command == "prepare":
            payload = prepare_review_only_orchestrator_bundle(
                request_path=args.request,
                output_path=args.output,
                ohlcv_adapter_request_path=args.ohlcv_adapter_request,
                knihomol_adapter_request_path=args.knihomol_adapter_request,
            )
        elif args.command == "run":
            input_path = args.bundle_request or args.prepared_bundle
            bundle_request = _load_json_object(_resolved_local_file(input_path))
            payload = run_isolated_orchestrator_runner(bundle_request, output_dir=Path(args.output_dir).expanduser())
        elif args.command == "verify":
            payload = verify_orchestrator_run_directory(Path(args.run_dir).expanduser())
        else:
            payload = verify_orchestrator_run_directory(
                Path(args.run_dir).expanduser(),
                replay_output_dir=Path(args.replay_output_dir).expanduser(),
            )
    except FileExistsError:
        return _emit_failure(args.command, "output_already_exists", EXIT_OUTPUT_EXISTS)
    except (OSError, json.JSONDecodeError):
        return _emit_failure(args.command, "io_failure", EXIT_IO_FAILURE)
    except ValueError as exc:
        return _emit_failure(args.command, str(exc), EXIT_VALIDATION_FAILURE)

    print(json.dumps(payload, sort_keys=True))
    if args.command == "verify":
        return 0 if payload["verification_status"] == "VERIFIED" else EXIT_VALIDATION_FAILURE
    if args.command == "replay":
        return 0 if payload["verification_status"] == "REPLAY_MATCH" else EXIT_VALIDATION_FAILURE
    if args.command == "run":
        return 0 if payload["version"] == RUN_REPORT_VERSION and payload["execution_status"] == "completed" else EXIT_VALIDATION_FAILURE
    return 0


def _prepare_ohlcv_binding(*, normalized_request: dict[str, Any], adapter_request_path: str | Path) -> dict[str, Any]:
    request = _load_json_object(_resolved_local_file(adapter_request_path))
    adapter_result = build_local_ohlcv_file_input_adapter(copy.deepcopy(request))
    if adapter_result["status"] != "SUCCESS":
        raise ValueError("local OHLCV adapter request must produce status=SUCCESS.")
    downstream = _required_mapping(adapter_result.get("downstream_adapter_result"), name="downstream_adapter_result")
    dataset_identity = _required_mapping(normalized_request["experiment_manifest_request"]["dataset_identity"], name="dataset_identity")
    robustness_request = _required_mapping(normalized_request["robustness_pipeline_request"], name="robustness_pipeline_request")

    if adapter_result["dataset_id"] != dataset_identity["dataset_id"]:
        raise ValueError("OHLCV adapter dataset_id must match experiment_manifest_request.dataset_identity.dataset_id.")
    if adapter_result["symbol"] != str(robustness_request["symbol"]).upper():
        raise ValueError("OHLCV adapter symbol must match robustness_pipeline_request.symbol after normalization.")
    expected_dataset_symbol = f"SYNTH_{adapter_result['symbol']}"
    if dataset_identity["symbol"] != expected_dataset_symbol:
        raise ValueError("experiment_manifest_request.dataset_identity.symbol must match the isolated real-data adapter symbol.")
    if downstream.get("symbol") != expected_dataset_symbol:
        raise ValueError("local OHLCV downstream adapter symbol must match experiment_manifest_request.dataset_identity.symbol.")
    if downstream.get("source_symbol") != adapter_result["symbol"]:
        raise ValueError("local OHLCV downstream adapter source_symbol must match the adapter symbol.")

    normalized_request["robustness_pipeline_request"]["input_bars"] = copy.deepcopy(
        _required_list(downstream.get("synthetic_bars"), name="synthetic_bars")
    )
    return {
        "artifact_hashes": {
            "ohlcv_source_sha256": adapter_result["source_sha256"],
            "ohlcv_normalized_rows_hash": adapter_result["normalized_rows_hash"],
            "ohlcv_downstream_adapter_output_sha256": _required_text(downstream, "output_payload_sha256"),
        }
    }


def _prepare_knihomol_binding(
    *,
    normalized_request: dict[str, Any],
    adapter_request_path: str | Path,
    manual_evidence: dict[str, Any],
) -> dict[str, Any]:
    request = _load_json_object(_resolved_local_file(adapter_request_path))
    adapter_result = build_knihomol_readonly_evidence_adapter(copy.deepcopy(request))
    expected_note_ids = _knowledge_note_ids(normalized_request)
    bridge_result = build_knihomol_orchestrator_evidence_binding(
        {
            "version": "knihomol_orchestrator_evidence_binding_request_v1",
            "adapter_result": copy.deepcopy(adapter_result),
            "expected_note_ids": expected_note_ids,
            "expected_adapter_content_sha256": adapter_result["content_sha256"],
            "mapping_policy_version": KNIHOMOL_MAPPING_POLICY_VERSION,
            "provenance": copy.deepcopy(normalized_request["provenance"]),
        }
    )
    normalized_manual = _normalize_validated_knihomol_evidence(manual_evidence)
    normalized_bridge = _normalize_validated_knihomol_evidence(bridge_result["validated_knihomol_evidence"])
    if normalized_manual != normalized_bridge:
        raise ValueError("manually supplied validated_knihomol_evidence must exactly match the adapter-derived bridge result.")

    adapter_requested_ids = sorted(_required_text_list(adapter_result.get("requested_note_ids"), name="requested_note_ids"))
    bridge_source_ids = list(bridge_result["source_note_ids"])
    nested_ids = _evidence_note_ids(bridge_result["validated_knihomol_evidence"])
    if not (
        expected_note_ids == adapter_requested_ids == bridge_source_ids == nested_ids
    ):
        raise ValueError("Knihomol evidence IDs must exactly align across manifest, expected IDs, adapter, bridge, and nested evidence.")

    normalized_request["robustness_pipeline_request"]["robustness_review_inputs"]["validated_knihomol_evidence"] = copy.deepcopy(
        bridge_result["validated_knihomol_evidence"]
    )
    return {
        "artifact_hashes": {
            "knihomol_adapter_content_sha256": adapter_result["content_sha256"],
            "knihomol_bridge_output_sha256": bridge_result["output_payload_sha256"],
        }
    }


def _normalize_validated_knihomol_evidence(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="validated_knihomol_evidence")
    notes = _required_list(payload.get("notes"), name="validated_knihomol_evidence.notes")
    normalized_notes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in notes:
        note = _required_mapping(item, name="validated_knihomol_evidence.note")
        if set(note) != {"note_id", "status", "topic", "summary", "supports"}:
            raise ValueError("validated_knihomol_evidence.note contains unsupported fields.")
        note_id = _required_text(note, "note_id")
        if note_id in seen_ids:
            raise ValueError("validated_knihomol_evidence.note_id values must be unique.")
        seen_ids.add(note_id)
        status = _required_text(note, "status")
        if status != "validated":
            raise ValueError("validated_knihomol_evidence notes must have status=validated.")
        normalized_notes.append(
            {
                "note_id": note_id,
                "status": status,
                "topic": _required_text(note, "topic"),
                "summary": _required_text(note, "summary"),
                "supports": sorted(_required_text_list(note.get("supports"), name="validated_knihomol_evidence.supports")),
            }
        )
    return {"notes": sorted(normalized_notes, key=lambda item: item["note_id"])}


def _knowledge_note_ids(normalized_request: dict[str, Any]) -> list[str]:
    return _required_text_list(
        normalized_request["experiment_manifest_request"]["knowledge_note_ids"],
        name="knowledge_note_ids",
    )


def _evidence_note_ids(evidence: dict[str, Any]) -> list[str]:
    return [note["note_id"] for note in _normalize_validated_knihomol_evidence(evidence)["notes"]]


def _derived_run_id(normalized_request: dict[str, Any]) -> str:
    experiment_id = _required_text(normalized_request["experiment_manifest_request"], "experiment_id")
    digest = _canonical_sha256(normalized_request)[:12].upper()
    return f"RUN-{experiment_id}-{digest}"


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return payload


def _write_json_object_no_overwrite(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolved_local_file(path_like: str | Path) -> Path:
    path = Path(path_like).expanduser()
    if not path.is_absolute():
        raise ValueError("all input paths must be absolute.")
    if not path.exists() or not path.is_file():
        raise ValueError(f"input file does not exist: {path}")
    return path.resolve()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_text_list(value: Any, *, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} must contain non-empty text values.")
        text = item.strip()
        if text in seen:
            raise ValueError(f"{name} must not contain duplicate values.")
        seen.add(text)
        normalized.append(text)
    return sorted(normalized)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _emit_failure(command: str, failure_reason: str, exit_code: int) -> int:
    payload = {
        "version": CLI_RESULT_VERSION,
        "cli_version": CLI_VERSION,
        "command": command,
        "status": "failed",
        "failure_reason": failure_reason,
        "production_runtime_supported": False,
    }
    print(json.dumps(payload, sort_keys=True))
    return exit_code
