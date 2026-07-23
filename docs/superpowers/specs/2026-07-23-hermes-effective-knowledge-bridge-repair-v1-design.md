# Hermes Effective Knowledge Bridge Repair v1

## Goal

Make scheduled Hermes hypothesis generation genuinely book-informed and stop
re-testing the same queued executable hypothesis against the same market-data
snapshot. The repair remains research-only and must not weaken validation,
promotion, drawdown, cost, or deployment gates.

## Verified Problem

The canonical Knihomol index exists on Hetzner and contains 296 book records,
but the last seven scheduled Hermes runs reported:

- `canonical_blocker_id=""`;
- `blocker_diagnostic="unrecognized_blocker"`;
- `note_count=0`;
- zero selected books and notes.

The latest run still invoked the OpenAI-compatible provider, generated seven
hypotheses, rejected six as invalid or duplicate, and committed one generic
hypothesis. A scheduled run can therefore look successful without using book
knowledge.

The failure is caused by an interface mismatch. The daily report's
`biggest risk discovered` line can contain a natural-language provider/status
sentence, while Knihomol retrieval accepts a small canonical blocker taxonomy.
The same report already contains structured rejection counts that identify the
actual research blockers.

Queued hypotheses are also not consumed. Executable fingerprints deduplicate
the queue, but a retained queued hypothesis can be selected again while the
underlying data snapshot is unchanged.

## Considered Approaches

### 1. Map the current sentence only

Add more keywords to the natural-language blocker parser.

This is small but fragile. Report wording can change and a provider-status
sentence can continue to outrank the actual strategy failures.

### 2. Structured blocker selection plus bounded execution-state repair

Derive the dominant blocker from structured rejection counts, fail closed when
an available canonical book store yields no usable notes, require accepted
book-informed hypotheses to cite selected notes, and skip a queued executable
hypothesis already tested on the same deterministic data snapshot.

This is the selected approach. It repairs the observed end-to-end failure
without expanding LLM authority or the builder catalog.

### 3. Allow agents to create arbitrary strategy code

This could expand the search space but crosses the current safety boundary and
would mix knowledge retrieval, code generation, validation, and execution in
one change. It is explicitly out of scope.

## Architecture

### Structured blocker selection

`research_lab.hermes.artifacts.dominant_blocker` will parse the daily report's
structured `rejection_reasons` summary before considering descriptive prose.
Known rejection reasons map to canonical blocker IDs:

- `insufficient walk-forward robustness` -> `walk_forward_fail`;
- `max drawdown too deep` -> `drawdown_fail`;
- `failed cost stress` -> `cost_stress`.

The largest count wins. Ties use a fixed priority order:
walk-forward robustness, drawdown, then cost stress. Existing prose parsing
remains a compatibility fallback.

### Fail-closed book-context gate

`run_hypothesis_generation` will determine whether the configured canonical
book index and extracted-notes directory are available.

When both are available but retrieval yields no selected note, the run will:

- not invoke the LLM provider;
- not modify the hypothesis queue;
- write an immutable terminal artifact;
- return `status="book_context_unavailable"`;
- preserve the canonical blocker and bounded diagnostic reason.

When canonical inputs are absent, existing provider-unavailable and fixture
workflows remain compatible. This avoids turning unit tests or explicitly
generic development environments into production authority.

When book context is present, every accepted hypothesis must contain at least
one valid `used_note_id` selected for that run. A proposal that ignores the
provided evidence is rejected with `book_evidence_not_used`.

### Lineage

The existing lineage is retained:

`selected note_id -> queued hypothesis -> strategy spec -> experiment result
-> hypothesis result`

The repair will add regression coverage proving that exact selected note IDs
reach the persisted experiment and hypothesis-result payloads. No raw book
content, private paths, prompts, or provider responses are persisted.

### Same-snapshot execution dedupe

A deterministic `data_snapshot_identity` will be derived from a strict,
normalized subset of each data manifest:

- source/provider identity;
- timeframe/interval when present;
- ordered symbols;
- start and end timestamps;
- fallback status when explicitly present or safely derivable, otherwise the
  literal state `unknown`;
- available content hash fields when present.

Every completed result stores this identity. Before an LLM-generated queued
hypothesis is backtested, the runner checks a bounded recent experiment tail for
the pair:

`strategy_execution_fingerprint + data_snapshot_identity`

If that pair already exists, the runner skips the expensive backtest, records a
bounded `same_snapshot_skipped` diagnostic, performs no registry append for the
skip, and continues. Baseline and manually supplied strategies are not changed
by this rule. A new data snapshot remains eligible for a new test.

Legacy results without an explicit snapshot identity are compared using a
deterministic identity derived from their stored data manifest.

## Safety Boundaries

This milestone does not:

- add builders, symbols, leverage, or risk permissions;
- weaken validation, drawdown, cost, stability, or promotion gates;
- promote or deploy a strategy;
- connect to a broker or place orders;
- store secrets, raw prompts, raw model responses, or book text;
- delete or rewrite historical registry, report, or Knihomol artifacts;
- automatically install or restart services.

The final live verification is one explicitly bounded scheduled-style provider
invocation followed by one deterministic EODHD daily research run. It occurs
only after merge and safe Hetzner synchronization. It may append the normal
immutable Hermes and research artifacts already authorized by the user, but it
cannot promote or deploy.

## Testing

Tests must prove:

1. structured rejection counts outrank misleading descriptive prose;
2. deterministic tie handling;
3. available canonical inputs plus zero selected notes block the provider and
   preserve the queue;
4. accepted book-informed hypotheses require selected note IDs;
5. selected note IDs propagate through the existing result lineage;
6. snapshot identity is deterministic and changes when material data identity
   changes;
7. the same queued executable hypothesis is skipped on the same snapshot;
8. the same hypothesis is eligible on a new snapshot;
9. baseline strategies retain existing behavior;
10. focused and full repository suites pass.

## Acceptance Evidence

The milestone is complete only when:

- independent strict review returns no P0/P1/P2 findings;
- the exact reviewed head is merged;
- local main, origin/main, GitHub main, and Hetzner main align;
- Hetzner focused tests pass;
- a bounded live Hermes artifact reports a recognized canonical blocker,
  `note_count > 0`, selected note IDs, and no generic bookless success;
- any imported hypothesis contains selected `used_note_ids`;
- the subsequent real-data result either records the same lineage or reports an
  explicit deterministic rejection/skip reason;
- no broker, promotion, deployment, or risk-gate action occurs.
