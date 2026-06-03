from research_lab.self_improvement import _weak_points


def test_eodhd_leaderboard_is_not_reported_as_synthetic_only():
    points = _weak_points(
        [{"strategy_id": "EODHD1", "data_source": "eodhd", "tier": "C"}],
        [{"hypothesis_id": "H1"} for _ in range(5)],
        [{"hypothesis_id": "H1", "strategy_id": "EODHD1"}],
    )

    assert "Current leaderboard is synthetic-only; capital relevance is zero until real data ingestion runs." not in points
