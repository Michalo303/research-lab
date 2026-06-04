from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable


EMPTY_DRAWDOWN_DIAGNOSTICS = {
    "worst_drawdown_start": "",
    "worst_drawdown_trough": "",
    "worst_drawdown_recovery": "",
    "drawdown_duration_days": 0,
    "max_drawdown": 0.0,
    "worst_year_return": 0.0,
    "best_year_return": 0.0,
    "cagr_to_drawdown_ratio": 0.0,
}


def compute_drawdown_diagnostics(equity_curve: Any, cagr: float | None = None) -> dict[str, Any]:
    points = _equity_points(equity_curve)
    if not points:
        return dict(EMPTY_DRAWDOWN_DIAGNOSTICS)

    max_drawdown = 0.0
    peak_date, peak_value = points[0]
    worst_start = ""
    worst_trough = ""
    worst_peak_value = peak_value
    trough_index = 0

    for index, (point_date, value) in enumerate(points):
        if value > peak_value:
            peak_date = point_date
            peak_value = value
        drawdown = value / peak_value - 1.0 if peak_value > 0 else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            worst_start = _format_date(peak_date)
            worst_trough = _format_date(point_date)
            worst_peak_value = peak_value
            trough_index = index

    if max_drawdown >= 0.0:
        result = dict(EMPTY_DRAWDOWN_DIAGNOSTICS)
        result["worst_year_return"] = _worst_year_return(points)
        result["best_year_return"] = _best_year_return(points)
        return result

    recovery_date = ""
    for point_date, value in points[trough_index + 1 :]:
        if value >= worst_peak_value:
            recovery_date = _format_date(point_date)
            break

    start_date = points[0][0] if not worst_start else _parse_date(worst_start)
    end_date = _parse_date(recovery_date) if recovery_date else points[-1][0]
    ratio_cagr = _finite_number(cagr)
    if ratio_cagr is None:
        ratio_cagr = _infer_cagr(points)

    return {
        "worst_drawdown_start": worst_start,
        "worst_drawdown_trough": worst_trough,
        "worst_drawdown_recovery": recovery_date,
        "drawdown_duration_days": max((end_date - start_date).days, 0),
        "max_drawdown": float(max_drawdown),
        "worst_year_return": _worst_year_return(points),
        "best_year_return": _best_year_return(points),
        "cagr_to_drawdown_ratio": round(float(ratio_cagr / abs(max_drawdown)), 16) if max_drawdown < 0 else 0.0,
    }


def drawdown_diagnostics_for_result(result: dict[str, Any]) -> dict[str, Any]:
    existing = result.get("drawdown_diagnostics")
    if isinstance(existing, dict):
        return _normalize_diagnostics(existing)

    cagr = _result_cagr(result)
    equity_curve = result.get("equity_curve")
    if equity_curve:
        return compute_drawdown_diagnostics(equity_curve, cagr=cagr)

    diagnostics = dict(EMPTY_DRAWDOWN_DIAGNOSTICS)
    unseen = result.get("split_metrics", {}).get("unseen", {})
    max_drawdown = _finite_number(unseen.get("max_drawdown"))
    if max_drawdown is not None:
        diagnostics["max_drawdown"] = max_drawdown
    if max_drawdown is not None and max_drawdown < 0 and cagr is not None:
        diagnostics["cagr_to_drawdown_ratio"] = float(cagr / abs(max_drawdown))
    return diagnostics


def _normalize_diagnostics(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(EMPTY_DRAWDOWN_DIAGNOSTICS)
    result.update(value)
    for key in (
        "max_drawdown",
        "worst_year_return",
        "best_year_return",
        "cagr_to_drawdown_ratio",
    ):
        result[key] = float(result.get(key, 0.0) or 0.0)
    result["drawdown_duration_days"] = int(result.get("drawdown_duration_days", 0) or 0)
    for key in ("worst_drawdown_start", "worst_drawdown_trough", "worst_drawdown_recovery"):
        result[key] = str(result.get(key, "") or "")
    return result


def _equity_points(equity_curve: Any) -> list[tuple[date, float]]:
    if equity_curve is None:
        return []
    if hasattr(equity_curve, "dropna") and hasattr(equity_curve, "items"):
        items = equity_curve.dropna().items()
    elif isinstance(equity_curve, dict):
        items = equity_curve.items()
    else:
        items = equity_curve if isinstance(equity_curve, Iterable) else []

    points = []
    for item in items:
        parsed = _parse_equity_item(item)
        if parsed is not None:
            points.append(parsed)
    return sorted(points, key=lambda item: item[0])


def _parse_equity_item(item: Any) -> tuple[date, float] | None:
    if isinstance(item, dict):
        raw_date = item.get("date") or item.get("timestamp")
        raw_value = item.get("value", item.get("equity"))
    else:
        try:
            raw_date, raw_value = item
        except (TypeError, ValueError):
            return None
    point_date = _parse_date(raw_date)
    value = _finite_number(raw_value)
    if point_date is None or value is None:
        return None
    return point_date, value


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _format_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def _year_returns(points: list[tuple[date, float]]) -> list[float]:
    by_year: dict[int, list[float]] = {}
    for point_date, value in points:
        by_year.setdefault(point_date.year, []).append(value)
    returns = []
    for values in by_year.values():
        first = values[0]
        last = values[-1]
        if first > 0:
            returns.append(round(float(last / first - 1.0), 12))
    return returns


def _worst_year_return(points: list[tuple[date, float]]) -> float:
    returns = _year_returns(points)
    return min(returns) if returns else 0.0


def _best_year_return(points: list[tuple[date, float]]) -> float:
    returns = _year_returns(points)
    return max(returns) if returns else 0.0


def _infer_cagr(points: list[tuple[date, float]]) -> float:
    if len(points) < 2:
        return 0.0
    start_date, start_value = points[0]
    end_date, end_value = points[-1]
    duration_days = max((end_date - start_date).days, 1)
    if start_value <= 0 or end_value <= 0:
        return 0.0
    years = duration_days / 365.25
    return float((end_value / start_value) ** (1.0 / years) - 1.0)


def _result_cagr(result: dict[str, Any]) -> float | None:
    for candidate in (
        result.get("metrics", {}).get("cagr"),
        result.get("split_metrics", {}).get("unseen", {}).get("cagr"),
    ):
        value = _finite_number(candidate)
        if value is not None:
            return value
    return None


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
