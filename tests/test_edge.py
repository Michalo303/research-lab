from research_lab.edge import classify_edge, summarize_edge_audit


def test_classify_edge_detects_smart_money_flow():
    item = {"title": "Dataroma 13F holding", "tags": ["apify", "13f"], "rationale": "Fund holds AAPL"}

    edge = classify_edge(item)

    assert edge["edge_bucket"] == "smart_money_flow"
    assert edge["edge_strength"] == "plausible_filter"


def test_classify_edge_detects_momentum():
    item = {"title": "Regime filtered momentum rotation", "rationale": "Rank by relative strength"}

    edge = classify_edge(item)

    assert edge["edge_bucket"] == "behavioral_momentum"


def test_classify_edge_reads_backtest_hypothesis_fields():
    item = {"strategy_id": "S1", "hypothesis": "Monthly top-N momentum rotation may improve risk-adjusted return."}

    edge = classify_edge(item)

    assert edge["edge_bucket"] == "behavioral_momentum"


def test_classify_edge_marks_unclear_idea():
    edge = classify_edge({"title": "Interesting chart setup"})

    assert edge["edge_bucket"] == "unclear"
    assert edge["edge_strength"] == "missing"


def test_summarize_edge_audit_counts_buckets():
    rows = [
        {"edge_bucket": "smart_money_flow", "edge_strength": "plausible_filter"},
        {"edge_bucket": "unclear", "edge_strength": "missing"},
    ]

    lines = summarize_edge_audit(rows)

    assert any("edge-audited ideas: 2" in line for line in lines)
    assert any("unclear or weak/data-limited ideas: 1" in line for line in lines)
