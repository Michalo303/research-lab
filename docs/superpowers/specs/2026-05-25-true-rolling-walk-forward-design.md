# True Rolling Walk-Forward Validation Design

## Goal

Introduce a true rolling out-of-sample walk-forward validation layer for `research_lab`.

The current `rolling_walk_forward` in `research_lab/backtest.py` is legacy pseudo-WF because it evaluates already-computed full-history weights and returns. The new layer must rebuild strategy weights per rolling train/test window using only data available inside that window slice.

This PR validates strategy stability without mixing in parameter refit or optimization.

## Architecture

Add a new module:

- `research_lab/walk_forward.py`

Keep the existing `rolling_walk_forward` in `research_lab/backtest.py` compatible for now, or gradually redirect it later. Do not mix the true WF implementation into `backtest.py`.

The true WF output becomes the single `result.json["walk_forward"]` source consumed by tiering, robustness, deployment gate, and weekly reporting. Legacy `rolling_train_then_test` output must not be accepted as passing the deployment gate.

## Scope

In scope:

- True rolling OOS WF validation.
- Explicit rolling windows:
  - `train_years = 5`
  - `test_years = 1`
  - `step_years = 1`
- Same strategy parameters in every window.
- Window-level CAGR, max drawdown, MAR, trades, exposure, regime, and pass flag.
- Aggregate pass rate, median/worst window metrics, and regime summaries.
- Integration with runner, robustness output, deployment gate, and weekly report.

Out of scope:

- Parameter refit.
- Parameter optimization based on WF results.
- Portfolio combination layer.
- Paper/live/broker changes.
- Apify changes.

## Public API

```python
run_true_walk_forward(
    spec: StrategySpec,
    daily_panel: pd.DataFrame,
    intraday_panel: pd.DataFrame | None,
    close: pd.DataFrame,
    cost_bps: float,
    periods_per_year: int,
    train_years: int = 5,
    test_years: int = 1,
    step_years: int = 1,
) -> dict
```

## Window Logic

For each rolling window:

1. Define `train_start`, `train_end`, `test_start`, and `test_end` from the available index.
2. Slice input data from `train_start` through `test_end`.
3. Call `build_weights(spec, sliced_daily_panel, sliced_intraday_panel)` only on that sliced data.
4. Evaluate returned weights only on `test_start:test_end`.
5. Treat any weights before `test_start` as warm-up/training context only.
6. Apply transaction costs based on turnover inside the test interval.
7. Store test metrics and exposure.

`build_weights(...)` must never be called on full-history data for true WF. This is the main behavioral boundary that prevents future full-history weight leakage from re-entering the validation path.

## No-Leakage Rule

For a test day `T`, the strategy may use historical data `<= T` but never data after `T`.

Rolling pandas indicators over the `train_start:test_end` slice are acceptable because rolling calculations do not look forward by default. The implementation must never compute weights on full-history data and then slice the test interval afterward.

## Output Contract

`result.json["walk_forward"]` must use this structure:

```json
{
  "method": "true_rolling_oos",
  "train_years": 5,
  "test_years": 1,
  "step_years": 1,
  "window_count": 12,
  "pass_rate": 0.75,
  "median_test_cagr": 0.11,
  "median_test_mar": 0.72,
  "worst_test_cagr": -0.04,
  "worst_test_drawdown": -0.18,
  "windows": []
}
```

Each window row:

```json
{
  "window": 1,
  "train_start": "...",
  "train_end": "...",
  "test_start": "...",
  "test_end": "...",
  "test_cagr": 0.08,
  "test_max_drawdown": -0.09,
  "test_mar": 0.89,
  "test_trade_count": 14,
  "test_average_exposure": 0.62,
  "regime": "bull",
  "passed": true
}
```

Valid `regime` values:

- `bull`
- `bear`
- `sideways`
- `crisis`
- `inflation_rates`
- `unknown`

## Regime Tags

The first version is deterministic and does not require an external macro feed.

If `SPY` is available in the `close` columns:

- `crisis`: SPY test-window max drawdown <= -25%.
- `bear`: SPY test return < 0 and SPY max drawdown <= -15%.
- `bull`: SPY test return > 10%.
- `sideways`: absolute SPY test return <= 10%.
- `inflation_rates`: optional only if a static year/tag map already exists in the codebase.

If `SPY` is not available:

- Use `regime = "unknown"`.
- Do not fabricate a macro regime.

## Tests

Add `tests/test_walk_forward.py`:

- Generate 7-8 years of synthetic daily data.
- Verify exact rolling `train=5y`, `test=1y`, `step=1y` window boundaries.
- Verify `window_count` equals the number of valid rolling train/test windows that can be formed from the available index.
- Verify no leakage: a strategy signal/weight for a test date must not depend on data after that date.
- Verify window metrics:
  - `test_cagr`
  - `test_max_drawdown`
  - `test_mar`
  - `test_trade_count`
  - `test_average_exposure`
- Verify aggregate metrics:
  - `pass_rate`
  - `median_test_cagr`
  - `median_test_mar`
  - `worst_test_cagr`
  - `worst_test_drawdown`

Extend `tests/test_robustness.py`:

- Robustness rows read true WF fields.
- Robustness output includes regime distribution/pass summary.
- A strategy without true WF is marked fail/missing.

Extend `tests/test_deployment_gate.py`:

- Gate requires `walk_forward.method == "true_rolling_oos"`.
- Old `rolling_train_then_test` or legacy `rolling_walk_forward` output is insufficient for pass.
- Gate thresholds:
  - `window_count >= min_walk_forward_windows`
  - `pass_rate >= 0.67`
  - `median_test_cagr > 0`
  - `worst_test_drawdown >= -0.20`

Extend weekly report tests:

- Weekly report contains true WF summary.
- Weekly report contains regime breakdown.
- Portfolio and paper sections remain unchanged.

## Integration

In `research_lab/runner.py`:

- Keep `weighted_backtest` for full-history metrics, split metrics, return series, equity curve, cost stress, and the existing result contract.
- Replace the persisted `walk_forward` field with true WF output before tiering, robustness, and deployment gate consume the result.
- Do not create two competing WF sources of truth.

In `research_lab/robustness.py`:

- Add or expose:
  - `walk_forward_method`
  - `pass_rate`
  - `median_test_mar`
  - `worst_window_cagr`
  - `worst_window_drawdown`
  - regime distribution/pass summary
- Require `method == "true_rolling_oos"` for a pass verdict.

In `research_lab/deployment_gate.py`:

- Add `min_walk_forward_windows` to gate config defaults.
- Avoid reusing `min_rebalance_observations` for WF gating because WF windows are not rebalance observations.
- Require true WF method and the configured WF thresholds before `walk_forward_verdict` can pass.

In `scripts/run_weekly_deep_research.py`:

- Keep portfolio, paper, broker, and Apify behavior unchanged.
- Include true WF summary and regime breakdown in the robustness findings section.

## Configuration

Add a conservative gate setting:

```python
min_walk_forward_windows: int = 3
```

Environment override:

```text
PAPER_GATE_MIN_WALK_FORWARD_WINDOWS
```

Existing `min_rebalance_observations` remains available for its current meaning and should not be repurposed for WF window count.

## Implementation Order

1. Write failing tests for `research_lab/walk_forward.py`.
2. Implement `research_lab/walk_forward.py`.
3. Integrate `runner.py`.
4. Update robustness reporting.
5. Update deployment gate.
6. Update weekly report.
7. Keep legacy `backtest.py` behavior compatible unless explicitly redirected later.

## Acceptance Criteria

- True WF rebuilds weights per window from sliced data only.
- Full-history weights are never sliced to simulate true WF.
- Same parameters are used across all windows.
- `result.json["walk_forward"]["method"] == "true_rolling_oos"` for new runs.
- Deployment gate rejects legacy WF output.
- Weekly robustness output reports true WF metrics and regime breakdown.
- No portfolio, paper, live, broker, Apify, or parameter optimization behavior changes in this PR.
