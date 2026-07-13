from __future__ import annotations

import ast
import copy
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path("research_lab/execution/corporate_actions_contract_v1.py")
_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _action(action_type: str, *, action_id: str = "action-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action_id": action_id,
        "instrument_id": "spy-us",
        "action_type": action_type,
        "announcement_timestamp": "2024-01-02T00:00:00Z",
        "availability_timestamp": "2024-01-03T00:00:00Z",
        "ex_timestamp": "2024-01-04T00:00:00Z",
        "effective_timestamp": "2024-01-04T00:00:00Z",
        "source_identity": "provider-actions-feed-v1",
        "source_sha256": _SHA_A,
        "point_in_time_status": "POINT_IN_TIME_VERIFIED",
        "provenance": {"source": "unit_test"},
    }
    if action_type in {"SPLIT", "REVERSE_SPLIT", "STOCK_DIVIDEND"}:
        payload["factor"] = 2.0
    if action_type == "CASH_DIVIDEND":
        payload["amount"] = 1.25
        payload["currency"] = "USD"
    if action_type == "SYMBOL_CHANGE":
        payload["predecessor_symbol"] = "SPY"
        payload["successor_symbol"] = "SPY.NEW"
    if action_type in {"MERGER", "SPINOFF"}:
        payload["successor_symbol"] = "SUCCESSOR"
    return {**payload, **overrides}


def _request(*, policy: str = "RAW_PRICES_NO_ADJUSTMENT") -> dict[str, object]:
    return {
        "version": "corporate_actions_contract_request_v1",
        "corporate_actions_id": "spy-us-corporate-actions-v1",
        "instrument_identity": {"instrument_id": "spy-us", "provider_symbol": "SPY.US"},
        "adjustment_policy": policy,
        "actions": [_action("CASH_DIVIDEND")],
        "expected_price_series_identity": {
            "price_series_id": "spy-us-raw-v1",
            "adjustment_basis": "RAW_PRICES",
            "source_sha256": _SHA_B,
        },
        "expected_source_hashes": {"provider-actions-feed-v1": _SHA_A},
        "as_of_timestamp": "2024-01-03T00:00:00Z",
        "provenance": {"request_source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("corporate_actions_contract_v1", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_corporate_actions_contract(copy.deepcopy(request))


@pytest.mark.parametrize(
    "action_type",
    [
        "CASH_DIVIDEND",
        "STOCK_DIVIDEND",
        "SPLIT",
        "REVERSE_SPLIT",
        "SYMBOL_CHANGE",
        "MERGER",
        "SPINOFF",
        "DELISTING",
    ],
)
def test_supported_action_types_are_validated_and_deterministically_ordered(action_type: str):
    request = _request()
    request["actions"] = [
        _action(action_type, action_id="action-b", ex_timestamp="2024-01-05T00:00:00Z", effective_timestamp="2024-01-05T00:00:00Z"),
        _action("CASH_DIVIDEND", action_id="action-a", amount=0.5),
    ]

    result = _run(request)

    assert [item["action_id"] for item in result["validated_actions"]] == ["action-a", "action-b"]
    assert result["action_timeline"][0]["availability_timestamp"] == "2024-01-03T00:00:00Z"
    assert result["provider_calls_used"] == 0
    assert result["network_used"] is False
    assert result["registry_write_performed"] is False
    assert result["production_runtime_supported"] is False


def test_no_actions_declared_is_explicit_evidence_not_proof_of_no_actions():
    request = _request()
    request["actions"] = [_action("NO_ACTIONS_DECLARED", announcement_timestamp=None, ex_timestamp=None, effective_timestamp=None)]

    result = _run(request)

    assert result["validated_actions"][0]["action_type"] == "NO_ACTIONS_DECLARED"
    assert result["point_in_time_coverage"]["no_actions_declared"] is True
    assert "NO_ACTIONS_DECLARED is evidence limited to the declared source and as-of timestamp." in result["warnings"]


def test_cash_dividend_currency_and_exact_availability_boundary_are_enforced():
    request = _request()
    result = _run(request)
    assert result["validated_actions"][0]["currency"] == "USD"

    missing_currency = _request()
    missing_currency["actions"] = [_action("CASH_DIVIDEND", currency=None)]
    with pytest.raises(ValueError, match="cash dividends require currency"):
        _run(missing_currency)

    future = _request()
    future["actions"] = [_action("CASH_DIVIDEND", availability_timestamp="2024-01-03T00:00:01Z")]
    with pytest.raises(ValueError, match="not available at as_of_timestamp"):
        _run(future)


def test_rejects_duplicate_semantic_actions_and_invalid_split_factors():
    duplicate_id = _request()
    duplicate_id["actions"] = [_action("SPLIT"), _action("SPLIT")]
    with pytest.raises(ValueError, match="duplicate action_id"):
        _run(duplicate_id)

    contradictory = _request()
    contradictory["actions"] = [_action("SPLIT", action_id="split-a", factor=2.0), _action("SPLIT", action_id="split-b", factor=3.0)]
    with pytest.raises(ValueError, match="contradictory split factors"):
        _run(contradictory)

    corrected_cash_dividend = _request()
    corrected_cash_dividend["actions"] = [
        _action("CASH_DIVIDEND", action_id="cash-a", amount=1.0),
        _action("CASH_DIVIDEND", action_id="cash-b", amount=1.5),
    ]
    with pytest.raises(ValueError, match="duplicate semantic action"):
        _run(corrected_cash_dividend)

    invalid = _request()
    invalid["actions"] = [_action("REVERSE_SPLIT", factor=0.0)]
    with pytest.raises(ValueError, match="positive finite factor"):
        _run(invalid)


def test_rejects_symbol_cycles_wrong_instrument_hash_mismatch_and_policy_mismatch():
    cycle = _request()
    cycle["actions"] = [
        _action("SYMBOL_CHANGE", action_id="one", predecessor_symbol="AAA", successor_symbol="BBB"),
        _action("SYMBOL_CHANGE", action_id="two", predecessor_symbol="BBB", successor_symbol="AAA"),
    ]
    with pytest.raises(ValueError, match="symbol-change cycle"):
        _run(cycle)

    wrong_instrument = _request()
    wrong_instrument["actions"] = [_action("DELISTING", instrument_id="qqq-us")]
    with pytest.raises(ValueError, match="does not match instrument_identity"):
        _run(wrong_instrument)

    mismatch = _request()
    mismatch["expected_source_hashes"] = {"provider-actions-feed-v1": _SHA_B}
    with pytest.raises(ValueError, match="source_sha256 does not match"):
        _run(mismatch)

    policy_mismatch = _request(policy="SPLIT_ADJUSTED_ONLY")
    with pytest.raises(ValueError, match="requires SPLIT_ADJUSTED price series"):
        _run(policy_mismatch)


def test_rejects_unknown_fields_invalid_timestamp_order_and_non_finite_amount():
    unknown = _request()
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="unknown field"):
        _run(unknown)

    invalid_order = _request()
    invalid_order["actions"] = [_action("CASH_DIVIDEND", announcement_timestamp="2024-01-04T00:00:00Z")]
    with pytest.raises(ValueError, match="announcement_timestamp must not be later"):
        _run(invalid_order)

    non_finite = _request()
    non_finite["actions"] = [_action("CASH_DIVIDEND", amount=float("inf"))]
    with pytest.raises(ValueError, match="amount must be finite"):
        _run(non_finite)


def test_deterministic_hashes_input_immutability_and_no_network_imports():
    request = _request()
    original = copy.deepcopy(request)

    first = _run(request)
    second = _run(request)

    assert request == original
    assert first == second
    assert first["input_sha256"] == second["input_sha256"]
    assert first["output_payload_sha256"] == second["output_payload_sha256"]

    forbidden_roots = ("requests", "urllib", "http", "socket", "aiohttp")
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports = {
        alias.name if isinstance(node, ast.Import) else node.module
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [node])
        if (isinstance(node, ast.Import) or node.module)
    }
    assert not any(
        import_name == root or import_name.startswith(root + ".")
        for import_name in imports
        for root in forbidden_roots
    )
