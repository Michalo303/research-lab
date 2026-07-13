from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from research_lab.execution.multi_strategy_signal_aggregation_contract_v1 import (
    build_multi_strategy_signal_aggregation_contract,
)
from research_lab.execution.portfolio_backtest_acceptance_v1 import (
    run_portfolio_backtest_acceptance,
)
from research_lab.execution.portfolio_capital_allocation_contract_v1 import (
    build_portfolio_capital_allocation_contract,
)
from research_lab.execution.portfolio_position_sizing_contract_v1 import (
    build_portfolio_position_sizing_contract,
)
from research_lab.execution.portfolio_risk_overlay_v1 import (
    build_portfolio_risk_overlay,
)


REQUEST_VERSION = "e2e_portfolio_research_orchestrator_request_v1"
RESULT_VERSION = "e2e_portfolio_research_orchestrator_acceptance_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def run_e2e_portfolio_research_orchestrator_acceptance(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Compose the review-only portfolio research pipeline through a human gate."""
    validated = _request(request)
    _validate_market_bindings(validated)
    aggregation = build_multi_strategy_signal_aggregation_contract(
        validated["aggregation_request"]
    )
    candidates, signal_by_key = _allocation_candidates(
        aggregation,
        evidence=validated["allocation_evidence"],
        provenance=validated["provenance"],
    )
    allocation_request = {
        "version": "portfolio_capital_allocation_request_v1",
        **validated["allocation_config"],
        "candidates": candidates,
        "provenance": validated["provenance"],
    }
    allocation = build_portfolio_capital_allocation_contract(allocation_request)
    if not allocation["research_allocations"]:
        raise ValueError("capital allocation produced no review allocations.")

    risk_allocations = _risk_allocations(
        allocation,
        signal_by_key=signal_by_key,
        bindings=validated["risk_group_bindings"],
        provenance=validated["provenance"],
    )
    risk_request = {
        "version": "portfolio_risk_overlay_request_v1",
        **validated["risk_overlay_config"],
        "allocations": risk_allocations,
        "provenance": validated["provenance"],
    }
    risk_overlay = build_portfolio_risk_overlay(risk_request)
    if risk_overlay["review_status"].startswith("REJECTED"):
        raise ValueError("risk overlay rejected the portfolio.")

    sizing_allocations = _sizing_allocations(
        risk_overlay,
        evidence=validated["sizing_evidence"],
        provenance=validated["provenance"],
    )
    sizing_request = {
        "version": "portfolio_position_sizing_request_v1",
        **validated["sizing_config"],
        "allocations": sizing_allocations,
        "provenance": validated["provenance"],
    }
    sizing = build_portfolio_position_sizing_contract(sizing_request)
    if not sizing["capital_reconciled"]:
        raise ValueError("position sizing failed capital reconciliation.")

    backtest_decisions = _backtest_decisions(
        validated["backtest_decision_schedule"],
        sizing=sizing,
        aggregation=aggregation,
        allocation=allocation,
        risk_overlay=risk_overlay,
    )
    backtest_request = {
        "version": "portfolio_backtest_acceptance_request_v1",
        "synthetic_data_only": True,
        **validated["backtest_config"],
        "decisions": backtest_decisions,
        "provenance": validated["provenance"],
    }
    backtest = run_portfolio_backtest_acceptance(backtest_request)
    _require_backtest_proofs(backtest)

    complete_lineage = {
        "aggregation_sha256": aggregation["output_sha256"],
        "capital_allocation_sha256": allocation["output_sha256"],
        "risk_overlay_sha256": risk_overlay["output_sha256"],
        "position_sizing_sha256": sizing["output_sha256"],
        "backtest_sha256": backtest["output_sha256"],
    }
    human_gate = _pending_human_gate(
        run_id=validated["run_id"],
        created_at=validated["created_at"],
        complete_lineage=complete_lineage,
        provenance=validated["provenance"],
    )
    review_artifact = {
        "run_id": validated["run_id"],
        "created_at": validated["created_at"],
        "strategy_lineage": _strategy_lineage(sizing),
        "asset_lineage": _asset_lineage(candidates),
        "market_lineage": {
            symbol: _canonical_sha256(_normalized_market_bars(bars))
            for symbol, bars in sorted(
                validated["backtest_config"]["market_data"].items()
            )
        },
        "protective_exits": [
            {
                "allocation_id": item["allocation_id"],
                "symbol": item["symbol"],
                "protective_exit": item["protective_exit"],
            }
            for item in sizing["review_only_quantities"]
        ],
        "complete_lineage": complete_lineage,
        "conflict_policy": aggregation["conflict_policy"],
        "capital_limits": validated["allocation_config"],
        "risk_limits": validated["risk_overlay_config"]["limits"],
        "sizing_limits": validated["sizing_config"],
        "capital_reconciled": allocation["capital_reconciled"]
        and sizing["capital_reconciled"],
        "cash_reconciled": backtest["cash_reconciled"],
        "equity_reconciled": backtest["equity_reconciled"],
        "initial_cash": backtest["initial_cash"],
        "ending_cash": backtest["ending_cash"],
        "ending_equity": backtest["ending_equity"],
        "transaction_costs": backtest["transaction_costs"],
        "slippage_costs": backtest["slippage_costs"],
        "human_gate_status": "HUMAN_APPROVAL_REQUIRED",
    }
    review_artifact["review_artifact_sha256"] = _canonical_sha256(review_artifact)
    result = {
        "version": RESULT_VERSION,
        "request_sha256": _canonical_sha256(validated),
        "run_id": validated["run_id"],
        "created_at": validated["created_at"],
        "aggregation_result": aggregation,
        "capital_allocation_result": allocation,
        "risk_overlay_result": risk_overlay,
        "position_sizing_result": sizing,
        "backtest_result": backtest,
        "review_artifact": review_artifact,
        "human_approval_gate": human_gate,
        "complete_lineage": complete_lineage,
        "final_status": "HUMAN_APPROVAL_REQUIRED",
        "deterministic_replay_supported": True,
        "provenance": validated["provenance"],
        "provider_calls_used": 0,
        "network_used": False,
        "broker_orders_emitted": False,
        "broker_actions_used": 0,
        "paper_trading_performed": False,
        "registry_write_performed": False,
        "deployment_performed": False,
        "promotion_performed": False,
        "generated_code_executed": False,
        "automatic_approval_performed": False,
        "automatic_strategy_application_performed": False,
        "automatic_allocation_application_performed": False,
        "production_runtime_supported": False,
    }
    result["output_sha256"] = _canonical_sha256(result)
    return result


def replay_e2e_portfolio_research_orchestrator_acceptance(
    request: dict[str, Any], *, expected_output_sha256: str
) -> dict[str, Any]:
    expected = _sha256(expected_output_sha256, "expected_output_sha256")
    replayed = run_e2e_portfolio_research_orchestrator_acceptance(request)
    actual = replayed["output_sha256"]
    return {
        "version": "e2e_portfolio_research_orchestrator_replay_v1",
        "replay_status": "REPLAY_MATCH" if actual == expected else "REPLAY_MISMATCH",
        "expected_output_sha256": expected,
        "replayed_output_sha256": actual,
        "complete_lineage": replayed["complete_lineage"],
        "production_runtime_supported": False,
    }


def _request(raw: Any) -> dict[str, Any]:
    payload = _mapping(raw, "E2E portfolio request")
    allowed = {
        "version",
        "run_id",
        "created_at",
        "synthetic_data_only",
        "aggregation_request",
        "allocation_config",
        "allocation_evidence",
        "risk_overlay_config",
        "risk_group_bindings",
        "sizing_config",
        "sizing_evidence",
        "backtest_config",
        "backtest_decision_schedule",
        "provenance",
    }
    _reject_unknown(payload, allowed, "E2E portfolio request")
    if payload.get("version") != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    if payload.get("synthetic_data_only") is not True:
        raise ValueError("synthetic_data_only must be true.")
    return {
        "version": REQUEST_VERSION,
        "run_id": _text(payload.get("run_id"), "run_id"),
        "created_at": _format_timestamp(_timestamp(payload.get("created_at"), "created_at")),
        "synthetic_data_only": True,
        "aggregation_request": _json_mapping(
            payload.get("aggregation_request"), "aggregation_request"
        ),
        "allocation_config": _json_mapping(
            payload.get("allocation_config"), "allocation_config"
        ),
        "allocation_evidence": _evidence_map(
            payload.get("allocation_evidence"), "allocation evidence"
        ),
        "risk_overlay_config": _json_mapping(
            payload.get("risk_overlay_config"), "risk_overlay_config"
        ),
        "risk_group_bindings": _evidence_map(
            payload.get("risk_group_bindings"), "risk group bindings"
        ),
        "sizing_config": _json_mapping(payload.get("sizing_config"), "sizing_config"),
        "sizing_evidence": _evidence_map(
            payload.get("sizing_evidence"), "sizing evidence"
        ),
        "backtest_config": _json_mapping(
            payload.get("backtest_config"), "backtest_config"
        ),
        "backtest_decision_schedule": _schedule(
            payload.get("backtest_decision_schedule")
        ),
        "provenance": _json_mapping(payload.get("provenance"), "provenance"),
    }


def _allocation_candidates(
    aggregation: dict[str, Any],
    *,
    evidence: dict[str, dict[str, Any]],
    provenance: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    accepted = aggregation["accepted_signals"]
    long_groups = {
        (item["symbol"], item["decision_timestamp"])
        for item in aggregation["aggregated_target_intents"]
        if item["target_intent"] == "LONG"
    }
    signals = [
        signal
        for signal in accepted
        if signal["target_intent"] == "LONG"
        and (signal["symbol"], signal["decision_timestamp"]) in long_groups
    ]
    if not signals:
        raise ValueError("signal aggregation produced no LONG allocation candidates.")
    signal_by_key = {_allocation_key(signal): signal for signal in signals}
    if set(evidence) != set(signal_by_key):
        raise ValueError("allocation evidence must exactly match aggregated LONG candidates.")
    candidates: list[dict[str, Any]] = []
    for key, signal in sorted(signal_by_key.items()):
        item = evidence[key]
        _reject_unknown(
            item,
            {"score", "estimated_loss_fraction", "asset_lineage"},
            "allocation evidence item",
        )
        candidates.append(
            {
                "strategy_id": signal["strategy_id"],
                "strategy_version": signal["strategy_version"],
                "strategy_builder": signal["strategy_builder"],
                "variant_id": signal["variant_id"],
                "symbol": signal["symbol"],
                "target_intent": "LONG",
                "score": item.get("score"),
                "risk_evidence": {
                    "estimated_loss_fraction": item.get("estimated_loss_fraction"),
                    "protective_exit_sha256": _canonical_sha256(signal["protective_exit"]),
                    "source_input_sha256": signal["source_input_sha256"],
                },
                "asset_lineage": item.get("asset_lineage"),
                "provenance": provenance,
            }
        )
    return candidates, signal_by_key


def _validate_market_bindings(validated: dict[str, Any]) -> None:
    market_data = validated["backtest_config"].get("market_data")
    if not isinstance(market_data, dict) or not market_data:
        raise ValueError("backtest_config.market_data must be a non-empty object.")
    aggregation_symbols = {
        str(signal.get("symbol", "")).upper()
        for signal in validated["aggregation_request"].get("signals", [])
        if isinstance(signal, dict)
    }
    if not aggregation_symbols or aggregation_symbols - set(market_data):
        raise ValueError("aggregation symbols must exactly bind to available market data.")
    for key, evidence in validated["allocation_evidence"].items():
        lineage = evidence.get("asset_lineage")
        if not isinstance(lineage, dict):
            raise ValueError("allocation evidence asset_lineage must be an object.")
        symbol = lineage.get("symbol")
        if not isinstance(symbol, str) or symbol not in market_data:
            raise ValueError("allocation evidence asset_lineage symbol has no market data.")
        expected = _canonical_sha256(_normalized_market_bars(market_data[symbol]))
        if lineage.get("market_data_sha256") != expected:
            raise ValueError(
                f"allocation evidence market_data_sha256 does not bind evaluated bars for {key}."
            )


def _normalized_market_bars(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("market data bars must be a list.")
    normalized = []
    for bar in raw:
        if not isinstance(bar, dict):
            raise ValueError("market data bar must be an object.")
        normalized.append(
            {
                "timestamp": bar.get("timestamp"),
                "open": float(bar.get("open")),
                "high": float(bar.get("high")),
                "low": float(bar.get("low")),
                "close": float(bar.get("close")),
                "volume": float(bar.get("volume")),
                "source_input_sha256": bar.get("source_input_sha256"),
            }
        )
    return normalized


def _risk_allocations(
    allocation: dict[str, Any],
    *,
    signal_by_key: dict[str, dict[str, Any]],
    bindings: dict[str, dict[str, Any]],
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    allocations = allocation["research_allocations"]
    keys = {_allocation_key(item) for item in allocations}
    if set(bindings) != keys:
        raise ValueError("risk group bindings must exactly match capital allocations.")
    result = []
    for item in allocations:
        key = _allocation_key(item)
        binding = bindings[key]
        _reject_unknown(
            binding,
            {"concentration_group_ids", "correlation_group_ids"},
            "risk group binding",
        )
        signal = signal_by_key[key]
        result.append(
            {
                "allocation_id": key,
                "strategy_id": item["strategy_id"],
                "strategy_version": item["strategy_version"],
                "strategy_builder": item["strategy_builder"],
                "variant_id": item["variant_id"],
                "symbol": item["symbol"],
                "direction": "LONG",
                "allocated_capital": item["allocated_capital"],
                "estimated_loss_fraction": item["risk_evidence"]["estimated_loss_fraction"],
                "protective_exit": signal["protective_exit"],
                "source_allocation_sha256": item["allocation_sha256"],
                "concentration_group_ids": binding.get("concentration_group_ids"),
                "correlation_group_ids": binding.get("correlation_group_ids"),
                "provenance": provenance,
            }
        )
    return result


def _sizing_allocations(
    risk_overlay: dict[str, Any],
    *,
    evidence: dict[str, dict[str, Any]],
    provenance: dict[str, Any],
) -> list[dict[str, Any]]:
    disposition: list[tuple[dict[str, Any], float]] = [
        (item, item["allocated_capital"])
        for item in risk_overlay["accepted_allocations"]
    ] + [
        (item, item["accepted_capital"])
        for item in risk_overlay["clipped_allocations"]
    ]
    keys = {item["allocation_id"] for item, _ in disposition}
    if set(evidence) != keys:
        raise ValueError("sizing evidence must exactly match risk-accepted allocations.")
    result = []
    for item, capital in sorted(disposition, key=lambda pair: pair[0]["allocation_id"]):
        key = item["allocation_id"]
        sizing_evidence = evidence[key]
        _reject_unknown(
            sizing_evidence,
            {"price_evidence", "atr_evidence", "volatility_evidence", "kelly_evidence"},
            "sizing evidence item",
        )
        result.append(
            {
                "allocation_id": key,
                "strategy_id": item["strategy_id"],
                "strategy_version": item["strategy_version"],
                "strategy_builder": item["strategy_builder"],
                "variant_id": item["variant_id"],
                "symbol": item["symbol"],
                "allocated_capital": capital,
                "price_evidence": sizing_evidence.get("price_evidence"),
                "protective_exit": item["protective_exit"],
                "per_unit_risk": item["protective_exit"][
                    "per_unit_loss_to_protective_exit"
                ],
                "atr_evidence": sizing_evidence.get("atr_evidence"),
                "volatility_evidence": sizing_evidence.get("volatility_evidence"),
                "kelly_evidence": sizing_evidence.get("kelly_evidence"),
                "source_allocation_sha256": item["source_allocation_sha256"],
                "provenance": provenance,
            }
        )
    return result


def _backtest_decisions(
    schedule: list[dict[str, str]],
    *,
    sizing: dict[str, Any],
    aggregation: dict[str, Any],
    allocation: dict[str, Any],
    risk_overlay: dict[str, Any],
) -> list[dict[str, Any]]:
    sized = {item["allocation_id"]: item for item in sizing["review_only_quantities"]}
    invalid = sorted({item["allocation_key"] for item in schedule} - set(sized))
    if invalid:
        raise ValueError("backtest decision allocation_key is not present in sizing output.")
    rejected_signals = [
        f"{item['strategy_id']}:{item['decision_timestamp']}:STALE"
        for item in aggregation["rejected_stale_signals"]
    ] + [
        f"{item['strategy_id']}:{item['decision_timestamp']}:DUPLICATE"
        for item in aggregation["rejected_duplicates"]
    ]
    rejected_allocations = [
        f"{item['strategy_id']}:{item['symbol']}:{item['reason']}"
        for item in allocation["rejected_allocations"]
    ] + [
        f"{item['strategy_id']}:{item['symbol']}:{item['reason']}"
        for item in risk_overlay["rejected_allocations"]
    ]
    risk_events = risk_overlay["binding_constraints"]
    stage_lineage = {
        "aggregation_sha256": aggregation["output_sha256"],
        "capital_allocation_sha256": allocation["output_sha256"],
        "risk_overlay_sha256": risk_overlay["output_sha256"],
        "position_sizing_sha256": sizing["output_sha256"],
    }
    result = []
    for item in schedule:
        source = sized[item["allocation_key"]]
        intent = item["target_intent"]
        result.append(
            {
                "decision_id": item["decision_id"],
                "decision_timestamp": item["decision_timestamp"],
                "strategy_id": source["strategy_id"],
                "strategy_version": source["strategy_version"],
                "strategy_builder": source["strategy_builder"],
                "variant_id": source["variant_id"],
                "symbol": source["symbol"],
                "target_intent": intent,
                "target_quantity": source["quantity"] if intent == "LONG" else 0.0,
                "protective_exit_price": source["protective_exit"]["protective_exit_price"]
                if intent == "LONG"
                else None,
                "stage_lineage": stage_lineage,
                "rejected_signals": rejected_signals,
                "rejected_allocations": rejected_allocations,
                "risk_limit_events": risk_events,
                "provenance": source["provenance"],
            }
        )
    return result


def _pending_human_gate(
    *,
    run_id: str,
    created_at: str,
    complete_lineage: dict[str, str],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    gate = {
        "version": "portfolio_human_approval_gate_v1",
        "gate_status": "HUMAN_APPROVAL_REQUIRED",
        "run_id": run_id,
        "requested_at": created_at,
        "bound_artifact_hashes": complete_lineage,
        "reviewer_identity": None,
        "approval_timestamp": None,
        "approval_artifact": None,
        "provenance": provenance,
        "execution_authority_granted": False,
        "automatic_approval_performed": False,
        "production_runtime_supported": False,
    }
    gate["output_sha256"] = _canonical_sha256(gate)
    return gate


def _require_backtest_proofs(backtest: dict[str, Any]) -> None:
    required = {
        "cash_reconciled": True,
        "equity_reconciled": True,
        "no_same_bar_fill_proof": True,
        "chronological_execution_proof": True,
        "no_future_data_used": True,
    }
    failed = sorted(key for key, expected in required.items() if backtest.get(key) is not expected)
    if failed:
        raise ValueError(f"backtest proof validation failed: {', '.join(failed)}.")


def _strategy_lineage(sizing: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "strategy_id": item["strategy_id"],
            "strategy_version": item["strategy_version"],
            "strategy_builder": item["strategy_builder"],
            "variant_id": item["variant_id"],
            "symbol": item["symbol"],
        }
        for item in sizing["review_only_quantities"]
    ]


def _asset_lineage(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"symbol": item["symbol"], **item["asset_lineage"]}
        for item in candidates
    ]


def _allocation_key(item: dict[str, Any]) -> str:
    return f"{item['strategy_id']}|{item['variant_id']}|{item['symbol']}"


def _schedule(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("backtest_decision_schedule must be a non-empty list.")
    result = []
    for item in raw:
        value = _mapping(item, "backtest decision schedule item")
        _reject_unknown(
            value,
            {"decision_id", "allocation_key", "decision_timestamp", "target_intent"},
            "backtest decision schedule item",
        )
        intent = _text(value.get("target_intent"), "target_intent")
        if intent not in {"LONG", "FLAT"}:
            raise ValueError("schedule target_intent must be LONG or FLAT.")
        result.append(
            {
                "decision_id": _text(value.get("decision_id"), "decision_id"),
                "allocation_key": _text(value.get("allocation_key"), "allocation_key"),
                "decision_timestamp": _format_timestamp(
                    _timestamp(value.get("decision_timestamp"), "decision_timestamp")
                ),
                "target_intent": intent,
            }
        )
    ids = [item["decision_id"] for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("backtest decision schedule contains duplicate decision_id.")
    return sorted(
        result,
        key=lambda item: (
            item["decision_timestamp"],
            item["allocation_key"],
            item["decision_id"],
        ),
    )


def _evidence_map(raw: Any, name: str) -> dict[str, dict[str, Any]]:
    value = _mapping(raw, name)
    if not value:
        raise ValueError(f"{name} must not be empty.")
    return {
        _text(key, f"{name} key"): _json_mapping(item, f"{name} item")
        for key, item in sorted(value.items())
    }


def _timestamp(raw: Any, name: str) -> datetime:
    value = _text(raw, name)
    if not value.endswith("Z"):
        raise ValueError(f"{name} must be UTC and end in Z.")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UTC timestamp.") from exc


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _sha256(raw: Any, name: str) -> str:
    value = _text(raw, name)
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256.")
    return value


def _text(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
        raise ValueError(f"{name} must be non-empty text without outer whitespace.")
    return raw


def _mapping(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be an object.")
    return raw


def _json_mapping(raw: Any, name: str) -> dict[str, Any]:
    value = _mapping(raw, name)
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain canonical JSON data.") from exc


def _reject_unknown(payload: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}.")


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
