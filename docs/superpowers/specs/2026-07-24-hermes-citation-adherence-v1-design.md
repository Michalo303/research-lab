# Hermes Citation Adherence V1 Design

**Date:** 2026-07-24
**Status:** Approved for implementation
**Scope:** Hermes/OpenRouter hypothesis generation only

## Goal

Make the existing Hermes provider contract unambiguous: whenever canonical
Knihomol evidence is available, every generated hypothesis must cite at least
one exact `note_id` selected for that run. Preserve the existing fail-closed
validator, queue atomicity, research-only execution boundary, and downstream
lineage.

This milestone does not attempt to prove that a cited note semantically implies
the proposed strategy. It proves the narrower, auditable property that the
provider received a bounded evidence allowlist and explicitly attributed each
accepted hypothesis to at least one item from that allowlist.

## Verified Starting Point

PRs #189 and #190 are merged. Local `main`, `origin/main`, and the canonical
Hetzner checkout were verified at
`e5f055938e0fd43666d545cd65d248e77f17203a`.

The bounded live run
`20260723T210822195314Z-e5f0559` established:

- the daily blocker resolved from `drawdown_fail` to canonical `drawdown`;
- four citable notes from three books were selected;
- the provider returned twelve schema-shaped proposals;
- all twelve omitted `used_note_ids`;
- all twelve were rejected as `book_evidence_not_used`;
- the hypothesis queue remained unchanged at 370 rows with SHA-256
  `a8e001a7c60f90d56874d39de247fcf213558763b688e15eeed2322b964771c5`;
- no EODHD daily run was started.

The immediate root cause is an internal contract contradiction.
`schema_prompt_text()` currently tells the model that `used_note_ids` may be
omitted or set to `[]`, while the runtime correctly rejects an empty list when
book context is present. The JSON example in `build_hermes_prompt()` also omits
the field.

## Chosen Approach

Use the existing portable OpenAI-compatible JSON-object response mode and make
the citation contract explicit in the prompt and validator.

The current adapter already sends:

```json
{"response_format": {"type": "json_object"}}
```

V1 will not add provider-specific `json_schema` payloads, model switching,
fallback models, or retries. Those features vary by OpenRouter model and could
turn a narrow contract repair into provider-routing behavior. The server-side
validator remains the authority regardless of provider formatting support.

## Citation Contract

### Allowlist

The Hermes runner already obtains a bounded tuple of promotion-eligible
`selected_note_ids`. It will pass that exact tuple into the schema prompt
builder.

The prompt will contain a dedicated citation section with:

- the exact allowed `note_id` values for this run;
- the rule that every hypothesis must contain `used_note_ids`;
- a minimum of one and maximum of five IDs;
- the rule that every ID must come from the displayed allowlist;
- an explicit statement that invented, copied-from-elsewhere, or empty IDs
  cause rejection;
- a JSON example containing an actual selected ID.

Only note identities are repeated. No private corpus path, raw book text,
source passage, provider response, or secret is added.

### Validator

`validate_hypothesis(..., allowed_note_ids=...)` will own the mandatory
citation rule:

- `allowed_note_ids is None`: retain generic/legacy validation semantics;
- a non-empty allowlist plus missing or empty `used_note_ids`:
  `missing_used_note_ids`;
- malformed type, malformed ID, or more than five IDs:
  `invalid_used_note_ids`;
- any well-formed ID outside the exact allowlist:
  `unknown_used_note_id`;
- one to five allowed IDs: accepted and deduplicated in stable input order.

The runner may retain a defensive assertion or rejection guard, but it must not
auto-fill, infer, repair, or substitute citations after provider output.

### Prompt Safety Fallback

The current prompt builder has a sanitized fallback when unrelated diagnostics
contain a forbidden private-path reference. The exact citation allowlist and
mandatory citation instructions must appear in both the normal prompt and that
sanitized fallback. The citation contract and IDs alone are not evidence:
whenever notes were selected, the final prompt must also contain the exact safe
selected book context.

If either a non-empty selected-note allowlist or its selected book context
cannot be represented in the final prompt, the provider must not be called.
The run must finish with an immutable no-queue-change artifact and a bounded
reason code.

## Data Flow

```text
structured daily blocker
  -> canonical blocker
  -> eligible Knihomol notes
  -> exact selected_note_ids allowlist
  -> citation-aware JSON prompt
  -> one provider invocation
  -> envelope parsing
  -> hypothesis schema + exact citation validation
  -> atomic queue append for accepted hypotheses only
  -> used_note_ids lineage in queue and later research result
```

There is no automatic retry. A provider response that omits or invents
citations is an audited research failure, not a reason to weaken the contract.

## Artifacts and Diagnostics

Existing immutable Hermes artifacts remain authoritative. V1 may add bounded
counts or reason codes, but must not persist:

- the raw prompt;
- the raw provider response;
- book text or source passages;
- private corpus paths;
- API keys, authorization headers, or environment values.

Expected rejection reasons remain per-hypothesis and deterministic. The run
terminal status remains `completed_with_rejections` when proposals were
returned but none passed.

## Testing

Tests must prove:

1. the citation-aware schema text displays the exact selected allowlist;
2. the full JSON example contains `used_note_ids` with an allowed ID;
3. both normal and sanitized prompts preserve the citation contract;
4. missing and empty citations fail when an allowlist is supplied;
5. unknown, malformed, non-list, and oversized citations fail;
6. one or more allowed citations pass and preserve stable deduplicated order;
7. no helper automatically inserts a citation into provider output;
8. provider invocation remains exactly once;
9. the OpenAI-compatible request remains bounded JSON-object mode and does not
   expose secrets;
10. accepted IDs propagate unchanged through queue and hypothesis-result
    lineage;
11. rejected outputs leave the queue byte-for-byte unchanged;
12. generic fixture workflows without a required evidence allowlist retain
    their current behavior.

Validation before publication:

- focused Hermes, prompt, provider, queue, and daily-runner tests;
- `py_compile`;
- `git diff --check`;
- full `pytest`;
- independent strict base-to-head review with no unresolved P0/P1/P2.

## Post-Merge Verification

After a strict-review PASS and merge:

1. fast-forward the separate clean local `main`;
2. synchronize Hetzner only through
   `scripts/run_safe_sync_with_preflight.sh` under the `trading` account;
3. run the focused Hetzner suite before any provider action;
4. prove Hermes and daily services are inactive;
5. record safe queue row count and SHA-256;
6. start Hermes exactly once through its existing systemd service;
7. inspect only bounded artifact metadata and citation reason codes;
8. run the EODHD daily service exactly once only if at least one new
   hypothesis ID was atomically imported;
9. otherwise stop and report the exact deterministic rejection reason.

No second provider attempt, fallback model, manual queue edit, manual corpus
edit, broker call, promotion, deployment, or risk-limit change is authorized.

## Acceptance Criteria

The code milestone passes when all tests and strict review pass and the
post-merge bounded run proves one of these honest outcomes:

- at least one hypothesis cites only selected note IDs and is atomically
  imported, after which one bounded EODHD research run records its normal
  walk-forward, drawdown, cost, and stability verdict; or
- the provider still violates the citation contract, every invalid proposal is
  rejected, the queue remains unchanged, and the terminal artifact identifies
  the exact reason.

Neither outcome is evidence of a profitable edge by itself. A successfully
imported hypothesis remains a research candidate and must pass all existing
validation and drawdown gates.

## Out of Scope

- changing the OpenRouter model or provider routing;
- retries, repair prompts, fallback prompts, or automatic citation insertion;
- semantic entailment scoring between book text and strategy logic;
- new strategy builders, symbols, leverage, or portfolio permissions;
- changing the 15% drawdown objective or any existing promotion threshold;
- paper/live trading, broker integration, deployment, or production
  orchestration changes;
- modifying or promoting Knihomol notes.
