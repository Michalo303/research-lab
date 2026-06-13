# Hermes Book Extraction Agent Design

## Objective

Build the first blocker-first Hermes Book Extraction / Learning Agent. The agent turns evidence from a small, relevant subset of private books into short, testable proposed notes, requires explicit promotion before runtime use, records which promoted notes influenced generated hypotheses, and updates note and book priorities from deterministic experiment feedback.

The primary v1 blocker is `walk_forward_fail`. The feature extends the existing `hermes_knowledge` package and reuses `research_lab.hermes.providers.invoke_provider`; it does not create a parallel `hermes_books` package or a second validation/provider stack.

## Data Flow

The complete v1 flow is:

```text
blocker
  -> selected_books
  -> passage_candidates
  -> proposed_notes
  -> extracted_notes
  -> Hermes retrieval
  -> used_note_ids
  -> feedback
```

Each transition is explicit and auditable:

1. A supported blocker selects a bounded set of indexed books.
2. Only selected books are opened for text extraction.
3. Short, localized passage candidates are stored outside Git with source provenance.
4. One bounded provider call transforms each candidate into at most one proposed note.
5. Schema validation may accept a proposed note but never promotes it.
6. An explicit `promote --note-id` operation copies one selected valid note into `extracted_notes`.
7. Existing Hermes runtime retrieval reads only `extracted_notes`.
8. Selected note IDs are preserved through Hermes hypothesis provenance and experiment metadata.
9. Deterministic feedback recalculates note and book priorities from later results.

## Existing Safety Boundary

The current runtime already loads `/opt/trading/private/hermes_books/extracted_notes`, validates each entry, rejects entries with unsafe private-file references, verifies book hashes against the index, ignores nonpositive priority, and fails open when private inputs are unavailable. This feature retains those invariants.

`proposed_notes` is a staging area only. No runtime default, environment variable fallback, glob, or CLI command may cause proposed notes to enter Hermes prompt construction. Promotion is the only transition into `extracted_notes`, and promotion always names a specific `note_id`.

## Storage Layout

Runtime data remains outside the source repository:

```text
/opt/trading/private/hermes_books/
  index/book_index.json
  raw/*.pdf
  text/*.txt
  passage_candidates/walk_forward_fail.jsonl
  proposed_notes/walk_forward_fail.jsonl
  extracted_notes/walk_forward_fail.jsonl
  feedback/note_feedback.jsonl
  feedback/priorities.json
```

The text directory is optional and may contain pre-extracted UTF-8 sidecars. Tests use only small temporary fixtures. `.gitignore` covers private raw books, indexes, text, candidate passages, proposed notes, extracted notes, and feedback artifacts.

Writes use deterministic JSON serialization and atomic replacement when an existing logical artifact must be updated. Append-only evidence and feedback logs retain prior records. No command deletes source PDFs, historical candidates, notes, or feedback.

## Components

### Blocker Taxonomy

`hermes_knowledge/blocker_taxonomy.py` defines supported blockers and weighted search concepts. V1 includes `walk_forward_fail` with concepts covering walk-forward validation, robustness, parameter stability, overfitting, regime change, adaptive systems, volatility normalization, sample splitting, trend persistence, and model decay.

The taxonomy is deterministic data, not a catalog of strategy rules. It may rank evidence but may not emit hypotheses or testable notes by itself. Unsupported blockers fail with a clear validation error.

### Book Selector

`hermes_knowledge/book_selector.py` ranks `BookRecord` values for one blocker using normalized book title, optional index metadata, and optional bounded text-preview matches. Ranking includes:

- weighted blocker-term matches;
- deterministic title and book-ID tie breaking;
- deduplication by normalized title;
- an optional persisted book-priority adjustment produced by feedback;
- a hard maximum of five books in v1.

Selection output contains safe book identity, score, matched terms, and concise reasons. Raw filesystem paths are used internally for extraction but are not sent to Hermes prompts or written into note fields that can reach prompts.

### Passage Extractor

`hermes_knowledge/passage_extractor.py` opens only selected books. It prefers a configured UTF-8 text sidecar and otherwise uses an available local PDF text reader. PDF support is optional: an unavailable reader or unreadable PDF produces a per-book diagnostic rather than failing the run.

Extraction searches normalized text for blocker concepts, creates localized windows around matches, and ranks candidates deterministically. Each candidate includes a stable `passage_id`, book identity, blocker, page or text location, matched terms, a short evidence excerpt, and extraction reason.

V1 limits are:

- at most five books;
- at most three passages per book;
- at most 1,200 characters in the private passage candidate;
- at most 280 excerpt characters in any note that may later reach Hermes;
- duplicate or substantially overlapping windows from the same book are collapsed.

Passage candidates remain private runtime artifacts and are never loaded by Hermes generation.

### Note Generator

`hermes_knowledge/note_generator.py` sends one bounded passage candidate per call through the existing `invoke_provider` abstraction. It uses the same `command` and `openai_compatible` provider configuration as scheduled Hermes but has a separate prompt contract.

The prompt contains only the selected short passage, blocker, safe book title/ID, location, and an exact JSON schema. It asks for one concise, testable note and forbids executable code, gate changes, leverage expansion, generic advice, and unsupported claims.

Provider output must be a JSON object. The generator supplies repository-controlled provenance fields and validates the complete proposed-note record locally. Provider failure, missing output, invalid JSON, envelope mismatch, forbidden reference, oversized text, or schema violation skips only that passage and emits a short diagnostic without raw provider output or private paths.

### Note Schema And Identity

The current knowledge-entry schema remains the runtime note payload. It is extended with stable provenance needed by the learning loop:

- `note_id` derived deterministically from blocker, book ID, passage ID, and normalized note content;
- `source_location` containing a page number or bounded text location, never a private path;
- `source_passage_id` linking the note to private evidence;
- `implementation_hint` containing a short repository-facing implementation direction.

Proposed-note files use a separate envelope with `status: proposed`, generation diagnostics, and an `entry` object containing the prospective runtime note. `validate_proposed_note` validates both the envelope and its nested entry. Promotion writes only the nested entry to `extracted_notes`; it does not copy the lifecycle envelope or status. Runtime loading continues to call only `validate_entry` on extracted-note files, so a proposal envelope cannot pass runtime validation even if it is manually copied into the extracted directory.

Runtime prompt rendering exposes only short validated hypothesis content and omits storage provenance that is not useful to hypothesis generation. The runtime note itself has no mutable lifecycle status: membership in the private `extracted_notes` directory is the promotion boundary.

Every generated note includes blocker, hypothesis, testable rule, implementation hint, source book ID/title/location, and priority. Notes remain constrained by the existing total-text and excerpt limits.

### Note Store And Promotion

`hermes_knowledge/note_store.py` owns proposal validation, deduplication, and promotion.

`validate` reads proposed notes, reports valid and invalid counts, and performs no writes to `extracted_notes`. Duplicate detection uses `note_id` and a normalized semantic fingerprint of blocker, hypothesis, and testable rules.

`promote --note-id <id>` performs these checks:

1. exactly one matching proposed note exists;
2. the note passes the complete schema;
3. its source book is present in the current index with the same hash;
4. the note is not already present in extracted notes;
5. the destination is the configured private `extracted_notes` directory.

Promotion writes an `extracted` runtime note atomically. It does not remove the proposal, so provenance remains auditable. There is no bulk or automatic promotion in v1.

### Hermes Retrieval And Provenance

`hermes_knowledge.runtime.load_book_knowledge_context` retains `extracted_notes` as its sole input directory. The returned context adds selected `note_id` values alongside selected book IDs.

`research_lab.hermes.run_hypothesis_generation` records these IDs in immutable Hermes artifacts. Valid hypotheses imported from a prompt enriched by book notes preserve `used_note_ids` in queue provenance. When the deterministic runner executes such a queue record, the IDs are copied into experiment and hypothesis-result metadata.

The IDs are provenance only. They do not alter builders, strategy parameters, validation gates, promotion gates, allocation logic, or execution behavior.

### Feedback

`hermes_knowledge/feedback.py` consumes mockable experiment records containing `used_note_ids`, baseline and resulting walk-forward pass rates, baseline and resulting unseen max drawdown, and gate outcome.

For each note, a deterministic bounded delta is calculated from:

- improvement or deterioration in walk-forward pass rate;
- reduction or increase in unseen max drawdown;
- pass or failure of the existing gate outcome.

The exact weights are repository constants and priorities are clamped to `[0, 100]`. A book adjustment is the bounded aggregate of feedback assigned to its notes. Missing metrics contribute zero; malformed records are rejected; repeated feedback events are deduplicated by event ID. Feedback never changes notes in `extracted_notes` directly. It writes a separate priority overlay consumed by selection and retrieval ranking, preserving immutable source notes and an auditable event history.

## CLI

`python -m hermes_knowledge.cli` provides four commands:

```powershell
python -m hermes_knowledge.cli extract --blocker walk_forward_fail --limit-books 5 --passages-per-book 3
python -m hermes_knowledge.cli validate --blocker walk_forward_fail
python -m hermes_knowledge.cli promote --blocker walk_forward_fail --note-id <note-id>
python -m hermes_knowledge.cli feedback --input <experiment-metadata.jsonl>
```

Path defaults point to `/opt/trading/private/hermes_books` and may be overridden explicitly for tests or nonproduction environments. Limits above the v1 maxima are rejected rather than silently expanded.

`extract` writes only passage candidates and proposed notes. It never writes extracted notes. Its summary includes selected, extracted, proposed, and skipped counts plus short diagnostic codes.

`validate` is read-only. `promote` requires one ID. `feedback` updates only feedback artifacts and priority overlays.

## Failure Handling

- Missing index: command fails before extraction with a concise error and no output mutation.
- Unsupported blocker: command fails validation before opening books.
- Missing sidecar and unavailable PDF reader: skip the book and record `missing_text`.
- Unreadable or empty content: skip the book and record `unreadable_text` or `empty_text`.
- No term matches: produce no candidate for that book and record `no_match`.
- Provider unavailable or error: skip the passage and record provider status.
- Invalid provider JSON or schema: skip the passage and record a bounded diagnostic.
- Duplicate candidate or note: retain the first deterministic record and report the duplicate.
- Promotion validation failure: write nothing to extracted notes.
- Feedback record failure: reject that record without changing priorities for it.

Diagnostics never contain full passages, raw provider responses, credentials, environment values, or private filesystem paths.

## Testing

Focused tests cover:

- blocker-specific deterministic book selection and five-book limit;
- sidecar extraction, localized windows, location provenance, overlap deduplication, and three-passage limit;
- fake-provider success and per-passage failure isolation;
- strict JSON and note schema validation;
- proposed/extracted storage separation;
- read-only validation;
- explicit single-note promotion and duplicate rejection;
- runtime refusal to read proposed notes;
- `used_note_ids` propagation through Hermes artifacts, queue records, and experiment metadata;
- deterministic note/book feedback, clamping, missing metrics, and event deduplication;
- CLI commands and safe path overrides;
- Git safety rules for all private Hermes directories and artifacts.

The focused suite runs before the full pytest suite. Tests use temporary directories, small text fixtures, and fake providers. They do not require private books, network access, provider credentials, daily research, or service management.

## Non-Goals And Safety Constraints

- No changes to trading strategies or strategy builders.
- No changes to validation, promotion, drawdown, allocation, or deployment gates.
- No automatic promotion of notes.
- No runtime use of proposed notes or passage candidates.
- No global extraction across the private library.
- No committing private PDFs, indexes, sidecars, passages, proposed notes, extracted notes, or feedback artifacts.
- No daily research execution.
- No deployment, sync, provider-runtime, systemd, timer, or service changes.
- No service restart.
- No deletion of runtime artifacts.
- No `git reset --hard` or `git clean`.
- No storage of raw prompts, raw provider responses, credentials, or large book excerpts.

## Acceptance Criteria

1. CLI supports `extract`, `validate`, `promote`, and `feedback`.
2. Extraction for `walk_forward_fail` selects at most five relevant books and at most three short passages per book.
3. Extraction writes only private passage candidates and proposed notes.
4. Validation checks proposed notes without promotion.
5. Promotion explicitly names one `note_id` and writes only a valid extracted note.
6. Hermes runtime reads only extracted notes.
7. Selected note IDs propagate into Hermes provenance and deterministic experiment metadata without changing strategies or gates.
8. Feedback deterministically updates separate bounded note/book priority overlays.
9. Focused tests pass, and the full suite passes when feasible.
10. Final reporting lists exact changed files, commands, test results, and safety confirmations.
