import copy

from research_lab.queue_dedupe import candidate_fingerprint
from research_lab.strategy_templates import generate_strategy_candidates


def _template(**overrides):
    base = {
        "template_id": "swing_pullback",
        "family": "SWING",
        "asset_class": "ETF",
        "timeframe": "1D",
        "title": "Template RSI pullback",
        "hypothesis": "Trend-filtered pullbacks can create positive expectancy.",
        "rules": "Buy pullbacks above trend and exit on RSI recovery.",
        "builder": "swing_trend_filtered_pullback",
        "parameter_grid": {
            "symbol": ["SPY", "QQQ"],
            "rsi_entry": [35, 40],
            "weights": [[0.6, 0.4]],
        },
        "filters": {"trend": "sma"},
        "risk_controls": {"max_position_weight": 0.75},
        "source": "template-fixture",
    }
    base.update(overrides)
    return base


def test_generation_is_deterministic_with_stable_candidate_ids():
    templates = [_template()]

    first = generate_strategy_candidates(templates)
    second = generate_strategy_candidates(templates)

    assert first == second
    assert [item["hypothesis_id"] for item in first] == [item["hypothesis_id"] for item in second]
    assert all(item["hypothesis_id"].startswith("TPL_SWING_PULLBACK_") for item in first)


def test_parameter_grid_expands_in_stable_order():
    candidates = generate_strategy_candidates([_template()])

    assert [item["parameters"]["symbol"] for item in candidates] == ["SPY", "SPY", "QQQ", "QQQ"]
    assert [item["parameters"]["rsi_entry"] for item in candidates] == [35, 40, 35, 40]


def test_limit_bounds_generated_candidates():
    candidates = generate_strategy_candidates([_template()], limit=2)

    assert len(candidates) == 2
    assert [item["parameters"]["symbol"] for item in candidates] == ["SPY", "SPY"]


def test_malformed_templates_are_skipped_with_diagnostics():
    valid = _template()
    malformed = {"template_id": "broken", "family": "SWING"}

    candidates, diagnostics = generate_strategy_candidates([malformed, valid], return_diagnostics=True)

    assert len(candidates) == 4
    assert diagnostics["template_count"] == 2
    assert diagnostics["malformed_template_count"] == 1
    assert diagnostics["skipped_template_ids"] == ["broken"]
    assert diagnostics["warnings"] == ["template broken missing required fields: asset_class,builder,timeframe,title"]


def test_dedupe_removes_semantic_duplicates():
    first = _template()
    second = _template()

    candidates, diagnostics = generate_strategy_candidates([first, second], return_diagnostics=True)

    assert len(candidates) == 4
    assert diagnostics["generated_count"] == 8
    assert diagnostics["retained_count"] == 4
    assert diagnostics["duplicate_count"] == 4


def test_ordered_weight_lists_remain_distinct_for_dedupe():
    template = _template(parameter_grid={"symbol": ["SPY"], "weights": [[0.6, 0.4], [0.4, 0.6]]})

    candidates = generate_strategy_candidates([template])

    assert len(candidates) == 2
    assert candidate_fingerprint(candidates[0]) != candidate_fingerprint(candidates[1])


def test_set_like_universe_lists_are_order_insensitive_for_dedupe():
    template = _template(parameter_grid={"universe": [["SPY", "QQQ"], ["QQQ", "SPY"]], "lookback": [126]})

    candidates = generate_strategy_candidates([template])

    assert len(candidates) == 1
    assert candidates[0]["parameters"]["universe"] == ["SPY", "QQQ"]


def test_generated_candidates_contain_runner_compatible_fields():
    candidate = generate_strategy_candidates([_template()], limit=1)[0]

    assert {
        "hypothesis_id",
        "title",
        "family",
        "asset_class",
        "timeframe",
        "hypothesis",
        "parameters",
        "rules",
        "builder",
        "status",
        "research_only",
        "source_key",
    }.issubset(candidate)
    assert candidate["status"] == "queued"
    assert candidate["research_only"] is True


def test_generation_does_not_mutate_input_templates():
    templates = [_template()]
    before = copy.deepcopy(templates)

    generate_strategy_candidates(templates)

    assert templates == before
