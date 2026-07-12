from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from research_lab.execution.knihomol_readonly_evidence_adapter_v1 import (
    build_knihomol_readonly_evidence_adapter,
)


MODULE_PATH = Path("research_lab/execution/knihomol_readonly_evidence_adapter_v1.py")


def _book(source_sha256: str, title: str = "Risk Control Book") -> dict[str, object]:
    return {
        "name": title,
        "path": f"C:/private/{source_sha256[:12]}.pdf",
        "extension": ".pdf",
        "size_bytes": 1234,
        "sha256": source_sha256,
    }


def _note(note_id: str, *, blocker: str, source_sha256: str, book_id: str | None = None, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "book_id": book_id or f"book-{source_sha256[:12]}",
        "source_title": "Risk Control Book",
        "source_path": f"private-book:{source_sha256[:12]}",
        "source_sha256": source_sha256,
        "concept": "Risk control",
        "hypothesis": "Use bounded defensive overlays.",
        "summary": "A short promoted note.",
        "source_excerpt": "brief excerpt",
        "testable_rules": ["Target lower volatility under stress."],
        "compatible_builders": ["risk_overlay"],
        "asset_classes": ["ETF"],
        "timeframes": ["1D"],
        "expected_edge": "Lower drawdown.",
        "known_failure_modes": ["Can reduce upside in rebounds."],
        "addresses_blockers": [blocker],
        "priority_score": 80.0,
        "note_id": note_id,
        "source_location": "page:10",
        "source_passage_id": "passage-1111111111111111",
        "implementation_hint": "Add a defensive risk overlay.",
    }
    payload.update(overrides)
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "knihomol"
    drawdown_sha = "a" * 64
    walk_forward_sha = "b" * 64
    _write_json(
        corpus / "index" / "book_index.json",
        {
            "schema_version": 1,
            "books": [
                _book(drawdown_sha, "Drawdown Systems"),
                _book(walk_forward_sha, "Walk Forward Systems"),
            ],
        },
    )
    _write_jsonl(
        corpus / "extracted_notes" / "drawdown_fail.jsonl",
        [
            _note("note-1111111111111111", blocker="drawdown_fail", source_sha256=drawdown_sha, source_passage_id="passage-1111111111111111"),
        ],
    )
    _write_jsonl(
        corpus / "extracted_notes" / "walk_forward_fail.jsonl",
        [
            _note(
                "note-2222222222222222",
                blocker="walk_forward_fail",
                source_sha256=walk_forward_sha,
                source_title="Walk Forward Systems",
                source_path=f"private-book:{walk_forward_sha[:12]}",
                source_passage_id="passage-2222222222222222",
                source_location="page:22",
                implementation_hint="Increase rolling OOS coverage.",
                testable_rules=["Increase rolling windows and require stability."],
                compatible_builders=["walk_forward_review"],
            ),
        ],
    )
    return corpus


def _request(corpus: Path, requested_notes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "version": "knihomol_readonly_evidence_adapter_request_v1",
        "corpus_base": str(corpus),
        "requested_notes": requested_notes,
        "evidence_purpose": "robustness_review",
        "provenance": {"source": "unit_test"},
    }


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_knihomol_readonly_evidence_adapter(copy.deepcopy(request))


def test_valid_exact_note_load_multiple_blockers_and_deterministic_order(tmp_path):
    corpus = _corpus(tmp_path)
    request = _request(
        corpus,
        [
            {"note_id": "note-2222222222222222", "blocker": "walk_forward_fail"},
            {"note_id": "note-1111111111111111", "blocker": "drawdown_fail"},
        ],
    )

    first = _run(request)
    second = _run(request)

    assert first == second
    assert first["status"] == "SUCCESS"
    assert [note["note_id"] for note in first["notes"]] == [
        "note-1111111111111111",
        "note-2222222222222222",
    ]
    assert first["notes"][0]["blocker"] == "drawdown_fail"
    assert first["notes"][1]["blocker"] == "walk_forward_fail"
    assert first["writes_performed"] is False
    assert first["promotion_performed"] is False
    assert first["network_used"] is False
    assert first["provider_calls_used"] == 0


def test_missing_note_duplicate_note_duplicated_request_id_and_wrong_blocker_fail(tmp_path):
    corpus = _corpus(tmp_path)

    with pytest.raises(ValueError, match="not found"):
        _run(_request(corpus, [{"note_id": "note-missing0000000", "blocker": "drawdown_fail"}]))

    duplicate_rows = [_note("note-1111111111111111", blocker="drawdown_fail", source_sha256="a" * 64)] * 2
    _write_jsonl(corpus / "extracted_notes" / "drawdown_fail.jsonl", duplicate_rows)
    with pytest.raises(ValueError, match="duplicate note_id"):
        _run(_request(corpus, [{"note_id": "note-1111111111111111", "blocker": "drawdown_fail"}]))

    corpus = _corpus(tmp_path / "fresh")
    with pytest.raises(ValueError, match="must be unique"):
        _run(
            _request(
                corpus,
                [
                    {"note_id": "note-1111111111111111", "blocker": "drawdown_fail"},
                    {"note_id": "note-1111111111111111", "blocker": "drawdown_fail"},
                ],
            )
        )

    with pytest.raises(ValueError, match="not found"):
        _run(_request(corpus, [{"note_id": "note-1111111111111111", "blocker": "walk_forward_fail"}]))


def test_unknown_blocker_expected_book_and_expected_source_sha_fail(tmp_path):
    corpus = _corpus(tmp_path)

    with pytest.raises(ValueError, match="canonical blocker"):
        _run(_request(corpus, [{"note_id": "note-1111111111111111", "blocker": "unknown_blocker"}]))

    with pytest.raises(ValueError, match="expected_book_id mismatch"):
        _run(
            _request(
                corpus,
                [{"note_id": "note-1111111111111111", "blocker": "drawdown_fail", "expected_book_id": "book-ffffffffffff"}],
            )
        )

    with pytest.raises(ValueError, match="expected_source_sha256 mismatch"):
        _run(
            _request(
                corpus,
                [
                    {
                        "note_id": "note-1111111111111111",
                        "blocker": "drawdown_fail",
                        "expected_source_sha256": "f" * 64,
                    }
                ],
            )
        )


def test_malformed_schema_unknown_note_fields_and_missing_source_passage_identity_fail(tmp_path):
    corpus = _corpus(tmp_path)
    _write_jsonl(
        corpus / "extracted_notes" / "drawdown_fail.jsonl",
        [_note("bad-note-id", blocker="drawdown_fail", source_sha256="a" * 64)],
    )
    with pytest.raises(ValueError, match="invalid"):
        _run(_request(corpus, [{"note_id": "bad-note-id", "blocker": "drawdown_fail"}]))

    corpus = _corpus(tmp_path / "unknown")
    bad_note = _note("note-1111111111111111", blocker="drawdown_fail", source_sha256="a" * 64)
    bad_note["unexpected_field"] = True
    _write_jsonl(corpus / "extracted_notes" / "drawdown_fail.jsonl", [bad_note])
    with pytest.raises(ValueError, match="invalid"):
        _run(_request(corpus, [{"note_id": "note-1111111111111111", "blocker": "drawdown_fail"}]))

    corpus = _corpus(tmp_path / "missing-passage")
    missing_passage = _note("note-1111111111111111", blocker="drawdown_fail", source_sha256="a" * 64)
    missing_passage.pop("source_passage_id")
    _write_jsonl(corpus / "extracted_notes" / "drawdown_fail.jsonl", [missing_passage])
    with pytest.raises(ValueError, match="missing required promoted provenance: source_passage_id"):
        _run(_request(corpus, [{"note_id": "note-1111111111111111", "blocker": "drawdown_fail"}]))


def test_legacy_filename_not_loaded_and_canonical_file_missing_fail(tmp_path):
    corpus = _corpus(tmp_path)
    _write_jsonl(
        corpus / "extracted_notes" / "drawdown_fail.jsonl",
        [],
    )
    _write_jsonl(
        corpus / "extracted_notes" / "drawdown_fail_notes_small.jsonl",
        [_note("note-legacy11111111", blocker="drawdown_fail", source_sha256="a" * 64)],
    )
    with pytest.raises(ValueError, match="not found"):
        _run(_request(corpus, [{"note_id": "note-legacy11111111", "blocker": "drawdown_fail"}]))

    corpus = _corpus(tmp_path / "missing-canonical")
    (corpus / "extracted_notes" / "walk_forward_fail.jsonl").unlink()
    with pytest.raises(ValueError, match="required canonical file is missing"):
        _run(_request(corpus, [{"note_id": "note-2222222222222222", "blocker": "walk_forward_fail"}]))


def test_symlink_escape_source_unchanged_and_no_writes(tmp_path, monkeypatch):
    corpus = _corpus(tmp_path)
    drawdown_path = corpus / "extracted_notes" / "drawdown_fail.jsonl"
    outside = tmp_path / "outside.jsonl"
    outside.write_text(drawdown_path.read_text(encoding="utf-8"), encoding="utf-8")
    drawdown_path.unlink()
    try:
        drawdown_path.symlink_to(outside)
    except OSError:
        original_is_symlink = Path.is_symlink
        original_resolve = Path.resolve

        def fake_is_symlink(self: Path) -> bool:
            if self == drawdown_path:
                return True
            return original_is_symlink(self)

        def fake_resolve(self: Path, strict: bool = False) -> Path:
            if self == drawdown_path:
                return outside
            return original_resolve(self, strict=strict)

        monkeypatch.setattr("research_lab.execution.knihomol_readonly_evidence_adapter_v1.Path.is_symlink", fake_is_symlink)
        monkeypatch.setattr("research_lab.execution.knihomol_readonly_evidence_adapter_v1.Path.resolve", fake_resolve)
        drawdown_path.write_text(outside.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="symlink"):
        _run(_request(corpus, [{"note_id": "note-1111111111111111", "blocker": "drawdown_fail"}]))

    corpus = _corpus(tmp_path / "immutability")
    before = {
        path.relative_to(corpus).as_posix(): path.read_bytes()
        for path in corpus.rglob("*")
        if path.is_file()
    }
    names_before = sorted(path.relative_to(corpus).as_posix() for path in corpus.rglob("*"))
    result = _run(
        _request(
            corpus,
            [{"note_id": "note-1111111111111111", "blocker": "drawdown_fail"}],
        )
    )
    after = {
        path.relative_to(corpus).as_posix(): path.read_bytes()
        for path in corpus.rglob("*")
        if path.is_file()
    }
    names_after = sorted(path.relative_to(corpus).as_posix() for path in corpus.rglob("*"))
    assert result["corpus_files_unchanged"] is True
    assert before == after
    assert names_before == names_after


def test_module_does_not_import_provider_or_broker_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.hermes",
        "requests",
        "urllib.request",
        "http",
        "socket",
        "ibapi",
        "ib_insync",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"unexpected forbidden import: {import_name}"
