from __future__ import annotations

import copy
from typing import Any


RISK_CONTROL_GUIDANCE: dict[str, str] = {
    "volatility_targeting": "Prefer volatility-targeted sizing over fixed full exposure when drawdown or crisis stability is weak.",
    "drawdown_circuit_breakers": "Test explicit de-risking rules after benchmark, strategy-equity, or sleeve drawdown breaches.",
    "cash_defensive_regimes": "Include cash or defensive-asset regimes instead of forcing risk exposure in hostile markets.",
    "exposure_caps": "Cap gross, net, single-asset, and correlated sleeve exposure before increasing return-seeking parameters.",
    "correlation_aware_portfolio_risk": "Measure whether selected assets stack the same risk factor and reduce correlated concentration.",
    "crisis_period_diagnostics": "Report behavior in crisis, bear, inflation/rate-shock, and volatility-spike windows.",
    "cost_slippage_stress": "Require cost and slippage stress to survive before promotion or portfolio inclusion.",
    "parameter_neighborhood_stability": "Prefer parameter neighborhoods that stay robust over isolated high-CAGR settings.",
}

RISK_OPTIMIZATION_OBJECTIVES = [
    "survival",
    "drawdown_containment",
    "walk_forward_robustness",
    "portfolio_level_risk",
]

DEPRIORITIZE_WHEN = {
    "high_cagr_unstable_drawdown": True,
    "weak_walk_forward": True,
    "poor_parameter_neighborhood_stability": True,
    "cost_slippage_fragile": True,
    "portfolio_correlation_concentration": True,
}

PROMOTION_BLOCKS = {
    "synthetic_or_fallback_data": True,
    "relaxed_gates": True,
    "relaxed_max_drawdown_threshold": True,
    "missing_cost_slippage_stress": True,
    "missing_walk_forward_robustness": True,
}

STRONG_ROTATION_OVERLAY_KEYS = {
    "volatility_targeting",
    "drawdown_circuit_breakers",
    "cash_defensive_regimes",
    "exposure_caps",
    "correlation_aware_portfolio_risk",
}


def risk_guidance_payload() -> dict[str, Any]:
    return {
        "risk_management_priority": "survival_first",
        "optimization_objectives": list(RISK_OPTIMIZATION_OBJECTIVES),
        "risk_controls": copy.deepcopy(RISK_CONTROL_GUIDANCE),
        "deprioritize_when": copy.deepcopy(DEPRIORITIZE_WHEN),
        "promotion_blocks": copy.deepcopy(PROMOTION_BLOCKS),
    }


def apply_risk_guidance(item: dict[str, Any]) -> dict[str, Any]:
    guided = copy.deepcopy(item)
    payload = risk_guidance_payload()
    explicit_risk_controls = copy.deepcopy(guided.get("risk_controls", {}))
    guided.setdefault("risk_management_priority", payload["risk_management_priority"])
    guided.setdefault("optimization_objectives", payload["optimization_objectives"])
    guided["risk_controls"] = {
        **payload["risk_controls"],
        **explicit_risk_controls,
    }
    guided.setdefault("explicit_risk_controls", explicit_risk_controls)
    guided.setdefault("deprioritize_when", payload["deprioritize_when"])
    guided.setdefault("promotion_blocks", payload["promotion_blocks"])
    return guided


def has_strong_rotation_risk_overlay(item: dict[str, Any]) -> bool:
    risk_controls = item.get("explicit_risk_controls", item.get("risk_controls"))
    if not isinstance(risk_controls, dict):
        return False
    present = {
        str(key)
        for key, value in risk_controls.items()
        if value not in (None, "", False, "none", "None", "NONE")
    }
    return STRONG_ROTATION_OVERLAY_KEYS.issubset(present)
