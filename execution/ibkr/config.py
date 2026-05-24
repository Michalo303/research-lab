from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IbkrConfig:
    account: str
    mode: str
    host: str
    port: int
    client_id: int
    read_only: bool
    root: Path

    @classmethod
    def from_env(cls, root: Path | None = None) -> "IbkrConfig":
        return cls(
            account=os.getenv("IBKR_ACCOUNT", "jcbfp583"),
            mode=os.getenv("IBKR_MODE", "paper").lower(),
            host=os.getenv("IBKR_HOST", "127.0.0.1"),
            port=int(os.getenv("IBKR_PORT", "4002")),
            client_id=int(os.getenv("IBKR_CLIENT_ID", "583")),
            read_only=os.getenv("IBKR_READ_ONLY", "1") == "1",
            root=(root or Path(os.getenv("RESEARCH_LAB_ROOT", "."))).resolve(),
        )


def assert_paper_only(config: IbkrConfig) -> None:
    if config.mode != "paper":
        raise RuntimeError("IBKR gateway refuses non-paper mode.")
    if not config.read_only:
        approval = config.root / "APPROVED_FOR_PAPER_IBKR_ORDERS.md"
        if not approval.exists():
            raise RuntimeError("IBKR order placement requires APPROVED_FOR_PAPER_IBKR_ORDERS.md.")
    live_approval = config.root / "APPROVED_FOR_LIVE_IBKR.md"
    if live_approval.exists():
        raise RuntimeError("Live IBKR approval file is not supported by this research lab.")

