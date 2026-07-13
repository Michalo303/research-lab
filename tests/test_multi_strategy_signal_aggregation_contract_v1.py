from __future__ import annotations

import copy
import hashlib
import itertools

import pytest

from research_lab.execution.multi_strategy_signal_aggregation_contract_v1 import (
    build_multi_strategy_signal_aggregation_contract,
)


def _signal(
    strategy_id: str,
    *,
    symbol: str = "SPY.US",
    intent: str = "LONG",
    decision_timestamp: str = "2026-01-10T15:00:00Z",
    score: float | None = 0.8,
) -> dict[str, object]:
    long = intent == "LONG"
    payload = {
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "strategy_builder": "review_only_test_builder",
        "variant_id": f"{strategy_id}-variant",
        "symbol": symbol,
        "signal_timestamp": decision_timestamp,
        "decision_timestamp": decision_timestamp,
        "action": "ENTRY" if long else "EXIT",
        "target_intent": intent,
        "confidence": 0.75,
        "score": score,
        "protective_exit": (
            {
                "entry_price": 100.0,
                "protective_exit_price": 95.0,
                "per_unit_loss_to_protective_exit": 5.0,
                "protective_exit_type": "price_stop",
                "strategy_provenance": f"{strategy_id}-signal",
            }
            if long
            else None
        ),
        "per_unit_loss": 5.0 if long else None,
        "source_input_sha256": hashlib.sha256(strategy_id.encode("utf-8")).hexdigest(),
        "provenance": {"source": "unit_test"},
    }
    return payload


def _request(signals: list[dict[str, object]], *, policy: str = "MAJORITY") -> dict[str, object]:
    strategy_ids = sorted({str(signal["strategy_id"]) for signal in signals})
    return {
        "version": "multi_strategy_signal_aggregation_request_v1",
        "as_of_timestamp": "2026-01-10T16:00:00Z",
        "maximum_signal_age_seconds": 7200,
        "conflict_policy": policy,
        "priority_weights": {
            strategy_id: index + 1.0 for index, strategy_id in enumerate(strategy_ids)
        },
        "allow_short": False,
        "signals": signals,
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_multi_strategy_signal_aggregation_contract(copy.deepcopy(request))


def test_multiple_assets_and_strategies_are_deterministic_and_order_independent():
    signals = [
        _signal("A"),
        _signal("B"),
        _signal("C", intent="FLAT"),
        _signal("D", symbol="QQQ.US"),
    ]
    expected = _run(_request(signals))
    for permutation in itertools.permutations(signals):
        assert _run(_request(list(permutation))) == expected

    assert [item["symbol"] for item in expected["aggregated_target_intents"]] == ["QQQ.US", "SPY.US"]
    spy = expected["aggregated_target_intents"][1]
    assert spy["target_intent"] == "LONG"
    assert spy["resolution"] == "MAJORITY_LONG"
    assert [item["strategy_id"] for item in spy["protective_exits"]] == ["A", "B"]
    assert [item["per_unit_loss"] for item in spy["per_unit_risk_lineage"]] == [5.0, 5.0]
    assert expected["broker_orders_emitted"] is False
    assert expected["production_runtime_supported"] is False


@pytest.mark.parametrize(
    ("policy", "expected_intent", "resolution"),
    [
        ("UNANIMOUS", "FLAT", "UNANIMOUS_CONFLICT_FLAT"),
        ("MAJORITY", "LONG", "MAJORITY_LONG"),
        ("PRIORITY_WEIGHTED", "FLAT", "PRIORITY_WEIGHTED_FLAT"),
        ("SCORE_WEIGHTED", "LONG", "SCORE_WEIGHTED_LONG"),
        ("RISK_FIRST_VETO", "FLAT", "RISK_FIRST_VETO_FLAT"),
    ],
)
def test_conflict_policies_are_explicit(policy, expected_intent, resolution):
    signals = [_signal("A"), _signal("B"), _signal("C", intent="FLAT", score=0.2)]
    request = _request(signals, policy=policy)
    request["priority_weights"] = {"A": 1.0, "B": 1.0, "C": 3.0}
    result = _run(request)
    aggregate = result["aggregated_target_intents"][0]
    assert aggregate["target_intent"] == expected_intent
    assert aggregate["resolution"] == resolution
    assert result["conflicts"][0]["policy"] == policy


def test_ties_resolve_flat_without_input_order_priority():
    signals = [_signal("A"), _signal("B", intent="FLAT")]
    for policy in ("MAJORITY", "PRIORITY_WEIGHTED", "SCORE_WEIGHTED"):
        request = _request(signals, policy=policy)
        request["priority_weights"] = {"A": 1.0, "B": 1.0}
        result = _run(request)
        assert result["aggregated_target_intents"][0]["target_intent"] == "FLAT"
        assert result["aggregated_target_intents"][0]["tie_resolved_to_flat"] is True


def test_stale_and_duplicate_signals_are_rejected_deterministically():
    stale = _signal("STALE", decision_timestamp="2026-01-10T12:00:00Z")
    duplicate = _signal("DUP")
    result = _run(_request([_signal("A"), stale, duplicate, copy.deepcopy(duplicate)]))
    assert [item["strategy_id"] for item in result["accepted_signals"]] == ["A"]
    assert [item["strategy_id"] for item in result["rejected_stale_signals"]] == ["STALE"]
    assert len(result["rejected_duplicates"]) == 2
    assert result["rejected_duplicates"][0]["duplicate_identity_sha256"] == result[
        "rejected_duplicates"
    ][1]["duplicate_identity_sha256"]


def test_protective_exit_and_per_unit_loss_are_preserved_exactly():
    signal = _signal("A")
    original_exit = copy.deepcopy(signal["protective_exit"])
    result = _run(_request([signal]))
    accepted = result["accepted_signals"][0]
    aggregate = result["aggregated_target_intents"][0]
    assert accepted["protective_exit"] == original_exit
    assert aggregate["protective_exits"][0]["protective_exit"] == original_exit
    assert aggregate["per_unit_risk_lineage"][0]["per_unit_loss"] == 5.0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(unexpected=True), "unknown field"),
        (lambda request: request.update(conflict_policy="ORDERED"), "conflict_policy"),
        (lambda request: request.update(allow_short=True), "short"),
        (lambda request: request["signals"][0].update(target_intent="SHORT"), "target_intent"),
        (lambda request: request["signals"][0].update(confidence=1.1), "confidence"),
        (lambda request: request["signals"][0].update(score=float("nan")), "score"),
        (lambda request: request["signals"][0].update(symbol=""), "symbol"),
        (
            lambda request: request["signals"][0].update(
                decision_timestamp="2026-01-10T17:00:00Z"
            ),
            "future",
        ),
        (lambda request: request["signals"][0].update(protective_exit=None), "protective_exit"),
        (lambda request: request["signals"][0].update(per_unit_loss=4.0), "per_unit_loss"),
    ],
)
def test_invalid_requests_and_signals_fail_closed(mutate, message):
    request = _request([_signal("A")])
    mutate(request)
    with pytest.raises(ValueError, match=message):
        _run(request)


def test_score_weighted_requires_scores_and_priority_policy_requires_weights():
    score_request = _request([_signal("A", score=None), _signal("B", intent="FLAT")], policy="SCORE_WEIGHTED")
    with pytest.raises(ValueError, match="score"):
        _run(score_request)

    priority_request = _request([_signal("A"), _signal("B", intent="FLAT")], policy="PRIORITY_WEIGHTED")
    priority_request["priority_weights"] = {"A": 1.0}
    with pytest.raises(ValueError, match="priority weight"):
        _run(priority_request)
