from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from hermes_knowledge.blocker_taxonomy import get_blocker_definition
from hermes_knowledge.book_selector import SelectedBook, load_text_previews, select_books_for_blocker
from hermes_knowledge.books import load_book_index
from hermes_knowledge.passage_extractor import (
    MAX_PASSAGES_PER_BOOK,
    ExtractionDiagnostic,
    PassageCandidate,
    _candidate,
    _match_positions,
    _sidecar_path,
    _windows,
)
from research_lab.hermes.providers import ProviderResult, invoke_provider


DEFAULT_MAX_BOOKS = 3
DEFAULT_MAX_PAGES_PER_BOOK = 40
DEFAULT_MAX_NOTES = 20
REQUEST_VERSION = "book_extraction_request_v1"
REQUEST_TYPE = "extract_book_notes_for_blocker"
REQUESTED_WORKER = "hermes_book_extraction"
SUPPORTED_BLOCKER = "drawdown_fail"
REQUIRED_PROVIDER_FIELDS = (
    "extracted_claim",
    "trading_hypothesis",
    "why_relevant_to_blocker",
    "implementation_hint",
    "risk_controls",
    "validation_hint",
    "source_excerpt",
    "confidence",
)
SOURCE_ROOT = Path(__file__).resolve().parents[1]
UNSAFE_NOTE_MARKERS = (
    "```",
    "def ",
    "class ",
    "import ",
    "subprocess",
    "os.system",
    "backtest(",
    "run_backtest",
    "deployment_gate",
    "promote",
    "promotion",
    "registry write",
    "execute trade",
    "broker",
    "ibkr",
)

ProviderInvoker = Callable[[str, str, Mapping[str, str]], ProviderResult]


class UnsafeNoteError(ValueError):
    """Raised when provider output contains code or automation instructions."""


def load_request(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    return payload


def run_book_extraction_request(
    *,
    request_path: str | Path,
    book_index_path: str | Path,
    books_root: str | Path,
    output_jsonl: str | Path,
    audit_json: str | Path,
    max_books: int = DEFAULT_MAX_BOOKS,
    max_pages_per_book: int = DEFAULT_MAX_PAGES_PER_BOOK,
    max_notes: int = DEFAULT_MAX_NOTES,
    env: Mapping[str, str] | None = None,
    provider_invoker: ProviderInvoker = invoke_provider,
) -> tuple[int, dict[str, Any]]:
    _validate_limits(max_books=max_books, max_pages_per_book=max_pages_per_book, max_notes=max_notes)
    request = load_request(request_path)
    blocker = _validate_request(request)
    current_env = dict(env or {})
    provider_name = str(current_env.get("HERMES_PROVIDER", "")).strip().casefold()
    root = Path(books_root).resolve()
    _ensure_output_path(Path(output_jsonl))
    _ensure_output_path(Path(audit_json))

    books = load_book_index(book_index_path)
    previews = load_text_previews(books, root / "text")
    selected = select_books_for_blocker(
        books,
        blocker,
        limit=max_books,
        text_previews=previews,
    )
    selected, selection_errors = _filter_selected_books_to_root(selected, root)
    candidates, diagnostics, pages_scanned_by_book, pdf_parser_used = _extract_bounded_passages(
        selected,
        blocker,
        books_root=root,
        max_pages_per_book=max_pages_per_book,
        max_notes=max_notes,
    )
    notes: list[dict[str, Any]] = []
    errors = selection_errors + [
        {
            "book_id": item.book_id,
            "code": item.code,
            "message": item.message,
        }
        for item in diagnostics
    ]
    for candidate in candidates:
        if len(notes) >= max_notes:
            break
        result = provider_invoker(provider_name, _prompt(candidate), current_env)
        if result.status != "ok" or not result.output:
            errors.append(
                {
                    "book_id": candidate.book_id,
                    "code": result.status or "provider_error",
                    "message": result.message or "provider did not return usable output",
                }
            )
            continue
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            errors.append(
                {
                    "book_id": candidate.book_id,
                    "code": "invalid_json",
                    "message": "provider output was not valid JSON",
                }
            )
            continue
        try:
            note = _build_note(candidate, payload, blocker=blocker)
        except UnsafeNoteError as exc:
            errors.append(
                {
                    "book_id": candidate.book_id,
                    "code": "unsafe_note_content",
                    "message": str(exc),
                }
            )
            continue
        except (TypeError, ValueError) as exc:
            errors.append(
                {
                    "book_id": candidate.book_id,
                    "code": "invalid_note",
                    "message": str(exc),
                }
            )
            continue
        notes.append(note)

    output_path = Path(output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for note in notes:
            handle.write(json.dumps(note, sort_keys=False, ensure_ascii=True) + "\n")

    audit = {
        "version": "book_extraction_audit_v1",
        "request_path": str(Path(request_path)),
        "book_index_path": str(Path(book_index_path)),
        "output_jsonl": str(output_path),
        "selected_blocker": blocker,
        "selected_books": [
            {
                "book_id": item.book.book_id,
                "book_title": item.book.title,
                "score": item.score,
                "matched_terms": list(item.matched_terms),
                "reasons": list(item.reasons),
            }
            for item in selected
        ],
        "pages_scanned_by_book": {
            item.book.book_id: int(pages_scanned_by_book.get(item.book.book_id, 0))
            for item in selected
        },
        "notes_written": len(notes),
        "provider_used": provider_name or None,
        "pdf_parser_used": {
            item.book.book_id: pdf_parser_used.get(item.book.book_id)
            for item in selected
        },
        "errors": errors,
        "safety": {
            "strategy_modification_allowed": False,
            "backtest_allowed": False,
            "promotion_allowed": False,
            "registry_write_allowed": False,
            "service_restart_allowed": False,
            "broker_calls_allowed": False,
        },
    }
    audit_path = Path(audit_json)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return len(notes), audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded Hermes book extraction request.")
    parser.add_argument("--request", required=True, help="Path to book_extraction_request_v1 JSON.")
    parser.add_argument("--book-index", required=True, help="Path to the private book index JSON.")
    parser.add_argument("--books-root", required=True, help="Root of the private Hermes books directory.")
    parser.add_argument("--output-jsonl", required=True, help="Path to the extracted notes JSONL output.")
    parser.add_argument("--audit-json", required=True, help="Path to the extraction audit JSON output.")
    parser.add_argument("--max-books", type=int, default=DEFAULT_MAX_BOOKS)
    parser.add_argument("--max-pages-per-book", type=int, default=DEFAULT_MAX_PAGES_PER_BOOK)
    parser.add_argument("--max-notes", type=int, default=DEFAULT_MAX_NOTES)
    args = parser.parse_args(argv)

    try:
        notes_written, audit = run_book_extraction_request(
            request_path=args.request,
            book_index_path=args.book_index,
            books_root=args.books_root,
            output_jsonl=args.output_jsonl,
            audit_json=args.audit_json,
            max_books=args.max_books,
            max_pages_per_book=args.max_pages_per_book,
            max_notes=args.max_notes,
            env=os.environ,
        )
    except ValueError as exc:
        parser.exit(1, f"error: {exc}\n")
    print(
        f"notes_written={notes_written} selected_books={len(audit['selected_books'])} "
        f"provider_used={audit['provider_used'] or 'none'} pdf_parser_used={audit['pdf_parser_used'] or 'none'}"
    )
    return 0


def _validate_request(request: dict[str, Any]) -> str:
    if request.get("no_request_reason") not in (None, "") or request.get("request_type") == "no_request":
        raise ValueError("no_request requests cannot be executed")
    if request.get("version") != REQUEST_VERSION:
        raise ValueError(f"request version must be {REQUEST_VERSION}")
    if request.get("request_type") != REQUEST_TYPE:
        raise ValueError(f"request_type must be {REQUEST_TYPE}")
    if request.get("requested_worker") != REQUESTED_WORKER:
        raise ValueError(f"requested_worker must be {REQUESTED_WORKER}")
    blocker = str(request.get("blocker") or "").strip()
    if blocker != SUPPORTED_BLOCKER:
        raise ValueError(f"only blocker {SUPPORTED_BLOCKER} is supported in this runner")
    _validate_request_safety(request.get("safety"))
    return blocker


def _validate_request_safety(safety: Any) -> None:
    if not isinstance(safety, dict):
        raise ValueError("safety block is required")
    required_false = (
        "worker_execution_allowed",
        "promotion_allowed",
        "registry_write_allowed",
    )
    for field in required_false:
        if safety.get(field) is not False:
            raise ValueError(f"{field} must be false")
    if safety.get("requires_manual_review") is not True:
        raise ValueError("requires_manual_review must be true")
    forbidden_true = (
        "backtest_allowed",
        "strategy_modification_allowed",
        "service_restart_allowed",
        "broker_calls_allowed",
        "llm_calls_allowed_in_this_step",
        "pdf_parsing_allowed_in_this_step",
    )
    for field in forbidden_true:
        if safety.get(field) is True:
            raise ValueError(f"{field} must be false")


def _validate_limits(*, max_books: int, max_pages_per_book: int, max_notes: int) -> None:
    if not 1 <= max_books <= DEFAULT_MAX_BOOKS:
        raise ValueError(f"max_books must be between 1 and {DEFAULT_MAX_BOOKS}")
    if not 1 <= max_pages_per_book <= DEFAULT_MAX_PAGES_PER_BOOK:
        raise ValueError(f"max_pages_per_book must be between 1 and {DEFAULT_MAX_PAGES_PER_BOOK}")
    if not 1 <= max_notes <= DEFAULT_MAX_NOTES:
        raise ValueError(f"max_notes must be between 1 and {DEFAULT_MAX_NOTES}")


def _ensure_output_path(path: Path) -> None:
    resolved = path.resolve()
    repo_tmp = SOURCE_ROOT / "tmp"
    if resolved == SOURCE_ROOT or SOURCE_ROOT in resolved.parents:
        if repo_tmp != resolved and repo_tmp not in resolved.parents:
            raise ValueError("output paths must stay outside the source repository except under tmp/")


def _filter_selected_books_to_root(
    selected: list[SelectedBook], books_root: Path
) -> tuple[list[SelectedBook], list[dict[str, str]]]:
    allowed: list[SelectedBook] = []
    errors: list[dict[str, str]] = []
    for item in selected:
        source = Path(item.book.source_path).resolve(strict=False)
        if not _is_path_within(source, books_root):
            errors.append(
                {
                    "book_id": item.book.book_id,
                    "code": "source_path_outside_books_root",
                    "message": f"Book source path escaped books_root: {source}",
                }
            )
            continue
        allowed.append(item)
    return allowed, errors


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _extract_bounded_passages(
    selected: list[SelectedBook],
    blocker: str,
    *,
    books_root: Path,
    max_pages_per_book: int,
    max_notes: int,
) -> tuple[list[PassageCandidate], list[ExtractionDiagnostic], dict[str, int], dict[str, str]]:
    definition = get_blocker_definition(blocker)
    passages_per_book = min(MAX_PASSAGES_PER_BOOK, max_notes)
    text_dir = books_root / "text"
    max_sidecar_chars = max_pages_per_book * 4000
    candidates: list[PassageCandidate] = []
    diagnostics: list[ExtractionDiagnostic] = []
    pages_scanned_by_book: dict[str, int] = {}
    pdf_parser_used: dict[str, str] = {}
    for item in selected:
        text, error, parser_used, pages_scanned = _read_bounded_book_text(
            item,
            text_dir=text_dir,
            max_pages_per_book=max_pages_per_book,
            max_sidecar_chars=max_sidecar_chars,
        )
        pages_scanned_by_book[item.book.book_id] = pages_scanned
        if parser_used is not None:
            pdf_parser_used[item.book.book_id] = parser_used
        if error:
            message = (
                "PDF extractor dependency is unavailable."
                if error == "pdf_reader_unavailable"
                else "Book text was unavailable."
            )
            diagnostics.append(ExtractionDiagnostic(item.book.book_id, error, message))
            continue
        if not text or not text.strip():
            diagnostics.append(
                ExtractionDiagnostic(item.book.book_id, "empty_text", "Book text was empty.")
            )
            continue
        matches = _match_positions(text, definition.term_weights)
        if not matches:
            diagnostics.append(
                ExtractionDiagnostic(item.book.book_id, "no_match", "No blocker evidence term matched.")
            )
            continue
        ranked = sorted(
            _windows(text, matches),
            key=lambda window: (-sum(weight for _, _, weight in window[2]), window[0]),
        )[:passages_per_book]
        candidates.extend(_candidate(item, definition.name, text, window) for window in ranked)
    return candidates, diagnostics, pages_scanned_by_book, pdf_parser_used


def _read_bounded_book_text(
    selected: SelectedBook,
    *,
    text_dir: Path,
    max_pages_per_book: int,
    max_sidecar_chars: int,
) -> tuple[str | None, str | None, str | None, int]:
    sidecar = _sidecar_path(selected, text_dir)
    if sidecar is not None:
        try:
            with sidecar.open("r", encoding="utf-8") as handle:
                return handle.read(max_sidecar_chars), None, "sidecar_text", 0
        except (OSError, UnicodeError):
            return None, "unreadable_text", "sidecar_text", 0
    source = Path(selected.book.source_path)
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        return None, "missing_text", None, 0
    try:
        text, pages_scanned = _read_pdf_text(source, max_pages=max_pages_per_book)
        return text, None, "pypdf", pages_scanned
    except RuntimeError as exc:
        if str(exc) == "pdf_reader_unavailable":
            return None, "pdf_reader_unavailable", None, 0
        return None, "unreadable_text", None, 0
    except Exception:
        return None, "unreadable_text", None, 0


def _read_pdf_text(path: Path, *, max_pages: int) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pdf_reader_unavailable") from exc
    reader = PdfReader(str(path))
    selected_pages = reader.pages[:max_pages]
    return "\f".join((page.extract_text() or "") for page in selected_pages), len(selected_pages)


def _prompt(candidate: PassageCandidate) -> str:
    return "\n".join(
        [
            "Return exactly one JSON object and no prose.",
            "Allowed fields:",
            ", ".join(REQUIRED_PROVIDER_FIELDS),
            "Ground every claim only in the source excerpt below.",
            "Do not generate strategy code.",
            "Do not invent unsupported claims.",
            "Keep source_excerpt short and confidence conservative.",
            f"Blocker: {candidate.blocker}",
            f"Book title: {candidate.source_title}",
            f"Location: {candidate.location}",
            f"Matched terms: {', '.join(candidate.matched_terms)}",
            f"Source excerpt: {candidate.text}",
        ]
    )


def _build_note(candidate: PassageCandidate, payload: Any, *, blocker: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("provider output must be a JSON object")
    missing = [field for field in REQUIRED_PROVIDER_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"missing provider fields: {', '.join(missing)}")
    confidence = str(payload["confidence"]).strip().lower()
    if confidence not in {"low", "medium", "high"}:
        raise ValueError("confidence must be low, medium, or high")
    risk_controls = payload["risk_controls"]
    if not isinstance(risk_controls, list) or not risk_controls or any(not isinstance(item, str) or not item.strip() for item in risk_controls):
        raise ValueError("risk_controls must be a non-empty list of strings")
    page_start, page_end = _page_range(candidate.location)
    note = {
        "version": "extracted_book_note_v1",
        "blocker": blocker,
        "source_type": "book",
        "book_title": candidate.source_title,
        "book_id": candidate.book_id,
        "page_start": page_start,
        "page_end": page_end,
        "extracted_claim": _short_text(payload["extracted_claim"], 400, "extracted_claim"),
        "trading_hypothesis": _short_text(payload["trading_hypothesis"], 400, "trading_hypothesis"),
        "why_relevant_to_blocker": _short_text(payload["why_relevant_to_blocker"], 400, "why_relevant_to_blocker"),
        "implementation_hint": _short_text(payload["implementation_hint"], 300, "implementation_hint"),
        "risk_controls": [_short_text(item, 120, "risk_control") for item in risk_controls],
        "validation_hint": _short_text(payload["validation_hint"], 300, "validation_hint"),
        "source_excerpt": _source_excerpt(candidate.text),
        "confidence": confidence,
        "created_by": "hermes_book_extraction",
        "promotion_status": "not_promoted",
    }
    for field in (
        "extracted_claim",
        "trading_hypothesis",
        "why_relevant_to_blocker",
        "implementation_hint",
        "validation_hint",
        "source_excerpt",
    ):
        if _contains_unsafe_note_content(str(note[field])):
            raise UnsafeNoteError(f"unsafe note content detected in {field}")
    return note


def _short_text(value: Any, maximum: int, field: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise ValueError(f"{field} must be non-empty")
    return text[:maximum]


def _source_excerpt(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:280]


def _page_range(location: str) -> tuple[int, int]:
    match = re.fullmatch(r"page:(\d+)", str(location or "").strip())
    if not match:
        return 0, 0
    page = int(match.group(1))
    return page, page


def _contains_unsafe_note_content(value: str) -> bool:
    lowered = str(value or "").casefold()
    return any(marker in lowered for marker in UNSAFE_NOTE_MARKERS)


if __name__ == "__main__":
    raise SystemExit(main())
