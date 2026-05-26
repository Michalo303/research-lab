from research_lab.runner import run_daily_research


def test_daily_results_persist_true_walk_forward(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_LAB_DATA_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_LAB_MODE", "research_only")

    results = run_daily_research(tmp_path)

    assert results
    assert all(result["walk_forward"]["method"] == "true_rolling_oos" for result in results)
