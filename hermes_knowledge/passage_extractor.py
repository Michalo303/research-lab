"""Extract short blocker-relevant evidence windows from selected books only."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
from typing import Callable, Iterable

from hermes_knowledge.book_selector import SelectedBook
from hermes_knowledge.blocker_taxonomy import get_blocker_definition


MAX_PASSAGES_PER_BOOK = 3
MAX_PASSAGE_CHARS = 1200
WINDOW_RADIUS = MAX_PASSAGE_CHARS // 2


@dataclass(frozen=True)
class PassageCandidate:
    passage_id: str
    book_id: str
    source_title: str
    source_sha256: str
    blocker: str
    location: str
    matched_terms: tuple[str, ...]
    text: str
    extraction_reason: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["matched_terms"] = list(self.matched_terms)
        return payload


@dataclass(frozen=True)
class ExtractionDiagnostic:
    book_id: str
    code: str
    message: str


PdfReader = Callable[[Path], str]


def pdf_extractor_status() -> tuple[bool, str]:
    try:
        from pypdf import PdfReader as _Reader  # noqa: F401
    except ImportError:
        return False, "pdf_reader_unavailable"
    return True, "available"


def _default_pdf_reader(path: Path) -> str:
    try:
        from pypdf import PdfReader as Reader
    except ImportError as exc:
        raise RuntimeError("pdf_reader_unavailable") from exc
    reader = Reader(str(path))
    return "\f".join((page.extract_text() or "") for page in reader.pages)


def _sidecar_path(book: SelectedBook, text_dir: Path) -> Path | None:
    candidates = (
        text_dir / f"{book.book.book_id}.txt",
        text_dir / f"{Path(book.book.source_path).stem}.txt",
    )
    return next((path for path in candidates if path.is_file()), None)


def _read_text(
    selected: SelectedBook, text_dir: Path, pdf_reader: PdfReader
) -> tuple[str | None, str | None]:
    sidecar = _sidecar_path(selected, text_dir)
    if sidecar is not None:
        try:
            return sidecar.read_text(encoding="utf-8"), None
        except (OSError, UnicodeError):
            return None, "unreadable_text"
    source = Path(selected.book.source_path)
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        return None, "missing_text"
    try:
        return pdf_reader(source), None
    except RuntimeError as exc:
        if str(exc) == "pdf_reader_unavailable":
            return None, "pdf_reader_unavailable"
        return None, "unreadable_text"
    except (OSError, ValueError):
        return None, "unreadable_text"


def _term_pattern(term: str) -> re.Pattern[str]:
    words = re.findall(r"[a-z0-9]+", term.casefold())
    return re.compile(r"\b" + r"[^a-z0-9]+".join(map(re.escape, words)) + r"\b")


def _match_positions(text: str, term_weights: dict[str, float] | object):
    lowered = text.casefold()
    matches: list[tuple[int, str, float]] = []
    for term, weight in term_weights.items():  # type: ignore[union-attr]
        for match in _term_pattern(term).finditer(lowered):
            matches.append((match.start(), term, float(weight)))
    return sorted(matches, key=lambda item: (item[0], -item[2], item[1]))


def _windows(text: str, matches: list[tuple[int, str, float]]):
    clusters: list[list[tuple[int, str, float]]] = []
    for match in matches:
        if clusters and match[0] - clusters[-1][0][0] < WINDOW_RADIUS:
            clusters[-1].append(match)
        else:
            clusters.append([match])
    windows: list[tuple[int, int, list[tuple[int, str, float]]]] = []
    for cluster in clusters:
        center = (cluster[0][0] + cluster[-1][0]) // 2
        start = max(0, center - WINDOW_RADIUS)
        end = min(len(text), start + MAX_PASSAGE_CHARS)
        start = max(0, end - MAX_PASSAGE_CHARS)
        windows.append((start, end, cluster))
    return windows


def _location(text: str, position: int) -> str:
    if "\f" in text:
        return f"page:{text.count(chr(12), 0, position) + 1}"
    return f"text-offset:{position}"


def _candidate(
    selected: SelectedBook,
    blocker: str,
    text: str,
    window: tuple[int, int, list[tuple[int, str, float]]],
) -> PassageCandidate:
    start, end, matches = window
    excerpt = re.sub(r"\s+", " ", text[start:end]).strip()[:MAX_PASSAGE_CHARS]
    terms = tuple(dict.fromkeys(term for _, term, _ in matches))
    location = _location(text, matches[0][0])
    digest_input = "\n".join(
        (selected.book.book_id, blocker, location, excerpt.casefold())
    ).encode("utf-8")
    passage_id = f"passage-{hashlib.sha256(digest_input).hexdigest()[:16]}"
    return PassageCandidate(
        passage_id=passage_id,
        book_id=selected.book.book_id,
        source_title=selected.book.title,
        source_sha256=selected.book.source_sha256,
        blocker=blocker,
        location=location,
        matched_terms=terms,
        text=excerpt,
        extraction_reason=f"Matched blocker terms: {', '.join(terms)}",
    )


def extract_passages(
    selected_books: Iterable[SelectedBook],
    blocker: str,
    *,
    text_dir: str | Path,
    passages_per_book: int = MAX_PASSAGES_PER_BOOK,
    pdf_reader: PdfReader = _default_pdf_reader,
) -> tuple[list[PassageCandidate], list[ExtractionDiagnostic]]:
    if not 1 <= passages_per_book <= MAX_PASSAGES_PER_BOOK:
        raise ValueError(
            f"passages_per_book must be between 1 and at most {MAX_PASSAGES_PER_BOOK}"
        )
    definition = get_blocker_definition(blocker)
    candidates: list[PassageCandidate] = []
    diagnostics: list[ExtractionDiagnostic] = []
    for selected in selected_books:
        text, error = _read_text(selected, Path(text_dir), pdf_reader)
        if error:
            message = (
                "PDF extractor dependency is unavailable."
                if error == "pdf_reader_unavailable"
                else "Book text was unavailable."
            )
            diagnostics.append(
                ExtractionDiagnostic(
                    selected.book.book_id, error, message
                )
            )
            continue
        if not text or not text.strip():
            diagnostics.append(
                ExtractionDiagnostic(
                    selected.book.book_id, "empty_text", "Book text was empty."
                )
            )
            continue
        matches = _match_positions(text, definition.term_weights)
        if not matches:
            diagnostics.append(
                ExtractionDiagnostic(
                    selected.book.book_id,
                    "no_match",
                    "No blocker evidence term matched.",
                )
            )
            continue
        ranked = sorted(
            _windows(text, matches),
            key=lambda window: (
                -sum(weight for _, _, weight in window[2]),
                window[0],
            ),
        )[:passages_per_book]
        candidates.extend(
            _candidate(selected, definition.name, text, window) for window in ranked
        )
    return candidates, diagnostics
