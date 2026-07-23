from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from hermes_knowledge.runtime import load_book_knowledge_context
from research_lab.hermes.artifacts import read_diagnostic_input, run_artifact_path, write_run_artifact
from research_lab.hermes.providers import ProviderResult, invoke_provider
from research_lab.hermes.schema import execution_fingerprint, schema_prompt_text, validate_hypothesis
from research_lab.llm.hypothesis_adapter import build_hermes_prompt
from research_lab.registry import append_jsonl_batch_atomic
from research_lab.reports import collect_git_info, generate_run_id
from research_lab.risk_management import apply_risk_guidance


def run_hypothesis_generation(
    root: Path,
    *,
    env: Mapping[str, str] | None = None,
    timestamp: datetime | None = None,
    provider_invoker: Callable[[str, str, Mapping[str, str]], ProviderResult] = invoke_provider,
    queue_committer: Callable[[Path, list[dict[str, Any]]], None] = append_jsonl_batch_atomic,
) -> dict[str, Any]:
    root = Path(root)
    current_env = dict(os.environ if env is None else env)
    timestamp_utc = _utc(timestamp)
    git_info = collect_git_info(root)
    run_id = generate_run_id(timestamp_utc, git_info.get("commit"))
    artifact_path = run_artifact_path(root, run_id, timestamp_utc)
    validated_artifact_path = run_artifact_path(root, run_id, timestamp_utc, suffix="validated")
    if artifact_path.exists() or validated_artifact_path.exists():
        existing = artifact_path if artifact_path.exists() else validated_artifact_path
        raise FileExistsError(f"Hermes run artifact already exists for run_id={run_id}: {existing}")
    provider = str(current_env.get("HERMES_PROVIDER", "")).strip().lower() or "not_configured"
    diagnostic = read_diagnostic_input(root)
    input_report_path = _relative(root, diagnostic.path) if diagnostic.path else ""
    book_index_path = Path(
        current_env.get(
            "HERMES_BOOK_INDEX_PATH",
            "/opt/trading/private/hermes_books/index/book_index.json",
        )
    )
    book_notes_dir = Path(
        current_env.get(
            "HERMES_BOOK_NOTES_DIR",
            "/opt/trading/private/hermes_books/extracted_notes",
        )
    )
    book_context = load_book_knowledge_context(
        book_index_path,
        book_notes_dir,
        dominant_blocker=diagnostic.blocker,
    )
    base = {
        "run_id": run_id,
        "timestamp_utc": timestamp_utc.isoformat(),
        "git_commit": git_info.get("commit"),
        "provider": provider,
        "input_report_path": input_report_path,
        "dominant_blocker": diagnostic.blocker,
        "generated_hypotheses_count": 0,
        "imported_hypotheses_count": 0,
        "rejected_hypotheses_count": 0,
        "rejection_reasons": [],
        "output_queue_path": "registry/hypothesis_queue.jsonl",
        "imported_hypothesis_ids": [],
        "queue_impact": {"state": "unchanged", "planned_append_count": 0, "committed_append_count": 0},
        "book_knowledge": {
            "note_count": book_context.note_count,
            "skipped_note_count": book_context.skipped_note_count,
            "selected_book_ids": list(book_context.selected_book_ids),
            "selected_note_ids": list(book_context.selected_note_ids),
            "canonical_blocker_id": book_context.canonical_blocker_id,
            "blocker_diagnostic": book_context.blocker_diagnostic,
        },
    }
    canonical_inputs_available = book_index_path.is_file() and book_notes_dir.is_dir()
    if canonical_inputs_available and not book_context.selected_note_ids:
        return _finish(
            root,
            {
                **base,
                "status": "book_context_unavailable",
                "artifact_phase": "no_queue_change",
                "rejection_reasons": [
                    book_context.blocker_diagnostic or "no_usable_book_notes"
                ],
            },
            timestamp_utc,
        )
    prompt = build_hermes_prompt(
        root,
        diagnostics_text=diagnostic.text,
        input_report_path=input_report_path,
        schema_text=schema_prompt_text(),
        dominant_blocker=diagnostic.blocker,
        book_context=book_context,
    )
    provider_result = provider_invoker(provider, prompt, current_env)
    if provider_result.status != "ok":
        return _finish(
            root,
            {
                **base,
                "status": provider_result.status,
                "artifact_phase": "no_queue_change",
                "rejection_reasons": [provider_result.message] if provider_result.message else [],
            },
            timestamp_utc,
        )
    envelope, envelope_error = _parse_envelope(provider_result.output)
    if envelope_error:
        return _finish(
            root,
            {**base, "status": "invalid_output", "artifact_phase": "no_queue_change", "rejection_reasons": [envelope_error]},
            timestamp_utc,
        )
    proposals = envelope["hypotheses"]
    accepted: list[dict[str, Any]] = []
    rejection_reasons: list[str] = []
    seen = _existing_execution_fingerprints(root)
    for index, proposal in enumerate(proposals, start=1):
        validation = validate_hypothesis(
            proposal, allowed_note_ids=frozenset(book_context.selected_note_ids)
        )
        if not validation.accepted or validation.hypothesis is None:
            rejection_reasons.extend(f"hypothesis_{index}:{reason}" for reason in validation.reasons)
            continue
        if book_context.note_count > 0 and not validation.hypothesis["used_note_ids"]:
            rejection_reasons.append(f"hypothesis_{index}:book_evidence_not_used")
            continue
        fingerprint = execution_fingerprint(validation.hypothesis)
        if fingerprint in seen:
            rejection_reasons.append(f"hypothesis_{index}:duplicate_hypothesis")
            continue
        seen.add(fingerprint)
        accepted.append(
            _queue_payload(
                validation.hypothesis,
                run_id,
                provider,
                index,
                fingerprint,
                used_note_ids=tuple(validation.hypothesis["used_note_ids"]),
            )
        )
    if not accepted:
        status = "completed_with_rejections" if rejection_reasons else "ok"
        return _finish(
            root,
            {
                **base,
                "status": status,
                "artifact_phase": "no_queue_change",
                "generated_hypotheses_count": len(proposals),
                "rejected_hypotheses_count": len(proposals),
                "rejection_reasons": rejection_reasons,
            },
            timestamp_utc,
        )
    planned_ids = [payload["hypothesis_id"] for payload in accepted]
    validated_artifact = {
        **base,
        "status": "validated",
        "artifact_phase": "artifact_written",
        "generated_hypotheses_count": len(proposals),
        "rejected_hypotheses_count": len(proposals) - len(accepted),
        "rejection_reasons": rejection_reasons,
        "planned_imported_hypotheses_count": len(accepted),
        "planned_hypothesis_ids": planned_ids,
        "queue_impact": {"state": "planned", "planned_append_count": len(accepted), "committed_append_count": 0},
    }
    precommit_path = write_run_artifact(root, validated_artifact, timestamp=timestamp_utc, suffix="validated")
    try:
        queue_committer(root / "registry" / "hypothesis_queue.jsonl", accepted)
    except OSError as exc:
        queue_failure_reasons = [*rejection_reasons, f"queue_commit_failed:{exc}"]
        return _finish(
            root,
            {
                **base,
                "status": "queue_commit_failed",
                "artifact_phase": "queue_commit_failed",
                "generated_hypotheses_count": len(proposals),
                "rejected_hypotheses_count": len(proposals),
                "rejection_reasons": queue_failure_reasons,
                "validated_artifact_path": _relative(root, precommit_path),
                "queue_impact": {
                    "state": "commit_failed",
                    "planned_append_count": len(accepted),
                    "committed_append_count": 0,
                },
            },
            timestamp_utc,
        )
    status = "completed_with_rejections" if rejection_reasons else "ok"
    return _finish(
        root,
        {
            **base,
            "status": status,
            "artifact_phase": "queue_committed",
            "generated_hypotheses_count": len(proposals),
            "imported_hypotheses_count": len(accepted),
            "rejected_hypotheses_count": len(proposals) - len(accepted),
            "rejection_reasons": rejection_reasons,
            "imported_hypothesis_ids": planned_ids,
            "validated_artifact_path": _relative(root, precommit_path),
            "queue_impact": {
                "state": "committed",
                "planned_append_count": len(accepted),
                "committed_append_count": len(accepted),
            },
        },
        timestamp_utc,
    )


def _parse_envelope(output: str | None) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(str(output or ""))
    except json.JSONDecodeError:
        return None, "provider_output_invalid_json"
    if not isinstance(payload, dict):
        return None, "provider_output_must_be_object"
    unknown_fields = [key for key in payload if key != "hypotheses"]
    if unknown_fields:
        return None, f"provider_output_unknown_field:{unknown_fields[0]}"
    if not isinstance(payload.get("hypotheses"), list):
        return None, "provider_output_hypotheses_must_be_array"
    if len(payload["hypotheses"]) > 15:
        return None, "provider_output_too_many_hypotheses"
    return payload, None


def _queue_payload(
    hypothesis: dict[str, Any],
    run_id: str,
    provider: str,
    index: int,
    fingerprint: str,
    *,
    used_note_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    family = hypothesis["family"]
    payload = {
        "hypothesis_id": f"HERMES_{run_id}_{index:03d}",
        "title": hypothesis["title"],
        "family": family,
        "asset_class": "CRYPTO" if family == "INTRADAY" else "ETF",
        "timeframe": "15M" if family == "INTRADAY" else "1D",
        "hypothesis": hypothesis["rationale"],
        "rationale": hypothesis["rationale"],
        "builder": hypothesis["builder"],
        "parameters": hypothesis["parameters"],
        "risk_controls": hypothesis["risk_controls"],
        "tags": hypothesis.get("tags", []),
        "source_url": hypothesis.get("source_url", ""),
        "source_title": "hermes",
        "source_key": f"hermes:{fingerprint}",
        "status": "queued",
        "research_only": True,
        "llm_generated": True,
        "hermes_run_id": run_id,
        "hermes_provider": provider,
        "used_note_ids": list(used_note_ids),
    }
    return apply_risk_guidance(payload)


def _existing_execution_fingerprints(root: Path) -> set[str]:
    path = root / "registry" / "hypothesis_queue.jsonl"
    if not path.exists():
        return set()
    fingerprints: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not item.get("llm_generated") or not item.get("builder"):
            continue
        validation = validate_hypothesis(item)
        if validation.accepted and validation.hypothesis is not None:
            fingerprints.add(execution_fingerprint(validation.hypothesis))
    return fingerprints


def _finish(root: Path, artifact: dict[str, Any], timestamp: datetime) -> dict[str, Any]:
    path = write_run_artifact(root, artifact, timestamp=timestamp)
    return {**artifact, "artifact_path": path}


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate Hermes strategy hypotheses.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    outcome = run_hypothesis_generation(args.root)
    print(
        "Hermes run "
        f"status={outcome['status']} generated={outcome['generated_hypotheses_count']} "
        f"imported={outcome['imported_hypotheses_count']} rejected={outcome['rejected_hypotheses_count']} "
        f"artifact={outcome['artifact_path']}"
    )
    return 0 if outcome["status"] in {"ok", "completed_with_rejections", "provider_unavailable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
