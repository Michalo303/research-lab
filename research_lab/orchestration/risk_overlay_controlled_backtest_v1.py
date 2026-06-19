from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from research_lab.orchestration.risk_overlay_execution_adapter_v1 import (
    ADAPTER_VERSION,
    OUTPUT_VERSION as EXECUTION_SPEC_ARTIFACT_VERSION,
    SUPPORTED_BASE_STRATEGIES,
    SUPPORTED_BLOCKER,
)


OUTPUT_ARTIFACT_VERSION = "controlled_backtest_request_v1"
CONTROLLED_BACKTEST_VERSION = "risk_overlay_controlled_backtest_v1"


def build_controlled_backtest_request(
    artifact: Any,
    *,
    source_execution_spec_path: str | None = None,
) -> dict[str, Any]:
    validated = _validated_execution_spec_artifact(artifact)
    output = {
        "version": OUTPUT_ARTIFACT_VERSION,
        "controlled_backtest_version": CONTROLLED_BACKTEST_VERSION,
        "execution_enabled_by_default": False,
        "appendable_to_registry": False,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "requires_human_review": True,
        "source_execution_spec_hash": _sha256(validated["source_artifact"]),
        "provenance": deepcopy(validated["provenance"]),
        "execution_spec": deepcopy(validated["execution_spec"]),
        "runner_handoff": {
            "kind": "single_execution_spec",
            "runner_compatible": True,
            "execution_requested": False,
            "review_only": True,
        },
    }
    if source_execution_spec_path:
        output["source_execution_spec_path"] = source_execution_spec_path
    return output


def _validated_execution_spec_artifact(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")
    if str(artifact.get("version") or "").strip() != EXECUTION_SPEC_ARTIFACT_VERSION:
        raise ValueError(f"artifact.version must be {EXECUTION_SPEC_ARTIFACT_VERSION}")
    if str(artifact.get("adapter_version") or "").strip() != ADAPTER_VERSION:
        raise ValueError(f"artifact.adapter_version must be {ADAPTER_VERSION}")
    if artifact.get("execution_spec_supported") is not True:
        raise ValueError("execution_spec_supported=true is required")
    if artifact.get("appendable_to_registry") is not False:
        raise ValueError("appendable_to_registry=false is required")
    if artifact.get("requires_human_review") is not True:
        raise ValueError("requires_human_review=true is required")
    if artifact.get("source_runtime_supported") is not False:
        raise ValueError("source_runtime_supported=false is required")
    _reject_registry_append_intent(artifact)

    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("provenance is required")
    blocker = str(provenance.get("blocker") or "").strip()
    if blocker != SUPPORTED_BLOCKER:
        raise ValueError(f"unsupported blocker: {blocker or '<missing>'}")
    source_note_ids = provenance.get("source_note_ids")
    if not isinstance(source_note_ids, list) or not source_note_ids:
        raise ValueError("provenance.source_note_ids are required")
    if any(not str(item).strip() for item in source_note_ids):
        raise ValueError("provenance.source_note_ids must be non-empty strings")

    execution_spec = artifact.get("execution_spec")
    if not isinstance(execution_spec, dict):
        raise ValueError("execution_spec must be a JSON object")
    if str(execution_spec.get("builder") or "").strip() != ADAPTER_VERSION:
        raise ValueError(f"execution_spec.builder must be {ADAPTER_VERSION}")

    parameters = execution_spec.get("parameters")
    if not isinstance(parameters, dict):
        raise ValueError("execution_spec.parameters must be a JSON object")
    if parameters.get("appendable_to_registry") is not False:
        raise ValueError("execution_spec.parameters.appendable_to_registry=false is required")
    if parameters.get("requires_human_review") is not True:
        raise ValueError("execution_spec.parameters.requires_human_review=true is required")
    if parameters.get("source_runtime_supported") is not False:
        raise ValueError("execution_spec.parameters.source_runtime_supported=false is required")
    if str(parameters.get("target_failure_mode") or "").strip() != blocker:
        raise ValueError("execution_spec.parameters.target_failure_mode must match provenance.blocker")
    _reject_registry_append_intent(execution_spec)
    _reject_registry_append_intent(parameters)

    _validate_supported_strategy_family(execution_spec, parameters)
    return {
        "source_artifact": deepcopy(artifact),
        "provenance": provenance,
        "execution_spec": execution_spec,
    }


def _validate_supported_strategy_family(execution_spec: dict[str, Any], parameters: dict[str, Any]) -> None:
    base_strategy = parameters.get("base_strategy")
    if not isinstance(base_strategy, dict):
        raise ValueError("execution_spec.parameters.base_strategy is required")

    family = str(base_strategy.get("family") or execution_spec.get("family") or "").strip()
    asset_class = str(base_strategy.get("asset_class") or execution_spec.get("asset_class") or "").strip()
    timeframe = str(base_strategy.get("timeframe") or execution_spec.get("timeframe") or "").strip()
    short_name = str(base_strategy.get("short_name") or "").strip()
    builder = str(base_strategy.get("builder") or "").strip()

    supported = SUPPORTED_BASE_STRATEGIES.get((family, asset_class, timeframe))
    if supported is None:
        raise ValueError(
            f"unsupported strategy family for {CONTROLLED_BACKTEST_VERSION}: "
            f"{family or '<missing>'}/{asset_class or '<missing>'}/{timeframe or '<missing>'}"
        )
    expected_builder = supported.get(short_name)
    if expected_builder is None:
        raise ValueError(f"unsupported base strategy short_name: {short_name or '<missing>'}")
    if builder != expected_builder:
        raise ValueError(
            f"base strategy short_name {short_name} requires builder {expected_builder}, got {builder or '<missing>'}"
        )


def _reject_registry_append_intent(payload: dict[str, Any]) -> None:
    for key in (
        "registry_append_intent",
        "append_to_registry",
        "registry_write_allowed",
        "appendable_to_registry",
    ):
        if key in payload and payload[key] not in (False, None):
            if key == "appendable_to_registry":
                continue
            raise ValueError("registry append intent is not allowed")


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
