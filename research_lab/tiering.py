from __future__ import annotations


def classify_strategy(family: str, metrics: dict, cost_stress: dict, data_source: str, data_years: float = 0.0) -> tuple[str, str]:
    unseen = metrics["unseen"]
    trade_based = family in {"SWING", "INTRADAY"}
    if unseen["cagr"] <= 0:
        return "Rejected", "Negative unseen result."
    if unseen["max_drawdown"] < -0.15:
        return "Rejected", "Unseen max drawdown exceeds 15%."
    if trade_based and unseen["trade_count"] < 100:
        return "Rejected", "Too few unseen trades for a trade-based strategy."
    if not cost_stress["survives_double_cost"]:
        return "Rejected", "Double transaction-cost stress destroys unseen profitability."
    if data_source not in {"yfinance", "massive"}:
        return "C", "Synthetic or non-production data source; usable for runner validation only, not capital research."
    if family in {"LONGTERM", "ROTATION"} and data_years < 10.0:
        return "C", "EOD history is shorter than the 10-year promotion requirement; keep as paper research."
    if family == "SWING" and data_years < 3.0:
        return "C", "Swing history is too short for promotion; keep as paper research."
    if unseen["max_drawdown"] >= -0.08 and (unseen["sharpe"] >= 1.0 or unseen["mar"] >= 1.0):
        if not trade_based or unseen["profit_factor"] >= 1.25:
            return "A", "Passes Tier A return, drawdown, cost, and trade-quality gates."
    if unseen["max_drawdown"] >= -0.15 and (unseen["sharpe"] >= 0.75 or unseen["mar"] >= 0.6):
        if not trade_based or unseen["profit_factor"] >= 1.15:
            return "B", "Passes Tier B validation gates with realistic costs."
    return "C", "Promising or incomplete; requires more robustness work before promotion."
