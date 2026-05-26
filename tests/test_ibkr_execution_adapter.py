import pytest

from execution.ibkr.execution_adapter import IbkrPaperExecutionAdapter


def test_execution_adapter_always_rejects_live_mode(tmp_path):
    adapter = IbkrPaperExecutionAdapter(root=tmp_path, mode="live")

    with pytest.raises(RuntimeError, match="Live IBKR trading is not supported"):
        adapter.submit_paper_orders([])


def test_execution_adapter_rejects_paper_orders_without_readiness_and_approvals(tmp_path):
    adapter = IbkrPaperExecutionAdapter(root=tmp_path, mode="paper")

    with pytest.raises(RuntimeError, match="blocked scaffold"):
        adapter.submit_paper_orders([{"symbol": "SPY", "quantity": 1}])
