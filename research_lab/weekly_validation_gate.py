from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from research_lab.config import REAL_EOD_DATA_SOURCES


PASS = "PASS"
WARNING = "WARNING"
FAIL = "FAIL"

DEPLOYABLE = "DEPLOYABLE"
WATCHLIST = "WATCHLIST"
REJECTED = "REJECTED"

MIN_REAL_DATA_YEARS = 5.0
MIN_DEPLOYABLE_DATA_YEARS = 10.0
MAX_DEPLOYABLE_DRAWDOWN = -0.15
MAX_FAIL_DRAWDOWN = -0.25
MIN_PASS_RATE = 0.50

REQUIRED_METRICS = (
    "experiments_run",
    "accepted_count",
    "best_unseen_return",
    "best_max_drawdown",
    "data_years",
    "data_source",
    "synthetic_used",
)


@dataclass(frozen=True)
class WeeklyValidationGateResult:
    status: str
    tier: str
    reasons: list[str]
    metrics: dict[str, Any]
    evaluated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_weekly_validation_gate(
    metrics: Mapping[str, Any],
    *,
    evaluated_at: datetime | str | None = None,
) -> WeeklyValidationGateResult:
    normalized = _normalize_metrics(metrics)
    reasons: list[str] = []

    missing_required = list(normalized["missing_required_metrics"])
    if missing_required:
        reasons.append(f"missing_required_metrics:{','.join(missing_required)}")

    experiments_run = normalized["experiments_run"]
    if experiments_run is not None and experiments_run <= 0:
        reasons.append("no_experiments_run")

    real_data_available = _real_data_available(normalized["data_source"])
    synthetic_used = normalized["synthetic_used"] is True
    if normalized["data_source"] is not None and synthetic_used and not real_data_available:
        reasons.append("synthetic_data_only")

    data_years = normalized["data_years"]
    if data_years is not None and data_years < MIN_REAL_DATA_YEARS:
        reasons.append("insufficient_real_data_history")

    best_unseen_return = normalized["best_unseen_return"]
    if best_unseen_return is not None and best_unseen_return <= 0:
        reasons.append("non_positive_unseen_return")

    best_max_drawdown = normalized["best_max_drawdown"]
    if best_max_drawdown is not None and best_max_drawdown < MAX_FAIL_DRAWDOWN:
        reasons.append("max_drawdown_worse_than_25pct")

    walk_forward_pass_rate = normalized["walk_forward_pass_rate"]
    if walk_forward_pass_rate is not None and walk_forward_pass_rate < MIN_PASS_RATE:
        reasons.append("walk_forward_pass_rate_below_threshold")

    robustness_pass_rate = normalized["robustness_pass_rate"]
    if robustness_pass_rate is not None and robustness_pass_rate < MIN_PASS_RATE:
        reasons.append("robustness_pass_rate_below_threshold")

    if reasons:
        return WeeklyValidationGateResult(
            status=FAIL,
            tier=REJECTED,
            reasons=reasons,
            metrics=normalized,
            evaluated_at=_timestamp(evaluated_at),
        )

    warning_reasons: list[str] = []
    if data_years is not None and MIN_REAL_DATA_YEARS <= data_years < MIN_DEPLOYABLE_DATA_YEARS:
        warning_reasons.append("limited_real_data_history")

    accepted_count = normalized["accepted_count"]
    if accepted_count == 0 and best_unseen_return is not None and best_unseen_return > 0:
        warning_reasons.append("positive_unseen_without_accepted_strategy")

    if walk_forward_pass_rate is None:
        warning_reasons.append("walk_forward_metrics_incomplete")
    if robustness_pass_rate is None:
        warning_reasons.append("robustness_metrics_incomplete")

    if (
        best_max_drawdown is not None
        and MAX_FAIL_DRAWDOWN <= best_max_drawdown < MAX_DEPLOYABLE_DRAWDOWN
    ):
        warning_reasons.append("drawdown_between_15pct_and_25pct")

    if synthetic_used and real_data_available:
        warning_reasons.append("synthetic_data_used_with_real_data")

    deployable = (
        real_data_available
        and synthetic_used is False
        and data_years is not None
        and data_years >= MIN_DEPLOYABLE_DATA_YEARS
        and accepted_count is not None
        and accepted_count > 0
        and best_unseen_return is not None
        and best_unseen_return > 0
        and best_max_drawdown is not None
        and best_max_drawdown >= MAX_DEPLOYABLE_DRAWDOWN
        and walk_forward_pass_rate is not None
        and walk_forward_pass_rate >= MIN_PASS_RATE
        and robustness_pass_rate is not None
        and robustness_pass_rate >= MIN_PASS_RATE
    )

    if deployable and not warning_reasons:
        status = PASS
        tier = DEPLOYABLE
        final_reasons = ["all_deployable_thresholds_met"]
    else:
        status = WARNING
        tier = WATCHLIST
        final_reasons = warning_reasons or ["deployable_thresholds_not_fully_met"]

    return WeeklyValidationGateResult(
        status=status,
        tier=tier,
        reasons=final_reasons,
        metrics=normalized,
        evaluated_at=_timestamp(evaluated_at),
    )


def build_weekly_validation_metrics(
    robustness_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    deployment_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> dict[str, Any]:
    rows = [row for row in (robustness_rows or []) if isinstance(row, dict)]
    deployment = [row for row in (deployment_rows or []) if isinstance(row, dict)]
    data_sources = [str(row.get("data_source", "")).strip().lower() for row in rows]
    real_sources = sorted({source for source in data_sources if source in REAL_EOD_DATA_SOURCES})
    synthetic_used = any(source in {"synthetic", ""} for source in data_sources) if rows else False
    data_years_values = [_finite_number(row.get("data_years")) for row in rows]
    unseen_values = [_finite_number(row.get("unseen_cagr")) for row in rows]
    validation_values = [_finite_number(row.get("median_test_cagr")) for row in rows]
    drawdown_values = [_finite_number(row.get("unseen_max_drawdown")) for row in rows]
    walk_forward_values = [_finite_number(row.get("pass_rate")) for row in rows]

    if deployment:
        accepted_count = sum(1 for row in deployment if row.get("paper_eligible") is True)
        rejected_count = sum(1 for row in deployment if row.get("paper_eligible") is not True)
    else:
        accepted_count = sum(1 for row in rows if row.get("robustness_verdict") == "pass")
        rejected_count = sum(1 for row in rows if row.get("robustness_verdict") == "fail")

    missing_required_metrics = []
    if rows and not any(value is not None for value in data_years_values):
        missing_required_metrics.append("data_years")
    if rows and not any(value is not None for value in unseen_values):
        missing_required_metrics.append("best_unseen_return")
    if rows and not any(value is not None for value in drawdown_values):
        missing_required_metrics.append("best_max_drawdown")
    if rows and not any(data_sources):
        missing_required_metrics.append("data_source")

    return {
        "experiments_run": len(rows),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "best_validation_return": _max_or_none(validation_values),
        "best_unseen_return": _max_or_none(unseen_values),
        "best_max_drawdown": _max_or_none(drawdown_values),
        "walk_forward_pass_rate": _max_or_none(walk_forward_values),
        "robustness_pass_rate": _share(row.get("robustness_verdict") == "pass" for row in rows) if rows else None,
        "data_years": _max_or_none(data_years_values),
        "data_source": ",".join(real_sources) if real_sources else ("synthetic" if synthetic_used else None),
        "synthetic_used": synthetic_used,
        "missing_required_metrics": missing_required_metrics,
    }


def render_weekly_validation_gate_markdown(result: WeeklyValidationGateResult | Mapping[str, Any]) -> str:
    payload = result.as_dict() if isinstance(result, WeeklyValidationGateResult) else dict(result)
    reasons = list(payload.get("reasons") or [])
    metrics = dict(payload.get("metrics") or {})
    lines = [
        "## Weekly Validation Gate",
        "",
        f"- status: {payload.get('status', '')}",
        f"- tier: {payload.get('tier', '')}",
        f"- evaluated_at: {payload.get('evaluated_at', '')}",
        "- key reasons:",
    ]
    lines.extend(f"  - {reason}" for reason in reasons)
    lines.append("- key metrics:")
    for key in sorted(metrics):
        lines.append(f"  - {key}: {metrics[key]}")
    return "\n".join(lines)


def _normalize_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(metrics)
    provided_missing = normalized.get("missing_required_metrics") or []
    if isinstance(provided_missing, str):
        missing_required = [provided_missing]
    else:
        missing_required = [str(item) for item in provided_missing]

    normalized["missing_required_metrics"] = _dedupe_preserving_order(missing_required)
    for key in (
        "experiments_run",
        "accepted_count",
        "rejected_count",
        "best_validation_return",
        "best_unseen_return",
        "best_max_drawdown",
        "walk_forward_pass_rate",
        "robustness_pass_rate",
        "data_years",
    ):
        normalized[key] = _finite_number(normalized.get(key))
    normalized["synthetic_used"] = _as_bool(normalized.get("synthetic_used"))
    normalized["data_source"] = _normalize_data_source(normalized.get("data_source"))
    for key in REQUIRED_METRICS:
        if normalized.get(key) is None:
            missing_required.append(key)
    normalized["missing_required_metrics"] = _dedupe_preserving_order(missing_required)
    return normalized


def _timestamp(value: datetime | str | None) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    return None


def _normalize_data_source(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip().lower() for item in value if str(item).strip()]
        return ",".join(sorted(parts)) if parts else None
    text = str(value).strip().lower()
    return text or None


def _real_data_available(data_source: str | None) -> bool:
    if not data_source:
        return False
    sources = {item.strip().lower() for item in data_source.split(",") if item.strip()}
    return any(source in REAL_EOD_DATA_SOURCES for source in sources)


def _max_or_none(values: list[float | None]) -> float | None:
    finite = [value for value in values if value is not None]
    if not finite:
        return None
    return max(finite)


def _share(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(1 for item in items if item) / len(items)


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
