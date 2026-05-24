from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LabConfig:
    root: Path
    mode: str = "research_only"
    data_provider: str = "synthetic"
    eod_cost_bps: float = 5.0
    intraday_cost_bps: float = 8.0
    use_yfinance: bool = False
    massive_api_key: str = ""
    massive_base_url: str = "https://api.massive.com"
    massive_start_date: str = "2021-05-24"
    massive_adjusted: bool = True

    @classmethod
    def from_env(cls, root: Path | None = None) -> "LabConfig":
        resolved_root = root or Path(os.getenv("RESEARCH_LAB_ROOT", ".")).resolve()
        return cls(
            root=resolved_root,
            mode=os.getenv("RESEARCH_LAB_MODE", "research_only"),
            data_provider=os.getenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic").lower(),
            eod_cost_bps=float(os.getenv("RESEARCH_LAB_EOD_COST_BPS", "5")),
            intraday_cost_bps=float(os.getenv("RESEARCH_LAB_INTRADAY_COST_BPS", "8")),
            use_yfinance=os.getenv("RESEARCH_LAB_USE_YFINANCE", "0") == "1",
            massive_api_key=os.getenv("MASSIVE_API_KEY", ""),
            massive_base_url=os.getenv("MASSIVE_BASE_URL", "https://api.massive.com"),
            massive_start_date=os.getenv("MASSIVE_START_DATE", "2021-05-24"),
            massive_adjusted=os.getenv("MASSIVE_ADJUSTED", "true").lower() in {"1", "true", "yes"},
        )


REQUIRED_DIRS = [
    "data/raw",
    "data/processed",
    "data/manifests",
    "strategies/long_term",
    "strategies/active_rotation",
    "strategies/swing",
    "strategies/intraday",
    "strategies/rejected",
    "backtests/runs",
    "backtests/walk_forward",
    "backtests/monte_carlo",
    "reports/daily",
    "reports/weekly",
    "reports/strategy_cards",
    "registry",
    "scripts",
]


def ensure_project_structure(root: Path) -> None:
    for rel in REQUIRED_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)
