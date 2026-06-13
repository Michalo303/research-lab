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
