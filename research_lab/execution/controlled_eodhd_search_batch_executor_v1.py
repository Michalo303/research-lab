"""Fail-closed M31Q executor for the one externally approved EODHD Search plan."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Callable

from research_lab.eodhd_approval_bound_search_metadata_adapter_v2 import (
    CONTRACT_VERSION as M31O_VERSION,
    resolve_approved_eodhd_search_v2,
)

CONTRACT_VERSION = "controlled_eodhd_search_batch_executor_v1"
APPROVAL_SHA256 = "3d4e7105b1637c37708fd6462460a3d6e18a686f2ef9ca9addc50ab36d6a4b0c"
PLAN_SHA256 = "dc7c89fb7212dac8c564d3f04759f82b39add96dae090750d2716ab52c995b43"
M31I_SHA256 = "d32525c57a865b3d2f4447ff9ac87da0466bb7a1a3096ab49b80eb17d5bd9c02"
M31N_SHA256 = "6822c2a00d7365b8f04c43e4e799829ea7eb9e2e9efea99f05c631fb3d07836b"
RUN_ROOT = "/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v3/run-3d4e7105b1637c37"
PENDING_ROOT = "/opt/trading/private/research_market_data_snapshots/pending_exact_symbol_resolution_v3"
_REQUEST_FIELDS = {"version", "execution_request_id", "mode", "m31i_manifest", "expected_m31i_canonical_manifest_sha256", "m31n_capability_manifest", "expected_m31n_canonical_capability_manifest_sha256", "m31p_readiness_result", "m31p_approval_manifest", "external_approved_approval_manifest_sha256", "external_approved_acquisition_plan_sha256", "approved_budget_policy", "m31o_adapter_contract_version", "allow_provider_calls", "journal", "result_store", "provenance"}
_EXECUTION_FIELDS = _REQUEST_FIELDS | {"provider_client", "credential"}
_REVIEW = {"REVIEW_REQUIRED_NO_EXACT_MATCH", "REVIEW_REQUIRED_AMBIGUOUS_EXACT_MATCH", "REVIEW_REQUIRED_PROVIDER_TYPE_TAXONOMY", "REVIEW_REQUIRED_PROVIDER_NAMESPACE"}


class ControlledExecutionError(ValueError):
    """A deterministic validation or fail-closed execution error."""


class InMemoryExecutionJournal:
    """Test journal with the same exclusive-create semantics as the file journal."""
    def __init__(self) -> None: self._items: dict[str, dict[str, Any]] = {}
    def run_exists(self) -> bool: return bool(self._items)
    def create_intent(self, value: dict[str, Any]) -> None: self._create("intent", value)
    def create_started(self, sequence: int, value: dict[str, Any]) -> None: self._create(f"started-{sequence}", value)
    def create_completed(self, sequence: int, value: dict[str, Any]) -> None: self._create(f"completed-{sequence}", value)
    def create_summary(self, value: dict[str, Any]) -> None: self._create("summary", value)
    def states(self) -> dict[str, dict[str, Any]]: return copy.deepcopy(self._items)
    def _create(self, key: str, value: dict[str, Any]) -> None:
        if key in self._items: raise ControlledExecutionError("EXISTING_JOURNAL_ARTIFACT")
        self._items[key] = copy.deepcopy(value)


class FilesystemExecutionJournal:
    """Atomic filesystem journal rooted solely at the approved run directory."""
    def __init__(self, root: str | Path) -> None: self.root = Path(root)
    def run_exists(self) -> bool: return self.root.exists() and any(self.root.iterdir())
    def create_intent(self, value: dict[str, Any]) -> None: self._create("execution-intent.json", value)
    def create_started(self, sequence: int, value: dict[str, Any]) -> None: self._create(f"{sequence:02d}-CALL_STARTED.json", value)
    def create_completed(self, sequence: int, value: dict[str, Any]) -> None: self._create(f"{sequence:02d}-CALL_COMPLETED.json", value)
    def create_summary(self, value: dict[str, Any]) -> None: self._create("execution-summary.json", value)
    def states(self) -> dict[str, dict[str, Any]]:
        if not self.root.exists(): return {}
        return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")}
    def _create(self, name: str, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        if target.exists(): raise ControlledExecutionError("EXISTING_JOURNAL_ARTIFACT")
        _atomic_json_create(target, value)


class InMemoryResultStore:
    def __init__(self, root: str = PENDING_ROOT) -> None: self.root = root.rstrip("/"); self.items: dict[str, dict[str, Any]] = {}
    def exists(self, destination: str) -> bool: return destination in self.items
    def publish(self, destination: str, value: dict[str, Any]) -> None:
        _validate_destination(destination, self.root)
        if destination in self.items: raise ControlledExecutionError("EXISTING_RESULT_DESTINATION")
        self.items[destination] = copy.deepcopy(value)


class FilesystemResultStore:
    def __init__(self, root: str | Path) -> None: self.root = Path(root)
    def exists(self, destination: str) -> bool: return self._target(destination).exists()
    def publish(self, destination: str, value: dict[str, Any]) -> None:
        target = self._target(destination)
        if target.exists(): raise ControlledExecutionError("EXISTING_RESULT_DESTINATION")
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json_create(target, value)
    def _target(self, destination: str) -> Path:
        _validate_destination(destination, str(self.root).replace("\\", "/"))
        suffix = PurePosixPath(destination).relative_to(PurePosixPath(str(self.root).replace("\\", "/")))
        target = self.root.joinpath(*suffix.parts)
        if target.parent.exists() and target.parent.resolve().is_relative_to(self.root.resolve()) is False: raise ControlledExecutionError("UNSAFE_RESULT_DESTINATION")
        return target


def build_controlled_eodhd_search_schedule_v1(request: dict[str, object]) -> list[dict[str, Any]]:
    value = _validate(request)
    return copy.deepcopy(value["schedule"])


def run_controlled_eodhd_search_batch_v1(request: dict[str, object]) -> dict[str, object]:
    value = _validate(request)
    schedule = value["schedule"]
    safety = _safety()
    if value["mode"] == "DRY_RUN":
        return _finish({"version": "controlled_eodhd_search_batch_execution_result_v1", "contract_version": CONTRACT_VERSION, "execution_request_id": value["execution_request_id"], "status": "DRY_RUN_VALIDATED", "schedule": schedule, "approval_manifest_sha256": APPROVAL_SHA256, "acquisition_plan_sha256": PLAN_SHA256, "safety_fields": safety})
    journal, store = value["journal"], value["result_store"]
    states = _states(journal)
    if states: return _manual_or_refuse(states)
    intent = {"version": "m31q_execution_intent_v1", "approval_manifest_sha256": APPROVAL_SHA256, "acquisition_plan_sha256": PLAN_SHA256, "schedule_sha256": _sha(schedule)}
    journal.create_intent(intent); safety["journal_writes"] += 1
    completed: list[int] = []; outcomes: list[dict[str, Any]] = []
    for record in schedule:
        sequence = record["sequence"]
        states = _states(journal)
        if f"started-{sequence}" in states or f"{sequence:02d}-CALL_STARTED.json" in states:
            return _manual_or_refuse(states)
        if store.exists(record["future_destination"]): raise ControlledExecutionError("EXISTING_RESULT_DESTINATION")
        journal.create_started(sequence, {"sequence": sequence, "record_sha256": record["canonical_per_call_record_sha256"]}); safety["journal_writes"] += 1
        try:
            result = resolve_approved_eodhd_search_v2({"mode": "APPROVED_EXECUTION", "approval_manifest": value["m31p_approval_manifest"], "external_approved_approval_manifest_sha256": APPROVAL_SHA256, "acquisition_plan_sha256": PLAN_SHA256, "selected_sequence": sequence, "selected_record": record, "allow_provider_calls": True, "consumed_call_ledger": {"consumed_metadata_calls": 0}, "client": value["provider_client"], "credentials": value["credential"]})
        except Exception as exc:
            raise ControlledExecutionError("TRANSPORT_FAILURE_AFTER_CALL_STARTED") from None
        safety["provider_calls_used"] += 1; safety["provider_credentials_accessed"] = True
        if result.get("resolution_status") == "FAILED_VALIDATION": raise ControlledExecutionError("ADAPTER_FAILED_VALIDATION_AFTER_CALL_STARTED")
        artifact = {"sequence": sequence, "record_sha256": record["canonical_per_call_record_sha256"], "adapter_result": result, "adapter_result_sha256": _sha(result)}
        store.publish(record["future_destination"], artifact); safety["result_store_writes"] += 1
        journal.create_completed(sequence, {"sequence": sequence, "adapter_result_sha256": artifact["adapter_result_sha256"]}); safety["journal_writes"] += 1
        completed.append(sequence); outcomes.append({"sequence": sequence, "resolution_status": result["resolution_status"], "adapter_result_sha256": artifact["adapter_result_sha256"]})
    summary = {"completed_sequences": completed, "outcomes": outcomes, "provider_calls_used": safety["provider_calls_used"]}
    journal.create_summary(summary); safety["journal_writes"] += 1
    return _finish({"version": "controlled_eodhd_search_batch_execution_result_v1", "contract_version": CONTRACT_VERSION, "execution_request_id": value["execution_request_id"], "status": "EXECUTION_COMPLETED", "schedule": schedule, "completed_sequences": completed, "outcomes": outcomes, "execution_summary_sha256": _sha(summary), "approval_manifest_sha256": APPROVAL_SHA256, "acquisition_plan_sha256": PLAN_SHA256, "safety_fields": safety})


def _validate(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict): raise ControlledExecutionError("REQUEST_MUST_BE_MAPPING")
    fields = _EXECUTION_FIELDS if raw.get("mode") == "APPROVED_EXECUTION" else _REQUEST_FIELDS
    if set(raw) != fields: raise ControlledExecutionError("UNKNOWN_OR_MISSING_REQUEST_FIELDS")
    injected = {"journal", "result_store", "provider_client", "credential"}
    value = {key: (item if key in injected else copy.deepcopy(item)) for key, item in raw.items()}
    if value["version"] != "controlled_eodhd_search_batch_execution_request_v1" or not isinstance(value["execution_request_id"], str) or value["mode"] not in {"DRY_RUN", "APPROVED_EXECUTION"}: raise ControlledExecutionError("INVALID_REQUEST_IDENTITY")
    if value["m31o_adapter_contract_version"] != M31O_VERSION: raise ControlledExecutionError("M31O_VERSION_MISMATCH")
    if value["expected_m31i_canonical_manifest_sha256"] != M31I_SHA256 or value["expected_m31n_canonical_capability_manifest_sha256"] != M31N_SHA256: raise ControlledExecutionError("UPSTREAM_EXPECTED_HASH_MISMATCH")
    _check_hash(value["m31i_manifest"], "canonical_manifest_sha256", M31I_SHA256)
    _check_hash(value["m31n_capability_manifest"], "canonical_capability_manifest_sha256", M31N_SHA256)
    readiness = value["m31p_readiness_result"]
    if not isinstance(readiness, dict) or readiness.get("m31i_canonical_manifest_sha256") != M31I_SHA256 or readiness.get("m31n_canonical_capability_sha256") != M31N_SHA256 or readiness.get("m31o_adapter_contract_version") != M31O_VERSION: raise ControlledExecutionError("M31P_UPSTREAM_BINDING_MISMATCH")
    if value["external_approved_approval_manifest_sha256"] != APPROVAL_SHA256 or value["external_approved_acquisition_plan_sha256"] != PLAN_SHA256: raise ControlledExecutionError("EXTERNAL_APPROVAL_MISMATCH")
    if readiness.get("approval_manifest_sha256") != APPROVAL_SHA256 or readiness.get("acquisition_plan_sha256") != PLAN_SHA256: raise ControlledExecutionError("M31P_APPROVAL_MISMATCH")
    manifest = value["m31p_approval_manifest"]
    if manifest != readiness.get("approval_manifest"): raise ControlledExecutionError("APPROVAL_MANIFEST_MEMBERSHIP_MISMATCH")
    _check_hash(manifest, "canonical_approval_manifest_sha256", APPROVAL_SHA256)
    if manifest.get("acquisition_plan_sha256") != PLAN_SHA256 or manifest.get("adapter_contract_version") != M31O_VERSION: raise ControlledExecutionError("APPROVAL_MANIFEST_BINDING_MISMATCH")
    policy = value["approved_budget_policy"]
    if policy != readiness.get("call_budgets") or policy != manifest.get("call_budgets") or policy != {"metadata_calls_max": 15, "historical_calls_max": 0, "corporate_action_calls_max": 0, "calendar_calls_max": 0, "total_calls_max": 15, "retries": 0, "sequential_only": True, "stop_on_first_failure": True, "fallback_provider_allowed": False, "pagination_calls": 0, "health_check_calls": 0, "hidden_calls": 0}: raise ControlledExecutionError("BUDGET_POLICY_MISMATCH")
    schedule = copy.deepcopy(readiness.get("complete_plan"))
    if not isinstance(schedule, list) or len(schedule) != 15 or _sha(schedule) != PLAN_SHA256: raise ControlledExecutionError("COMPLETE_PLAN_MISMATCH")
    if manifest.get("authorized_records") != schedule: raise ControlledExecutionError("AUTHORIZED_RECORD_MEMBERSHIP_MISMATCH")
    destinations = set()
    for expected, record in enumerate(schedule, 1):
        if not isinstance(record, dict) or record.get("sequence") != expected: raise ControlledExecutionError("SEQUENCE_MISMATCH")
        _check_hash(record, "canonical_per_call_record_sha256", record.get("canonical_per_call_record_sha256"))
        if record.get("call_count") != 1 or record.get("authorization_status") != "AUTHORIZABLE_BOUNDED_SEARCH_V2" or record.get("request_path") != f"/api/search/{record.get('isin')}" or record.get("query_parameters", {}).get("limit") != 10: raise ControlledExecutionError("INVALID_APPROVED_RECORD")
        destination = record.get("future_destination"); _validate_destination(destination, PENDING_ROOT)
        if destination in destinations: raise ControlledExecutionError("DUPLICATE_DESTINATION")
        destinations.add(destination)
    if value["mode"] == "DRY_RUN":
        if value["allow_provider_calls"]: raise ControlledExecutionError("DRY_RUN_CANNOT_ALLOW_CALLS")
    elif not value["allow_provider_calls"] or not callable(value["provider_client"]) or value["credential"] is None:
        raise ControlledExecutionError("EXECUTION_REQUIRES_INJECTED_CLIENT_CREDENTIAL")
    value["schedule"] = schedule
    return value


def _check_hash(value: object, key: str, expected: object) -> None:
    if not isinstance(value, dict) or not isinstance(expected, str): raise ControlledExecutionError("CANONICAL_HASH_MISMATCH")
    body = copy.deepcopy(value); supplied = body.pop(key, None)
    if supplied != expected or _sha(body) != expected: raise ControlledExecutionError("CANONICAL_HASH_MISMATCH")

def _validate_destination(destination: object, root: str) -> None:
    if not isinstance(destination, str) or not destination.startswith(root.rstrip("/") + "/") or ".." in PurePosixPath(destination).parts or "/SPY/" in destination: raise ControlledExecutionError("UNSAFE_RESULT_DESTINATION")

def _states(journal: Any) -> dict[str, dict[str, Any]]:
    return journal.states() if hasattr(journal, "states") else {}

def _manual_or_refuse(states: dict[str, dict[str, Any]]) -> dict[str, object]:
    started = {str(value.get("sequence")) for key, value in states.items() if key.startswith("started-") or "CALL_STARTED" in key}
    completed = {str(value.get("sequence")) for key, value in states.items() if key.startswith("completed-") or "CALL_COMPLETED" in key}
    if started - completed:
        return _finish({"status": "MANUAL_REVIEW_REQUIRED_POSSIBLE_CALL_ALREADY_CONSUMED", "safety_fields": _safety()})
    raise ControlledExecutionError("EXISTING_OR_INCONSISTENT_RUN")

def _atomic_json_create(target: Path, value: dict[str, Any]) -> None:
    encoded = _canonical(value)
    fd, temp_name = tempfile.mkstemp(prefix=".m31q-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
        # link is an atomic exclusive publication: unlike replace it cannot
        # silently overwrite a concurrently created evidence artifact.
        try:
            os.link(temp_name, target)
        except FileExistsError as exc:
            raise ControlledExecutionError("EXISTING_ARTIFACT") from None
    finally:
        if os.path.exists(temp_name): os.unlink(temp_name)

def _canonical(value: object) -> str: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
def _sha(value: object) -> str: return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
def _finish(value: dict[str, Any]) -> dict[str, object]:
    result = copy.deepcopy(value); result["output_payload_sha256"] = _sha(result); return result
def _safety() -> dict[str, object]: return {"provider_calls_used": 0, "provider_credentials_accessed": False, "journal_writes": 0, "result_store_writes": 0, "retries_used": 0, "fallback_used": False, "pagination_calls": 0, "health_check_calls": 0, "historical_calls": 0, "corporate_action_calls": 0, "calendar_calls": 0, "broker_calls": 0, "SPY_refetch_performed": False, "canonical_snapshot_mutations_performed": False, "production_runtime_supported": False}
