import math
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PaperGateConfig:
    min_wf_pass_rate: float = 0.67
    max_drawdown: float = -0.15
    min_walk_forward_windows: int = 3

    @classmethod
    def from_env(cls):
        return cls(
            min_wf_pass_rate=float(
                os.getenv("PAPER_GATE_MIN_WF_PASS_RATE", cls.min_wf_pass_rate)
            ),
            max_drawdown=float(
                os.getenv("PAPER_GATE_MAX_DRAWDOWN", cls.max_drawdown)
            ),
            min_walk_forward_windows=int(
                os.getenv(
                    "PAPER_GATE_MIN_WALK_FORWARD_WINDOWS",
                    cls.min_walk_forward_windows,
                )
            ),
        )


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def _passes_strict_walk_forward(walk_forward, robustness, config):
    if not isinstance(walk_forward, dict):
        return False

    window_count = _as_number(walk_forward.get("window_count"))
    pass_rate = _as_number(walk_forward.get("pass_rate"))
    median_test_cagr = _as_number(walk_forward.get("median_test_cagr"))
    worst_test_drawdown = _as_number(walk_forward.get("worst_test_drawdown"))
    windows = walk_forward.get("windows")

    if "windows" in walk_forward and (
        not isinstance(windows, list)
        or any(
            not isinstance(window, dict) or window.get("passed") is not True
            for window in windows
        )
    ):
        return False

    return (
        robustness.get("robustness_verdict") == "pass"
        and walk_forward.get("method") == "true_rolling_oos"
        and walk_forward.get("status") == "ok"
        and window_count is not None
        and window_count >= config.min_walk_forward_windows
        and pass_rate is not None
        and pass_rate >= config.min_wf_pass_rate
        and median_test_cagr is not None
        and median_test_cagr > 0
        and worst_test_drawdown is not None
        and worst_test_drawdown >= -0.20
    )


def _gate_row(item, robustness, parameter_by_group, portfolio, config):
    reasons = []
    robustness = robustness or {}
    portfolio = portfolio or {}
    walk_forward = item.get("walk_forward")

    walk_forward_passed = _passes_strict_walk_forward(
        walk_forward, robustness, config
    )
    if not walk_forward_passed:
        reasons.append("rolling_walk_forward_not_passed")

    unseen = item.get("split_metrics", {}).get("unseen", {})
    max_drawdown = _as_number(unseen.get("max_drawdown"))
    drawdown_passed = max_drawdown is not None and max_drawdown >= config.max_drawdown
    if not drawdown_passed:
        reasons.append("drawdown_below_threshold")

    parameter_verdict = parameter_by_group.get(
        (item.get("family"), item.get("short_name"))
    )
    if parameter_verdict != "pass":
        reasons.append("parameter_verdict_not_passed")

    paper_eligible = not reasons

    return {
        "strategy_id": item.get("strategy_id"),
        "family": item.get("family"),
        "short_name": item.get("short_name"),
        "tier": item.get("tier"),
        "paper_eligible": paper_eligible,
        "gate_verdict": "pass" if paper_eligible else "fail",
        "walk_forward_verdict": "pass" if walk_forward_passed else "fail",
        "drawdown_verdict": "pass" if drawdown_passed else "fail",
        "minimum_walk_forward_windows": config.min_walk_forward_windows,
        "reasons": reasons,
        "portfolio_score": portfolio.get("portfolio_score"),
        "suggested_weight_pct": portfolio.get("suggested_weight_pct"),
    }
