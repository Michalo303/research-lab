import json

import pytest

from hermes_knowledge.cli import main
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


def _private_fixture(tmp_path):
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
                        "name": "Trading Systems and Methods.pdf",
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
