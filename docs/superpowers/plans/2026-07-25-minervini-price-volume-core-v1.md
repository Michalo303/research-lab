# Minervini Price/Volume Core V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one frozen Minervini-style US-stock price/volume system, prove its mechanics with synthetic data, and run it once on an immutable local point-in-time dataset if the existing EODHD access can supply that dataset safely.

**Architecture:** Keep provider diagnostics, signal construction, portfolio simulation, and CLI orchestration separate. Every calculation is pure and deterministic after local inputs are loaded. Provider access is an explicit bounded diagnostic only; a wide historical acquisition is a later gated action because its call count cannot be known until capability and universe size are measured.

**Tech Stack:** Python 3.12, pandas, NumPy, pytest, existing local OHLCV and point-in-time universe contracts, SHA-256 canonical JSON.

---

### Task 1: Bounded EODHD capability diagnostic

**Files:**
- Create: `research_lab/research/minervini_eodhd_capability_v1.py`
- Create: `scripts/check_minervini_eodhd_capability_v1.py`
- Create: `tests/test_minervini_eodhd_capability_v1.py`

- [ ] **Step 1: Write failing tests for the four allowed probes**

Test an injected HTTP getter with exactly these sanitized endpoint identities:

```python
EXPECTED_PATHS = [
    "/api/exchange-symbol-list/US?type=common_stock",
    "/api/exchange-symbol-list/US?delisted=1&type=common_stock",
    "/api/eod/AAPL.US",
    "/api/splits/AAPL.US",
]

def test_capability_accepts_active_delisted_eod_and_splits_without_leaking_key():
    seen = []

    def getter(url):
        seen.append(url)
        if "exchange-symbol-list" in url:
            return [{"Code": "AAPL", "Type": "Common Stock"}], {"http_status": 200}
        if "/splits/" in url:
            return [{"date": "2020-08-31", "split": "4.000000/1.000000"}], {"http_status": 200}
        return [{"date": "2025-01-02", "open": 100, "high": 101, "low": 99,
                 "close": 100, "adjusted_close": 100, "volume": 1_000_000}], {"http_status": 200}

    result = run_minervini_eodhd_capability_v1(
        api_key="secret-value", http_get=getter
    )

    assert result["status"] == "CAPABLE"
    assert result["provider_calls_used"] == 4
    assert result["delisted_symbols_available"] is True
    assert "secret-value" not in json.dumps(result)
    assert len(seen) == 4
```

Also test missing key, non-200 status, wrong payload type, absent
`adjusted_close`, non-common-stock rows, and accidental fifth-call attempts.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_capability_v1.py -q
```

Expected: collection fails because
`research_lab.research.minervini_eodhd_capability_v1` does not exist.

- [ ] **Step 3: Implement the pure bounded diagnostic**

Expose:

```python
def run_minervini_eodhd_capability_v1(
    *,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    http_get: Callable[[str], tuple[object, dict[str, object]]] | None = None,
) -> dict[str, object]:
    """Perform exactly four read-only EODHD capability probes."""
```

Requirements:

- key comes only from the argument or `EODHD_API_KEY`;
- exactly four GETs and no retries, pagination, fallback, or health checks;
- URLs use the official active-symbol, delisted-symbol, EOD, and splits
  endpoints;
- result stores sanitized endpoint identities, authorization status, row
  counts, capability booleans, zero broker/registry/deployment actions, and a
  canonical output hash;
- response bodies and secrets are never persisted or printed;
- any incomplete capability returns `INSUFFICIENT_CAPABILITY`, never `CAPABLE`.

The CLI must default to no network and require `--execute-live`:

```python
if not args.execute_live:
    print("status=DRY_RUN")
    print("planned_provider_calls=4")
    return 0
```

- [ ] **Step 4: Run the focused tests and compile**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_capability_v1.py -q
python -m py_compile research_lab/research/minervini_eodhd_capability_v1.py scripts/check_minervini_eodhd_capability_v1.py
```

Expected: all tests pass; compilation exits zero.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- research_lab/research/minervini_eodhd_capability_v1.py scripts/check_minervini_eodhd_capability_v1.py tests/test_minervini_eodhd_capability_v1.py
git commit -m "feat: add bounded Minervini EODHD capability diagnostic"
```

### Task 2: Frozen Trend Template and VCP signal engine

**Files:**
- Create: `research_lab/research/minervini_price_volume_core_v1.py`
- Create: `tests/test_minervini_price_volume_core_v1.py`

- [ ] **Step 1: Write failing eligibility and no-look-ahead tests**

Construct a three-symbol synthetic daily MultiIndex OHLCV panel. Test:

```python
def test_signal_requires_complete_trend_template_and_cross_sectional_rs():
    signals = build_minervini_signals_v1(_qualifying_panel())
    row = signals.loc[pd.Timestamp("2025-12-31"), "LEADER"]
    assert row["eligible"] is True
    assert row["rs_percentile"] >= 0.80
    assert row["signal"] is True

def test_future_mutation_cannot_change_past_signal():
    panel = _qualifying_panel()
    cutoff = panel.index[-20]
    before = build_minervini_signals_v1(panel.loc[:cutoff])
    panel.loc[panel.index > cutoff, ("LEADER", "close")] *= 10
    after = build_minervini_signals_v1(panel)
    assert before.loc[cutoff, "LEADER"] == after.loc[cutoff, "LEADER"]
```

Add independent failures for price, liquidity, each SMA relation, rising
SMA200, 52-week bounds, RS percentile, each contraction, volume dry-up, pivot
breakout, and breakout volume.

- [ ] **Step 2: Run the signal tests and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_price_volume_core_v1.py -q
```

Expected: import failure for the new module.

- [ ] **Step 3: Implement frozen configuration and feature calculation**

Define an immutable configuration with only the approved values:

```python
@dataclass(frozen=True)
class MinerviniCoreConfigV1:
    minimum_price: float = 5.0
    minimum_dollar_volume_20: float = 10_000_000.0
    relative_strength_percentile: float = 0.80
    vcp_block_sessions: int = 20
    vcp_final_to_first_max: float = 0.60
    dry_up_sessions: int = 10
    dry_up_reference_sessions: int = 50
    dry_up_max_ratio: float = 0.70
    breakout_volume_multiple: float = 1.50
    maximum_gap_above_pivot: float = 0.02
    atr_sessions: int = 20
    minimum_stop_atr: float = 2.0
    maximum_stop_fraction: float = 0.07
```

Expose `build_minervini_signals_v1(daily_panel, *, instrument_types,
config=MinerviniCoreConfigV1()) -> pd.DataFrame`.

The result has MultiIndex columns per symbol for `eligible`, `pivot`,
`structural_stop`, `atr20`, `r_multiple_price`, `signal`, and rejection
reasons. Rolling highs and pivots use `.shift(1)` where the current bar would
otherwise leak into its own threshold. Unknown instrument type and missing
values fail closed.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/test_minervini_price_volume_core_v1.py -q
```

Expected: all signal tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- research_lab/research/minervini_price_volume_core_v1.py tests/test_minervini_price_volume_core_v1.py
git commit -m "feat: add frozen Minervini signal engine"
```

### Task 3: Event-driven portfolio evaluator

**Files:**
- Create: `research_lab/research/minervini_portfolio_evaluator_v1.py`
- Create: `tests/test_minervini_portfolio_evaluator_v1.py`

- [ ] **Step 1: Write failing execution and risk tests**

Cover next-open entry, 2% gap rejection, structural-stop rejection below
2 ATR or beyond 7%, 0.5% equity risk sizing, 12.5% notional cap, eight-position
cap, 100% gross cap, gap-through-stop fills, +2R break-even transition, SMA50
exit, costs, and no same-bar fill.

Representative assertion:

```python
def test_risk_sizing_and_gap_through_stop_are_portfolio_accounted():
    result = run_minervini_portfolio_v1(
        panel=_two_trade_panel(),
        signals=_two_trade_signals(),
        instrument_types={"AAA": "Common Stock", "BBB": "Common Stock"},
        initial_cash=100_000.0,
    )
    first = result["trades"][0]
    assert first["initial_account_risk"] <= 500.0
    assert first["entry_notional"] <= 12_500.0
    assert first["exit_reason"] == "PROTECTIVE_STOP"
    assert result["maximum_drawdown"] <= 0
    assert result["no_same_bar_fill_proof"] is True
```

- [ ] **Step 2: Run the portfolio tests and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_portfolio_evaluator_v1.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement one chronological portfolio ledger**

Expose `run_minervini_portfolio_v1(*, panel, signals, instrument_types,
initial_cash=100_000.0, cost_bps_per_side=15.0) -> dict[str, object]`.

Use integer share quantities rounded down. Each timestamp processes:

1. prior-close exit and stop updates;
2. current open pending exits;
3. current open pending entries in descending prior-close RS order;
4. current intraday protective stops;
5. current close mark-to-market and next-session decisions.

The result includes trades, equity curve, cash reconciliation, exposure,
turnover, cost drag, CAGR, cumulative return, maximum drawdown, MAR, win rate,
average win/loss, and deterministic hashes. It explicitly records zero network,
provider, broker, registry, promotion, and deployment actions.

- [ ] **Step 4: Run focused portfolio and signal tests**

Run:

```powershell
python -m pytest tests/test_minervini_price_volume_core_v1.py tests/test_minervini_portfolio_evaluator_v1.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- research_lab/research/minervini_portfolio_evaluator_v1.py tests/test_minervini_portfolio_evaluator_v1.py
git commit -m "feat: add Minervini portfolio evaluator"
```

### Task 4: Evidence gate and local-only CLI

**Files:**
- Create: `research_lab/research/minervini_evaluation_gate_v1.py`
- Create: `scripts/run_minervini_price_volume_core_v1.py`
- Create: `tests/test_minervini_evaluation_gate_v1.py`
- Modify: `research_lab/research/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing verdict tests**

```python
@pytest.mark.parametrize(
    ("cagr", "drawdown", "trades", "blockers", "verdict"),
    [
        (0.10, -0.15, 100, [], "CANDIDATE"),
        (0.0999, -0.10, 100, [], "FAIL"),
        (0.20, -0.1501, 100, [], "FAIL"),
        (0.20, -0.10, 99, [], "INSUFFICIENT_EVIDENCE"),
        (0.20, -0.10, 100, ["SURVIVORSHIP_BIAS_PRESENT"], "INSUFFICIENT_EVIDENCE"),
    ],
)
def test_gate_is_closed_world(cagr, drawdown, trades, blockers, verdict):
    assert evaluate_minervini_result_v1(
        _result(cagr, drawdown, trades), data_blockers=blockers
    )["verdict"] == verdict
```

CLI tests must prove default mode performs no provider call and writes nothing.
It accepts only an absolute local manifest whose entries bind each symbol to a
local adapter result and exact SHA-256.

- [ ] **Step 2: Run gate tests and verify RED**

Run:

```powershell
python -m pytest tests/test_minervini_evaluation_gate_v1.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement gate and CLI**

Implement the gate directly:

```python
def evaluate_minervini_result_v1(portfolio_result, *, data_blockers):
    if data_blockers:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif int(portfolio_result["trade_count"]) < 100:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif (
        float(portfolio_result["cagr"]) < 0.10
        or float(portfolio_result["maximum_drawdown"]) < -0.15
    ):
        verdict = "FAIL"
    else:
        verdict = "CANDIDATE"
    return {
        "version": "minervini_evaluation_gate_result_v1",
        "verdict": verdict,
        "data_blockers": sorted(data_blockers),
        "portfolio_result_sha256": portfolio_result["output_sha256"],
    }
```

The CLI:

- reads a closed-world local JSON manifest;
- validates exact per-file hashes through the existing local OHLCV adapter;
- builds a multi-asset immutable snapshot identity;
- never downloads missing symbols;
- runs the frozen signal and portfolio modules;
- prints only the verdict and key metrics unless `--write-result` and an
  explicit output directory are supplied;
- reports `BLOCKED_DATASET_UNAVAILABLE` when the manifest is absent or cannot
  satisfy point-in-time and delisting requirements.

- [ ] **Step 4: Run all Minervini-focused tests and compile**

Run:

```powershell
python -m pytest tests/test_minervini_eodhd_capability_v1.py tests/test_minervini_price_volume_core_v1.py tests/test_minervini_portfolio_evaluator_v1.py tests/test_minervini_evaluation_gate_v1.py -q
python -m py_compile research_lab/research/minervini_*.py scripts/*minervini*.py
git diff --check
```

Expected: all focused tests pass; compile and diff check exit zero.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- research_lab/research/minervini_evaluation_gate_v1.py scripts/run_minervini_price_volume_core_v1.py tests/test_minervini_evaluation_gate_v1.py research_lab/research/__init__.py README.md
git commit -m "feat: add local Minervini evaluation gate"
```

### Task 5: One synthetic end-to-end acceptance

**Files:**
- Create: `tests/test_minervini_price_volume_core_e2e_v1.py`

- [ ] **Step 1: Add one deterministic end-to-end test**

Build at least ten synthetic common stocks with two qualifying breakouts, one
gap rejection, one protective stop, and one +2R/SMA50 exit. Run the entire
local pipeline twice and assert identical result hashes, chronological fills,
cash reconciliation, risk bounds, and zero side effects.

- [ ] **Step 2: Run only the Minervini suite**

Run:

```powershell
python -m pytest tests/test_minervini_* -q
```

Expected: all tests pass.

- [ ] **Step 3: Commit Task 5**

```powershell
git add -- tests/test_minervini_price_volume_core_e2e_v1.py
git commit -m "test: cover Minervini research pipeline end to end"
```

### Task 6: Capability execution and real-test gate

**Files:**
- No code changes expected.

- [ ] **Step 1: Run dry-run capability planning**

Run:

```powershell
python scripts/check_minervini_eodhd_capability_v1.py
```

Expected:

```text
status=DRY_RUN
planned_provider_calls=4
```

- [ ] **Step 2: Run exactly one live capability diagnostic**

Only after confirming `EODHD_API_KEY` is present, run:

```powershell
python scripts/check_minervini_eodhd_capability_v1.py --execute-live
```

Expected: exactly four provider calls, no secret in output, and either
`CAPABLE` or `INSUFFICIENT_CAPABILITY`.

- [ ] **Step 3: Decide the data path without guessing authority**

If a complete existing immutable local US-stock snapshot already exists, run:

```powershell
python scripts/run_minervini_price_volume_core_v1.py --manifest C:\Users\lojka\trading\data\minervini-us-v1\manifest.json
```

Default mode performs no write. Report CAGR, cumulative return, maximum
drawdown, trade count, exposure, cost drag, data blockers, and verdict.

If no complete local snapshot exists, stop with:

```text
MINERVINI_V1_BLOCKED_DATASET_UNAVAILABLE
```

Report active and delisted universe row counts, the exact number of symbols
requiring history, estimated EODHD calls, date range, and storage bound. Do not
start a wide acquisition without a separate explicit approval because it is a
material provider operation.

### Task 7: Final verification before publication

**Files:**
- No code changes expected unless verification reveals a defect.

- [ ] **Step 1: Run focused tests**

```powershell
python -m pytest tests/test_minervini_* -q
```

- [ ] **Step 2: Run adjacent contract tests**

```powershell
python -m pytest tests/test_local_ohlcv_file_input_adapter_v1.py tests/test_multi_asset_immutable_ohlcv_snapshot_contract_v1.py tests/test_point_in_time_universe_contract_v1.py -q
```

- [ ] **Step 3: Run compile and diff checks**

```powershell
python -m py_compile research_lab/research/minervini_*.py scripts/*minervini*.py
git diff --check
git status --short
```

Do not run the full repository suite, create a PR, push, acquire a wide
dataset, or contact a broker unless the user separately requests that action.
