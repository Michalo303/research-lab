from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence


RESULT_VERSION = "minervini_evaluation_gate_result_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def evaluate_minervini_result_v1(
    portfolio_result: Mapping[str, object],
    *,
    data_blockers: Sequence[str],
) -> dict[str, object]:
    if (
        portfolio_result.get("version")
        != "minervini_portfolio_evaluator_result_v1"
    ):
        raise ValueError("portfolio result version is unsupported.")
    portfolio_hash = portfolio_result.get("output_sha256")
    if not isinstance(portfolio_hash, str) or not _SHA256_RE.fullmatch(
        portfolio_hash
    ):
        raise ValueError("portfolio result hash is invalid.")
    blockers = sorted(_validated_blockers(data_blockers))
    cagr = _finite_number(portfolio_result.get("cagr"), "cagr")
    drawdown = _finite_number(
        portfolio_result.get("maximum_drawdown"), "maximum_drawdown"
    )
    trade_count = portfolio_result.get("trade_count")
    if (
        isinstance(trade_count, bool)
        or not isinstance(trade_count, int)
        or trade_count < 0
    ):
        raise ValueError("trade_count must be a non-negative integer.")
    if blockers:
        verdict = "INSUFFICIENT_EVIDENCE"
        reasons = blockers
    elif trade_count < 100:
        verdict = "INSUFFICIENT_EVIDENCE"
        reasons = ["MINIMUM_OOS_TRADES_NOT_MET"]
    elif cagr < 0.10:
        verdict = "FAIL"
        reasons = ["MINIMUM_OOS_CAGR_NOT_MET"]
    elif drawdown < -0.15:
        verdict = "FAIL"
        reasons = ["MAXIMUM_OOS_DRAWDOWN_BREACHED"]
    else:
        verdict = "CANDIDATE"
        reasons = ["PRIMARY_RESEARCH_GATES_MET"]
    result: dict[str, object] = {
        "version": RESULT_VERSION,
        "verdict": verdict,
        "reasons": reasons,
        "data_blockers": blockers,
        "portfolio_result_sha256": portfolio_hash,
        "cagr": cagr,
        "maximum_drawdown": drawdown,
        "trade_count": trade_count,
        "provider_calls_used": 0,
        "network_used": False,
        "broker_actions_used": 0,
        "registry_write_performed": False,
        "promotion_performed": False,
        "deployment_performed": False,
        "production_runtime_supported": False,
    }
    result["output_payload_sha256"] = hashlib.sha256(
        json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return result


def _validated_blockers(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("data_blockers must be a sequence of text values.")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError("data_blockers must contain normalized text.")
        result.append(value)
    if len(result) != len(set(result)):
        raise ValueError("data_blockers must not contain duplicates.")
    return result


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite.")
    number = float(value)
    if not (-float("inf") < number < float("inf")):
        raise ValueError(f"{name} must be finite.")
    return number
