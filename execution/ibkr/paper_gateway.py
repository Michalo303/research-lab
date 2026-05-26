from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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
        "connect_timeout": config.connect_timeout,
        "market_data_type": config.market_data_type,
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
        "Current scaffold is read-only unless the paper approval file and explicit env acknowledgements are present."
    )


def read_only_account_snapshot(root: Path | None = None, ib_client=None) -> dict:
    config = IbkrConfig.from_env(root)
    assert_paper_only(config)
    if not config.read_only:
        raise RuntimeError("Read-only account snapshot requires IBKR_READ_ONLY=1.")

    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "account": config.account,
        "mode": config.mode,
        "host": config.host,
        "port": config.port,
        "client_id": config.client_id,
        "read_only": True,
        "market_data_type": config.market_data_type,
        "live_trading": False,
        "orders_enabled": False,
        "status": "not_started",
        "managed_accounts": [],
        "account_summary": [],
        "positions": [],
        "market_data_checks": [],
        "error": "",
    }

    client = ib_client or _load_ib_client()
    if client is None:
        payload["status"] = "missing_ib_insync"
        payload["error"] = "Install ib_insync in the server venv to run the IBKR read-only connection check."
        _write_snapshot(config.root, payload)
        return payload

    try:
        _connect_read_only(client, config)
        _set_market_data_type(client, config.market_data_type)
        payload["managed_accounts"] = _managed_accounts(client)
        payload["account_summary"] = _account_summary(client, config.account)
        payload["positions"] = _positions(client, config.account)
        payload["market_data_checks"] = _market_data_checks(client, ["SPY", "QQQ", "TLT", "GLD"])
        payload["status"] = "connected_read_only"
    except Exception as exc:
        payload["status"] = "connection_failed"
        payload["error"] = str(exc)
    finally:
        _disconnect(client)

    _write_snapshot(config.root, payload)
    return payload


def _load_ib_client():
    try:
        from ib_insync import IB
    except ImportError:
        return None
    return IB()


def _connect_read_only(client, config: IbkrConfig) -> None:
    client.connect(
        config.host,
        config.port,
        clientId=config.client_id,
        timeout=config.connect_timeout,
        readonly=True,
        account=config.account,
    )


def _set_market_data_type(client, market_data_type: int) -> None:
    if hasattr(client, "reqMarketDataType"):
        client.reqMarketDataType(market_data_type)


def _managed_accounts(client) -> list[str]:
    if not hasattr(client, "managedAccounts"):
        return []
    return [str(account) for account in client.managedAccounts()]


def _account_summary(client, account: str) -> list[dict]:
    if not hasattr(client, "accountSummary"):
        return []
    rows = []
    for item in client.accountSummary(account):
        rows.append(
            {
                "account": str(getattr(item, "account", "")),
                "tag": str(getattr(item, "tag", "")),
                "value": str(getattr(item, "value", "")),
                "currency": str(getattr(item, "currency", "")),
            }
        )
    return rows


def _positions(client, account: str) -> list[dict]:
    if not hasattr(client, "positions"):
        return []
    rows = []
    for item in client.positions():
        item_account = str(getattr(item, "account", ""))
        if account and item_account and item_account != account:
            continue
        contract = getattr(item, "contract", None)
        rows.append(
            {
                "account": item_account,
                "symbol": str(getattr(contract, "symbol", "")),
                "sec_type": str(getattr(contract, "secType", "")),
                "exchange": str(getattr(contract, "exchange", "")),
                "currency": str(getattr(contract, "currency", "")),
                "position": float(getattr(item, "position", 0.0) or 0.0),
                "avg_cost": float(getattr(item, "avgCost", 0.0) or 0.0),
            }
        )
    return rows


def _market_data_checks(client, symbols: list[str]) -> list[dict]:
    rows = []
    for symbol in symbols:
        contract = _stock_contract(symbol)
        row = {
            "symbol": symbol,
            "status": "missing",
            "bid": None,
            "ask": None,
            "last": None,
            "market_data_type": None,
            "error": "",
        }
        try:
            ticker = client.reqMktData(contract, "", False, False)
            if hasattr(client, "sleep"):
                client.sleep(1)
            row["bid"] = _quote_float(getattr(ticker, "bid", None))
            row["ask"] = _quote_float(getattr(ticker, "ask", None))
            row["last"] = _quote_float(getattr(ticker, "last", None))
            row["market_data_type"] = _quote_market_data_type(ticker)
            row["status"] = _quote_status(row)
        except Exception as exc:
            row["status"] = "error"
            row["error"] = str(exc)
        finally:
            if hasattr(client, "cancelMktData"):
                try:
                    client.cancelMktData(contract)
                except Exception:
                    pass
        rows.append(row)
    return rows


def _stock_contract(symbol: str):
    try:
        from ib_insync import Stock

        return Stock(symbol, "SMART", "USD")
    except ImportError:
        return SimpleNamespace(symbol=symbol, secType="STK", exchange="SMART", currency="USD")


def _quote_status(row: dict) -> str:
    market_data_type = row.get("market_data_type")
    if market_data_type in {2, 4}:
        return "delayed"
    if market_data_type == 3:
        return "frozen"
    if row.get("bid") is not None:
        return "bid"
    if row.get("ask") is not None:
        return "ask"
    if row.get("last") is not None:
        return "last"
    return "missing"


def _quote_market_data_type(ticker) -> int | None:
    value = getattr(ticker, "marketDataType", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _quote_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _disconnect(client) -> None:
    if hasattr(client, "disconnect"):
        client.disconnect()


def _write_snapshot(root: Path, payload: dict) -> Path:
    report_dir = root / "reports" / "execution"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "ibkr_paper_read_only_snapshot.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
