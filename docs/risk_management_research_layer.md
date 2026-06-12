# Risk-Management Research Layer

Risk management is a first-class research objective. Strategy research must optimize for survival, drawdown containment, walk-forward robustness, and portfolio-level risk alongside return.

This layer is guidance only. It must not weaken deterministic validation, promotion gates, drawdown limits, data-quality blocks, registry logic, or deployment rules.

## Required Research Controls

Every strategy hypothesis catalog path should consider and record:

- volatility targeting;
- drawdown circuit breakers;
- cash or defensive regimes;
- exposure caps;
- correlation-aware portfolio risk;
- crisis-period diagnostics;
- cost and slippage stress;
- parameter-neighborhood stability.

## Candidate Prioritization

Near-miss candidates such as `LONGTERM_ETF_1D_TREND_VOL_CAP` should be mutated primarily through risk controls: lower volatility targets, lower exposure caps, smoother volatility estimates, stricter cash filters, and drawdown circuit breakers.

Strategies with high CAGR but unstable drawdown are lower priority than strategies with lower return and stronger survival characteristics. Rotation families with historically extreme drawdowns should not be expanded into generic return-chasing variants until a stronger risk overlay is present.

Synthetic or fallback-data candidates remain blocked from promotion regardless of risk metadata.
