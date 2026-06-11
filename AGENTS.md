# Agent Guidance

## Token-safe operating mode

Start each new agent session with:

```powershell
python scripts/agent_brief.py
```

Use that brief to choose one narrow next action before reading more files. The brief is read-only and must not be treated as a validation source; strategy promotion and rejection still come only from the existing deterministic reports, registry, leaderboard, and gate code.

Avoid token-heavy exploration unless the current task explicitly requires it:

- do not read `INVENTORY_full_diff.patch` unless auditing that artifact;
- do not load full `reports/runs/`, `backtests/runs/`, `data/processed/`, or large JSONL/CSV runtime artifacts into chat;
- do not run unrestricted recursive `rg` over generated artifacts;
- prefer targeted commands with explicit paths, `rg -n` patterns, `Get-Content -TotalCount`, and small deterministic summaries.

The token-saving workflow must not reduce model rigor. If the brief is insufficient, inspect the smallest relevant source file or report section needed to prove the next step.

## Future agent startup contract

Every new Codex/agent task must first run:

```powershell
python scripts/agent_brief.py
```

The agent must read and follow the resulting brief before broad exploration.
The agent must not start by reading large generated artifacts.
The agent must not use unrestricted recursive search over runtime/generated outputs.
The agent must choose one narrow next action after reading the brief.
The brief is read-only orientation, not a validation source.
The brief must never override deterministic validation, promotion, deployment, registry, leaderboard, or gate logic.
If the brief is insufficient, the agent must inspect the smallest relevant source file or report section needed to prove the next step.
This contract applies to all future agentic work unless the user explicitly requests a different procedure for a specific task.

## Current research-system status

The research pipeline is operational. The main blocker is not infrastructure execution.

As of the latest daily reports:

- deterministic runner works;
- registry generation works;
- leaderboard generation works;
- strategy-card/reporting pipeline works;
- EODHD real EOD data is enabled;
- available EODHD history is approximately 33.3 years for the main ETF universe;
- Massive/Polygon paid data access is also working and currently paid for;
- Hetzner GitHub sync automation is installed and runs under the `trading` user.

The current research problem is strategy quality, not pipeline availability.

Primary blockers observed in daily reports:

- insufficient rolling walk-forward robustness;
- excessive unseen max drawdown;
- too many weak or duplicate candidate variants;
- insufficient unseen trade samples for some strategies;
- no strategy currently meets promotion requirements for live deployment.

Future work should prioritize:

1. improving candidate generation quality;
2. reducing duplicate or near-duplicate daily experiments;
3. improving walk-forward robustness;
4. reducing drawdown under unseen validation;
5. improving parameter-neighborhood stability;
6. preserving strict promotion gates.

Do not weaken validation gates merely to produce accepted strategies.
Do not treat rejected strategies as infrastructure failures.
Do not deploy strategies unless existing promotion and validation gates pass.
