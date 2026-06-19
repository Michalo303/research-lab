from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from research_lab.orchestration.risk_overlay_execution_adapter_v1 import (
    ADAPTER_VERSION,
)
from research_lab.orchestration.risk_overlay_single_controlled_backtest_v1 import (
    OUTPUT_ARTIFACT_VERSION as INPUT_ARTIFACT_VERSION,
    SINGLE_CONTROLLED_BACKTEST_VERSION,
)


OUTPUT_ARTIFACT_VERSION = "single_backtest_preflight_v1"
PREFLIGHT_VERSION = "risk_overlay_single_backtest_preflight_v1"


def build_single_backtest_preflight(
    artifact: Any,
    *,
    source_single_controlled_backtest_plan_path: str | None = None,
) -> dict[str, Any]:
    validated = _validated_single_controlled_backtest_plan(artifact)
    inspection = _inspect_runner_compatibility(validated["execution_spec"])
    output = {
        "version": OUTPUT_ARTIFACT_VERSION,
        "preflight_version": PREFLIGHT_VERSION,
        "execution_performed": False,
        "appendable_to_registry": False,
        "promotion_allowed": False,
        "deployment_allowed": False,
        "requires_human_review": True,
        "source_single_controlled_backtest_plan_hash": _sha256(validated["source_artifact"]),
        "runner_interface_available": inspection["runner_interface_available"],
        "side_effect_risk": inspection["side_effect_risk"],
        "blocking_reasons": inspection["blocking_reasons"],
        "provenance": deepcopy(validated["provenance"]),
        "execution_spec": deepcopy(validated["execution_spec"]),
    }
    if source_single_controlled_backtest_plan_path:
        output["source_single_controlled_backtest_plan_path"] = source_single_controlled_backtest_plan_path
    return output


def _validated_single_controlled_backtest_plan(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be a JSON object")
    if str(artifact.get("version") or "").strip() != INPUT_ARTIFACT_VERSION:
        raise ValueError(f"artifact.version must be {INPUT_ARTIFACT_VERSION}")
    if str(artifact.get("single_controlled_backtest_version") or "").strip() != SINGLE_CONTROLLED_BACKTEST_VERSION:
        raise ValueError(f"single_controlled_backtest_version must be {SINGLE_CONTROLLED_BACKTEST_VERSION}")
    if artifact.get("execution_performed") is not False:
        raise ValueError("execution_performed=false is required")
    if artifact.get("explicit_execution_required") is not True:
        raise ValueError("explicit_execution_required=true is required")
    if artifact.get("appendable_to_registry") is not False:
        raise ValueError("appendable_to_registry=false is required")
    if artifact.get("promotion_allowed") is not False:
        raise ValueError("promotion_allowed=false is required")
    if artifact.get("deployment_allowed") is not False:
        raise ValueError("deployment_allowed=false is required")
    if artifact.get("requires_human_review") is not True:
        raise ValueError("requires_human_review=true is required")

    source_hash = str(artifact.get("source_controlled_backtest_request_hash") or "").strip()
    if not source_hash:
        raise ValueError("source_controlled_backtest_request_hash is required")

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

    return {
        "source_artifact": deepcopy(artifact),
        "provenance": provenance,
        "execution_spec": execution_spec,
    }


def _inspect_runner_compatibility(execution_spec: dict[str, Any]) -> dict[str, Any]:
    base_strategy = execution_spec.get("parameters", {}).get("base_strategy", {})
    family = str(base_strategy.get("family") or execution_spec.get("family") or "").strip()
    asset_class = str(base_strategy.get("asset_class") or execution_spec.get("asset_class") or "").strip()
    timeframe = str(base_strategy.get("timeframe") or execution_spec.get("timeframe") or "").strip()

    research_lab_root = Path(__file__).resolve().parents[1]
    runner_path = research_lab_root / "runner.py"
    baselines_path = research_lab_root / "strategies" / "baselines.py"

    blocking_reasons: list[str] = []
    runner_interface_available = False
    side_effect_risk = "unknown"

    runner_names = _top_level_defs(runner_path)
    if not {"run_single_backtest", "run_single_controlled_backtest", "run_execution_spec"}.intersection(runner_names):
        blocking_reasons.append(
            f"no dedicated single-execution runner interface found for {family or '<missing>'}/{asset_class or '<missing>'}/{timeframe or '<missing>'}"
        )

    runner_text = _read_text(runner_path)
    if runner_text is None:
        blocking_reasons.append("runner.py is unavailable for static inspection")
    else:
        if any(token in runner_text for token in _unsafe_runner_tokens()):
            side_effect_risk = "unsafe"
            blocking_reasons.append("existing runner writes backtests, registry, or reports during execution")

    baselines_text = _read_text(baselines_path)
    if baselines_text is None:
        blocking_reasons.append("strategies/baselines.py is unavailable for static inspection")
    elif "RISK_OVERLAY queue rows are not executable with the current runtime." in baselines_text:
        blocking_reasons.append("current runtime explicitly marks risk overlay execution as unsupported")

    if not blocking_reasons:
        runner_interface_available = True
        side_effect_risk = "safe"
    elif side_effect_risk == "unknown":
        side_effect_risk = "unsafe" if not runner_interface_available else "unknown"

    return {
        "runner_interface_available": runner_interface_available,
        "side_effect_risk": side_effect_risk,
        "blocking_reasons": blocking_reasons,
    }


def _top_level_defs(path: Path) -> set[str]:
    source = _read_text(path)
    if source is None:
        return set()
    tree = ast.parse(source)
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _unsafe_runner_tokens() -> tuple[str, ...]:
    return (
        'root / "backtests" / "runs"',
        'root / "registry"',
        'root / "reports"',
        "append_jsonl(",
        "write_leaderboard(",
        "write_allocation_model(",
        "write_daily_report_artifacts(",
        "write_strategy_card(",
    )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
