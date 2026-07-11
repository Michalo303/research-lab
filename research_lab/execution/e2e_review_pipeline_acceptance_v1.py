from __future__ import annotations

import hashlib
import json
from typing import Any

from research_lab.execution.isolated_real_data_adapter_contract_v1 import (
    build_isolated_real_data_adapter_contract,
)
from research_lab.execution.markov_hmm_regime_pilot_v1 import (
    run_markov_hmm_regime_pilot,
)
from research_lab.execution.qlib_isolated_evaluator_v1 import (
    run_qlib_isolated_evaluator,
)
from research_lab.execution.rd_agent_proposal_contract_v1 import (
    build_rd_agent_proposal_contract,
)
from research_lab.execution.result_review_gate_v1 import (
    build_result_review_gate,
)
from research_lab.execution.strategy_execution_bridge_synthetic_executor_v1 import (
    run_strategy_execution_bridge_synthetic_executor,
)
from research_lab.execution.swing_trend_filtered_pullback_strategy_contract_v1 import (
    build_swing_trend_filtered_pullback_strategy_contract,
)


REQUEST_VERSION = "e2e_review_pipeline_acceptance_request_v1"
RESULT_VERSION = "e2e_review_pipeline_acceptance_result_v1"
PIPELINE_VERSION = "e2e_review_pipeline_acceptance_v1"


def run_e2e_review_pipeline_acceptance(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    adapter_result = build_isolated_real_data_adapter_contract(
        {
            "version": "isolated_real_data_adapter_contract_request_v1",
            "symbol": validated["symbol"],
            "input_bars": validated["input_bars"],
            "provenance": validated["provenance"],
        }
    )
    strategy_contract_result = build_swing_trend_filtered_pullback_strategy_contract(
        {
            "version": "swing_trend_filtered_pullback_strategy_contract_request_v1",
            "symbol": adapter_result["symbol"],
            "synthetic_bars": adapter_result["synthetic_bars"],
            "strategy_parameters": validated["strategy_parameters"],
            "provenance": validated["provenance"],
        }
    )
    bridge_request = {
        "version": "strategy_execution_capability_bridge_request_v1",
        "strategy_builder": "swing_trend_filtered_pullback",
        "symbol": strategy_contract_result["symbol"],
        "synthetic_bars": strategy_contract_result["synthetic_bars"],
        "strategy_signal_plan": strategy_contract_result["strategy_signal_plan"],
        "provenance": validated["provenance"],
    }
    bridge_executor_result = run_strategy_execution_bridge_synthetic_executor(
        {
            "version": "strategy_execution_bridge_synthetic_executor_request_v1",
            "bridge_request": bridge_request,
            "executor_config": validated["executor_config"],
            "provenance": validated["provenance"],
        }
    )
    review_artifact = build_result_review_gate(
        {
            "version": "result_review_gate_request_v1",
            "adapter_result": adapter_result,
            "strategy_contract_result": strategy_contract_result,
            "bridge_result": bridge_executor_result["bridge_result"],
            "isolated_execution_result": bridge_executor_result["executor_result"],
            "provenance": validated["provenance"],
        }
    )
    qlib_evaluation = run_qlib_isolated_evaluator(
        {
            "version": "qlib_isolated_evaluator_request_v1",
            "input_type": "review_artifact",
            "review_artifact": review_artifact,
            "evaluation_mode": "deterministic_local",
            "provenance": validated["provenance"],
        }
    )
    regime_pilot_result = run_markov_hmm_regime_pilot(
        {
            "version": "markov_hmm_regime_pilot_request_v1",
            "review_artifact": review_artifact,
            "provenance": validated["provenance"],
        }
    )
    rd_agent_proposal = build_rd_agent_proposal_contract(
        {
            "version": "rd_agent_proposal_contract_request_v1",
            "review_artifact": review_artifact,
            "qlib_evaluation": qlib_evaluation,
            "regime_pilot_result": regime_pilot_result,
            "parameters": {"mode": "deterministic_local"},
            "provenance": validated["provenance"],
        }
    )

    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "adapter_result": adapter_result,
        "strategy_contract_result": strategy_contract_result,
        "bridge_result": bridge_executor_result["bridge_result"],
        "bridge_executor_result": bridge_executor_result,
        "isolated_execution_result": bridge_executor_result["executor_result"],
        "review_artifact": review_artifact,
        "qlib_evaluation": qlib_evaluation,
        "regime_pilot_result": regime_pilot_result,
        "rd_agent_proposal": rd_agent_proposal,
        "provider_calls_used": 0,
        "registry_write_performed": False,
        "broker_actions_used": 0,
        "deployment_gate_run": False,
        "hermes_state_touched": False,
        "hetzner_state_touched": False,
        "promotion_performed": False,
        "production_runtime_supported": False,
        "input_sha256": _canonical_sha256(validated),
        "provenance": validated["provenance"],
    }
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "symbol", "input_bars", "strategy_parameters", "executor_config", "provenance"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    return {
        "version": version,
        "symbol": _required_text(payload, "symbol").upper(),
        "input_bars": _required_list(payload.get("input_bars"), name="input_bars"),
        "strategy_parameters": _required_mapping(payload.get("strategy_parameters"), name="strategy_parameters"),
        "executor_config": _required_mapping(payload.get("executor_config"), name="executor_config"),
        "provenance": _validate_provenance(payload.get("provenance")),
    }


def _validate_provenance(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, Any] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        if raw is None or isinstance(raw, (str, int, float, bool)):
            normalized[key_name] = raw
            continue
        raise ValueError(f"provenance.{key_name} must be a JSON scalar.")
    return normalized


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")
