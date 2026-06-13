import builtins
from pathlib import Path
import tomllib

import pytest

from hermes_knowledge.book_selector import SelectedBook
from hermes_knowledge.books import BookRecord
from hermes_knowledge.passage_extractor import extract_passages


def _selected(tmp_path: Path, *, source_name: str = "Robust Systems.pdf") -> SelectedBook:
    sha256 = "a" * 64
    book = BookRecord(
        book_id="book-aaaaaaaaaaaa",
        title="Robust Systems",
        source_path=str(tmp_path / source_name),
        source_sha256=sha256,
        size_bytes=100,
    )
    return SelectedBook(book, 20.0, ("robustness",), ("matched:robustness",))


def test_extracts_localized_bounded_passages_from_book_id_sidecar(tmp_path):
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    text_dir.joinpath("book-aaaaaaaaaaaa.txt").write_text(
        "First page about unrelated material.\f"
        + ("context " * 120)
        + "Parameter stability across a walk-forward test reduces overfitting. "
        + ("tail " * 120),
        encoding="utf-8",
    )

    candidates, diagnostics = extract_passages(
        [_selected(tmp_path)],
        "walk_forward_fail",
        text_dir=text_dir,
        passages_per_book=3,
    )

    assert diagnostics == []
    assert candidates
    candidate = candidates[0]
    assert candidate.book_id == "book-aaaaaaaaaaaa"
    assert candidate.location == "page:2"
    assert "parameter stability" in candidate.text.casefold()
    assert len(candidate.text) <= 1200
    assert candidate.passage_id.startswith("passage-")


def test_source_stem_sidecar_is_supported(tmp_path):
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    text_dir.joinpath("Robust Systems.txt").write_text(
        "Regime change can cause model decay in adaptive trading systems.",
        encoding="utf-8",
    )

    candidates, diagnostics = extract_passages(
        [_selected(tmp_path)], "walk_forward_fail", text_dir=text_dir
    )

    assert len(candidates) == 1
    assert diagnostics == []


def test_overlapping_term_matches_are_collapsed_and_limited(tmp_path):
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    text = " ".join(
        [
            "walk-forward robustness parameter stability overfitting",
            "x " * 800,
            "regime model decay adaptive trading",
            "y " * 800,
            "sample splitting trend persistence robustness",
            "z " * 800,
            "volatility normalization walk forward",
        ]
    )
    text_dir.joinpath("book-aaaaaaaaaaaa.txt").write_text(text, encoding="utf-8")

    candidates, _ = extract_passages(
        [_selected(tmp_path)],
        "walk_forward_fail",
        text_dir=text_dir,
        passages_per_book=3,
    )

    assert len(candidates) == 3
    assert len({item.passage_id for item in candidates}) == 3


def test_missing_text_skips_book_without_private_path_in_diagnostic(tmp_path):
    candidates, diagnostics = extract_passages(
        [_selected(tmp_path, source_name="secret-private-name.pdf")],
        "walk_forward_fail",
        text_dir=tmp_path / "missing",
    )

    assert candidates == []
    assert [item.code for item in diagnostics] == ["missing_text"]
    assert "secret-private-name" not in diagnostics[0].message


def test_passage_limit_above_v1_maximum_is_rejected(tmp_path):
    try:
        extract_passages(
            [_selected(tmp_path)],
            "walk_forward_fail",
            text_dir=tmp_path,
            passages_per_book=4,
        )
    except ValueError as exc:
        assert "at most 3" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_pypdf_is_declared_as_bounded_runtime_dependency():
    pyproject = tomllib.loads(
        Path(__file__).parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    )

    assert "pypdf>=6.0,<7" in pyproject["project"]["dependencies"]


def test_pdf_without_sidecar_uses_injected_pdf_reader(tmp_path):
    selected = _selected(tmp_path)
    pdf_path = Path(selected.book.source_path)
    pdf_path.write_bytes(b"%PDF-1.7\n")
    calls = []

    def fake_pdf_reader(path):
        calls.append(path)
        return "Walk-forward robustness improves parameter stability."

    candidates, diagnostics = extract_passages(
        [selected],
        "walk_forward_fail",
        text_dir=tmp_path / "missing-sidecars",
        pdf_reader=fake_pdf_reader,
    )

    assert calls == [pdf_path]
    assert diagnostics == []
    assert len(candidates) == 1


def test_missing_pypdf_reports_pdf_reader_unavailable(tmp_path, monkeypatch):
    selected = _selected(tmp_path)
    Path(selected.book.source_path).write_bytes(b"%PDF-1.7\n")
    original_import = builtins.__import__

    def reject_pypdf(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pypdf":
            raise ImportError("blocked for test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_pypdf)

    candidates, diagnostics = extract_passages(
        [selected],
        "walk_forward_fail",
        text_dir=tmp_path / "missing-sidecars",
    )

    assert candidates == []
    assert [item.code for item in diagnostics] == ["pdf_reader_unavailable"]
    assert diagnostics[0].message == "PDF extractor dependency is unavailable."


def test_pdf_parser_exception_becomes_unreadable_text_diagnostic(tmp_path):
    selected = _selected(tmp_path)
    Path(selected.book.source_path).write_bytes(b"%PDF-1.7\n")

    def broken_pdf_reader(_path):
        raise Exception("simulated PdfReadError")

    candidates, diagnostics = extract_passages(
        [selected],
        "walk_forward_fail",
        text_dir=tmp_path / "missing-sidecars",
        pdf_reader=broken_pdf_reader,
    )

    assert candidates == []
    assert [item.code for item in diagnostics] == ["unreadable_text"]
    assert diagnostics[0].message == "Book text was unavailable."
