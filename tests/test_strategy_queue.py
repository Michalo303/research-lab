import json

from research_lab.strategies.baselines import queued_hypothesis_strategies


def test_queued_hypotheses_keep_same_title_with_distinct_tickers(tmp_path):
    queue_path = tmp_path / "registry" / "hypothesis_queue.jsonl"
    queue_path.parent.mkdir(parents=True)
    items = [
        {
            "hypothesis_id": "h1",
            "source_key": "dataroma:AAPL",
            "family": "SWING",
            "ticker": "AAPL",
            "title": "Dataroma full-holdings smart-money pullback",
            "rationale": "AAPL accumulation",
        },
        {
            "hypothesis_id": "h2",
            "source_key": "dataroma:MSFT",
            "family": "SWING",
            "ticker": "MSFT",
            "title": "Dataroma full-holdings smart-money pullback",
            "rationale": "MSFT accumulation",
        },
    ]
    queue_path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")

    specs = queued_hypothesis_strategies(tmp_path, limit=4)

    assert [spec.parameters["symbol"] for spec in specs] == ["AAPL", "MSFT"]
