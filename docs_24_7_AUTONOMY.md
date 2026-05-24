# 24/7 Autonomy Design

The research lab should run on Hetzner, not on a notebook.

## Architecture

Use four independent loops:

1. Hourly source scan
   - Reads `config/research_sources.json`.
   - Collects paper/RSS items only when `RESEARCH_LAB_NETWORK=1`.
   - Writes `registry/source_items.jsonl`.
   - Generates a broad creative idea pool in `registry/creative_ideas.jsonl`.
   - Promotes selected ideas into queued hypotheses in `registry/hypothesis_queue.jsonl`.

2. Daily deterministic validation
   - Runs baseline and queued strategy tests.
   - Saves JSON, CSV, and Markdown artifacts.
   - Rejects weak results before any allocation suggestion.

3. Weekly deep validation
   - Reserved for walk-forward tests, parameter stability, Monte Carlo, and portfolio combinations.

4. Daily self-improvement
   - Reads failures and queue state.
   - Writes a short engineering/research audit.
   - Suggests what the lab itself needs next.

## LLM/Hermes Layer

Hermes or another LLM should be used as a creativity layer only:

- summarize papers
- propose hypotheses
- suggest new parameter neighborhoods
- explain failure modes
- write research notes

The system deliberately separates divergent and convergent thinking:

- divergent: generate many creative hypotheses cheaply
- convergent: run deterministic tests, penalize parameters, reject weak results

It must not:

- place trades
- edit production bot directories
- promote a strategy without deterministic results
- overwrite registries
- hide rejected experiments

The formal contract is stored in `HERMES_ROLE_CONTRACT.md`.

## Source Policy

Good sources:

- arXiv and other open paper feeds
- public RSS feeds from quant research blogs
- forum threads used as inspiration only
- vendor docs and public dataset descriptions

Bad sources:

- private forum scraping without permission
- copying paid strategy code without license review
- treating social popularity as evidence
- running web automation that violates site rules

## Server Principle

Prefer systemd timers over a single daemon. Each cycle is resumable, observable, and has its own log in `journalctl`.
