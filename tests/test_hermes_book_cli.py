import json

import pytest

import hermes_knowledge.cli as book_cli
from hermes_knowledge.cli import main
from hermes_knowledge.passage_extractor import extract_passages
from research_lab.hermes.providers import ProviderResult


def _provider_note():
    return {
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
    assert "feedback_overlay=missing" in output
    assert "ready_for_new_knihomol_hypothesis_generation=no" in output
    assert "normalized_blocker_counts=drawdown:1,walk_forward_robustness:1" in output
    assert "unknown_blocker_ids=unknown_blocker:1" in output
    assert "short phrase" not in output
    assert "Trading Systems and Methods" not in output


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
