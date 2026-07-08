# Risk Overlay Runtime Contract V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pure deterministic execution-time contracts and fail-closed capability declarations needed for truthful future risk-overlay execution, without running any backtests.

**Architecture:** Introduce one new execution-contract module for validated JSON-compatible runtime payloads and pure transition/sizing functions, plus one small capability declaration module with fail-closed defaults for current strategy builders. Keep existing strategy generation, adapter logic, and backtest behavior unchanged; prove that with focused regression tests.

**Tech Stack:** Python, pytest, dataclasses, pure validation helpers, existing strategy builders in `research_lab/strategies/baselines.py`

---

### Task 1: Add failing contract tests

**Files:**
- Create: `tests/test_risk_execution_contract_v1.py`
- Create: `tests/test_strategy_execution_capabilities_v1.py`
- Test: `tests/test_risk_execution_contract_v1.py`
- Test: `tests/test_strategy_execution_capabilities_v1.py`

- [ ] **Step 1: Write the failing tests**

```python
from research_lab.execution.risk_execution_contract_v1 import (
    build_circuit_breaker_transition,
    build_fixed_fractional_sizing,
    build_portfolio_overlay_state,
    build_protective_exit_contract,
    build_strategy_event,
)
from research_lab.execution.strategy_execution_capabilities_v1 import (
    get_strategy_execution_capability,
)


def test_valid_entry_event_passes():
    payload = build_strategy_event(
        {
            "timestamp": "2026-01-05",
            "event_type": "entry",
            "symbol": "SPY",
            "target_direction": "long",
            "strategy_identity": "SWING_ETF_1D_QUEUE_PULLBACK",
            "event_id": "evt-001",
        }
    )
    assert payload["event_type"] == "entry"


def test_fixed_fractional_risk_budget_is_correct():
    result = build_fixed_fractional_sizing(
        {
            "current_equity": 100_000.0,
            "selected_risk_per_trade_pct": 1.0,
            "per_unit_loss_to_protective_exit": 2.5,
            "price": 50.0,
            "available_capital": 100_000.0,
            "strategy_position_cap": 100_000.0,
            "portfolio_exposure_cap": 100_000.0,
            "leverage_allowed": False,
            "fractional_units_allowed": False,
        }
    )
    assert result["risk_budget"] == 1000.0


def test_swing_trend_filtered_pullback_remains_unsupported():
    capability = get_strategy_execution_capability("swing_trend_filtered_pullback")
    assert capability["supported_for_risk_overlay_execution"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_risk_execution_contract_v1.py tests/test_strategy_execution_capabilities_v1.py`
Expected: `FAIL` with import errors because the new execution modules do not exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/test_risk_execution_contract_v1.py tests/test_strategy_execution_capabilities_v1.py
git commit -m "test: add failing runtime contract coverage"
```

### Task 2: Implement the pure execution contract module

**Files:**
- Create: `research_lab/execution/risk_execution_contract_v1.py`
- Test: `tests/test_risk_execution_contract_v1.py`

- [ ] **Step 1: Write the minimal implementation for event and protective-exit validation**

```python
ALLOWED_EVENT_TYPES = {"entry", "exit", "rebalance"}
ALLOWED_DIRECTIONS = {"long", "flat"}


def build_strategy_event(payload: dict[str, object]) -> dict[str, object]:
    ...


def build_protective_exit_contract(payload: dict[str, object]) -> dict[str, object]:
    ...
```

- [ ] **Step 2: Write the minimal implementation for fixed-fractional sizing**

```python
def build_fixed_fractional_sizing(payload: dict[str, object]) -> dict[str, object]:
    risk_budget = current_equity * selected_risk_per_trade_pct / 100.0
    raw_units = risk_budget / per_unit_loss_to_protective_exit
    if not fractional_units_allowed:
        raw_units = math.floor(raw_units)
    ...
```

- [ ] **Step 3: Write the minimal implementation for overlay state and circuit-breaker transitions**

```python
def build_portfolio_overlay_state(payload: dict[str, object]) -> dict[str, object]:
    ...


def build_circuit_breaker_transition(payload: dict[str, object]) -> dict[str, object]:
    ...
```

- [ ] **Step 4: Run focused contract tests and make them pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_risk_execution_contract_v1.py`
Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add research_lab/execution/risk_execution_contract_v1.py tests/test_risk_execution_contract_v1.py
git commit -m "feat: add risk execution runtime contract"
```

### Task 3: Implement the fail-closed capability module

**Files:**
- Create: `research_lab/execution/strategy_execution_capabilities_v1.py`
- Test: `tests/test_strategy_execution_capabilities_v1.py`

- [ ] **Step 1: Add the capability schema and fail-closed defaults**

```python
def get_strategy_execution_capability(builder: str) -> dict[str, object]:
    ...
```

- [ ] **Step 2: Encode explicit unsupported status for every current builder**

```python
CAPABILITIES = {
    "long_term_trend_filter": {...},
    "swing_trend_filtered_pullback": {
        "supported_for_risk_overlay_execution": False,
        "unsupported_reason": "... ATR stop remains embedded in local weight-builder state ...",
    },
}
```

- [ ] **Step 3: Run focused capability tests and make them pass**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_strategy_execution_capabilities_v1.py`
Expected: `PASS`

- [ ] **Step 4: Commit**

```bash
git add research_lab/execution/strategy_execution_capabilities_v1.py tests/test_strategy_execution_capabilities_v1.py
git commit -m "feat: add strategy execution capability declarations"
```

### Task 4: Prove no regression in existing risk-overlay planning and strategy output

**Files:**
- Test: `tests/test_risk_overlay_execution_adapter.py`
- Test: `tests/test_risk_overlay_controlled_backtest.py`
- Test: `tests/test_risk_overlay_single_controlled_backtest.py`
- Test: `tests/test_risk_overlay_single_backtest_preflight.py`
- Test: `tests/test_risk_overlay_isolated_single_runner_contract.py`
- Test: `tests/test_etf_risk_variants.py`
- Test: `tests/test_risk_overlay_queue_runtime.py`

- [ ] **Step 1: Run focused regression coverage**

Run: `.venv\Scripts\python.exe -m pytest -q tests/test_risk_execution_contract_v1.py tests/test_strategy_execution_capabilities_v1.py tests/test_risk_overlay_execution_adapter.py tests/test_risk_overlay_controlled_backtest.py tests/test_risk_overlay_single_controlled_backtest.py tests/test_risk_overlay_single_backtest_preflight.py tests/test_risk_overlay_isolated_single_runner_contract.py tests/test_etf_risk_variants.py tests/test_risk_overlay_queue_runtime.py`
Expected: `PASS`

- [ ] **Step 2: Run full verification**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: `PASS`

- [ ] **Step 3: Run diff and tree hygiene checks**

Run: `git diff --check`
Expected: no output

Run: `git diff --name-status`
Expected: only the new execution modules, tests, and plan file

Run: `git status --short --branch`
Expected: branch `codex/risk-overlay-runtime-contract-v1` with only intended modifications

