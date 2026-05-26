from __future__ import annotations

from pathlib import Path
from typing import Any


class IbkrPaperExecutionAdapter:
    """Blocked scaffold for future IBKR paper execution readiness checks."""

    def __init__(self, root: Path, mode: str = "paper") -> None:
        self.root = root
        self.mode = mode.lower()

    def submit_paper_orders(self, orders: list[dict[str, Any]]) -> None:
        if self.mode != "paper":
            raise RuntimeError("Live IBKR trading is not supported by this research lab.")
        raise RuntimeError(
            "IBKR paper order placement remains a blocked scaffold until stable read-only snapshots, "
            "reconciliation pass, simulator pass, explicit env acknowledgement, approval file, and account allowlist are all present."
        )
