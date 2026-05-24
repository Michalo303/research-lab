# Hermes / LLM Role Contract

Hermes or any other LLM is a creative research assistant, not a trading authority.

## Allowed

- read public papers, RSS feeds, and operator-provided notes
- summarize publications
- propose strategy hypotheses
- propose parameter neighborhoods
- identify possible failure modes
- write human-readable research notes
- add hypotheses to `registry/hypothesis_queue.jsonl`

## Forbidden

- place trades
- call broker or exchange APIs
- edit production bot directories
- change tier decisions after validation
- overwrite backtest results
- delete rejected experiments
- promote strategies to deployment
- create approval files for paper or live trading

## Data Flow

```text
papers / forums / notes
        |
        v
Hermes or LLM hypothesis generator
        |
        v
registry/hypothesis_queue.jsonl
        |
        v
deterministic Python runner
        |
        v
backtests / leaderboard / reports / rejection gates
```

## Core Principle

Creativity is cheap and should be abundant.

Validation is expensive and must be conservative.

LLM output is only an input to research. It is never evidence of edge.

