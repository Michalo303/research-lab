from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from research_lab.orchestration.risk_overlay_controlled_backtest_v1 import (
    CONTROLLED_BACKTEST_VERSION,
)
from research_lab.orchestration.risk_overlay_execution_adapter_v1 import (
    ADAPTER_VERSION,
)


OUTPUT_ARTIFACT_VERSION = "single_controlled_backtest_plan_v1"
SINGLE_CONTROLLED_BACKTEST_VERSION = "risk_overlay_single_controlled_backtest_v1"
INPUT_ARTIFACT_VERSION = "controlled_backtest_request_v1"


def build_single_controlled_backtest_plan(
    artifact: Any,
    *,
    source_controlled_backtest_request_path: str | None = None,
    run_single_controlled_backtest: bool = False,
) -> dict[str, Any]:
    if run_single_controlled_backtest:
        raise ValueError(
            "real single controlled backtest execution is disabled in v1; build the plan only."
        )

    validated = _validated_controlled_backtest_request(artifact)
    output = {
        "version": OUTPUT_ARTIFACT_VERSION,
        "single_controlled_backtest_version": SINGLE_CONTROLLED_BACKTEST_VERSION,
        "execution_performed": False,
        "appendable_to_registry": False,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "requires_human_review": True,
        "source_controlled_backtest_request_hash": _sha256(validated["source_artifact"]),
        "source_execution_spec_hash": validated["source_execution_spec_hash"],
        "provenance": deepcopy(validated["provenance"]),
        "execution_spec": deepcopy(validated["execution_spec"]),
        "runner_compatibility_checked": validated["runner_compatibility_checked"],
        "explicit_execution_required": True,
    }
    if source_controlled_backtest_request_path:
        output["source_controlled_backtest_request_path"] = source_controlled_backtest_request_path
    return output


def _validated_controlled_backtest_request(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")
    if str(artifact.get("version") or "").strip() != INPUT_ARTIFACT_VERSION:
        raise ValueError(f"artifact.version must be {INPUT_ARTIFACT_VERSION}")
    if str(artifact.get("controlled_backtest_version") or "").strip() != CONTROLLED_BACKTEST_VERSION:
        raise ValueError(f"controlled_backtest_version must be {CONTROLLED_BACKTEST_VERSION}")
    if artifact.get("execution_enabled_by_default") is not False:
        raise ValueError("execution_enabled_by_default=false is required")
    if artifact.get("appendable_to_registry") is not False:
        raise ValueError("appendable_to_registry=false is required")
    if artifact.get("promotion_allowed") is not False:
        raise ValueError("promotion_allowed=false is required")
    if artifact.get("deployment_allowed") is not False:
        raise ValueError("deployment_allowed=false is required")
    if artifact.get("requires_human_review") is not True:
        raise ValueError("requires_human_review=true is required")

    source_execution_spec_hash = str(artifact.get("source_execution_spec_hash") or "").strip()
    if not source_execution_spec_hash:
        raise ValueError("source_execution_spec_hash is required")

    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("provenance is required")

    execution_spec = artifact.get("execution_spec")
    if not isinstance(execution_spec, dict):
        raise ValueError("execution_spec is required")
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

    runner_handoff = artifact.get("runner_handoff")
    runner_compatibility_checked = False
    if isinstance(runner_handoff, dict):
        if str(runner_handoff.get("kind") or "").strip() != "single_execution_spec":
            raise ValueError("runner_handoff.kind must be single_execution_spec")
        if runner_handoff.get("execution_requested") is not False:
            raise ValueError("runner_handoff.execution_requested=false is required")
        if runner_handoff.get("review_only") is not True:
            raise ValueError("runner_handoff.review_only=true is required")
        runner_compatibility_checked = runner_handoff.get("runner_compatible") is True

    return {
        "source_artifact": deepcopy(artifact),
        "source_execution_spec_hash": source_execution_spec_hash,
        "provenance": provenance,
        "execution_spec": execution_spec,
        "runner_compatibility_checked": runner_compatibility_checked,
    }


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
