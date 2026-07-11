from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from typing import Any


REQUEST_VERSION = "rd_agent_proposal_contract_request_v1"
CONTRACT_VERSION = "rd_agent_proposal_contract_v1"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_REJECTED = "REJECTED"


def build_rd_agent_proposal_contract(request: dict[str, object]) -> dict[str, object]:
    rd_agent_available = importlib.util.find_spec("rdagent") is not None
    try:
        validated = _validate_request(request)
        input_hash = _canonical_sha256(validated)
        mode = validated["parameters"]["mode"]
        if mode == "availability_check" and not rd_agent_available:
            return _result(
                rd_agent_available=rd_agent_available,
                proposal_run=False,
                input_hash=input_hash,
                candidate_hypotheses=[],
                factor_proposals=[],
                strategy_candidate_notes=[],
                review_status=STATUS_UNAVAILABLE,
                failure_reason="rdagent_unavailable",
                source_review_candidate_id=_required_text(validated["review_artifact"], "candidate_id"),
            )

        candidate_hypotheses = _candidate_hypotheses(
            review_artifact=validated["review_artifact"],
            qlib_evaluation=validated["qlib_evaluation"],
            regime_pilot_result=validated["regime_pilot_result"],
        )
        factor_proposals = _factor_proposals(validated["qlib_evaluation"], validated["regime_pilot_result"])
        strategy_candidate_notes = _strategy_candidate_notes(
            review_artifact=validated["review_artifact"],
            qlib_evaluation=validated["qlib_evaluation"],
            regime_pilot_result=validated["regime_pilot_result"],
        )
        return _result(
            rd_agent_available=rd_agent_available,
            proposal_run=True,
            input_hash=input_hash,
            candidate_hypotheses=candidate_hypotheses,
            factor_proposals=factor_proposals,
            strategy_candidate_notes=strategy_candidate_notes,
            review_status=STATUS_REVIEW_REQUIRED,
            failure_reason=None,
            source_review_candidate_id=_required_text(validated["review_artifact"], "candidate_id"),
        )
    except ValueError as exc:
        return _result(
            rd_agent_available=rd_agent_available,
            proposal_run=False,
            input_hash=_safe_input_hash(request),
            candidate_hypotheses=[],
            factor_proposals=[],
            strategy_candidate_notes=[],
            review_status=STATUS_REJECTED,
            failure_reason=str(exc),
            source_review_candidate_id=_safe_review_candidate_id(request),
        )


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={"version", "review_artifact", "qlib_evaluation", "regime_pilot_result", "provenance", "parameters"},
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    review_artifact = _required_mapping(payload.get("review_artifact"), name="review_artifact")
    qlib_evaluation = _optional_mapping(payload.get("qlib_evaluation"), name="qlib_evaluation")
    regime_pilot_result = _optional_mapping(payload.get("regime_pilot_result"), name="regime_pilot_result")
    _validate_review_artifact(review_artifact)
    _validate_qlib_evaluation(qlib_evaluation)
    _validate_regime_pilot_result(regime_pilot_result)
    return {
        "version": version,
        "review_artifact": review_artifact,
        "qlib_evaluation": qlib_evaluation,
        "regime_pilot_result": regime_pilot_result,
        "provenance": _validate_provenance(payload.get("provenance")),
        "parameters": _validate_parameters(payload.get("parameters")),
    }


def _validate_review_artifact(review_artifact: dict[str, Any]) -> None:
    if str(review_artifact.get("version") or "") != "result_review_gate_result_v1":
        raise ValueError("review_artifact.version must be result_review_gate_result_v1.")
    candidate_id = _required_text(review_artifact, "candidate_id")
    final_review_status = _required_text(review_artifact, "final_review_status")
    if final_review_status != "REVIEW_REQUIRED":
        raise ValueError("review_artifact.final_review_status must be REVIEW_REQUIRED.")
    _ = candidate_id
    adapter_result = _required_mapping(review_artifact.get("adapter_result"), name="review_artifact.adapter_result")
    if adapter_result.get("production_runtime_supported") is not False:
        raise ValueError("review_artifact.adapter_result.production_runtime_supported must be false.")
    if int(review_artifact.get("provider_calls_used") or 0) != 0:
        raise ValueError("review_artifact.provider_calls_used must be 0.")
    if review_artifact.get("registry_write_performed") is not False:
        raise ValueError("review_artifact.registry_write_performed must be false.")
    if int(review_artifact.get("broker_actions_used") or 0) != 0:
        raise ValueError("review_artifact.broker_actions_used must be 0.")
    if review_artifact.get("deployment_gate_run") is not False:
        raise ValueError("review_artifact.deployment_gate_run must be false.")
    if review_artifact.get("hermes_state_touched") is not False:
        raise ValueError("review_artifact.hermes_state_touched must be false.")
    if review_artifact.get("hetzner_state_touched") is not False:
        raise ValueError("review_artifact.hetzner_state_touched must be false.")
    if review_artifact.get("promotion_performed") is not False:
        raise ValueError("review_artifact.promotion_performed must be false.")


def _validate_qlib_evaluation(qlib_evaluation: dict[str, Any] | None) -> None:
    if qlib_evaluation is None:
        return
    if str(qlib_evaluation.get("qlib_evaluator_version") or "") != "qlib_isolated_evaluator_v1":
        raise ValueError("qlib_evaluation.qlib_evaluator_version must be qlib_isolated_evaluator_v1.")
    _validate_safety_flags(qlib_evaluation, name="qlib_evaluation")


def _validate_regime_pilot_result(regime_pilot_result: dict[str, Any] | None) -> None:
    if regime_pilot_result is None:
        return
    if str(regime_pilot_result.get("regime_pilot_version") or "") != "markov_hmm_regime_pilot_v1":
        raise ValueError("regime_pilot_result.regime_pilot_version must be markov_hmm_regime_pilot_v1.")
    _validate_safety_flags(regime_pilot_result, name="regime_pilot_result")


def _validate_safety_flags(payload: dict[str, Any], *, name: str) -> None:
    if int(payload.get("provider_calls_used") or 0) != 0:
        raise ValueError(f"{name}.provider_calls_used must be 0.")
    if payload.get("registry_write_performed") is not False:
        raise ValueError(f"{name}.registry_write_performed must be false.")
    if int(payload.get("broker_actions_used") or 0) != 0:
        raise ValueError(f"{name}.broker_actions_used must be 0.")
    if payload.get("deployment_gate_run") is not False:
        raise ValueError(f"{name}.deployment_gate_run must be false.")
    if payload.get("hermes_state_touched") is not False:
        raise ValueError(f"{name}.hermes_state_touched must be false.")
    if payload.get("hetzner_state_touched") is not False:
        raise ValueError(f"{name}.hetzner_state_touched must be false.")
    if payload.get("promotion_performed") is not False:
        raise ValueError(f"{name}.promotion_performed must be false.")
    if payload.get("production_runtime_supported") is not False:
        raise ValueError(f"{name}.production_runtime_supported must be false.")


def _candidate_hypotheses(
    *,
    review_artifact: dict[str, Any],
    qlib_evaluation: dict[str, Any] | None,
    regime_pilot_result: dict[str, Any] | None,
) -> list[str]:
    hypotheses = [
        "Review lower drawdown variants before any promotion path.",
        "Prefer defensive pullback entries with explicit human review of synthetic-only results.",
    ]
    drawdown = review_artifact.get("drawdown")
    if _is_number(drawdown):
        hypotheses.append(f"Current isolated-path drawdown was {float(drawdown):.6f}; prioritize controls that reduce peak loss.")
    if qlib_evaluation is not None and qlib_evaluation.get("final_status") == "COMPLETED_LOCAL_STUB":
        hypotheses.append("Qlib local evaluator completed; compare candidate robustness against its deterministic return summary.")
    if regime_pilot_result is not None and isinstance(regime_pilot_result.get("drawdown_timing_hint"), str):
        hypotheses.append("Regime timing hints are available; review whether defensive exposure changes align with flagged regimes.")
    return hypotheses


def _factor_proposals(
    qlib_evaluation: dict[str, Any] | None,
    regime_pilot_result: dict[str, Any] | None,
) -> list[str]:
    proposals = [
        "Drawdown containment factor",
        "Exposure discipline factor",
    ]
    if qlib_evaluation is not None and isinstance(qlib_evaluation.get("metrics"), dict):
        proposals.append("Qlib simple-return cross-check factor")
    if regime_pilot_result is not None and isinstance(regime_pilot_result.get("regime_summary"), str):
        proposals.append("Regime-aware defensive timing factor")
    return proposals


def _strategy_candidate_notes(
    *,
    review_artifact: dict[str, Any],
    qlib_evaluation: dict[str, Any] | None,
    regime_pilot_result: dict[str, Any] | None,
) -> list[str]:
    notes = [
        "Review-only proposal artifact. Not executable and not a promotion decision.",
        f"Source review candidate: {_required_text(review_artifact, 'candidate_id')}.",
    ]
    if qlib_evaluation is not None:
        notes.append(f"qlib status: {str(qlib_evaluation.get('final_status') or 'UNKNOWN').lower()}.")
    if regime_pilot_result is not None:
        notes.append(f"regime status: {str(regime_pilot_result.get('final_status') or 'UNKNOWN').lower()}.")
    return notes


def _validate_parameters(value: Any) -> dict[str, Any]:
    if value is None:
        return {"mode": "deterministic_local"}
    payload = _required_mapping(value, name="parameters")
    _reject_unknown_fields(payload, allowed={"mode"}, name="parameters")
    mode = _required_text(payload, "mode")
    if mode not in {"availability_check", "deterministic_local"}:
        raise ValueError("parameters.mode must be availability_check or deterministic_local.")
    return {"mode": mode}


def _result(
    *,
    rd_agent_available: bool,
    proposal_run: bool,
    input_hash: str,
    candidate_hypotheses: list[str],
    factor_proposals: list[str],
    strategy_candidate_notes: list[str],
    review_status: str,
    failure_reason: str | None,
    source_review_candidate_id: str | None,
) -> dict[str, Any]:
    result = {
        "rd_agent_contract_version": CONTRACT_VERSION,
        "rd_agent_available": rd_agent_available,
        "proposal_run": proposal_run,
        "input_hash": input_hash,
        "candidate_hypotheses": candidate_hypotheses,
        "factor_proposals": factor_proposals,
        "strategy_candidate_notes": strategy_candidate_notes,
        "review_status": review_status,
        "failure_reason": failure_reason,
        "source_review_candidate_id": source_review_candidate_id,
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


def _safe_input_hash(request: Any) -> str:
    try:
        return _canonical_sha256(request)
    except Exception:
        return hashlib.sha256(repr(request).encode("utf-8", errors="replace")).hexdigest()


def _safe_review_candidate_id(request: Any) -> str | None:
    if not isinstance(request, dict):
        return None
    artifact = request.get("review_artifact")
    if not isinstance(artifact, dict):
        return None
    value = artifact.get("candidate_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _optional_mapping(value: Any, *, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _required_mapping(value, name=name)


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


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
