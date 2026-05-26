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
    connect_timeout: float
    market_data_type: int
    root: Path

    @classmethod
    def from_env(cls, root: Path | None = None) -> "IbkrConfig":
        return cls(
            account=os.getenv("IBKR_ACCOUNT", ""),
            mode=os.getenv("IBKR_MODE", "paper").lower(),
            host=os.getenv("IBKR_HOST", "127.0.0.1"),
            port=int(os.getenv("IBKR_PORT", "4002")),
            client_id=int(os.getenv("IBKR_CLIENT_ID", "1")),
            read_only=os.getenv("IBKR_READ_ONLY", "1") == "1",
            connect_timeout=float(os.getenv("IBKR_CONNECT_TIMEOUT", "8")),
            market_data_type=int(os.getenv("IBKR_MARKET_DATA_TYPE", "4")),
            root=(root or Path(os.getenv("RESEARCH_LAB_ROOT", "."))).resolve(),
        )


def assert_paper_only(config: IbkrConfig) -> None:
    if config.mode != "paper":
        raise RuntimeError("IBKR gateway refuses non-paper mode.")
    live_approval = config.root / "APPROVED_FOR_LIVE_IBKR.md"
    if live_approval.exists():
        raise RuntimeError("Live IBKR approval file is not supported by this research lab.")
    if not config.read_only:
        if os.getenv("RESEARCH_LAB_ALLOW_PAPER_ORDERS", "") != "YES_I_UNDERSTAND":
            raise RuntimeError("IBKR order placement requires RESEARCH_LAB_ALLOW_PAPER_ORDERS=YES_I_UNDERSTAND.")
        if not config.account:
            raise RuntimeError("IBKR order placement requires an explicit IBKR_ACCOUNT.")
        allowlist = {
            account.strip()
            for account in os.getenv("IBKR_PAPER_ACCOUNT_ALLOWLIST", "").split(",")
            if account.strip()
        }
        if allowlist and config.account not in allowlist:
            raise RuntimeError("IBKR account is not present in IBKR_PAPER_ACCOUNT_ALLOWLIST.")
        if not allowlist and not config.account.upper().startswith("DU"):
            raise RuntimeError("IBKR paper orders require a DU-prefixed paper account or IBKR_PAPER_ACCOUNT_ALLOWLIST.")
        approval = config.root / "APPROVED_FOR_PAPER_IBKR_ORDERS.md"
        if not approval.exists():
            raise RuntimeError("IBKR order placement requires APPROVED_FOR_PAPER_IBKR_ORDERS.md.")
