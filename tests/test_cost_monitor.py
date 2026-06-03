from research_lab.cost_monitor import run_research_cost_monitor, summarize_research_costs


def test_cost_monitor_counts_apify_rows_and_market_units(tmp_path, monkeypatch):
    apify_dir = tmp_path / "data" / "processed" / "apify_dataroma"
    apify_dir.mkdir(parents=True)
    (apify_dir / "holdings_1.json").write_text('{"item_count": 250}', encoding="utf-8")
    manifest_dir = tmp_path / "data" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "daily_universe.json").write_text(
        '{"source": "massive", "symbols": ["SPY", "QQQ", "TLT"]}',
        encoding="utf-8",
    )
    queue_dir = tmp_path / "registry"
    queue_dir.mkdir()
    (queue_dir / "hypothesis_queue.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_COST_APIFY_DOLLARS_PER_1000_ROWS", "2")
    monkeypatch.setenv("RESEARCH_COST_MARKET_DATA_DOLLARS_PER_SYMBOL_REFRESH", "0.1")

    result = run_research_cost_monitor(tmp_path, "2026-W21")

    assert result["path"].exists()
    assert result["total_estimated_cost_usd"] == 0.8
    assert any("$0.8000" in line for line in summarize_research_costs(result["rows"]))


def test_cost_monitor_counts_eodhd_market_units(tmp_path, monkeypatch):
    manifest_dir = tmp_path / "data" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "daily_universe.json").write_text(
        '{"source": "eodhd", "symbols": ["SPY", "QQQ", "TLT", "GLD"]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_COST_MARKET_DATA_DOLLARS_PER_SYMBOL_REFRESH", "0.1")

    result = run_research_cost_monitor(tmp_path, "2026-W21")

    market = next(row for row in result["rows"] if row["category"] == "market_data")
    assert market["quantity"] == 4
    assert market["estimated_cost_usd"] == 0.4
    assert "eodhd=4" in market["notes"]
