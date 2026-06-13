import json

import pytest

import hermes_knowledge.extract_notes as extract_notes
from hermes_knowledge.extract_notes import main
from hermes_knowledge.schema import validate_entry


def _write_index(tmp_path):
    path = tmp_path / "book_index.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "books": [
                    {
                        "name": "Risk Management Systems.pdf",
                        "path": "/private/raw/must-not-be-read.pdf",
                        "extension": ".pdf",
                        "size_bytes": 123,
                        "sha256": "b" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _args(index_path, notes_dir, *extra):
    return [
        "--book-index",
        str(index_path),
        "--notes-dir",
        str(notes_dir),
        "--topics",
        "drawdown",
        "walk_forward_robustness",
        "--max-books",
        "1",
        "--max-notes-per-book",
        "2",
        *extra,
    ]


def test_extraction_cli_dry_run_writes_nothing(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "notes"

    assert main(_args(index_path, notes_dir, "--dry-run")) == 0

    assert not notes_dir.exists()


def test_extraction_cli_writes_schema_valid_skeletons(tmp_path):
    index_path = _write_index(tmp_path)
    notes_dir = tmp_path / "notes"

    assert main(_args(index_path, notes_dir)) == 0

    output_files = list(notes_dir.glob("*.jsonl"))
    assert len(output_files) == 1
    rows = [json.loads(line) for line in output_files[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(validate_entry(row) for row in rows)
    assert all(row["priority_score"] == 0 for row in rows)
    assert all(row["source_excerpt"] == "" for row in rows)
    assert "/private/raw/must-not-be-read.pdf" not in output_files[0].read_text(encoding="utf-8")


def test_extraction_refuses_output_inside_source_repository(tmp_path, monkeypatch):
    source_root = tmp_path / "research-lab"
    source_root.mkdir()
    index_path = _write_index(tmp_path)
    monkeypatch.setattr(extract_notes, "SOURCE_ROOT", source_root.resolve())

    with pytest.raises(ValueError, match="outside the source Git repository"):
        extract_notes.prepare_note_skeletons(
            index_path,
            source_root / "private-notes",
            ["drawdown"],
            max_books=1,
            max_notes_per_book=1,
            dry_run=False,
        )

    assert not (source_root / "private-notes").exists()
