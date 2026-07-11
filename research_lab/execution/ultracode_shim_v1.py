from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


REQUEST_VERSION = "ultracode_shim_request_v1"
SHIM_VERSION = "ultracode_shim_v1"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_REJECTED = "REJECTED"

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[-_ ]?key|token|secret|password|credential|auth)\b"),
    re.compile(r"(?i)\b(sk-[A-Za-z0-9]{8,}|OPENAI_API_KEY\s*=|EODHD_API_KEY\s*=|MASSIVE_API_KEY\s*=)"),
)
_ACTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "provider_action_detected": re.compile(r"(?i)\b(provider|api call|external data fetch|fetch external data)\b"),
    "broker_action_detected": re.compile(r"(?i)\b(broker|order|trade execution|submit order)\b"),
    "registry_action_detected": re.compile(r"(?i)\b(registry write|update registry|append to registry|write registry)\b"),
    "deployment_action_detected": re.compile(r"(?i)\b(deploy|deployment|release prod|production release)\b"),
    "hermes_action_detected": re.compile(r"(?i)\bhermes\b"),
    "hetzner_action_detected": re.compile(r"(?i)\bhetzner\b"),
    "production_runtime_enablement_detected": re.compile(r"(?i)\b(enable production runtime|production_runtime_supported\s*=\s*true)\b"),
}


def build_ultracode_shim_artifact(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    proposal_hash = _canonical_sha256(validated)
    changed_paths = [item["path"] for item in validated["proposed_changes"]]
    rejection_reasons = _rejection_reasons(validated)
    result = {
        "ultracode_shim_version": SHIM_VERSION,
        "proposal_hash": proposal_hash,
        "review_status": STATUS_REJECTED if rejection_reasons else STATUS_REVIEW_REQUIRED,
        "rejection_reasons": rejection_reasons,
        "allowed_paths": validated["allowed_paths"],
        "denied_paths": validated["denied_paths"],
        "changed_paths": changed_paths,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "production_runtime_supported": False,
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(payload, allowed={"version", "proposed_changes", "patch_summary", "allowed_paths", "denied_paths", "provenance"}, name="request")
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    proposed_changes = payload.get("proposed_changes")
    patch_summary = payload.get("patch_summary")
    if (proposed_changes is None) == (patch_summary is None):
        raise ValueError("exactly one of proposed_changes or patch_summary must be provided.")
    if patch_summary is not None:
        proposed_changes = _proposed_changes_from_patch_summary(patch_summary)
    return {
        "version": version,
        "proposed_changes": _validate_proposed_changes(proposed_changes),
        "allowed_paths": _validate_paths(payload.get("allowed_paths"), field="allowed_paths"),
        "denied_paths": _validate_paths(payload.get("denied_paths"), field="denied_paths"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _proposed_changes_from_patch_summary(value: Any) -> list[dict[str, str]]:
    summary = _required_mapping(value, name="patch_summary")
    _reject_unknown_fields(summary, allowed={"changed_paths", "summary"}, name="patch_summary")
    changed_paths = summary.get("changed_paths")
    if not isinstance(changed_paths, list) or not changed_paths:
        raise ValueError("patch_summary.changed_paths must be a non-empty list.")
    summary_text = _required_text(summary, "summary")
    result: list[dict[str, str]] = []
    for item in changed_paths:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("patch_summary.changed_paths items must be non-empty text.")
        result.append({"path": item.strip(), "change_summary": summary_text})
    return result


def _validate_proposed_changes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("proposed_changes must be a non-empty list.")
    normalized: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for item in value:
        payload = _required_mapping(item, name="proposed change")
        _reject_unknown_fields(payload, allowed={"path", "change_summary"}, name="proposed change")
        path = _required_text(payload, "path")
        if path in seen_paths:
            raise ValueError("proposed_changes paths must be unique.")
        normalized.append({"path": path, "change_summary": _required_text(payload, "change_summary")})
        seen_paths.add(path)
    return normalized


def _validate_paths(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} items must be non-empty text.")
        normalized.append(item.strip())
    return normalized


def _rejection_reasons(validated: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for change in validated["proposed_changes"]:
        path = change["path"]
        summary = change["change_summary"]
        if not any(_path_matches(path, allowed) for allowed in validated["allowed_paths"]):
            reasons.append(f"changed_path_outside_allowed_paths:{path}")
        if any(_path_matches(path, denied) for denied in validated["denied_paths"]):
            reasons.append(f"changed_path_matches_denied_paths:{path}")
        if any(pattern.search(summary) for pattern in _SECRET_PATTERNS):
            reasons.append(f"secret_like_content_detected:{path}")
        for reason, pattern in _ACTION_PATTERNS.items():
            if pattern.search(summary):
                reasons.append(f"{reason}:{path}")
    return sorted(dict.fromkeys(reasons))


def _path_matches(path: str, rule: str) -> bool:
    normalized_path = path.replace("\\", "/")
    normalized_rule = rule.replace("\\", "/")
    if normalized_rule.endswith("/"):
        return normalized_path.startswith(normalized_rule)
    return normalized_path == normalized_rule or normalized_path.startswith(normalized_rule)


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        normalized[key_name] = _json_scalar(raw, name=f"provenance.{key_name}")
    return normalized


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")


def _json_scalar(value: Any, *, name: str) -> str | int | float | None | bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value
    raise ValueError(f"{name} must be a JSON scalar.")
