import copy

from research_lab.hypothesis_diagnostics import summarize_hypothesis_diagnostics
from research_lab.queue_dedupe import candidate_fingerprint


def _hypothesis(**overrides):
    base = {
        "hypothesis_id": "H1",
        "family": "SWING",
        "asset_class": "ETF",
        "timeframe": "1D",
        "template": "rsi_pullback",
        "parameters": {"symbol": "SPY", "rsi_entry": 35, "rsi_exit": 55},
        "source_key": "seed:one",
    }
    base.update(overrides)
    return base


def test_empty_input_returns_safe_zero_diagnostics():
    diagnostics = summarize_hypothesis_diagnostics([])

    assert diagnostics == {
        "total_hypotheses_seen": 0,
        "unique_fingerprints": 0,
        "duplicate_fingerprints": 0,
        "duplicate_rate": 0.0,
        "family_counts": {},
        "asset_counts": {},
        "timeframe_counts": {},
        "source_counts": {},
        "skipped_or_deduped_reason_counts": {},
        "top_duplicate_fingerprints": [],
    }


def test_duplicates_are_counted_correctly_and_bounded():
    duplicate_one = _hypothesis(hypothesis_id="A", source_key="seed:a")
    duplicate_two = _hypothesis(hypothesis_id="B", source_key="seed:b")
    duplicate_three = _hypothesis(hypothesis_id="C", source_key="seed:c")
    unique = _hypothesis(
        hypothesis_id="D",
        parameters={"symbol": "QQQ", "rsi_entry": 35, "rsi_exit": 55},
        source_key="seed:d",
    )

    diagnostics = summarize_hypothesis_diagnostics([duplicate_one, duplicate_two, duplicate_three, unique], top_n=1)

    assert diagnostics["total_hypotheses_seen"] == 4
    assert diagnostics["unique_fingerprints"] == 2
    assert diagnostics["duplicate_fingerprints"] == 1
    assert diagnostics["duplicate_rate"] == 0.5
    assert len(diagnostics["top_duplicate_fingerprints"]) == 1
    assert diagnostics["top_duplicate_fingerprints"][0]["count"] == 3
    assert diagnostics["top_duplicate_fingerprints"][0]["duplicate_count"] == 2
    assert "items" not in diagnostics["top_duplicate_fingerprints"][0]


def test_reuses_queue_dedupe_fingerprints_for_ordered_and_unordered_lists():
    ordered_first = _hypothesis(hypothesis_id="W1", parameters={"symbols": ["SPY", "QQQ"], "weights": [0.6, 0.4]})
    ordered_second = _hypothesis(hypothesis_id="W2", parameters={"symbols": ["SPY", "QQQ"], "weights": [0.4, 0.6]})
    unordered_first = _hypothesis(hypothesis_id="S1", parameters={"symbols": ["SPY", "QQQ"], "lookback": 126})
    unordered_second = _hypothesis(hypothesis_id="S2", parameters={"symbols": ["QQQ", "SPY"], "lookback": 126})

    diagnostics = summarize_hypothesis_diagnostics([ordered_first, ordered_second, unordered_first, unordered_second])

    assert candidate_fingerprint(ordered_first) != candidate_fingerprint(ordered_second)
    assert candidate_fingerprint(unordered_first) == candidate_fingerprint(unordered_second)
    assert diagnostics["total_hypotheses_seen"] == 4
    assert diagnostics["unique_fingerprints"] == 3
    assert diagnostics["duplicate_fingerprints"] == 1


def test_counts_by_family_asset_timeframe_and_source():
    items = [
        _hypothesis(family="SWING", asset_class="ETF", timeframe="1D", source_key="smartmoney:AAPL:80"),
        _hypothesis(family="SWING", asset_class="ETF", timeframe="1D", source_key="smartmoney:MSFT:75"),
        _hypothesis(
            family="ROTATION",
            asset_class="CRYPTO",
            timeframe="4H",
            source_key="",
            source_title="arxiv paper",
            parameters={"symbol": "BTCUSDT", "lookback": 24},
        ),
    ]

    diagnostics = summarize_hypothesis_diagnostics(items)

    assert diagnostics["family_counts"] == {"SWING": 2, "ROTATION": 1}
    assert diagnostics["asset_counts"] == {"ETF": 2, "CRYPTO": 1}
    assert diagnostics["timeframe_counts"] == {"1D": 2, "4H": 1}
    assert diagnostics["source_counts"] == {"smartmoney": 2, "arxiv paper": 1}


def test_diagnostics_do_not_mutate_input_hypotheses():
    items = [_hypothesis(), _hypothesis(hypothesis_id="B")]
    before = copy.deepcopy(items)

    summarize_hypothesis_diagnostics(items)

    assert items == before


def test_skipped_and_deduped_reason_counts_are_reported_when_present():
    items = [
        _hypothesis(status="skipped", skip_reason="missing data"),
        _hypothesis(status="deduped", dedupe_reason="semantic_duplicate"),
        _hypothesis(status="queued"),
    ]

    diagnostics = summarize_hypothesis_diagnostics(items)

    assert diagnostics["skipped_or_deduped_reason_counts"] == {
        "missing data": 1,
        "semantic_duplicate": 1,
    }
