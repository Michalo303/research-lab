from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from execution.ibkr.config import IbkrConfig, assert_paper_only


def check_ibkr_paper_config(root: Path | None = None) -> dict:
    config = IbkrConfig.from_env(root)
    assert_paper_only(config)
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "account": config.account,
        "mode": config.mode,
        "host": config.host,
        "port": config.port,
        "client_id": config.client_id,
        "read_only": config.read_only,
        "status": "configured_read_only" if config.read_only else "configured_paper_orders_requires_manual_test",
        "live_trading": False,
    }
    report_dir = config.root / "reports" / "execution"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "ibkr_paper_config_check.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def explain_connection_requirements(root: Path | None = None) -> str:
    config = IbkrConfig.from_env(root)
    return (
        "IBKR paper execution requires IB Gateway or TWS running and logged into the paper account. "
        f"The lab will connect to {config.host}:{config.port} with client_id={config.client_id}. "
        "Current scaffold is read-only unless APPROVED_FOR_PAPER_IBKR_ORDERS.md exists."
    )

