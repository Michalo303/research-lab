"""Versioned, deterministic research-only contracts."""

from research_lab.research.research_objective_promotion_gate_v1 import (
    build_research_objective_policy_v1,
    evaluate_research_objective_promotion_gate_v1,
)
from research_lab.research.global_experiment_ledger_v1 import build_global_experiment_ledger_v1
from research_lab.research.minervini_eodhd_capability_v1 import (
    run_minervini_eodhd_capability_v1,
)
from research_lab.research.minervini_evaluation_gate_v1 import (
    evaluate_minervini_result_v1,
)
from research_lab.research.minervini_portfolio_evaluator_v1 import (
    run_minervini_portfolio_v1,
)
from research_lab.research.minervini_price_volume_core_v1 import (
    MinerviniCoreConfigV1,
    build_minervini_signals_v1,
)
from research_lab.research.minervini_eodhd_acquisition_pilot_v1 import (
    build_minervini_eodhd_acquisition_plan_v1,
    run_minervini_eodhd_acquisition_pilot_v1,
)
from research_lab.research.minervini_eodhd_acquisition_pilot_v2 import (
    build_minervini_eodhd_acquisition_plan_v2,
    run_minervini_eodhd_acquisition_pilot_v2,
    validate_minervini_symbol_splits_v2,
)
from research_lab.research.minervini_immutable_pilot_artifacts_v1 import (
    replay_minervini_pilot_artifacts_v1,
)
from research_lab.research.real_qlib_eodhd_edge_discovery_pilot_v1 import (
    run_real_qlib_eodhd_edge_discovery_pilot_v1,
)

__all__ = [
    "MinerviniCoreConfigV1",
    "build_minervini_eodhd_acquisition_plan_v1",
    "build_minervini_eodhd_acquisition_plan_v2",
    "build_research_objective_policy_v1",
    "build_minervini_signals_v1",
    "evaluate_research_objective_promotion_gate_v1",
    "evaluate_minervini_result_v1",
    "build_global_experiment_ledger_v1",
    "run_minervini_eodhd_capability_v1",
    "run_minervini_eodhd_acquisition_pilot_v1",
    "run_minervini_eodhd_acquisition_pilot_v2",
    "run_minervini_portfolio_v1",
    "run_real_qlib_eodhd_edge_discovery_pilot_v1",
    "replay_minervini_pilot_artifacts_v1",
    "validate_minervini_symbol_splits_v2",
]
