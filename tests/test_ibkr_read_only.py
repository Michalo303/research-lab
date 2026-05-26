from types import SimpleNamespace

import pytest

from execution.ibkr.config import IbkrConfig, assert_paper_only
from execution.ibkr.paper_gateway import read_only_account_snapshot


class FakeIbClient:
    def __init__(self):
        self.connected = False
        self.disconnected = False
        self.connect_kwargs = {}
        self.market_data_type = None

    def connect(self, host, port, **kwargs):
        self.connected = True
        self.connect_kwargs = {"host": host, "port": port, **kwargs}

    def reqMarketDataType(self, market_data_type):
        self.market_data_type = market_data_type

    def managedAccounts(self):
        return ["DU123456"]

    def accountSummary(self, account):
        return [
            SimpleNamespace(account=account, tag="NetLiquidation", value="100000", currency="USD"),
            SimpleNamespace(account=account, tag="BuyingPower", value="200000", currency="USD"),
        ]

    def positions(self):
        contract = SimpleNamespace(symbol="SPY", secType="STK", exchange="SMART", currency="USD")
        return [SimpleNamespace(account="DU123456", contract=contract, position=10, avgCost=500.0)]

    def reqMktData(self, contract, *args, **kwargs):
        symbol = contract.symbol
        if symbol == "SPY":
            return SimpleNamespace(bid=500.0, ask=500.1, last=500.05, marketDataType=1)
        if symbol == "QQQ":
            return SimpleNamespace(bid=420.0, ask=420.2, last=420.1, marketDataType=4)
        if symbol == "TLT":
            return SimpleNamespace(bid=90.0, ask=90.1, last=90.05, marketDataType=3)
        return SimpleNamespace(bid=None, ask=None, last=None, marketDataType=1)

    def cancelMktData(self, contract):
        pass

    def disconnect(self):
        self.disconnected = True


def test_read_only_snapshot_connects_readonly_and_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT", "DU123456")
    monkeypatch.setenv("IBKR_MODE", "paper")
    monkeypatch.setenv("IBKR_READ_ONLY", "1")
    client = FakeIbClient()

    result = read_only_account_snapshot(tmp_path, ib_client=client)

    assert result["status"] == "connected_read_only"
    assert result["orders_enabled"] is False
    assert client.connect_kwargs["readonly"] is True
    assert client.disconnected is True
    assert result["account_summary"][0]["tag"] == "NetLiquidation"
    assert result["positions"][0]["symbol"] == "SPY"
    assert (tmp_path / "reports" / "execution" / "ibkr_paper_read_only_snapshot.json").exists()


def test_missing_ib_insync_returns_controlled_status(tmp_path, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT", "DU123456")
    monkeypatch.setenv("IBKR_MODE", "paper")
    monkeypatch.setenv("IBKR_READ_ONLY", "1")
    monkeypatch.setattr("execution.ibkr.paper_gateway._load_ib_client", lambda: None)

    result = read_only_account_snapshot(tmp_path)

    assert result["status"] == "missing_ib_insync"
    assert result["market_data_checks"] == []


def test_read_only_snapshot_records_quote_status_for_core_etfs(tmp_path, monkeypatch):
    monkeypatch.setenv("IBKR_ACCOUNT", "DU123456")
    monkeypatch.setenv("IBKR_MODE", "paper")
    monkeypatch.setenv("IBKR_READ_ONLY", "1")
    monkeypatch.setenv("IBKR_MARKET_DATA_TYPE", "1")

    result = read_only_account_snapshot(tmp_path, ib_client=FakeIbClient())

    statuses = {row["symbol"]: row["status"] for row in result["market_data_checks"]}
    assert statuses == {
        "SPY": "bid",
        "QQQ": "delayed",
        "TLT": "frozen",
        "GLD": "missing",
    }


def test_ibkr_config_defaults_do_not_embed_personal_account(monkeypatch, tmp_path):
    monkeypatch.delenv("IBKR_ACCOUNT", raising=False)
    monkeypatch.delenv("IBKR_CLIENT_ID", raising=False)

    config = IbkrConfig.from_env(tmp_path)

    assert config.account == ""
    assert config.client_id == 1


def test_paper_orders_require_env_acknowledgement_even_with_approval_file(tmp_path, monkeypatch):
    (tmp_path / "APPROVED_FOR_PAPER_IBKR_ORDERS.md").write_text("approved for paper testing only", encoding="utf-8")
    monkeypatch.delenv("RESEARCH_LAB_ALLOW_PAPER_ORDERS", raising=False)
    config = IbkrConfig(
        account="DU123456",
        mode="paper",
        host="127.0.0.1",
        port=4002,
        client_id=1,
        read_only=False,
        connect_timeout=8.0,
        market_data_type=4,
        root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="RESEARCH_LAB_ALLOW_PAPER_ORDERS"):
        assert_paper_only(config)
