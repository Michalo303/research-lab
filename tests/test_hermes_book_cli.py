import json

import pytest

import hermes_knowledge.cli as book_cli
from hermes_knowledge.book_selector import SelectedBook
from hermes_knowledge.books import load_book_index
from hermes_knowledge.cli import main
from hermes_knowledge.passage_extractor import extract_passages
from research_lab.hermes.providers import ProviderResult


def _provider_note(**overrides):
    note = {
        "concept": "Parameter neighborhood stability",
        "hypothesis": "Stable parameter regions improve walk-forward reliability.",
        "summary": "Prefer broad stable regions over isolated optima.",
        "testable_rules": ["Penalize unstable adjacent parameter values."],
        "compatible_builders": ["active_momentum_rotation"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Increase walk-forward pass rate.",
        "known_failure_modes": ["Regime changes can invalidate old regions."],
        "implementation_hint": "Measure adjacent parameter dispersion.",
        "priority_score": 70,
    }
    note.update(overrides)
    return note


def _private_fixture(tmp_path, *, title="Trading Systems and Methods.pdf"):
    base = tmp_path / "hermes_books"
    index = base / "index" / "book_index.json"
    text = base / "text" / "book-aaaaaaaaaaaa.txt"
    index.parent.mkdir(parents=True)
    text.parent.mkdir(parents=True)
    index.write_text(
        json.dumps(
            {
                "books": [
                    {
                        "name": title,
                        "path": str(base / "raw" / "book.pdf"),
                        "size_bytes": 100,
                        "sha256": "a" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    text.write_text(
        "Parameter stability and walk-forward robustness reduce overfitting.",
        encoding="utf-8",
    )
    return base


def _legacy_extracted_note():
    return {
        "book_id": "book-aaaaaaaaaaaa",
        "source_title": "Trading Systems and Methods",
        "source_path": "private-book:book-aaaaaaaaaaaa",
        "source_sha256": "a" * 64,
        "concept": "Legacy unstable note",
        "hypothesis": "Old extracted note missing provenance should be re-extracted safely.",
        "summary": "This row is intentionally missing modern provenance fields.",
        "source_excerpt": "Parameter stability and walk-forward robustness reduce overfitting.",
        "testable_rules": ["Prefer parameter regions that remain stable nearby."],
        "compatible_builders": ["active_momentum_rotation"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Improve walk-forward pass rate.",
        "known_failure_modes": ["Regime changes can still invalidate the thesis."],
        "addresses_blockers": ["walk_forward_fail"],
        "priority_score": 70,
        "implementation_hint": "Re-extract with explicit provenance.",
    }


def _write_reextract_source(base):
    extracted = base / "extracted_notes"
    extracted.mkdir(parents=True, exist_ok=True)
    path = extracted / "walk_forward_fail.jsonl"
    path.write_text(json.dumps(_legacy_extracted_note()) + "\n", encoding="utf-8")
    return path


def _candidate_review_entry(**overrides):
    entry = {
        "note_id": "note-1111111111111111",
        "source_location": "page:12",
        "source_passage_id": "passage-1111111111111111",
        "blocker_tags": ["walk_forward_fail"],
        "thesis": "Stable parameter neighborhoods improve walk-forward reliability.",
        "evidence_summary": "Prefer broad stable regions over isolated optima.",
        "risk_control_hint": "Measure adjacent parameter dispersion.",
    }
    entry.update(overrides)
    return entry


def _resolved_candidate_review_entry(base, **overrides):
    book = load_book_index(base / "index" / "book_index.json")[0]
    passages, diagnostics = extract_passages(
        [SelectedBook(book=book, score=0.0, matched_terms=(), reasons=("reextract_candidate",))],
        "walk_forward_fail",
        text_dir=base / "text",
        passages_per_book=1,
    )
    assert diagnostics == []
    assert len(passages) == 1
    passage = passages[0]
    entry = {
        "note_id": "note-1111111111111111",
        "source_location": passage.location,
        "source_passage_id": passage.passage_id,
        "blocker_tags": ["walk_forward_fail"],
        "thesis": "Stable parameter neighborhoods improve walk-forward reliability.",
        "evidence_summary": "Prefer broad stable regions over isolated optima.",
        "risk_control_hint": "Measure adjacent parameter dispersion.",
    }
    entry.update(overrides)
    return entry


def test_extract_validate_and_promote_flow(tmp_path, capsys):
    base = _private_fixture(tmp_path)

    def fake_provider(provider, prompt, env):
        assert provider == "command"
        return ProviderResult("ok", output=json.dumps(_provider_note()))

    assert (
        main(
            [
                "extract",
                "--base-dir",
                str(base),
                "--blocker",
                "walk_forward_fail",
                "--limit-books",
                "5",
                "--passages-per-book",
                "3",
            ],
            env={"HERMES_PROVIDER": "command"},
            provider_invoker=fake_provider,
        )
        == 0
    )

    proposed_path = base / "proposed_notes" / "walk_forward_fail.jsonl"
    candidate_path = base / "passage_candidates" / "walk_forward_fail.jsonl"
    extracted_path = base / "extracted_notes" / "walk_forward_fail.jsonl"
    assert proposed_path.exists()
    assert candidate_path.exists()
    assert not extracted_path.exists()
    proposal = json.loads(proposed_path.read_text(encoding="utf-8").splitlines()[0])
    note_id = proposal["entry"]["note_id"]
    assert "proposed=1" in capsys.readouterr().out

    before = proposed_path.read_bytes()
    assert main(["validate", "--base-dir", str(base), "--blocker", "walk_forward_fail"]) == 0
    assert proposed_path.read_bytes() == before
    assert not extracted_path.exists()
    assert "valid=1" in capsys.readouterr().out

    assert (
        main(
            [
                "promote",
                "--base-dir",
                str(base),
                "--blocker",
                "walk_forward_fail",
                "--note-id",
                note_id,
            ]
        )
        == 0
    )
    assert extracted_path.exists()
    extracted = json.loads(extracted_path.read_text(encoding="utf-8").splitlines()[0])
    assert extracted["note_id"] == note_id


@pytest.mark.parametrize(
    "extra",
    [
        ["--limit-books", "6"],
        ["--passages-per-book", "4"],
    ],
)
def test_extract_rejects_limits_above_v1_maximum(tmp_path, extra):
    base = _private_fixture(tmp_path)

    with pytest.raises(SystemExit):
        main(
            [
                "extract",
                "--base-dir",
                str(base),
                "--blocker",
                "walk_forward_fail",
                *extra,
            ],
            env={"HERMES_PROVIDER": "command"},
            provider_invoker=lambda *_: ProviderResult("ok", output="{}"),
        )

    assert not (base / "extracted_notes").exists()


def test_extract_uses_bounded_sidecar_preview_for_opaque_book_title(tmp_path):
    base = _private_fixture(tmp_path, title="Collected Essays.pdf")

    assert (
        main(
            [
                "extract",
                "--base-dir",
                str(base),
                "--blocker",
                "walk_forward_fail",
            ],
            env={"HERMES_PROVIDER": "command"},
            provider_invoker=lambda *_: ProviderResult(
                "ok", output=json.dumps(_provider_note())
            ),
        )
        == 0
    )

    assert (base / "proposed_notes" / "walk_forward_fail.jsonl").exists()


def test_extract_reports_bounded_diagnostic_codes(tmp_path, capsys):
    base = _private_fixture(tmp_path)

    assert (
        main(
            [
                "extract",
                "--base-dir",
                str(base),
                "--blocker",
                "walk_forward_fail",
            ],
            env={"HERMES_PROVIDER": "command"},
            provider_invoker=lambda *_: ProviderResult("provider_error"),
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "diagnostics=provider_error:1" in output
    assert "Parameter stability" not in output


@pytest.mark.parametrize(
    ("available", "expected_status", "expected_code"),
    [
        (True, "available", 0),
        (False, "unavailable", 1),
    ],
)
def test_preflight_reports_pdf_extractor_status(
    monkeypatch, capsys, available, expected_status, expected_code
):
    monkeypatch.setattr(
        "hermes_knowledge.cli.pdf_extractor_status",
        lambda: (available, "available" if available else "pdf_reader_unavailable"),
    )

    assert main(["preflight"]) == expected_code
    output = capsys.readouterr().out
    assert f"pdf_extractor=pypdf status={expected_status}" in output
    if not available:
        assert "diagnostic=pdf_reader_unavailable" in output


def test_broken_pdf_skips_provider_and_note_output(tmp_path, monkeypatch, capsys):
    base = _private_fixture(tmp_path)
    sidecar = base / "text" / "book-aaaaaaaaaaaa.txt"
    sidecar.unlink()
    pdf_path = base / "raw" / "book.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")
    provider_calls = []

    def broken_pdf_reader(_path):
        raise Exception("simulated PdfReadError")

    def extract_with_broken_reader(selected, blocker, *, text_dir, passages_per_book):
        return extract_passages(
            selected,
            blocker,
            text_dir=text_dir,
            passages_per_book=passages_per_book,
            pdf_reader=broken_pdf_reader,
        )

    monkeypatch.setattr(book_cli, "extract_passages", extract_with_broken_reader)

    assert (
        main(
            [
                "extract",
                "--base-dir",
                str(base),
                "--blocker",
                "walk_forward_fail",
                "--limit-books",
                "1",
                "--passages-per-book",
                "1",
            ],
            env={"HERMES_PROVIDER": "command"},
            provider_invoker=lambda *args: provider_calls.append(args),
        )
        == 0
    )

    assert provider_calls == []
    assert not (base / "passage_candidates" / "walk_forward_fail.jsonl").exists()
    assert not (base / "proposed_notes" / "walk_forward_fail.jsonl").exists()
    output = capsys.readouterr().out
    assert "passages=0" in output
    assert "proposed=0" in output
    assert "diagnostics=unreadable_text:1" in output


def test_feedback_cli_updates_overlay_without_editing_extracted_note(tmp_path):
    base = _private_fixture(tmp_path)
    extracted = base / "extracted_notes" / "walk_forward_fail.jsonl"
    extracted.parent.mkdir(parents=True)
    entry = {
        "book_id": "book-aaaaaaaaaaaa",
        "source_title": "Trading Systems and Methods",
        "source_path": "private-book:book-aaaaaaaaaaaa",
        "source_sha256": "a" * 64,
        "concept": "Parameter stability",
        "hypothesis": "Stable regions improve walk-forward reliability.",
        "summary": "Prefer broad stable regions.",
        "source_excerpt": "Short evidence.",
        "testable_rules": ["Penalize unstable adjacent values."],
        "compatible_builders": ["active_momentum_rotation"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Improve walk-forward pass rate.",
        "known_failure_modes": ["Regimes can change."],
        "addresses_blockers": ["walk_forward_fail"],
        "priority_score": 70,
        "note_id": "note-1111111111111111",
        "source_location": "page:10",
        "source_passage_id": "passage-1111111111111111",
        "implementation_hint": "Measure adjacent dispersion.",
    }
    extracted.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    before = extracted.read_bytes()
    feedback_input = tmp_path / "feedback.jsonl"
    feedback_input.write_text(
        json.dumps(
            {
                "event_id": "run-1",
                "used_note_ids": ["note-1111111111111111"],
                "baseline_wf_pass_rate": 0.42,
                "wf_pass_rate": 0.58,
                "baseline_max_drawdown": 0.22,
                "max_drawdown": 0.13,
                "gate_passed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "feedback",
                "--base-dir",
                str(base),
                "--input",
                str(feedback_input),
            ]
        )
        == 0
    )

    assert extracted.read_bytes() == before
    priorities = json.loads(
        (base / "feedback" / "priorities.json").read_text(encoding="utf-8")
    )
    assert priorities["notes"]["note-1111111111111111"] == pytest.approx(4.1)


def test_audit_cli_reports_safe_provenance_counts_without_provider_calls(
    tmp_path, monkeypatch, capsys
):
    base = _private_fixture(tmp_path)
    extracted = base / "extracted_notes" / "notes.jsonl"
    extracted.parent.mkdir(parents=True)
    extracted.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "book_id": "book-aaaaaaaaaaaa",
                        "source_title": "Trading Systems and Methods",
                        "source_path": "private-book:book-aaaaaaaaaaaa",
                        "source_sha256": "a" * 64,
                        "concept": "Volatility targeting",
                        "hypothesis": "Lower exposure when realized volatility rises.",
                        "summary": "Prefer lower risk in unstable regimes.",
                        "source_excerpt": "short phrase",
                        "testable_rules": ["Target eight percent annualized volatility."],
                        "compatible_builders": ["long_term_vol_target_cap"],
                        "asset_classes": ["ETF"],
                        "timeframes": ["1D"],
                        "expected_edge": "Contain drawdown in unstable regimes.",
                        "known_failure_modes": ["Fast reversals may cause underexposure."],
                        "addresses_blockers": ["drawdown_fail"],
                        "priority_score": 90,
                        "note_id": "note-1111111111111111",
                        "source_location": "page:10",
                        "source_passage_id": "passage-1111111111111111",
                        "implementation_hint": "Lower exposure as realized volatility rises.",
                    }
                ),
                json.dumps(
                    {
                        "book_id": "book-aaaaaaaaaaaa",
                        "summary": "legacy row",
                        "addresses_blockers": ["walk_forward_fail", "unknown_blocker"],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("audit must not invoke providers"),
    )

    assert main(["audit", "--base-dir", str(base)]) == 1

    output = capsys.readouterr().out
    assert "total_note_rows=2" in output
    assert "current_format_note_rows=1" in output
    assert "legacy_note_rows=1" in output
    assert "rows_eligible_for_provenance_aware_retrieval=1" in output
    assert "rows_excluded_from_promoted_used_note_ids=1" in output
    assert (
        "excluded_by_reason=legacy_format:1,missing_note_id:1,missing_source_location:1,"
        "missing_source_passage_id:1,no_recognized_blocker:0,unknown_only_blockers:0"
    ) in output
    assert "missing_field_counts=note_id:1,source_location:1,source_passage_id:1" in output
    assert "feedback_overlay=missing" in output
    assert "feedback_overlay_expected_path=feedback/priorities.json" in output
    assert "canonical_blocker_preview=none" in output
    assert (
        "remediation_remaining_blockers=legacy_format:1,missing_note_id:1,"
        "missing_source_location:1,missing_source_passage_id:1,no_recognized_blocker:0,"
        "unknown_only_blockers:0,feedback_overlay_missing:1"
    ) in output
    assert "remediation_readiness=blocked" in output
    assert "ready_for_new_knihomol_hypothesis_generation=no" in output
    assert "normalized_blocker_counts=drawdown:1,walk_forward_robustness:1" in output
    assert "unknown_blocker_ids=unknown_blocker:1" in output
    assert "short phrase" not in output
    assert "Trading Systems and Methods" not in output
    assert "private-book:book-aaaaaaaaaaaa" not in output


def test_audit_cli_does_not_modify_private_files_or_feedback(tmp_path):
    base = _private_fixture(tmp_path)
    extracted = base / "extracted_notes" / "notes.jsonl"
    extracted.parent.mkdir(parents=True)
    extracted.write_text(
        json.dumps(
            {
                "book_id": "book-aaaaaaaaaaaa",
                "summary": "legacy row",
                "addresses_blockers": ["drawdown_fail"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    before_notes = extracted.read_bytes()
    feedback_dir = base / "feedback"
    before_feedback_exists = feedback_dir.exists()

    assert main(["audit", "--base-dir", str(base)]) == 1

    assert extracted.read_bytes() == before_notes
    assert feedback_dir.exists() is before_feedback_exists


def test_audit_cli_reports_safe_backfill_plan_aggregates_only(tmp_path, monkeypatch, capsys):
    base = _private_fixture(tmp_path)
    extracted = base / "extracted_notes" / "notes.jsonl"
    extracted.parent.mkdir(parents=True)
    backfillable = {
        "book_id": "book-aaaaaaaaaaaa",
        "source_title": "Trading Systems and Methods",
        "source_path": "private-book:book-aaaaaaaaaaaa",
        "source_sha256": "a" * 64,
        "concept": "Volatility targeting",
        "hypothesis": "Lower exposure when realized volatility rises.",
        "summary": "Prefer lower risk in unstable regimes.",
        "source_excerpt": "short phrase",
        "testable_rules": ["Target eight percent annualized volatility."],
        "compatible_builders": ["long_term_vol_target_cap"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Contain drawdown in unstable regimes.",
        "known_failure_modes": ["Fast reversals may cause underexposure."],
        "addresses_blockers": ["drawdown_fail"],
        "priority_score": 90,
        "source_location": "page:10",
        "source_passage_id": "passage-1111111111111111",
        "implementation_hint": "Lower exposure as realized volatility rises.",
    }
    extracted.write_text(json.dumps(backfillable) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("audit must not invoke providers"),
    )

    assert main(["audit", "--base-dir", str(base)]) == 1

    output = capsys.readouterr().out
    assert "total_rows=1" in output
    assert "rows_with_deterministic_source_file_metadata=1" in output
    assert "rows_with_deterministic_passage_id_source=1" in output
    assert "rows_backfillable_all_required_fields=1" in output
    assert "rows_not_backfillable=0" in output
    assert (
        "not_backfillable_reasons=legacy_format:0,missing_source_file_metadata:0,"
        "ambiguous_source_location:0,missing_passage_anchor:0,duplicate_candidate_identity:0"
    ) in output
    assert "proposed_backfill_fields=note_id:1,source_location:0,source_passage_id:0" in output
    assert (
        "safety_verdict=plan_only,no_write_performed,generation_still_blocked"
    ) in output
    assert "short phrase" not in output
    assert "Trading Systems and Methods" not in output


def test_audit_cli_reports_safe_reextraction_plan_aggregates_only(tmp_path, monkeypatch, capsys):
    base = _private_fixture(tmp_path)
    extracted = base / "extracted_notes" / "notes.jsonl"
    extracted.parent.mkdir(parents=True)
    complete = {
        "book_id": "book-aaaaaaaaaaaa",
        "source_title": "Trading Systems and Methods",
        "source_path": "private-book:book-aaaaaaaaaaaa",
        "source_sha256": "a" * 64,
        "concept": "Volatility targeting",
        "hypothesis": "Lower exposure when realized volatility rises.",
        "summary": "Prefer lower risk in unstable regimes.",
        "source_excerpt": "short phrase",
        "testable_rules": ["Target eight percent annualized volatility."],
        "compatible_builders": ["long_term_vol_target_cap"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Contain drawdown in unstable regimes.",
        "known_failure_modes": ["Fast reversals may cause underexposure."],
        "addresses_blockers": ["drawdown_fail"],
        "priority_score": 90,
        "note_id": "note-1111111111111111",
        "source_location": "page:10",
        "source_passage_id": "passage-1111111111111111",
        "implementation_hint": "Lower exposure as realized volatility rises.",
    }
    unsalvageable = dict(complete)
    unsalvageable["book_id"] = "book-bbbbbbbbbbbb"
    unsalvageable["source_title"] = "Private Inventory Book"
    unsalvageable["source_path"] = "private-book:book-bbbbbbbbbbbb"
    unsalvageable["source_sha256"] = "b" * 64
    unsalvageable["addresses_blockers"] = ["walk_forward_fail"]
    unsalvageable.pop("note_id")
    extracted.write_text(
        json.dumps(complete) + "\n" + json.dumps(unsalvageable) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("audit must not invoke providers"),
    )

    assert main(["audit", "--base-dir", str(base)]) == 1

    output = capsys.readouterr().out
    assert "reextraction_existing_total_rows=2" in output
    assert "reextraction_existing_provenance_complete_rows=1" in output
    assert "reextraction_existing_unsalvageable_rows=1" in output
    assert "reextraction_candidate_source_count=1" in output
    assert "reextraction_rows_with_book_id=1" in output
    assert "reextraction_rows_missing_book_id=0" in output
    assert "reextraction_candidate_blocker_counts=walk_forward_robustness:1" in output
    assert (
        "reextraction_target_schema_required_fields="
        "note_id,source_location,source_passage_id,blocker_tags,thesis,evidence_summary,risk_control_hint"
    ) in output
    assert "reextraction_future_write_required=true" in output
    assert "reextraction_current_pr_write_allowed=false" in output
    assert "reextraction_provider_required_for_future_execution=true" in output
    assert "reextraction_current_pr_provider_calls_allowed=false" in output
    assert "reextraction_generation_still_blocked=true" in output
    assert "reextraction_next_execution_mode=separate_explicit_reextraction_pr" in output
    assert "book-aaaaaaaaaaaa" not in output
    assert "book-bbbbbbbbbbbb" not in output
    assert "Trading Systems and Methods" not in output
    assert "Private Inventory Book" not in output
    assert "private-book:book-bbbbbbbbbbbb" not in output
    assert "short phrase" not in output


def test_reextract_plan_cli_reports_flat_safe_execution_contract(monkeypatch, capsys):
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("reextract-plan must not invoke providers"),
    )

    assert main(["reextract-plan"]) == 0

    output = capsys.readouterr().out.strip()
    assert "command=reextract-plan" in output
    assert "design_only=true" in output
    assert "dry_run_default=true" in output
    assert "provider_allowed=false" in output
    assert "provider_attempted=false" in output
    assert "current_pr_provider_calls_allowed=false" in output
    assert "provider_required_for_future_execution=true" in output
    assert "max_books_required=true" in output
    assert "max_passages_per_book_required=true" in output
    assert "max_notes_required=true" in output
    assert "max_provider_calls_required=true" in output
    assert "no_overwrite_default=true" in output
    assert "output_path_required=true" in output
    assert "timestamped_output_required=true" in output
    assert "schema_validation_required=true" in output
    assert "post_generation_audit_required=true" in output
    assert "promotion_allowed=false" in output
    assert "queue_insertion_allowed=false" in output
    assert "selected_note_ids_unchanged=true" in output
    assert "audit_command_unchanged=true" in output
    assert "generation_still_blocked=true" in output
    assert "next_execution_mode=separate_explicit_reextraction_execution_pr" in output
    assert "Trading Systems and Methods" not in output
    assert "private-book:" not in output
    assert "book-" not in output
    assert "source_location=" not in output
    assert "source_passage_id=" not in output


def test_reextract_plan_cli_does_not_write_private_or_feedback_files(tmp_path):
    base = _private_fixture(tmp_path)
    feedback_dir = base / "feedback"
    extracted_dir = base / "extracted_notes"
    proposed_dir = base / "proposed_notes"
    candidates_dir = base / "passage_candidates"
    before_feedback = feedback_dir.exists()
    before_extracted = extracted_dir.exists()
    before_proposed = proposed_dir.exists()
    before_candidates = candidates_dir.exists()

    assert main(["reextract-plan"]) == 0

    assert feedback_dir.exists() is before_feedback
    assert extracted_dir.exists() is before_extracted
    assert proposed_dir.exists() is before_proposed
    assert candidates_dir.exists() is before_candidates


def test_reextract_run_cli_reports_safe_default_noop(monkeypatch, capsys):
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("reextract-run must not invoke providers"),
    )

    assert (
        main(
            [
                "reextract-run",
                "--output-path",
                "candidate-output.jsonl",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    output = captured.out.strip()
    assert "command=reextract-run" in output
    assert "dry_run=true" in output
    assert "aborted=false" in output
    assert "abort_reason=none" in output
    assert "provider_allowed=false" in output
    assert "provider_attempted=false" in output
    assert "provider_calls_used=0" in output
    assert "max_provider_calls=0" in output
    assert "output_path_required=true" in output
    assert "output_path_provided=true" in output
    assert "output_path_redacted=true" in output
    assert "provider_name_provided=false" in output
    assert "provider_name_redacted=true" in output
    assert "model_name_provided=false" in output
    assert "model_name_redacted=true" in output
    assert "timestamped_output_required=true" in output
    assert "overwrite_allowed=false" in output
    assert "notes_generated=0" in output
    assert "notes_written=0" in output
    assert "notes_schema_valid=0" in output
    assert "notes_schema_invalid=0" in output
    assert "post_generation_audit_required=true" in output
    assert "post_generation_audit_run=false" in output
    assert "promotion_allowed=false" in output
    assert "queue_insertion_allowed=false" in output
    assert "generation_still_blocked=true" in output
    assert "private-book:" not in output
    assert "book-" not in output
    assert "Trading Systems and Methods" not in output
    assert "candidate-output.jsonl" not in output
    assert "candidate-output.jsonl" not in captured.err


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (
            ["--output-path", "candidate-output.jsonl", "--dry-run", "false"],
            "dry_run_required",
        ),
        (
            ["--output-path", "candidate-output.jsonl", "--max-provider-calls", "1"],
            "allow_provider_calls_required",
        ),
        (
            [
                "--output-path",
                "candidate-output.jsonl",
                "--allow-provider-calls",
                "true",
            ],
            "provider_required",
        ),
        (
            [],
            "output_path_required",
        ),
        (
            ["--output-path", "candidate-output.jsonl", "--overwrite"],
            "overwrite_forbidden",
        ),
        (
            ["--output-path", "candidate-output.jsonl", "--promotion"],
            "promotion_forbidden",
        ),
        (
            ["--output-path", "candidate-output.jsonl", "--queue-insertion"],
            "queue_insertion_forbidden",
        ),
    ],
)
def test_reextract_run_cli_fails_closed(monkeypatch, capsys, args, reason):
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("reextract-run must not invoke providers"),
    )

    assert main(["reextract-run", *args]) == 1

    captured = capsys.readouterr()
    output = captured.out.strip()
    assert "command=reextract-run" in output
    assert "aborted=true" in output
    assert f"abort_reason={reason}" in output
    assert "provider_attempted=false" in output
    assert "provider_calls_used=0" in output
    assert "output_path_required=true" in output
    assert (
        "output_path_provided=false" in output
        if reason == "output_path_required"
        else "output_path_provided=true" in output
    )
    assert "output_path_redacted=true" in output
    assert "provider_name_redacted=true" in output
    assert "model_name_redacted=true" in output
    assert "candidate-output.jsonl" not in output
    assert "candidate-output.jsonl" not in captured.err


@pytest.mark.parametrize(
    "blocked_output",
    [
        ("raw", "candidate.jsonl"),
        ("text", "candidate.jsonl"),
        ("index", "candidate.jsonl"),
        ("extracted_notes", "candidate.jsonl"),
        ("feedback", "candidate.jsonl"),
        ("registry", "candidate.jsonl"),
        ("reports", "candidate.jsonl"),
        ("queue", "candidate.jsonl"),
    ],
)
def test_reextract_run_cli_rejects_private_tree_candidate_output_paths(
    tmp_path, monkeypatch, capsys, blocked_output
):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)
    blocked_dir, filename = blocked_output
    target = base / blocked_dir / filename
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("provider must not run for blocked output roots"),
    )

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(target),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    output = captured.out.strip()
    assert "abort_reason=isolated_output_path_required" in output
    assert "provider_attempted=false" in output
    assert str(target) not in output
    assert str(target) not in captured.err


def test_reextract_run_cli_live_dry_run_requires_model(tmp_path, monkeypatch, capsys):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("provider must not run when model gate is missing"),
    )

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(tmp_path / "candidate-output.jsonl"),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=model_required" in output
    assert "provider_attempted=false" in output
    assert "provider_calls_used=0" in output


def test_reextract_run_cli_live_dry_run_enforces_single_provider_call(tmp_path, monkeypatch, capsys):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("provider must not run when max call gate is invalid"),
    )

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(tmp_path / "candidate-output.jsonl"),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "2",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=max_provider_calls_must_equal_one" in output
    assert "provider_attempted=false" in output


def test_reextract_run_cli_live_dry_run_fails_closed_on_provider_failure(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    active_path = _write_reextract_source(base)
    output_path = tmp_path / "candidate-output.jsonl"
    before = active_path.read_bytes()

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(output_path),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ],
            provider_invoker=lambda *_args: ProviderResult("provider_error", message="failed"),
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=provider_failure" in output
    assert "provider_attempted=true" in output
    assert "provider_calls_used=1" in output
    assert not output_path.exists()
    assert active_path.read_bytes() == before


def test_reextract_run_cli_live_dry_run_reports_fixed_provider_reason_only(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    active_path = _write_reextract_source(base)
    output_path = tmp_path / "candidate-output.jsonl"
    before = active_path.read_bytes()

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(output_path),
                "--allow-provider-calls",
                "true",
                "--provider",
                "openai_compatible",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ],
            provider_invoker=lambda *_args: ProviderResult(
                "provider_error",
                message="OpenAI-compatible provider request failed: unauthorized",
                reason="authentication_failure",
            ),
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=provider_failure" in output
    assert "diagnostic_code=provider_error" in output
    assert "diagnostic_reason=authentication_failure" in output
    assert "unauthorized" not in output
    assert "test-model" not in output
    assert not output_path.exists()
    assert active_path.read_bytes() == before


def test_reextract_run_cli_live_dry_run_fails_closed_on_invalid_provider_response(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(tmp_path / "candidate-output.jsonl"),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ],
            provider_invoker=lambda *_args: ProviderResult("ok", output="not json"),
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=invalid_provider_response" in output
    assert "notes_generated=0" in output
    assert "notes_written=0" in output


def test_reextract_run_cli_live_dry_run_fails_closed_on_schema_validation(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(tmp_path / "candidate-output.jsonl"),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ],
            provider_invoker=lambda *_args: ProviderResult(
                "ok",
                output=json.dumps(_provider_note(priority_score=999)),
            ),
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=schema_validation_failed" in output
    assert "diagnostic_code=schema_violation" in output
    assert "diagnostic_reason=invalid_field_value" in output
    assert "notes_schema_invalid=1" in output
    assert "notes_written=0" in output


def test_reextract_run_cli_live_dry_run_reports_redacted_schema_diagnostic_only(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)
    provider_note = _provider_note()
    provider_note.pop("summary")

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(tmp_path / "candidate-output.jsonl"),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ],
            provider_invoker=lambda *_args: ProviderResult(
                "ok",
                output=json.dumps(provider_note),
            ),
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=schema_validation_failed" in output
    assert "diagnostic_code=schema_violation" in output
    assert "diagnostic_reason=missing_required_field" in output
    assert "summary" not in output
    assert "Parameter neighborhood stability" not in output
    assert "candidate-output.jsonl" not in output


def test_reextract_run_cli_live_dry_run_requires_isolated_output_path(tmp_path, monkeypatch, capsys):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("provider must not run for non-isolated output"),
    )

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(base / "extracted_notes" / "candidate-output.jsonl"),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=isolated_output_path_required" in output
    assert "provider_attempted=false" in output


def test_reextract_run_cli_live_dry_run_never_overwrites_existing_candidate_output(tmp_path, monkeypatch, capsys):
    base = _private_fixture(tmp_path)
    _write_reextract_source(base)
    output_path = tmp_path / "candidate-output.jsonl"
    output_path.write_text("{\"existing\": true}\n", encoding="utf-8")
    before = output_path.read_bytes()
    monkeypatch.setattr(
        "hermes_knowledge.cli.invoke_provider",
        lambda *_args, **_kwargs: pytest.fail("provider must not run when overwrite is false"),
    )

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(output_path),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out.strip()
    assert "abort_reason=overwrite_forbidden" in output
    assert output_path.read_bytes() == before


def test_reextract_run_cli_live_dry_run_writes_isolated_candidate_output_and_audits_it(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    active_path = _write_reextract_source(base)
    before = active_path.read_bytes()
    output_path = tmp_path / "candidate-output.jsonl"

    assert (
        main(
            [
                "reextract-run",
                "--base-dir",
                str(base),
                "--output-path",
                str(output_path),
                "--allow-provider-calls",
                "true",
                "--provider",
                "command",
                "--model",
                "test-model",
                "--max-provider-calls",
                "1",
                "--max-books",
                "1",
                "--max-passages-per-book",
                "1",
                "--max-notes",
                "1",
            ],
            provider_invoker=lambda *_args: ProviderResult("ok", output=json.dumps(_provider_note())),
        )
        == 0
    )

    output = capsys.readouterr().out.strip()
    row = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert "abort_reason=none" in output
    assert "provider_attempted=true" in output
    assert "provider_calls_used=1" in output
    assert "notes_generated=1" in output
    assert "notes_written=1" in output
    assert "notes_schema_valid=1" in output
    assert "notes_schema_invalid=0" in output
    assert "post_generation_audit_required=true" in output
    assert "post_generation_audit_run=true" in output
    assert "candidate_readiness=ready" in output
    assert "provider_name_provided=true" in output
    assert "provider_name_redacted=true" in output
    assert "model_name_provided=true" in output
    assert "model_name_redacted=true" in output
    assert "promotion_allowed=false" in output
    assert "queue_insertion_allowed=false" in output
    assert "active_generation_still_blocked=true" in output
    assert row["note_id"].startswith("note-")
    assert row["blocker_tags"] == ["walk_forward_fail"]
    assert row["thesis"] == "Stable parameter regions improve walk-forward reliability."
    assert row["evidence_summary"] == "Prefer broad stable regions over isolated optima."
    assert row["risk_control_hint"] == "Measure adjacent parameter dispersion."
    assert set(row) == {
        "note_id",
        "source_location",
        "source_passage_id",
        "blocker_tags",
        "thesis",
        "evidence_summary",
        "risk_control_hint",
    }
    assert "source_excerpt" not in row
    assert "Parameter stability and walk-forward robustness reduce overfitting." not in output_path.read_text(encoding="utf-8")
    assert active_path.read_bytes() == before
    assert str(output_path) not in output
    assert str(base / "raw" / "book.pdf") not in output
    assert "command" not in output_path.read_text(encoding="utf-8")
    assert "Trading Systems and Methods" not in output
    assert "Parameter stability and walk-forward robustness reduce overfitting." not in output
    assert "test-model" not in output_path.read_text(encoding="utf-8")
    assert "test-model" not in output


def test_reextract_run_cli_does_not_write_private_or_feedback_files(tmp_path):
    base = _private_fixture(tmp_path)
    feedback_dir = base / "feedback"
    extracted_dir = base / "extracted_notes"
    proposed_dir = base / "proposed_notes"
    candidates_dir = base / "passage_candidates"
    before_feedback = feedback_dir.exists()
    before_extracted = extracted_dir.exists()
    before_proposed = proposed_dir.exists()
    before_candidates = candidates_dir.exists()

    assert (
        main(
            [
                "reextract-run",
                "--output-path",
                "candidate-output.jsonl",
            ]
        )
        == 0
    )

    assert feedback_dir.exists() is before_feedback
    assert extracted_dir.exists() is before_extracted
    assert proposed_dir.exists() is before_proposed
    assert candidates_dir.exists() is before_candidates


def test_reextract_review_cli_emits_deterministic_valid_report(tmp_path, capsys):
    input_path = tmp_path / "candidate-output.jsonl"
    input_path.write_text(json.dumps(_candidate_review_entry()) + "\n", encoding="utf-8")

    assert main(["reextract-review", "--input-path", str(input_path)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "active_generation_still_blocked": True,
        "blocker_tags_seen": ["walk_forward_fail"],
        "duplicate_note_ids": [],
        "invalid_candidates": 0,
        "promotion_allowed": False,
        "queue_insertion_allowed": False,
        "review_valid": True,
        "total_candidates": 1,
        "valid_candidates": 1,
    }
    assert str(input_path) not in json.dumps(report)


def test_reextract_review_cli_rejects_empty_candidate_file(tmp_path, capsys):
    input_path = tmp_path / "candidate-output.jsonl"
    input_path.write_text("", encoding="utf-8")

    assert main(["reextract-review", "--input-path", str(input_path)]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "active_generation_still_blocked": True,
        "blocker_tags_seen": [],
        "duplicate_note_ids": [],
        "invalid_candidates": 0,
        "promotion_allowed": False,
        "queue_insertion_allowed": False,
        "review_valid": False,
        "total_candidates": 0,
        "valid_candidates": 0,
    }


def test_reextract_review_cli_emits_failed_report_without_touching_active_notes(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    active_path = _write_reextract_source(base)
    before = active_path.read_bytes()
    input_path = tmp_path / "candidate-output.jsonl"
    row = _candidate_review_entry(source_excerpt="private excerpt")
    input_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert main(["reextract-review", "--input-path", str(input_path)]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "active_generation_still_blocked": True,
        "blocker_tags_seen": [],
        "duplicate_note_ids": [],
        "invalid_candidates": 1,
        "promotion_allowed": False,
        "queue_insertion_allowed": False,
        "review_valid": False,
        "total_candidates": 1,
        "valid_candidates": 0,
    }
    assert active_path.read_bytes() == before


def test_reextract_promote_cli_rejects_empty_candidate_file(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    input_path = tmp_path / "candidate-output.jsonl"
    input_path.write_text("", encoding="utf-8")

    assert (
        main(
            [
                "reextract-promote",
                "--base-dir",
                str(base),
                "--input-path",
                str(input_path),
                "--note-id",
                "note-1111111111111111",
            ]
        )
        == 1
    )

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "active_generation_still_blocked": True,
        "explicit_promotion_used": False,
        "promoted_note_id": None,
        "promotion_allowed": False,
        "promotion_attempted": True,
        "promotion_succeeded": False,
        "provider_calls_used": 0,
        "queue_insertion_allowed": False,
        "target_blocker": None,
        "target_file_relative": None,
    }


def test_reextract_promote_cli_emits_success_json_and_writes_one_active_note(tmp_path, capsys):
    base = _private_fixture(tmp_path)
    extracted_dir = base / "extracted_notes"
    input_path = tmp_path / "candidate-output.jsonl"
    input_path.write_text(
        json.dumps(_resolved_candidate_review_entry(base)) + "\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "reextract-promote",
                "--base-dir",
                str(base),
                "--input-path",
                str(input_path),
                "--note-id",
                "note-1111111111111111",
            ]
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    target_path = extracted_dir / "walk_forward_robustness.jsonl"
    rows = [
        json.loads(line)
        for line in target_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert report == {
        "active_generation_still_blocked": True,
        "explicit_promotion_used": True,
        "promoted_note_id": "note-1111111111111111",
        "promotion_allowed": True,
        "promotion_attempted": True,
        "promotion_succeeded": True,
        "provider_calls_used": 0,
        "queue_insertion_allowed": False,
        "target_blocker": "walk_forward_robustness",
        "target_file_relative": "extracted_notes/walk_forward_robustness.jsonl",
    }
    assert len(rows) == 1
    assert str(target_path) not in json.dumps(report)


def test_reextract_promote_cli_emits_failed_json_for_invalid_candidate_file_without_touching_active_notes(
    tmp_path, capsys
):
    base = _private_fixture(tmp_path)
    active_path = _write_reextract_source(base)
    before = active_path.read_bytes()
    input_path = tmp_path / "candidate-output.jsonl"
    input_path.write_text(
        json.dumps(_candidate_review_entry(source_excerpt="private excerpt")) + "\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "reextract-promote",
                "--base-dir",
                str(base),
                "--input-path",
                str(input_path),
                "--note-id",
                "note-1111111111111111",
            ]
        )
        == 1
    )

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "active_generation_still_blocked": True,
        "explicit_promotion_used": False,
        "promoted_note_id": None,
        "promotion_allowed": False,
        "promotion_attempted": True,
        "promotion_succeeded": False,
        "provider_calls_used": 0,
        "queue_insertion_allowed": False,
        "target_blocker": None,
        "target_file_relative": None,
    }
    assert active_path.read_bytes() == before


def test_reextract_promote_cli_fails_when_current_source_identity_cannot_be_resolved(
    tmp_path, capsys
):
    base = _private_fixture(tmp_path)
    input_path = tmp_path / "candidate-output.jsonl"
    unresolved = _resolved_candidate_review_entry(base, source_passage_id="passage-ffffffffffffffff")
    input_path.write_text(json.dumps(unresolved) + "\n", encoding="utf-8")

    assert (
        main(
            [
                "reextract-promote",
                "--base-dir",
                str(base),
                "--input-path",
                str(input_path),
                "--note-id",
                "note-1111111111111111",
            ]
        )
        == 1
    )

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "active_generation_still_blocked": True,
        "explicit_promotion_used": False,
        "promoted_note_id": None,
        "promotion_allowed": False,
        "promotion_attempted": True,
        "promotion_succeeded": False,
        "provider_calls_used": 0,
        "queue_insertion_allowed": False,
        "target_blocker": None,
        "target_file_relative": None,
    }
