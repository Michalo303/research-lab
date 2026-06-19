from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from research_lab.orchestration.risk_overlay_single_backtest_preflight_v1 import (
    OUTPUT_ARTIFACT_VERSION as INPUT_ARTIFACT_VERSION,
    PREFLIGHT_VERSION,
)


OUTPUT_ARTIFACT_VERSION = "isolated_single_runner_contract_v1"
CONTRACT_VERSION = "risk_overlay_isolated_single_runner_contract_v1"


def build_isolated_single_runner_contract(
    artifact: Any,
    *,
    source_single_backtest_preflight_path: str | None = None,
) -> dict[str, Any]:
    validated = _validated_single_backtest_preflight(artifact)
    output = {
        "version": OUTPUT_ARTIFACT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "execution_performed": False,
        "contract_only": True,
        "appendable_to_registry": False,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "report_writes_allowed": False,
        "registry_writes_allowed": False,
        "backtests_runs_writes_allowed": False,
        "broker_or_order_actions_allowed": False,
        "provider_calls_allowed": False,
        "requires_human_review": True,
        "explicit_future_execution_required": True,
        "source_single_backtest_preflight_hash": _sha256(validated["source_artifact"]),
        "required_runner_capabilities": [
            "accepts_single_execution_spec",
            "supports_injected_output_sink",
            "supports_no_registry_write",
            "supports_no_report_write",
            "supports_no_backtests_runs_write",
            "supports_no_promotion",
            "supports_no_deployment",
            "supports_no_broker_or_order_action",
            "returns_result_in_memory",
        ],
        "disallowed_side_effects": [
            "registry_append",
            "report_write",
            "backtests_runs_write",
            "leaderboard_write",
            "cache_write",
            "deployment_gate_write",
            "broker_or_order_action",
            "provider_call",
            "service_restart",
        ],
        "blocking_reasons": deepcopy(validated["blocking_reasons"]),
        "provenance": deepcopy(validated["provenance"]),
        "execution_spec": deepcopy(validated["execution_spec"]),
        "execution_spec_metadata": _execution_spec_metadata(validated["execution_spec"]),
    }
    if source_single_backtest_preflight_path:
        output["source_single_backtest_preflight_path"] = source_single_backtest_preflight_path
    return output


def _validated_single_backtest_preflight(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")
    if str(artifact.get("version") or "").strip() != INPUT_ARTIFACT_VERSION:
        raise ValueError(f"artifact.version must be {INPUT_ARTIFACT_VERSION}")
    if str(artifact.get("preflight_version") or "").strip() != PREFLIGHT_VERSION:
        raise ValueError(f"preflight_version must be {PREFLIGHT_VERSION}")
    if artifact.get("execution_performed") is not False:
        raise ValueError("execution_performed=false is required")
    if artifact.get("appendable_to_registry") is not False:
        raise ValueError("appendable_to_registry=false is required")
    if artifact.get("promotion_allowed") is not False:
        raise ValueError("promotion_allowed=false is required")
    if artifact.get("deployment_allowed") is not False:
        raise ValueError("deployment_allowed=false is required")
    if artifact.get("requires_human_review") is not True:
        raise ValueError("requires_human_review=true is required")

    source_hash = str(artifact.get("source_single_controlled_backtest_plan_hash") or "").strip()
    if not source_hash:
        raise ValueError("source_single_controlled_backtest_plan_hash is required")

    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError("provenance is required")

    execution_spec = artifact.get("execution_spec")
    if not isinstance(execution_spec, dict):
        raise ValueError("execution_spec is required")

    side_effect_risk = str(artifact.get("side_effect_risk") or "").strip()
    if side_effect_risk not in {"unsafe", "unknown"}:
        raise ValueError("side_effect_risk must be unsafe or unknown")

    blocking_reasons = artifact.get("blocking_reasons")
    if not isinstance(blocking_reasons, list):
        raise ValueError("blocking_reasons must be a list")
    normalized_reasons = [str(reason).strip() for reason in blocking_reasons]
    if any(not reason for reason in normalized_reasons):
        raise ValueError("blocking_reasons must contain non-empty strings")

    return {
        "source_artifact": deepcopy(artifact),
        "blocking_reasons": normalized_reasons,
        "provenance": provenance,
        "execution_spec": execution_spec,
    }


def _execution_spec_metadata(execution_spec: dict[str, Any]) -> dict[str, Any]:
    parameters = execution_spec.get("parameters") if isinstance(execution_spec.get("parameters"), dict) else {}
    base_strategy = parameters.get("base_strategy") if isinstance(parameters.get("base_strategy"), dict) else {}
    return {
        "builder": str(execution_spec.get("builder") or "").strip(),
        "family": str(execution_spec.get("family") or base_strategy.get("family") or "").strip(),
        "asset_class": str(execution_spec.get("asset_class") or base_strategy.get("asset_class") or "").strip(),
        "timeframe": str(execution_spec.get("timeframe") or base_strategy.get("timeframe") or "").strip(),
        "short_name": str(execution_spec.get("short_name") or "").strip(),
        "base_strategy_builder": str(base_strategy.get("builder") or "").strip(),
    }


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
