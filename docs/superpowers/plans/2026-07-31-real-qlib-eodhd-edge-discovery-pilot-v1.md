# Real Qlib EODHD Edge Discovery Pilot V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one bounded, offline, genuine-Qlib price/volume factor-screen pilot that consumes approved local EODHD snapshots, binds every material factor attempt to the existing global experiment ledger, and returns `EDGE_CANDIDATE_FOUND`, `NO_PRICE_VOLUME_EDGE`, or a fail-closed data/runtime status.

**Architecture:** Keep provider acquisition outside this milestone. A strict local-snapshot loader produces a point-in-time eligible development frame; a pinned real-Qlib runtime owns dataset segmentation; a fixed eight-factor catalog is evaluated with deterministic weekly cross-sectional metrics; the existing research-objective policy and global ledger remain authoritative. A CLI writes a new immutable artifact directory and never calls providers, brokers, registries, deployment, Knihomol, RD-Agent, or sealed OOS.

**Tech Stack:** Python 3.10-3.12, pandas, NumPy, `pyqlib==0.9.7` in an isolated virtual environment, pytest, existing `global_experiment_ledger_v1` and `research_objective_promotion_gate_v1` contracts.

---

## Scope and file map

This plan implements only `REAL_QLIB_EODHD_EDGE_DISCOVERY_PILOT_V1`. Sharadar, Massive cross-provider reconciliation, LightGBM, portfolio optimization, sealed-OOS consumption, shadow execution, and IBKR each require a later plan.

**Create:**

- `requirements/qlib-edge-discovery-v1.txt` — pinned direct dependency for the isolated research environment.
- `research_lab/research/real_qlib_runtime_v1.py` — genuine-Qlib availability metadata, `DatasetH` segment preparation, and exact source/prepared parity.
- `research_lab/research/eodhd_qlib_dataset_v1.py` — immutable local-manifest validation, adjusted OHLCV normalization, point-in-time universe filter, and development-only frame.
- `research_lab/research/price_volume_factor_catalog_v1.py` — exactly eight frozen factor definitions and one five-session forward label.
- `research_lab/research/qlib_factor_screen_v1.py` — weekly RankIC, quantile-spread, cost-stress, stability, concentration, and factor decision metrics.
- `research_lab/research/edge_discovery_scorecard_v1.py` — single economic scorecard and non-promotion result state.
- `research_lab/research/real_qlib_eodhd_edge_discovery_pilot_v1.py` — request validation, Qlib execution, ledger append, and artifact assembly.
- `scripts/run_real_qlib_eodhd_edge_discovery_pilot_v1.py` — dry-run/execute CLI with fixed exit codes and atomic output.
- `tests/test_real_qlib_runtime_v1.py`
- `tests/test_eodhd_qlib_dataset_v1.py`
- `tests/test_price_volume_factor_catalog_v1.py`
- `tests/test_qlib_factor_screen_v1.py`
- `tests/test_edge_discovery_scorecard_v1.py`
- `tests/test_real_qlib_eodhd_edge_discovery_pilot_v1.py`
- `tests/test_run_real_qlib_eodhd_edge_discovery_pilot_v1.py`
- `tests/integration/test_real_qlib_edge_discovery_integration_v1.py`

**Modify:**

- `research_lab/research/__init__.py` — export only the new top-level pilot entry point.

The existing `research_lab/execution/qlib_isolated_evaluator_v1.py` remains unchanged. It is a historical stub used by existing contracts; the new result must never use `COMPLETED_LOCAL_STUB`.

## Frozen request and result vocabulary

Use these constants consistently across all tasks:

```python
REQUEST_VERSION = "real_qlib_eodhd_edge_discovery_request_v1"
RESULT_VERSION = "real_qlib_eodhd_edge_discovery_result_v1"
DATASET_MANIFEST_VERSION = "eodhd_qlib_dataset_manifest_v1"
RUNTIME_VERSION = "real_qlib_runtime_v1"
FACTOR_CATALOG_VERSION = "price_volume_factor_catalog_v1"
FACTOR_SCREEN_VERSION = "qlib_factor_screen_v1"
SCORECARD_VERSION = "edge_discovery_scorecard_v1"

EDGE_CANDIDATE_FOUND = "EDGE_CANDIDATE_FOUND"
NO_PRICE_VOLUME_EDGE = "NO_PRICE_VOLUME_EDGE"
QLIB_RUNTIME_UNAVAILABLE = "QLIB_RUNTIME_UNAVAILABLE"
QLIB_PREPARATION_PARITY_FAILED = "QLIB_PREPARATION_PARITY_FAILED"
DATASET_VALIDATION_FAILED = "DATASET_VALIDATION_FAILED"
LEDGER_BINDING_FAILED = "LEDGER_BINDING_FAILED"
```

The top-level request schema is closed-world:

```json
{
  "version": "real_qlib_eodhd_edge_discovery_request_v1",
  "pilot_id": "QLIB-PV-001",
  "dataset_manifest_path": "C:/absolute/input/manifest.json",
  "expected_dataset_manifest_sha256": "64-lowercase-hex",
  "previous_ledger_path": "C:/absolute/input/ledger.json",
  "expected_previous_ledger_sha256": "64-lowercase-hex",
  "output_dir": "C:/absolute/new/output",
  "discovery_interval": {"start": "2006-01-01", "end": "2018-12-31"},
  "development_interval": {"start": "2019-01-01", "end": "2022-12-31"},
  "sealed_oos_interval": {"dataset_version": "SEALED-PV-V1", "start": "2023-01-01", "end": "2026-06-30"},
  "universe": {
    "minimum_price": 5.0,
    "minimum_history_sessions": 252,
    "minimum_median_dollar_volume": 10000000.0,
    "maximum_instruments": 1500
  },
  "costs": {"base_bps_one_way": 15.0, "stress_bps_one_way": 30.0, "severe_bps_one_way": 50.0},
  "provenance": {"source": "operator_approved_local_snapshot"}
}
```

`expected_dataset_manifest_sha256` is the raw SHA-256 of the manifest file bytes. `expected_previous_ledger_sha256` must equal both the parsed ledger's `canonical_ledger_sha256` field and the hash recomputed according to `global_experiment_ledger_v1`; it is not a raw file hash. The implementation must reject unknown fields, non-absolute paths, symlinks, output paths that already exist, mismatched hashes, intervals that overlap or are out of order, any request that exposes sealed-OOS rows to the screen, and any factor list supplied by the caller.

### Task 1: Add the isolated real-Qlib runtime boundary

**Files:**

- Create: `requirements/qlib-edge-discovery-v1.txt`
- Create: `research_lab/research/real_qlib_runtime_v1.py`
- Test: `tests/test_real_qlib_runtime_v1.py`

- [ ] **Step 1: Write the dependency file**

```text
pyqlib==0.9.7
```

- [ ] **Step 2: Write failing runtime tests**

```python
from __future__ import annotations

import pandas as pd
import pytest

from research_lab.research.real_qlib_runtime_v1 import (
    QlibRuntimeUnavailable,
    build_real_qlib_runtime_metadata_v1,
    prepare_real_qlib_segments_v1,
)


def _frame() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.bdate_range("2020-01-01", periods=8), ["AAA", "BBB"]],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame(
        {"MOM_6_1": range(len(index)), "forward_return_5d": 0.01},
        index=index,
    )


def test_unavailable_runtime_fails_closed(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    metadata = build_real_qlib_runtime_metadata_v1()
    assert metadata["status"] == "QLIB_RUNTIME_UNAVAILABLE"
    assert metadata["is_real_qlib"] is False
    with pytest.raises(QlibRuntimeUnavailable):
        prepare_real_qlib_segments_v1(
            _frame(),
            feature_columns=("MOM_6_1",),
            label_column="forward_return_5d",
            segments={"discovery": ("2020-01-01", "2020-01-06"), "development": ("2020-01-07", "2020-01-10")},
        )


def test_internally_loaded_runtime_must_identify_itself():
    class FakeRuntime:
        is_real_qlib = False

    with pytest.raises(ValueError, match="real Qlib"):
        prepare_real_qlib_segments_v1(
            _frame(),
            feature_columns=("MOM_6_1",),
            label_column="forward_return_5d",
            segments={"discovery": ("2020-01-01", "2020-01-06"), "development": ("2020-01-07", "2020-01-10")},
        )
```

The unit test may monkeypatch the private `_load_real_runtime` boundary. No public production function accepts a caller-supplied runtime or a caller-controlled Qlib authenticity claim.

Add pure parity tests that do not require Qlib:

- `test_preparation_parity_passes_only_for_exact_segment_indices_features_and_labels`: pass identical direct and prepared frames and assert `status == "PASS"`, exact row counts, and matching canonical frame hashes.
- `test_preparation_parity_rejects_missing_reordered_or_changed_rows`: separately remove a row, reorder the MultiIndex, and change one label by `1e-12`; assert fail-closed `QlibPreparationParityError`.

- [ ] **Step 3: Run the tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_real_qlib_runtime_v1.py -q
```

Expected: collection fails because `real_qlib_runtime_v1` does not exist.

- [ ] **Step 4: Implement the runtime boundary**

Implement these public objects exactly:

```python
class QlibRuntimeUnavailable(RuntimeError):
    """Raised when the pinned genuine-Qlib runtime cannot be used."""


def build_real_qlib_runtime_metadata_v1() -> dict[str, object]:
    """Return stable availability metadata without importing provider or broker code."""


def prepare_real_qlib_segments_v1(
    frame: pd.DataFrame,
    *,
    feature_columns: tuple[str, ...],
    label_column: str,
    segments: dict[str, tuple[str, str]],
) -> dict[str, pd.DataFrame]:
    """Prepare discovery/development through Qlib DataHandlerLP and DatasetH."""


def build_real_qlib_preparation_parity_v1(
    source_frame: pd.DataFrame,
    prepared_segments: dict[str, pd.DataFrame],
    *,
    feature_columns: tuple[str, ...],
    label_column: str,
    segments: dict[str, tuple[str, str]],
) -> dict[str, object]:
    """Require exact Qlib/source parity before economic metrics are trusted."""
```

The production runtime must import `qlib`, `DataHandlerLP`, and `DatasetH`; set `is_real_qlib = True`; convert columns to Qlib's `feature` and `label` column groups; build `DataHandlerLP.from_df(grouped_frame)`; build `DatasetH(handler=handler, segments=segments)`; and call `dataset.prepare(segment, col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)` for each segment. The returned frames must restore flat feature names plus the single label name, sort `(datetime, instrument)`, and contain no row beyond that segment's end. Qlib owns segment preparation, while the explicit factor equations remain independently inspectable. Before screening, parity must compare every expected segment index, ordered column, finite/non-finite mask, and value bit-for-bit with the direct pandas interval slice; there is no numerical tolerance. The pilot must then deterministically left-join the original boolean `eligible` series back onto each prepared segment by `(datetime, instrument)` and reject missing or duplicate joins.

Runtime metadata must include only `version`, `status`, `is_real_qlib`, `qlib_version`, `python_version`, and `runtime_sha256`. Parity metadata must include only version, `status=PASS`, segment row counts, source/prepared frame hashes, and its canonical SHA-256. Do not include paths or environment values. A missing package returns `QLIB_RUNTIME_UNAVAILABLE`; an import/runtime failure raises `QlibRuntimeUnavailable` with a bounded message that contains no filesystem path.

- [ ] **Step 5: Run tests and verify GREEN**

Run the Task 1 test command. Expected: all Task 1 tests pass in the main environment without requiring Qlib.

- [ ] **Step 6: Commit Task 1**

```powershell
git add requirements/qlib-edge-discovery-v1.txt research_lab/research/real_qlib_runtime_v1.py tests/test_real_qlib_runtime_v1.py
git commit -m "feat: add genuine qlib runtime boundary"
```

### Task 2: Validate immutable local EODHD universe snapshots

**Files:**

- Create: `research_lab/research/eodhd_qlib_dataset_v1.py`
- Test: `tests/test_eodhd_qlib_dataset_v1.py`

- [ ] **Step 1: Write failing dataset tests**

Create tests that write two temporary CSV files with exactly these columns:

```python
CSV_COLUMNS = (
    "timestamp", "open", "high", "low", "close", "adjusted_close", "volume"
)
```

The manifest rows must use:

```python
{
    "instrument_id": "US-XNAS-AAA",
    "symbol": "AAA.US",
    "qlib_instrument": "AAA",
    "instrument_type": "COMMON_STOCK",
    "exchange_mic": "XNAS",
    "listing_start": "2018-01-01",
    "listing_end": None,
    "ohlcv_path": "AAA.US.csv",
    "ohlcv_sha256": sha256(csv_bytes).hexdigest(),
}
```

Required tests and exact assertions:

- `test_loads_only_discovery_and_development_rows_and_builds_adjusted_prices`: write one row with raw close 50 and adjusted close 100; assert returned open/high/low are multiplied by 2, returned close is 100, raw close remains 50, and the maximum timestamp is the development end.
- `test_point_in_time_liquidity_and_history_filter_never_uses_future_rows`: create 253 low-liquidity rows followed by 63 high-liquidity rows; assert no earlier eligibility value changes when the later rows are added.
- `test_delisted_instrument_is_eligible_before_listing_end_and_absent_after`: put `listing_end` on the middle timestamp; assert eligible is true immediately before and false immediately after that date.
- `test_rejects_hash_mismatch_path_escape_symlink_duplicate_symbol_and_unknown_field`: parameterize all five corruptions and assert `ValueError` identifies the violated field without printing a path.
- `test_rejects_missing_adjusted_close_invalid_ohlc_duplicate_or_unordered_timestamp`: parameterize all five row defects and assert fail-closed rejection.
- `test_rejects_manifest_or_csv_row_that_reaches_sealed_oos`: add one row on `sealed_oos_interval.start` and assert `ValueError("sealed OOS row exposed")`.

The success test must assert:

```python
frame, metadata = load_eodhd_qlib_development_frame_v1(request)
assert frame.index.names == ["datetime", "instrument"]
assert frame.index.get_level_values("datetime").max() <= pd.Timestamp("2022-12-31")
assert set(frame.columns) == {
    "open", "high", "low", "close", "volume", "raw_close", "dollar_volume", "eligible"
}
assert metadata["provider_calls_used"] == 0
assert metadata["sealed_oos_rows_read"] == 0
assert metadata["dataset_manifest_sha256"] == expected_manifest_sha256
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_eodhd_qlib_dataset_v1.py -q
```

Expected: collection fails because `eodhd_qlib_dataset_v1` does not exist.

- [ ] **Step 3: Implement closed-world manifest validation**

Expose:

```python
def load_eodhd_qlib_development_frame_v1(
    request: dict[str, object],
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Load hash-bound local files and return development-visible rows only."""
```

Implement the following exact algorithm:

1. Accept only `version`, `manifest_path`, `expected_manifest_sha256`, `discovery_interval`, `development_interval`, `sealed_oos_interval`, `universe`, and `provenance`.
2. Require the manifest path to be absolute, regular, non-symlinked, and hash-equal to `expected_manifest_sha256`.
3. Accept only manifest fields `version`, `dataset_id`, `created_utc`, `instruments`, and `provenance`; reject duplicate `instrument_id`, `symbol`, and `qlib_instrument`.
4. Resolve every relative `ohlcv_path` against the manifest directory and require the final path to remain inside that directory and not be a symlink.
5. Verify every CSV SHA-256 before parsing.
6. Reject any timestamp at or after `sealed_oos_interval.start`; the loader is intentionally development-only.
7. Validate finite positive OHLC, `high >= max(open, close)`, `low <= min(open, close)`, `high >= low`, positive `adjusted_close`, and non-negative finite volume.
8. Compute `adjustment_ratio = adjusted_close / raw_close`; set adjusted open/high/low to raw value times the same-day ratio; set adjusted close to `adjusted_close`; retain `raw_close`; compute `dollar_volume = raw_close * volume`.
9. For each instrument and timestamp, compute prior-session count and trailing 63-session median dollar volume using only rows through that timestamp.
10. Set `eligible` when raw close, prior history, liquidity, listing interval, common-stock type, and MIC requirements pass. On each timestamp, retain at most `maximum_instruments` by descending trailing median dollar volume with `instrument` as the deterministic tie-breaker.
11. Return all development-visible rows, including ineligible warm-up rows, because factor calculation needs history. The downstream screen must select `eligible` at each signal timestamp.

The metadata must include dataset ID, manifest hash, instrument counts, active/delisted counts, min/max timestamps, row count, eligible row count, input file hashes, `provider_calls_used=0`, `sealed_oos_rows_read=0`, and a canonical metadata SHA-256.

- [ ] **Step 4: Run Task 2 tests and verify GREEN**

Run the Task 2 test command. Expected: all dataset tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add research_lab/research/eodhd_qlib_dataset_v1.py tests/test_eodhd_qlib_dataset_v1.py
git commit -m "feat: validate local eodhd qlib dataset"
```

### Task 3: Freeze the eight-factor price/volume catalog

**Files:**

- Create: `research_lab/research/price_volume_factor_catalog_v1.py`
- Test: `tests/test_price_volume_factor_catalog_v1.py`

- [ ] **Step 1: Write failing catalog tests**

```python
from research_lab.research.price_volume_factor_catalog_v1 import (
    FACTOR_DEFINITIONS_V1,
    compute_price_volume_factor_frame_v1,
)


def test_catalog_is_closed_and_has_exactly_eight_factors():
    assert tuple(FACTOR_DEFINITIONS_V1) == (
        "MOM_12_1",
        "MOM_6_1",
        "TREND_200",
        "HIGH_252",
        "LOW_VOL_60",
        "DRAWDOWN_252",
        "VOLUME_CONFIRM_20",
        "SHORT_REVERSAL_5",
    )


def test_forward_label_uses_next_open_and_fifth_future_close():
    result = compute_price_volume_factor_frame_v1(frame_with_known_future_path())
    assert result.loc[(pd.Timestamp("2020-12-01"), "AAA"), "forward_return_5d"] == pytest.approx(0.10)
```

Add `test_factor_values_use_only_current_and_prior_rows`: copy the input, mutate every price after a chosen timestamp, recompute, and assert all eight factors at and before the chosen timestamp are unchanged. Add `test_future_price_mutation_changes_label_but_not_current_factor_values`: mutate only the fifth future close and assert only the label and future-dependent rows change. Add `test_ineligible_rows_are_retained_for_history_but_excluded_as_signal_rows`: assert the returned frame contains both eligibility states and preserves the complete source index.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_price_volume_factor_catalog_v1.py -q
```

Expected: collection fails because `price_volume_factor_catalog_v1` does not exist.

- [ ] **Step 3: Implement the catalog and label**

Expose an insertion-ordered mapping whose values contain `factor_id`, `family_id`, `description`, and `higher_is_better`. Implement per instrument, sorted by timestamp:

```python
MOM_12_1 = close.shift(21) / close.shift(252) - 1.0
MOM_6_1 = close.shift(21) / close.shift(126) - 1.0
TREND_200 = close / close.rolling(200, min_periods=200).mean() - 1.0
HIGH_252 = close / high.rolling(252, min_periods=252).max() - 1.0
LOW_VOL_60 = -(close.pct_change().rolling(60, min_periods=60).std() * numpy.sqrt(252.0))
DRAWDOWN_252 = close / close.rolling(252, min_periods=252).max() - 1.0
VOLUME_CONFIRM_20 = dollar_volume / dollar_volume.rolling(20, min_periods=20).mean() - 1.0
SHORT_REVERSAL_5 = -(close / close.shift(5) - 1.0)
forward_return_5d = close.shift(-5) / open.shift(-1) - 1.0
```

Expose:

```python
def compute_price_volume_factor_frame_v1(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the eight features, forward label, and eligible flag on the same MultiIndex."""
```

Drop neither warm-up rows nor trailing unlabeled rows inside this function. Reject non-finite source values, duplicate index rows, unsorted per-instrument timestamps, and any unknown source column. Add `build_price_volume_factor_catalog_metadata_v1() -> dict[str, object]`, returning the version, ordered definitions, factor count eight, and canonical catalog SHA-256 derived from the exact mapping.

- [ ] **Step 4: Run Task 3 tests and verify GREEN**

Run the Task 3 test command. Expected: all catalog tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add research_lab/research/price_volume_factor_catalog_v1.py tests/test_price_volume_factor_catalog_v1.py
git commit -m "feat: freeze price volume factor catalog v1"
```

### Task 4: Implement the deterministic weekly factor screen

**Files:**

- Create: `research_lab/research/qlib_factor_screen_v1.py`
- Test: `tests/test_qlib_factor_screen_v1.py`

- [ ] **Step 1: Write failing factor-screen tests**

Build deterministic synthetic weekly cross-sections with one positively predictive factor, one inverse/noise factor, two calendar years, and at least 60 weekly observations. Required assertions:

```python
result = run_qlib_factor_screen_v1(
    development_frame,
    factor_metadata=catalog_metadata,
    costs={"base_bps_one_way": 15.0, "stress_bps_one_way": 30.0, "severe_bps_one_way": 50.0},
)
assert result["version"] == "qlib_factor_screen_v1"
assert result["weekly_observation_count"] >= 60
assert result["factors"]["PREDICTIVE"]["decision"] == "FACTOR_CONTINUE"
assert result["factors"]["NOISE"]["decision"] == "FACTOR_STOP"
assert result["factors"]["PREDICTIVE"]["stress_net_top_minus_universe_return"] > 0.0
assert result["factors"]["PREDICTIVE"]["single_year_profit_share"] <= 0.40
```

Also implement these exact tests:

- `test_screen_uses_last_eligible_session_of_each_week_and_never_sealed_rows`: provide Monday and Friday rows, make only Friday eligible, and assert one Friday observation is selected and the result maximum date precedes sealed OOS.
- `test_rank_ic_and_quantile_spread_are_invariant_to_instrument_input_order`: shuffle instrument rows with a fixed seed and assert complete result equality.
- `test_factor_stops_when_stress_cost_destroys_edge`: construct gross spread between base and stress round-trip costs and assert `EDGE_DESTROYED_BY_STRESS_COSTS`.
- `test_factor_stops_when_one_year_or_one_instrument_dominates`: parameterize year and instrument concentration above the canonical limits and assert the matching failure taxonomy.
- `test_screen_rejects_fewer_than_52_weekly_observations`: provide 51 weeks and assert `ValueError("insufficient weekly observations")`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_qlib_factor_screen_v1.py -q
```

Expected: collection fails because `qlib_factor_screen_v1` does not exist.

- [ ] **Step 3: Implement screen metrics and gates**

Expose:

```python
def run_qlib_factor_screen_v1(
    development_frame: pd.DataFrame,
    *,
    factor_metadata: dict[str, object],
    costs: dict[str, float],
) -> dict[str, object]:
    """Evaluate every catalog factor on weekly eligible cross-sections."""
```

For each calendar week, use the last timestamp present in the frame. On that timestamp, require `eligible == True`, non-null factor, and non-null label. Require at least 30 instruments per weekly cross-section in production; tests may pass `minimum_cross_section_size=3` through a private helper, never through the public request.

For each factor calculate:

- Spearman cross-sectional RankIC per week;
- median RankIC;
- ICIR as mean RankIC divided by RankIC standard deviation;
- positive-RankIC week share;
- top-quintile minus bottom-quintile forward spread;
- top-quintile minus eligible-universe forward return;
- base, stress, and severe net top-minus-universe returns after two one-way costs;
- top-minus-bottom net spreads as diagnostics only, never as the continuation gate;
- compounded annualized net top-minus-universe return;
- per-year PnL and single-year positive-profit share;
- per-instrument contribution and single-instrument positive-profit share;
- weekly observation and instrument counts.

Use deterministic stable sorting by `(factor_value, instrument)`. A factor returns `FACTOR_CONTINUE` only when all are true:

```python
weekly_observation_count >= 52
positive_rank_ic_week_share >= 0.55
stress_net_top_minus_universe_return > 0.0
single_year_profit_share <= 0.40
single_instrument_profit_share <= 0.20
(
    median_rank_ic >= 0.015
    or annualized_net_top_minus_universe_return >= 0.02
)
```

Otherwise return `FACTOR_STOP` with a sorted list drawn from `INSUFFICIENT_WEEKLY_OBSERVATIONS`, `UNSTABLE_RANK_IC`, `WEAK_RANK_IC_AND_ECONOMIC_SPREAD`, `EDGE_DESTROYED_BY_STRESS_COSTS`, `ISOLATED_PERIOD_DOMINANCE`, and `SINGLE_INSTRUMENT_DOMINANCE`.

- [ ] **Step 4: Run Task 4 tests and verify GREEN**

Run the Task 4 test command. Expected: all factor-screen tests pass.

- [ ] **Step 5: Commit Task 4**

```powershell
git add research_lab/research/qlib_factor_screen_v1.py tests/test_qlib_factor_screen_v1.py
git commit -m "feat: add bounded qlib factor screen"
```

### Task 5: Build the single non-promotion economic scorecard

**Files:**

- Create: `research_lab/research/edge_discovery_scorecard_v1.py`
- Test: `tests/test_edge_discovery_scorecard_v1.py`

- [ ] **Step 1: Write failing scorecard tests**

```python
def test_edge_candidate_requires_at_least_one_factor_continue():
    result = build_edge_discovery_scorecard_v1(
        screen_with_one_continuing_factor(),
        qlib_runtime_metadata=real_runtime_metadata(),
        preparation_parity=passing_preparation_parity(),
        ledger_summary=ledger_summary_with_new_trials(8),
    )
    assert result["status"] == "EDGE_CANDIDATE_FOUND"
    assert result["promotion_authorized"] is False
    assert result["sealed_oos_opened"] is False


def test_no_edge_when_every_factor_stops():
    result = build_edge_discovery_scorecard_v1(
        screen_with_all_stopped(),
        qlib_runtime_metadata=real_runtime_metadata(),
        preparation_parity=passing_preparation_parity(),
        ledger_summary=ledger_summary_with_new_trials(8),
    )
    assert result["status"] == "NO_PRICE_VOLUME_EDGE"
    assert result["next_authorized_milestone"] == "SHARADAR_FUNDAMENTAL_EDGE_DISCOVERY_V1"
```

Add `test_scorecard_rejects_stub_runtime_failed_parity_or_unaccounted_trials`, setting `is_real_qlib=False`, preparation parity status `FAIL`, new trial count seven, new trial count nine, new hypothesis count seven, and new hypothesis count nine in separate requests and asserting fail-closed `ValueError`. Add `test_scorecard_is_deterministic_under_factor_mapping_reordering`, reversing the factor mapping and asserting byte-equivalent canonical JSON and identical SHA-256.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_edge_discovery_scorecard_v1.py -q
```

Expected: collection fails because `edge_discovery_scorecard_v1` does not exist.

- [ ] **Step 3: Implement scorecard states**

Expose:

```python
def build_edge_discovery_scorecard_v1(
    factor_screen: dict[str, object],
    *,
    qlib_runtime_metadata: dict[str, object],
    preparation_parity: dict[str, object],
    ledger_summary: dict[str, object],
) -> dict[str, object]:
    """Build the only authoritative economic output of the pilot."""
```

Require `is_real_qlib is True`, preparation parity `status == "PASS"`, exactly eight catalog factors, `new_trial_count == 8`, `new_hypothesis_count == 8`, eight distinct new experiment IDs, eight distinct new hypothesis IDs, zero sealed-OOS consumption, and no promotion/runtime action. Existing unrelated ledger trials do not count toward these eight. Return `EDGE_CANDIDATE_FOUND` when at least one factor continues; otherwise return `NO_PRICE_VOLUME_EDGE`. Include the complete factor metrics, continuing/stopped factor IDs, costs, prior and updated trial counts, new trial and hypothesis IDs, Qlib version, parity hash, dataset/catalog hashes, `promotion_authorized=False`, `production_runtime_supported=False`, `sealed_oos_opened=False`, and one canonical scorecard SHA-256.

`EDGE_CANDIDATE_FOUND` authorizes only a separately designed portfolio-construction milestone. `NO_PRICE_VOLUME_EDGE` authorizes only the separately designed Sharadar fundamental discovery milestone. Neither state authorizes sealed OOS, registry promotion, shadow, paper, or live execution.

- [ ] **Step 4: Run Task 5 tests and verify GREEN**

Run the Task 5 test command. Expected: all scorecard tests pass.

- [ ] **Step 5: Commit Task 5**

```powershell
git add research_lab/research/edge_discovery_scorecard_v1.py tests/test_edge_discovery_scorecard_v1.py
git commit -m "feat: add price volume edge scorecard"
```

### Task 6: Bind factor attempts to the global experiment ledger

**Files:**

- Create: `research_lab/research/real_qlib_eodhd_edge_discovery_pilot_v1.py`
- Test: `tests/test_real_qlib_eodhd_edge_discovery_pilot_v1.py`

- [ ] **Step 1: Write failing orchestration and ledger tests**

Use the existing ledger builders to create a canonical prior ledger with `max_global_trials=40`, at least eight remaining global and `PRICE_VOLUME_FACTOR` family trial slots, at least eight remaining hypothesis allocations under `max_total_hypotheses`, and no sealed-OOS consumption. Inject a fake runtime with `is_real_qlib=True` only into unit tests.

Required tests:

- `test_pilot_registers_eight_hypotheses_appends_eight_factor_trials_and_preserves_previous_ledger`: deep-copy the prior ledger, run the pilot, assert eight new hypothesis IDs and eight new experiment IDs in catalog order, and assert the input ledger equals its deep copy.
- `test_each_trial_binds_dataset_catalog_intervals_costs_and_factor_id`: iterate the eight appended records and assert exact dataset hash, factor ID, discovery/development/sealed intervals, and 15/30/50 bps identities.
- `test_rejects_wrong_previous_ledger_hash_or_insufficient_remaining_budget`: parameterize a wrong canonical hash, only seven remaining global trial slots, only seven remaining family trial slots, and only seven remaining hypothesis allocations; assert fail-closed rejection before Qlib preparation.
- `test_rejects_any_previous_sealed_oos_consumption_for_this_lineage`: add a matching-lineage consumption record and assert `SEALED_OOS_CONTAMINATION`.
- `test_unavailable_real_qlib_returns_fail_closed_without_artifact_writes`: inject unavailable runtime metadata, assert `QLIB_RUNTIME_UNAVAILABLE`, and assert no output path exists.
- `test_preparation_parity_failure_prevents_ledger_operations`: inject one altered Qlib-prepared value, assert `QLIB_PREPARATION_PARITY_FAILED`, zero new hypotheses and trials, and an unchanged previous ledger.
- `test_no_provider_broker_registry_deployment_knihomol_or_rd_agent_actions`: assert every corresponding counter or boolean in the returned safety mapping is zero or false.

Success assertions must include:

```python
assert result["provider_calls_used"] == 0
assert result["broker_calls_used"] == 0
assert result["registry_write_performed"] is False
assert result["deployment_performed"] is False
assert result["knihomol_used"] is False
assert result["rd_agent_used"] is False
assert result["sealed_oos_opened"] is False
assert result["new_hypothesis_count"] == 8
assert result["new_trial_count"] == 8
assert previous_ledger == original_previous_ledger
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_real_qlib_eodhd_edge_discovery_pilot_v1.py -q
```

Expected: collection fails because the pilot module does not exist.

- [ ] **Step 3: Implement closed-world request validation and ledger append**

Expose:

```python
def run_real_qlib_eodhd_edge_discovery_pilot_v1(
    request: dict[str, object],
) -> dict[str, object]:
    """Run one development-only real-Qlib price/volume pilot without writing files."""
```

The function must:

1. validate the frozen top-level request schema and exact default universe/cost values;
2. read and canonical-hash-verify the previous ledger without mutating it;
3. reject fewer than eight remaining global trial slots, `PRICE_VOLUME_FACTOR` family slots, or total hypothesis allocations;
4. load the development-only EODHD frame;
5. compute the frozen factor catalog and label;
6. pass only discovery/development features and labels through real Qlib, prove exact parity against direct pandas interval slices, then rejoin the original `eligible` series by exact index;
7. run the factor screen only after parity passes, using Qlib's prepared development frame with the rejoined eligibility flag;
8. for each factor in catalog order, register exactly one new hypothesis and append exactly one trial through separate `apply_global_experiment_ledger_operation_v1` calls;
9. build the scorecard using the appended ledger summary;
10. return an artifact mapping without writing it.

Each appended trial must use the existing ledger schema and bind:

- strategy family `PRICE_VOLUME_FACTOR`;
- one factor-specific hypothesis ID and experiment ID;
- the canonical dataset manifest SHA-256;
- discovery/development intervals;
- the still-unconsumed sealed-OOS interval;
- cost/slippage identity;
- the factor catalog and parameter-configuration hashes;
- screen metrics and deterministic `FACTOR_CONTINUE`/`FACTOR_STOP` status;
- failure taxonomy from the screen;
- provenance source `real_qlib_eodhd_edge_discovery_pilot_v1`.

Use deterministic statuses already accepted by the ledger contract: a `FACTOR_CONTINUE` trial is appended as `WALK_FORWARD_COMPLETE`, meaning only that development screening completed and continuation may be designed; a `FACTOR_STOP` trial is appended as `STRATEGY_GATE_FAIL`. Never use `STRATEGY_GATE_PASS`, `PARAMETERS_FROZEN`, or any portfolio/promotion status in this milestone. Each factor gets a distinct semantic fingerprint and a distinct hypothesis registration before its trial append. Duplicate or near-duplicate ledger classifications remain authoritative, consume the configured attempt budget, and force the corresponding scorecard factor to stop. If ledger accounting fails after the screen has observed factors, the pilot returns `LEDGER_BINDING_FAILED` with the full screen, the last valid ledger, attempted/accounted factor IDs, and `accounting_complete=false`; the CLI atomically persists that review bundle before returning exit four.

Do not call `evaluate_research_objective_promotion_gate_v1`; this first screen is not a portfolio and cannot pass a promotion scope.

- [ ] **Step 4: Run Task 6 tests and verify GREEN**

Run the Task 6 test command. Expected: all pilot unit tests pass.

- [ ] **Step 5: Commit Task 6**

```powershell
git add research_lab/research/real_qlib_eodhd_edge_discovery_pilot_v1.py tests/test_real_qlib_eodhd_edge_discovery_pilot_v1.py
git commit -m "feat: bind qlib factor pilot to trial ledger"
```

### Task 7: Add immutable artifact writing and CLI

**Files:**

- Create: `scripts/run_real_qlib_eodhd_edge_discovery_pilot_v1.py`
- Create: `tests/test_run_real_qlib_eodhd_edge_discovery_pilot_v1.py`
- Modify: `research_lab/research/__init__.py`

- [ ] **Step 1: Write failing CLI tests**

Required tests:

- `test_cli_defaults_to_dry_run_and_writes_nothing`: invoke without `--execute`, assert exit zero, fixed dry-run fields, and absent output directory.
- `test_execute_requires_absolute_nonexistent_output_and_exact_request_hash`: parameterize relative path, existing path, symlink, and wrong request hash and assert exit four without a final bundle.
- `test_execute_writes_complete_hash_verified_bundle_atomically`: inject a successful pure pilot result, assert the exact file set below, recompute every checksum, and assert `COMPLETE` was written last according to an injected ordered-writer spy.
- `test_no_edge_returns_exit_2_and_edge_found_returns_exit_0`: inject each scorecard status and assert the fixed exit code.
- `test_runtime_unavailable_returns_exit_3_without_complete_marker`: inject runtime unavailability and assert no final path or marker.
- `test_validation_or_ledger_failure_returns_exit_4_with_redacted_reason`: inject a secret-bearing exception, assert only `reason=VALIDATION_OR_LEDGER_FAILURE`, and assert the secret and path are absent.
- `test_accounting_failure_writes_a_complete_review_bundle`: inject `LEDGER_BINDING_FAILED` after observed attempts, assert exit four and an atomic `COMPLETE` bundle containing the last valid ledger and full screen.
- `test_cli_source_has_no_provider_broker_registry_deployment_or_agent_imports`: parse the AST and reject imports rooted at provider, broker, registry, deployment, Knihomol, RD-Agent, and the historical stub module.

The completed bundle must contain exactly:

```text
COMPLETE
request.json
dataset_metadata.json
qlib_runtime.json
factor_screen.json
reference_parity.json
economic_scorecard.json
updated_ledger.json
checksums.json
result.json
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_run_real_qlib_eodhd_edge_discovery_pilot_v1.py -q
```

Expected: collection fails because the CLI does not exist.

- [ ] **Step 3: Implement CLI and artifact writer**

CLI arguments are exactly:

```text
--request ABSOLUTE_JSON_PATH
--expected-request-sha256 64_HEX
--execute
```

Without `--execute`, validate only the request file hash and print `status=DRY_RUN`, the pilot ID, planned factor count `8`, planned provider calls `0`, and planned sealed-OOS reads `0`. Do not create the output directory.

With `--execute`, require the requested output path to be absolute, nonexistent, not a symlink, and outside the repository root. Write to a new sibling temporary directory, verify every JSON file by rereading and rehashing, write `checksums.json`, write `COMPLETE` last, and atomically rename the temporary directory to the requested output path. Validation, runtime, and I/O exceptions leave no final output path. A structured `LEDGER_BINDING_FAILED` result with `accounting_complete=false` is the sole exception: it is an intentional fail-closed review artifact preserving already observed attempts.

Use these exit codes:

```python
EXIT_EDGE_FOUND = 0
EXIT_NO_EDGE = 2
EXIT_QLIB_UNAVAILABLE = 3
EXIT_VALIDATION_OR_LEDGER_FAILURE = 4
EXIT_IO_FAILURE = 5
```

Errors printed to stdout contain only a fixed taxonomy value, never exception text, paths, request content, or credentials. Export `run_real_qlib_eodhd_edge_discovery_pilot_v1` from `research_lab/research/__init__.py`.

- [ ] **Step 4: Run Task 7 tests and verify GREEN**

Run the Task 7 test command. Expected: all CLI tests pass.

- [ ] **Step 5: Commit Task 7**

```powershell
git add scripts/run_real_qlib_eodhd_edge_discovery_pilot_v1.py tests/test_run_real_qlib_eodhd_edge_discovery_pilot_v1.py research_lab/research/__init__.py
git commit -m "feat: add immutable qlib edge pilot cli"
```

### Task 8: Prove genuine Qlib execution in the isolated environment

**Files:**

- Create: `tests/integration/test_real_qlib_edge_discovery_integration_v1.py`

- [ ] **Step 1: Write the integration test**

The integration test must use `pytest.importorskip("qlib")`, create a deterministic MultiIndex frame with at least 300 business days and 40 instruments, compute the frozen factor frame, prepare discovery/development via the production real-Qlib runtime, and assert:

```python
metadata = build_real_qlib_runtime_metadata_v1()
assert metadata["status"] == "AVAILABLE"
assert metadata["is_real_qlib"] is True
assert metadata["qlib_version"] == "0.9.7"
segments = prepare_real_qlib_segments_v1(
    factor_frame,
    feature_columns=tuple(FACTOR_DEFINITIONS_V1),
    label_column="forward_return_5d",
    segments={
        "discovery": ("2020-01-01", "2020-12-31"),
        "development": ("2021-01-01", "2022-12-31"),
    },
)
assert set(segments) == {"discovery", "development"}
assert not segments["discovery"].empty
assert not segments["development"].empty
assert segments["development"].index.get_level_values("datetime").max() <= pd.Timestamp("2022-12-31")
parity = build_real_qlib_preparation_parity_v1(
    factor_frame,
    segments,
    feature_columns=tuple(FACTOR_DEFINITIONS_V1),
    label_column="forward_return_5d",
    segments={
        "discovery": ("2020-01-01", "2020-12-31"),
        "development": ("2021-01-01", "2022-12-31"),
    },
)
assert parity["status"] == "PASS"
```

The test must also independently compute one weekly `MOM_12_1` top-quintile-minus-eligible-universe five-session gross-return stream directly from the source pandas frame and assert exact equality to the same stream built from the Qlib-prepared development segment. This reference calculation must not call `run_qlib_factor_screen_v1`. Assert that neither result nor captured output contains `COMPLETED_LOCAL_STUB`.

- [ ] **Step 2: Run the integration test in the main environment**

Run:

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/integration/test_real_qlib_edge_discovery_integration_v1.py -q
```

Expected: one skip because Qlib is intentionally absent from the main environment.

- [ ] **Step 3: Create the isolated environment outside the repository**

Run from the worktree:

```powershell
uv venv C:\Users\lojka\.venvs\research-lab-qlib-edge-v1 --python 3.12
uv pip install --python C:\Users\lojka\.venvs\research-lab-qlib-edge-v1\Scripts\python.exe -e . "pytest==9.0.3" -r requirements/qlib-edge-discovery-v1.txt
```

Expected: installation resolves `pyqlib==0.9.7` under Python 3.12, installs the worktree package and pytest into the isolated environment, and does not change the repository's main `.venv`. This command was dependency-resolved with `uv --dry-run` during plan review; actual installation remains an implementation step.

- [ ] **Step 4: Run the real-Qlib integration test**

Run:

```powershell
C:\Users\lojka\.venvs\research-lab-qlib-edge-v1\Scripts\python.exe -m pytest tests/integration/test_real_qlib_edge_discovery_integration_v1.py -q
```

Expected: PASS and runtime metadata identifies Qlib 0.9.7.

- [ ] **Step 5: Commit Task 8**

```powershell
git add tests/integration/test_real_qlib_edge_discovery_integration_v1.py
git commit -m "test: prove genuine qlib factor runtime"
```

### Task 9: Focused, full-suite, and safety verification

**Files:**

- Modify only if verification exposes a defect in files created by Tasks 1-8.

- [ ] **Step 1: Run the complete focused suite in the main environment**

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest tests/test_real_qlib_runtime_v1.py tests/test_eodhd_qlib_dataset_v1.py tests/test_price_volume_factor_catalog_v1.py tests/test_qlib_factor_screen_v1.py tests/test_edge_discovery_scorecard_v1.py tests/test_real_qlib_eodhd_edge_discovery_pilot_v1.py tests/test_run_real_qlib_eodhd_edge_discovery_pilot_v1.py tests/integration/test_real_qlib_edge_discovery_integration_v1.py -q
```

Expected: all unit tests pass and only the genuine-Qlib integration test skips.

- [ ] **Step 2: Run the genuine-Qlib integration test in the isolated environment**

Run the Task 8 isolated-environment command. Expected: PASS with no skip.

- [ ] **Step 3: Run the full repository suite**

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass; the existing known resource warnings may remain; the Qlib integration test skips in the main environment.

- [ ] **Step 4: Run static and diff checks**

```powershell
C:\Users\lojka\trading\research-lab\.venv\Scripts\python.exe -m py_compile research_lab/research/real_qlib_runtime_v1.py research_lab/research/eodhd_qlib_dataset_v1.py research_lab/research/price_volume_factor_catalog_v1.py research_lab/research/qlib_factor_screen_v1.py research_lab/research/edge_discovery_scorecard_v1.py research_lab/research/real_qlib_eodhd_edge_discovery_pilot_v1.py scripts/run_real_qlib_eodhd_edge_discovery_pilot_v1.py
git diff --check
git status --short
```

Expected: compilation and diff check pass; status contains only intentional milestone changes.

- [ ] **Step 5: Run secret/action surface audit**

```powershell
rg -n "api_key|api_token|EODHD_API_KEY|SHARADAR_API_KEY|MASSIVE_API_KEY|ib_insync|broker|registry|deploy|rdagent|knihomol|COMPLETED_LOCAL_STUB" research_lab/research/real_qlib_runtime_v1.py research_lab/research/eodhd_qlib_dataset_v1.py research_lab/research/price_volume_factor_catalog_v1.py research_lab/research/qlib_factor_screen_v1.py research_lab/research/edge_discovery_scorecard_v1.py research_lab/research/real_qlib_eodhd_edge_discovery_pilot_v1.py scripts/run_real_qlib_eodhd_edge_discovery_pilot_v1.py
```

Expected: no provider credential or forbidden action import; `COMPLETED_LOCAL_STUB` appears only in a negative assertion if present at all.

- [ ] **Step 6: Commit verification-only repairs if required**

If Tasks 9.1-9.5 required a repair, stage only the repaired milestone files and commit:

```powershell
git commit -m "fix: close qlib edge pilot verification gaps"
```

If no repair was required, do not create an empty commit.

## Controlled execution boundary after implementation

Implementation completion does not authorize a provider call or real-data experiment. Before the first economic run, require a separate reviewed request containing:

- one immutable EODHD universe manifest and all referenced local file hashes;
- exact discovery/development/sealed dates;
- the canonical prior global-ledger hash with at least eight remaining global trial slots, eight `PRICE_VOLUME_FACTOR` family slots, and eight hypothesis allocations;
- an absolute new output path outside the repository;
- the exact request SHA-256;
- confirmation that no sealed-OOS row exists in any file made visible to the runner.

The first approved run may return only `EDGE_CANDIDATE_FOUND`, `NO_PRICE_VOLUME_EDGE`, or a fail-closed status. It performs no Sharadar, Massive, EODHD, broker, registry, deployment, Knihomol, RD-Agent, shadow, paper, or live action.
