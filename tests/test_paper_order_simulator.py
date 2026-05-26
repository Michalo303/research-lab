import json

from execution.ibkr.paper_order_simulator import PaperOrderSimulationConfig, simulate_paper_orders


def test_simulator_respects_strategy_allowlist_and_rejects_unknown_strategy(tmp_path):
    config = PaperOrderSimulationConfig(strategy_allowlist={"STRAT_OK"}, max_order_notional=30000.0)
    result = simulate_paper_orders(
        tmp_path,
        candidates=[
            {"strategy_id": "STRAT_OK", "target_weights": {"SPY": 0.2}},
            {"strategy_id": "STRAT_BLOCKED", "target_weights": {"QQQ": 0.1}},
        ],
        latest_prices={"SPY": 500.0, "QQQ": 400.0},
        equity=100000.0,
        config=config,
    )

    statuses = {order["strategy_id"]: order["status"] for order in result["orders"]}
    assert statuses["STRAT_OK"] == "filled"
    assert statuses["STRAT_BLOCKED"] == "rejected"
    assert "not in PAPER_ORDER_STRATEGY_ALLOWLIST" in result["orders"][1]["reject_reason"]


def test_simulator_append_only_jsonl(tmp_path):
    config = PaperOrderSimulationConfig(strategy_allowlist={"STRAT_OK"}, max_order_notional=10000.0)
    for weight in (0.05, 0.06):
        simulate_paper_orders(
            tmp_path,
            candidates=[{"strategy_id": "STRAT_OK", "target_weights": {"SPY": weight}}],
            latest_prices={"SPY": 500.0},
            equity=100000.0,
            config=config,
        )

    path = tmp_path / "registry" / "paper_order_simulations.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["orders"][0]["target_weight"] == 0.05
    assert rows[1]["orders"][0]["target_weight"] == 0.06


def test_simulator_rejects_order_above_max_notional(tmp_path):
    config = PaperOrderSimulationConfig(strategy_allowlist={"STRAT_OK"}, max_order_notional=1000.0)
    result = simulate_paper_orders(
        tmp_path,
        candidates=[{"strategy_id": "STRAT_OK", "target_weights": {"SPY": 0.2}}],
        latest_prices={"SPY": 500.0},
        equity=100000.0,
        config=config,
    )

    assert result["orders"][0]["status"] == "rejected"
    assert "exceeds max_order_notional" in result["orders"][0]["reject_reason"]
